"""LLM-driven investigator agent: the LLM decides what to look at and when to
stop, using deterministic statistics as tools.

Unlike the Task 5 re-rankers (`groq_reranker*.py`), nothing here pre-selects
candidates or decides when the LLM gets control: the agent is given the
alert, the slice hierarchy and a tool budget, and runs on every trial.

LEAKAGE RULES (enforced by construction and by
`evaluation/test_llm_agent_leakage.py`):
  - `run_agent()` takes only the (possibly drifted) panel, the two windows
    and the Sentinel alert numbers. It never receives the injector's log,
    so `drift_type`, the true slice, magnitude and affected dates cannot
    reach the prompt or the tools.
  - The prompt and tool descriptions never use the injector's taxonomy
    words. Shapes are described generically (level shift, ramp, short
    bursts) because that is what the tools measure.
  - Raw M5 `sales` are integers; the sudden/gradual injectors multiply by
    non-integer factors, so drifted rows become fractional. Tools therefore
    only report per-row means, ratios and divergences (never raw quantiles
    or integer totals), so "non-integer unit sales" cannot act as a tell.

Backend: any OpenAI-compatible chat API with tool calling (Cerebras
`gpt-oss-120b` by default, Groq selectable), temperature 0, fixed seed.
A daily quota, exhausted credits or a rate limit that will not clear raise
`QuotaExhausted` so the harness can save and stop; any other unrecoverable
failure ends the trial as a logged fallback with no substitute answer.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import requests

from .groq_reranker_v2 import _parse_duration
from .investigator import Investigator, SLICE_COLUMNS
from .sentinel import DriftAlert

MAX_TOOL_CALLS = 10          # investigation calls; submit_answer is not counted
SCREEN_TOP_N = 8             # values returned when a tool is called without `val`
HISTORY_DAYS = 364           # long-run context for get_slice_history
BREAKDOWN_TOP_N = 5          # sub-groups listed in get_slice_history's change breakdown
PARENT_OF = {"item_id": "dept_id", "dept_id": "cat_id", "store_id": "state_id"}
CHILD_OF = {"cat_id": "dept_id", "dept_id": "item_id", "state_id": "store_id"}
PRODUCT_SIDE = ("cat_id", "dept_id", "item_id")
SPIKE_LEN_DAYS = 2           # same as Investigator.investigate_bursts default


def _r(x, nd=3):
    """Round for compact, integer-tell-free tool output."""
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return None
    return round(float(x), nd)


# ---------------------------------------------------------------------------
# Tools: deterministic statistics over one trial's panel
# ---------------------------------------------------------------------------

class SliceTools:
    """Per-trial tool backend. Built from the panel the agent is allowed to
    see plus the reference/current windows; nothing else."""

    def __init__(self, df: pd.DataFrame, reference_window: tuple, current_window: tuple):
        self.ref_start, self.ref_end = reference_window
        self.cur_start, self.cur_end = current_window
        hist_start = self.cur_start - pd.Timedelta(days=HISTORY_DAYS + 90)
        self.df = df[(df["date"] >= hist_start) & (df["date"] <= self.cur_end)]
        self.alert = DriftAlert("sales", float("nan"), float("nan"), True, reference_window, current_window)
        self.values = {c: sorted(self.df[c].dropna().astype(str).unique()) for c in SLICE_COLUMNS}
        self._daily_mean: dict[str, pd.DataFrame] = {}
        self._daily_sum: dict[str, pd.DataFrame] = {}
        self._js: dict[str, pd.Series] = {}
        self._coverage: dict[str, pd.Series] = {}

    # -- shared precomputation -------------------------------------------

    def _daily(self, col):
        if col not in self._daily_mean:
            g = self.df.groupby([col, "date"], observed=True)["sales"].agg(["sum", "count"])
            s = g["sum"].unstack(col).sort_index()
            n = g["count"].unstack(col).reindex_like(s)
            s.columns = s.columns.astype(str)
            n.columns = n.columns.astype(str)
            self._daily_sum[col] = s
            self._daily_mean[col] = s / n
        return self._daily_mean[col], self._daily_sum[col]

    def _window(self, frame, start, end):
        return frame[(frame.index >= start) & (frame.index <= end)]

    def _js_scores(self, col):
        """Within-slice JS divergence for every value of `col` -- the exact
        statistic the deterministic Investigator ranks by."""
        if col not in self._js:
            inv = Investigator(slice_columns=[col])
            cands = inv.investigate(self.df, self.alert, top_k=10 ** 6)
            self._js[col] = pd.Series({str(c.slice_val): c.score for c in cands}).sort_values(ascending=False)
            self._coverage[col] = pd.Series({str(c.slice_val): c.coverage_frac for c in cands})
        return self._js[col], self._coverage[col]

    def validate(self, col, val):
        if col not in SLICE_COLUMNS:
            return f"unknown col {col!r}; must be one of {SLICE_COLUMNS}"
        if val is not None and str(val) not in self.values[col]:
            hint = "" if col != "item_id" else " (screen item_id with val omitted to see real ids)"
            return f"unknown value {val!r} for {col}{hint}"
        return None

    # -- tool 1: long-run history ------------------------------------------

    def _history_stats(self, col):
        """Per value: ref/current mean, pct change, and how unusual that
        change is relative to the slice's own past 30-vs-preceding-60-day
        changes over the last year."""
        mean, _ = self._daily(col)
        ref = self._window(mean, self.ref_start, self.ref_end).mean()
        cur = self._window(mean, self.cur_start, self.cur_end).mean()
        eps = 0.05  # mean sales per item-store-day; keeps near-zero slices finite
        pct = (cur - ref) / (ref + eps)

        hist = mean[mean.index < self.cur_start]
        past = []
        for r_start, r_end, w_start, w_end in self._past_windows(hist.index.min()):
            r = self._window(hist, r_start, r_end).mean()
            c = self._window(hist, w_start, w_end).mean()
            past.append(((c - r) / (r + eps)).abs())
        past = pd.DataFrame(past)
        p90 = past.quantile(0.9) if len(past) else pd.Series(np.nan, index=pct.index)
        pctile = (past.le(pct.abs(), axis=1).mean() * 100) if len(past) else pd.Series(np.nan, index=pct.index)
        # Null for screening: in each past window, the column's TOP
        # change_vs_own_p90. A column with many values (198 items) has a
        # high top score every window by chance; a few values (3 states) do not.
        past_top = (past / (p90 + 1e-9)).max(axis=1) if len(past) else pd.Series(dtype=float)
        return ref, cur, pct, p90, pctile, len(past), past_top

    def _past_windows(self, earliest):
        """(ref_start, ref_end, cur_start, cur_end) for 30-day windows with a
        60-day preceding reference, stepping back weekly over the last year,
        all strictly before the current window."""
        for k in range(1, HISTORY_DAYS // 7 - 12):
            w_end = self.cur_start - pd.Timedelta(days=7 * k + 1)
            w_start = w_end - pd.Timedelta(days=29)
            r_start = w_start - pd.Timedelta(days=60)
            if r_start < earliest:
                break
            yield r_start, w_start - pd.Timedelta(days=1), w_start, w_end

    @staticmethod
    def _null_summary(past_top):
        if not len(past_top):
            return None
        return {"median": _r(past_top.median(), 2), "p90": _r(past_top.quantile(0.9), 2),
                "n_past_windows": len(past_top)}

    def get_slice_history(self, col, val=None):
        err = self.validate(col, val)
        if err:
            return {"error": err}
        ref, cur, pct, p90, pctile, n_past, past_top = self._history_stats(col)
        ratio = pct.abs() / (p90 + 1e-9)
        if val is None:
            top = ratio.sort_values(ascending=False).head(SCREEN_TOP_N)
            return {
                "col": col, "n_values": len(ratio),
                "ranked_by": "abs(pct_change) / p90 of this slice's own historical abs changes",
                "top": [{"val": v, "pct_change": _r(pct[v]), "change_vs_own_p90": _r(ratio[v], 2),
                         "change_percentile_vs_own_history": _r(pctile[v], 0)} for v in top.index],
            }
        v = str(val)
        mean, _ = self._daily(col)
        s = mean[v]
        months = []
        for k in range(12, 0, -1):
            b_end = self.cur_start - pd.Timedelta(days=30 * (k - 1) + 1)
            months.append(_r(self._window(s, b_end - pd.Timedelta(days=29), b_end).mean()))
        cur_s = self._window(s, self.cur_start, self.cur_end)
        blocks = [_r(b.mean()) for b in np.array_split(cur_s.to_numpy(), 6)]
        ref_s = self._window(s, self.ref_start, self.ref_end)
        return {
            "col": col, "val": v, "unit": "mean units sold per item-store-day",
            "monthly_means_past_12_30day_blocks_oldest_first": months,
            "reference_window_mean": _r(ref[v]), "current_window_mean": _r(cur[v]),
            "pct_change": _r(pct[v]),
            "current_window_6_blocks_of_~5_days": blocks,
            "own_history": {
                "n_past_windows": n_past,
                "p90_abs_pct_change": _r(p90[v]),
                "change_percentile_vs_own_history": _r(pctile[v], 0),
                "change_vs_own_p90": _r(ratio[v], 2),
            },
            "max_day_over_mean": {"reference": _r(ref_s.max() / (ref_s.mean() + 1e-9), 2),
                                  "current": _r(cur_s.max() / (ref_s.mean() + 1e-9), 2)},
            "where_the_change_lives": self._decomposition(col, v),
        }

    def _window_change(self, frame):
        """Mean daily total (ref, cur) and per-row pct change for `frame`."""
        out = {}
        for name, (a, b) in (("ref", (self.ref_start, self.ref_end)), ("cur", (self.cur_start, self.cur_end))):
            w = frame[(frame["date"] >= a) & (frame["date"] <= b)]
            days = max(w["date"].nunique(), 1)
            out[name + "_total"] = w["sales"].sum() / days
            out[name + "_row"] = w["sales"].mean() if len(w) else 0.0
        out["delta"] = out["cur_total"] - out["ref_total"]
        out["pct"] = (out["cur_row"] - out["ref_row"]) / (out["ref_row"] + 0.05)
        return out

    def _breakdown(self, col, v, by):
        """How slice col=v's change splits across the values of `by`: each
        group's own pct change and its share of the slice's total change."""
        sl = self.df[self.df[col].astype(str) == v]
        whole = self._window_change(sl)
        rows = []
        for b, g in sl.groupby(by, observed=True):
            c = self._window_change(g)
            share = c["delta"] / whole["delta"] if abs(whole["delta"]) > 1e-9 else float("nan")
            rows.append((str(b), c["pct"], share))
        rows.sort(key=lambda r: -abs(r[2]) if np.isfinite(r[2]) else 0)
        pcts = np.array([r[1] for r in rows])
        half = 0.5 * whole["pct"]
        n_follow = int((pcts >= half).sum()) if whole["pct"] >= 0 else int((pcts <= half).sum())
        return {
            "by": by, "n_groups": len(rows),
            "n_groups_moving_with_slice": n_follow,
            "median_group_pct_change": _r(float(np.median(pcts)) if len(pcts) else None),
            "top_groups_by_share_of_change": [{"val": b, "pct_change": _r(p), "share_of_change": _r(sh, 2)}
                                              for b, p, sh in rows[:BREAKDOWN_TOP_N]],
        }

    def _decomposition(self, col, v):
        out = {}
        if col in PARENT_OF:
            pcol = PARENT_OF[col]
            sl = self.df[self.df[col].astype(str) == v]
            pval = str(sl[pcol].iloc[0])
            me = self._window_change(sl)
            pframe = self.df[self.df[pcol].astype(str) == pval]
            par = self._window_change(pframe)
            out["parent"] = {"col": pcol, "val": pval, "parent_pct_change": _r(par["pct"]),
                             "share_of_parent_change": _r(me["delta"] / par["delta"], 3)
                             if abs(par["delta"]) > 1e-9 else None}
        if col in CHILD_OF:
            out["breakdown_by_children"] = self._breakdown(col, v, CHILD_OF[col])
        out["breakdown_across"] = self._breakdown(col, v, "store_id" if col in PRODUCT_SIDE else "dept_id")
        return out

    # -- tool 2: distribution comparison -----------------------------------

    def compare_windows(self, col, val=None):
        err = self.validate(col, val)
        if err:
            return {"error": err}
        js, cov = self._js_scores(col)
        ref, cur, pct, *_ = self._history_stats(col)  # noqa: unpacks extra fields
        rank = {v: i + 1 for i, v in enumerate(js.index)}
        if val is None:
            return {
                "col": col, "n_values": len(js), "ranked_by": "within-slice JS divergence, reference vs current",
                "top": [{"val": v, "js_divergence": _r(js[v], 5), "pct_change": _r(pct[v]),
                         "coverage": _r(cov[v], 4)} for v in js.index[:SCREEN_TOP_N]],
            }
        v = str(val)
        if v not in js.index:
            return {"col": col, "val": v, "error": "slice too small to score"}
        win = self.df[(self.df[col].astype(str) == v)]
        r = win[(win["date"] >= self.ref_start) & (win["date"] <= self.ref_end)]["sales"]
        c = win[(win["date"] >= self.cur_start) & (win["date"] <= self.cur_end)]["sales"]
        return {
            "col": col, "val": v,
            "js_divergence": _r(js[v], 5), "js_rank_in_column": f"{rank[v]} of {len(js)}",
            "column_median_js": _r(js.median(), 5),
            "reference_mean": _r(ref[v]), "current_mean": _r(cur[v]), "pct_change": _r(pct[v]),
            "zero_sales_share": {"reference": _r((r == 0).mean()), "current": _r((c == 0).mean())},
            "coverage_of_current_window": _r(cov[v], 4),
        }

    # -- tool 3: short-window bursts ---------------------------------------

    def _burst_scores(self, col, windows=None):
        """Same formula as Investigator.investigate_bursts, vectorized over
        the daily pivot: (max 2-day sum in current - max 2-day sum in
        reference) / (reference daily mean + 0.5)."""
        _, total = self._daily(col)
        r0, r1, c0, c1 = windows or (self.ref_start, self.ref_end, self.cur_start, self.cur_end)
        ref_t = self._window(total, r0, r1)
        cur_t = self._window(total, c0, c1)
        ref_roll = ref_t.rolling(SPIKE_LEN_DAYS).sum().max()
        cur_roll = cur_t.rolling(SPIKE_LEN_DAYS).sum().max()
        return ((cur_roll - ref_roll) / (ref_t.mean() + 0.5)).sort_values(ascending=False)

    def _burst_null(self, col):
        _, total = self._daily(col)
        tops = [self._burst_scores(col, w).max() for w in self._past_windows(total.index.min())]
        return self._null_summary(pd.Series(tops, dtype=float))

    def check_spikes(self, col, val=None):
        err = self.validate(col, val)
        if err:
            return {"error": err}
        burst = self._burst_scores(col)
        rank = {v: i + 1 for i, v in enumerate(burst.index)}
        mean, _ = self._daily(col)
        ref_m = self._window(mean, self.ref_start, self.ref_end)
        cur_m = self._window(mean, self.cur_start, self.cur_end)
        if val is None:
            return {
                "col": col, "n_values": len(burst),
                "ranked_by": f"burst score: largest {SPIKE_LEN_DAYS}-day surge in current vs reference, "
                             "scaled by reference daily level",
                "top": [{"val": v, "burst_score": _r(burst[v], 2),
                         "n_days_above_ref_max": int((cur_m[v] > ref_m[v].max()).sum())}
                        for v in burst.index[:SCREEN_TOP_N]],
            }
        v = str(val)
        base = ref_m[v].mean() + 1e-9
        roll = cur_m[v].rolling(SPIKE_LEN_DAYS).mean().dropna()
        peaks = []
        taken = []
        for d, x in roll.sort_values(ascending=False).items():
            if any(abs((d - t).days) < SPIKE_LEN_DAYS for t in taken):
                continue
            taken.append(d)
            peaks.append({"end_date": str(d.date()), "level_over_ref_mean": _r(x / base, 2)})
            if len(peaks) == 3:
                break
        ref_peak = ref_m[v].rolling(SPIKE_LEN_DAYS).mean().max() / base
        return {
            "col": col, "val": v,
            "burst_score": _r(burst[v], 2), "burst_rank_in_column": f"{rank[v]} of {len(burst)}",
            "column_median_burst": _r(burst.median(), 2),
            f"top_{SPIKE_LEN_DAYS}day_peaks_current": peaks,
            f"largest_{SPIKE_LEN_DAYS}day_peak_reference_over_ref_mean": _r(ref_peak, 2),
            "n_days_current_above_reference_max_day": int((cur_m[v] > ref_m[v].max()).sum()),
        }

    def call(self, name, args):
        fn = {"get_slice_history": self.get_slice_history, "compare_windows": self.compare_windows,
              "check_spikes": self.check_spikes}.get(name)
        if fn is None:
            # gpt-oss can invent tool names; Cerebras recommends saying so explicitly
            return {"error": f"unknown tool {name!r}: you're hallucinating a tool call. Use only "
                             "get_slice_history, compare_windows, check_spikes or submit_answer."}
        col = args.get("col")
        val = args.get("val")
        if val in ("", "*", "all", None):
            val = None
        return fn(col, val)


# ---------------------------------------------------------------------------
# Prompt and tool definitions (the literal text the model sees)
# ---------------------------------------------------------------------------

def _col_param():
    return {"type": "string", "enum": list(SLICE_COLUMNS),
            "description": "Hierarchy column: cat_id, dept_id, state_id, store_id or item_id."}


_VAL_PARAM = {"type": "string",
              "description": "One value of `col`. Omit to screen every value of the column and get the top ones."}

TOOL_DEFS = [
    {"type": "function", "function": {
        "name": "get_slice_history",
        "description": (
            "Long-run daily sales summary for a slice: twelve 30-day means before the current window, "
            "reference vs current mean, the current window split into six ~5-day blocks (a step shows "
            "as a jump between blocks, a ramp as a steady climb, short bursts as one or two high blocks), "
            "how unusual the current change is relative to this slice's own past 30-day changes over "
            "the last year, and where the change lives: the slice's share of its parent's change, and how "
            "its change splits across its children (cat->dept, dept->item, state->store) and across the "
            "other hierarchy (product slices by store, location slices by dept). Omit `val` to screen a "
            "column, ranked by change relative to own history."),
        "parameters": {"type": "object", "properties": {"col": _col_param(), "val": _VAL_PARAM},
                       "required": ["col"]}}},
    {"type": "function", "function": {
        "name": "compare_windows",
        "description": (
            "Reference vs current distribution shift for a slice: within-slice Jensen-Shannon divergence "
            "of daily item-store sales (and its rank among the column's values), mean change, share of "
            "zero-sales rows, and the slice's share of the current window. Omit `val` to screen a column, "
            "ranked by JS divergence."),
        "parameters": {"type": "object", "properties": {"col": _col_param(), "val": _VAL_PARAM},
                       "required": ["col"]}}},
    {"type": "function", "function": {
        "name": "check_spikes",
        "description": (
            f"Short-window burst statistic for a slice: the largest {SPIKE_LEN_DAYS}-day surge in the current "
            "window vs the largest in the reference window, scaled by the reference daily level (and its "
            "rank in the column), the top three surge dates, and how many current days exceed the "
            "reference window's maximum day. Omit `val` to screen a column, ranked by burst score."),
        "parameters": {"type": "object", "properties": {"col": _col_param(), "val": _VAL_PARAM},
                       "required": ["col"]}}},
    {"type": "function", "function": {
        "name": "submit_answer",
        "description": "Submit the final answer: exactly 3 distinct slices, most likely root cause first.",
        "parameters": {"type": "object", "properties": {
            "ranking": {"type": "array", "minItems": 3, "maxItems": 3, "items": {
                "type": "object", "properties": {"col": _col_param(), "val": {"type": "string"}},
                "required": ["col", "val"]}},
            "reasoning": {"type": "string", "description": "Why this ranking, citing the tool evidence."}},
            "required": ["ranking", "reasoning"]}}},
]

SYSTEM_PROMPT_TEMPLATE = """\
You are the root-cause investigator for a retail demand-forecasting monitor.

DATA: daily unit sales for {n_items} products in {n_stores} stores (Walmart M5 sample). One row = one item in one store on one day.
Hierarchy: item_id -> dept_id -> cat_id, and store_id -> state_id.
- cat_id: {cat_vals}
- dept_id: {dept_vals}
- state_id: {state_vals}
- store_id: {store_vals}
- item_id: {n_items} items named <dept_id>_<3-digit number> (e.g. FOODS_3_NNN). Screen item_id (omit val) to find specific item ids.

SITUATION: the monitor compared the current window ({cur_start} to {cur_end}) with the reference window ({ref_start} to {ref_end}, the days immediately before).
Whole-panel sales shift: JS divergence = {js:.6f}, L-infinity distance = {linf:.6f}; alert threshold exceeded: {is_drift}.

TASK: find the single slice (one column = one value) that most plausibly contains an anomalous change in sales in the current window, and submit a ranked top 3.
- The anomaly can sit at any level of the hierarchy, from a whole category or state down to one item. Do not assume a level; check several.
- Real slices also change organically (seasonality, launches, delistings, trends). Judge a change against the slice's own history, not just its raw size.
- The anomaly may be a lasting level shift, a ramp, or a few short bursts; the tools measure these differently.
- Rank first the level where the change actually lives, and verify it with get_slice_history's where_the_change_lives section rather than assuming it:
  - If most sub-groups across the other hierarchy move together (high n_groups_moving_with_slice), the change is not specific to one store or one department on that side.
  - If one child carries most of a parent's change while its siblings stay flat, the child is the better answer than the parent.
  - A child with a large % change but a small share_of_parent_change is not what drives its parent; large % swings in low-volume items are common and usually organic.

BUDGET: at most {max_calls} investigation tool calls; each result reports how many remain. Then call submit_answer with exactly 3 distinct slices (most likely first) and your reasoning. Only submit slices that exist in the data."""


def build_system_prompt(tools: SliceTools, js: float, linf: float, is_drift: bool, max_calls: int) -> str:
    v = tools.values
    return SYSTEM_PROMPT_TEMPLATE.format(
        n_items=len(v["item_id"]), n_stores=len(v["store_id"]),
        cat_vals=", ".join(v["cat_id"]), dept_vals=", ".join(v["dept_id"]),
        state_vals=", ".join(v["state_id"]), store_vals=", ".join(v["store_id"]),
        cur_start=tools.cur_start.date(), cur_end=tools.cur_end.date(),
        ref_start=tools.ref_start.date(), ref_end=tools.ref_end.date(),
        js=js, linf=linf, is_drift="yes" if is_drift else "no", max_calls=max_calls,
    )


USER_KICKOFF = "Begin the investigation."
NUDGE = "Continue: call an investigation tool, or call submit_answer."
BUDGET_DONE = "Tool budget exhausted. Call submit_answer now with your best ranked top 3."


# ---------------------------------------------------------------------------
# Chat client (OpenAI-compatible; Cerebras by default) with explicit
# failure classes
# ---------------------------------------------------------------------------

PROVIDERS = {
    # Cerebras free trial for gpt-oss-120b, as reported by the API's own
    # x-ratelimit-* headers (2026-10-04): 5 requests/min, 150/hour, 2400/day;
    # 30K tokens/min, 1M/hour, 1M/day. The daily token counter drops by
    # uncached prompt + completion tokens (cached prompt tokens are free).
    # 150 requests/hour is the binding limit: one request per 24.5s = 147/h.
    # A request is rejected up front if prompt + max_completion_tokens
    # exceeds the remaining bucket, so max_completion_tokens is kept moderate.
    "cerebras": dict(url="https://api.cerebras.ai/v1/chat/completions", key_env="CEREBRAS_API_KEY",
                     model="gpt-oss-120b", min_interval_s=24.5,
                     extra={"reasoning_format": "parsed"}),
    "groq": dict(url="https://api.groq.com/openai/v1/chat/completions", key_env="GROQ_API_KEY",
                 model="openai/gpt-oss-120b", min_interval_s=0.0, extra={}),
}
DEFAULT_PROVIDER = "cerebras"


class QuotaExhausted(Exception):
    """Daily quota, exhausted credits, or a rate limit that will not clear:
    the harness must save and stop. Never recorded as a trial fallback."""


class AuthFailed(Exception):
    """Bad or missing key: every trial would fail, so stop the run."""


@dataclass
class ChatResult:
    ok: bool
    message: dict | None = None
    error: str | None = None
    detail: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    latency_s: float = 0.0
    n_retries: int = 0
    ratelimit_headers: dict | None = None


def _ratelimit_headers(resp):
    return {k.lower(): v for k, v in resp.headers.items() if "ratelimit" in k.lower() or k.lower() == "retry-after"}


def _is_daily_limit(body: str, headers: dict) -> bool:
    """Neither provider documents a machine-readable daily-vs-minute flag,
    so check the message text and any day-scoped remaining counter."""
    text = body.lower()
    if any(s in text for s in ("per day", "tokens per day", "requests per day", "daily", "credit", "billing")):
        return True
    return any("day" in k and "remaining" in k and str(v).strip() in ("0", "0.0") for k, v in headers.items())


def _reset_wait(headers: dict) -> float | None:
    """Seconds until the per-minute bucket refills, from whatever reset
    header the provider sends (Groq: '2.5s'/'577ms'; others: plain seconds)."""
    if "retry-after" in headers:
        try:
            return float(headers["retry-after"])
        except ValueError:
            pass
    for k, v in headers.items():
        if "reset" in k and "day" not in k and ("token" in k or "request" in k):
            try:
                return float(v)
            except ValueError:
                d = _parse_duration(v)
                if d is not None:
                    return d
    return None


class ChatClient:
    def __init__(self, provider=DEFAULT_PROVIDER, model=None, reasoning_effort="medium",
                 max_completion_tokens=4096, api_key=None, timeout=90.0, max_retries=6,
                 min_interval_s=None, max_rate_limit_wait_s=600.0, seed=0):
        cfg = PROVIDERS[provider]
        self.provider = provider
        self.url = cfg["url"]
        self.extra = cfg["extra"]
        self.model = model or cfg["model"]
        self.reasoning_effort = reasoning_effort
        self.max_completion_tokens = max_completion_tokens
        self.api_key = api_key or os.environ.get(cfg["key_env"])
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval_s = cfg["min_interval_s"] if min_interval_s is None else min_interval_s
        self.max_rate_limit_wait_s = max_rate_limit_wait_s
        self.seed = seed
        self._last_request = 0.0
        self.last_ratelimit: dict = {}
        if not self.api_key:
            raise AuthFailed(f"{cfg['key_env']} not set")

    def _throttle(self):
        wait = self._last_request + self.min_interval_s - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.time()

    def __call__(self, messages, tools, tool_choice="auto") -> ChatResult:
        payload = {"model": self.model, "messages": messages, "tools": tools, "tool_choice": tool_choice,
                   "temperature": 0, "seed": self.seed, "max_completion_tokens": self.max_completion_tokens,
                   "reasoning_effort": self.reasoning_effort, **self.extra}
        retries = 0
        rate_wait = 0.0
        last = None
        while retries <= self.max_retries:
            self._throttle()
            t0 = time.time()
            try:
                resp = requests.post(self.url, json=payload, timeout=self.timeout,
                                     headers={"Authorization": f"Bearer {self.api_key}"})
            except requests.exceptions.RequestException as e:
                last = ("network_error", str(e)[:300])
                retries += 1
                time.sleep(min(5 * 2 ** retries, 60))
                continue
            latency = time.time() - t0
            body = resp.text[:1000]
            rl = _ratelimit_headers(resp)
            if rl:
                self.last_ratelimit = rl
            if resp.status_code in (401, 403):
                raise AuthFailed(body)
            if resp.status_code == 402:
                raise QuotaExhausted(f"payment required (credits exhausted?): {body}")
            if resp.status_code == 429:
                if _is_daily_limit(body, rl):
                    raise QuotaExhausted(f"{body} | headers={rl}")
                # Per-minute limit: wait and retry WITHOUT consuming a retry,
                # but never let it become a trial fallback. If it will not
                # clear, it is really a quota problem: stop the run.
                wait = min((_reset_wait(rl) or 15.0 * 2 ** min(retries, 2)) + 2.0, 75.0)
                rate_wait += wait
                if rate_wait > self.max_rate_limit_wait_s:
                    raise QuotaExhausted(f"rate limit did not clear after {rate_wait:.0f}s: {body} | headers={rl}")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                last = (f"http_{resp.status_code}", body)
                retries += 1
                time.sleep(min(5 * 2 ** retries, 60))
                continue
            if resp.status_code != 200:
                # e.g. 400 when the model emitted an unparseable tool call
                err = "tool_use_failed" if "tool_use_failed" in body else f"http_{resp.status_code}"
                return ChatResult(False, error=err, detail=body, latency_s=latency, n_retries=retries,
                                  ratelimit_headers=rl)
            data = resp.json()
            usage = data.get("usage") or {}
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
            return ChatResult(True, message=data["choices"][0]["message"],
                              prompt_tokens=int(usage.get("prompt_tokens", 0)),
                              completion_tokens=int(usage.get("completion_tokens", 0)),
                              cached_tokens=int(cached), latency_s=latency, n_retries=retries,
                              ratelimit_headers=rl)
        return ChatResult(False, error=f"retries_exhausted:{last[0] if last else '?'}",
                          detail=last[1] if last else None, n_retries=retries)


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    ok: bool
    fallback_reason: str | None
    ranking: list = field(default_factory=list)   # [(col, val), ...] len 3 when ok
    reasoning: str = ""
    n_tool_calls: int = 0                          # investigation calls executed
    n_invalid_calls: int = 0                       # calls rejected (bad args, over budget)
    n_submit_attempts: int = 0
    forced_submit: bool = False                    # budget ran out, submit was forced
    n_turns: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    tool_log: list = field(default_factory=list)   # one dict per tool call
    transcript: list = field(default_factory=list) # full messages incl. model reasoning


def _validate_submission(tools: SliceTools, args: dict):
    ranking = args.get("ranking")
    if not isinstance(ranking, list) or len(ranking) != 3:
        return None, "ranking must be a list of exactly 3 {col, val} objects"
    out = []
    for item in ranking:
        if not isinstance(item, dict):
            return None, "each ranking entry must be an object with col and val"
        col, val = item.get("col"), item.get("val")
        err = tools.validate(col, val) if val is not None else "val is required"
        if err:
            return None, err
        out.append((col, str(val)))
    if len(set(out)) != 3:
        return None, "the 3 slices must be distinct"
    return out, None


def run_agent(df: pd.DataFrame, reference_window: tuple, current_window: tuple,
              js: float, linf: float, is_drift: bool, chat,
              max_tool_calls: int = MAX_TOOL_CALLS, max_turns: int = 24,
              max_nudges: int = 2, max_tool_use_failures: int = 2,
              max_submit_attempts: int = 3) -> AgentResult:
    """Run one investigation. Inputs are the panel and the alert numbers
    only (see module docstring). Raises QuotaExhausted/AuthFailed; every
    other failure returns ok=False with a fallback_reason and no answer.

    `messages` is exactly what the API sees; `res.transcript` is the same
    conversation plus each assistant turn's hidden `reasoning` and harness
    events, for the trace log."""
    tools = SliceTools(df, reference_window, current_window)
    system = build_system_prompt(tools, js, linf, is_drift, max_tool_calls)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": USER_KICKOFF}]
    res = AgentResult(ok=False, fallback_reason=None)
    res.transcript = [dict(m) for m in messages]
    used = 0
    nudges = 0
    tool_use_failures = 0
    budget_done = False

    def send(m):
        messages.append(m)
        res.transcript.append(dict(m))

    def finish(reason=None):
        res.fallback_reason = reason
        res.n_tool_calls = used
        return res

    for turn in range(max_turns):
        tool_choice = {"type": "function", "function": {"name": "submit_answer"}} if budget_done else "auto"
        out = chat(messages, [TOOL_DEFS[-1]] if budget_done else TOOL_DEFS, tool_choice)
        res.n_turns += 1
        res.prompt_tokens += out.prompt_tokens
        res.completion_tokens += out.completion_tokens
        res.cached_tokens += out.cached_tokens
        if not out.ok:
            res.transcript.append({"role": "_harness", "content": f"{out.error}: {out.detail}"})
            if out.error == "tool_use_failed" and tool_use_failures < max_tool_use_failures:
                tool_use_failures += 1
                continue
            return finish(out.error)

        msg = out.message
        clean = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            clean["tool_calls"] = msg["tool_calls"]
        messages.append(clean)
        res.transcript.append(dict(clean, reasoning=msg.get("reasoning"),
                                   _usage=dict(prompt=out.prompt_tokens, completion=out.completion_tokens,
                                               cached=out.cached_tokens),
                                   _ratelimit=out.ratelimit_headers))
        calls = msg.get("tool_calls") or []

        if not calls:
            if nudges >= max_nudges:
                return finish("no_tool_call")
            nudges += 1
            send({"role": "user", "content": BUDGET_DONE if budget_done else NUDGE})
            continue

        for tc in calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = None
            entry = {"turn": turn, "tool": name, "args": args}

            if args is None:
                result = {"error": "arguments were not valid JSON"}
                res.n_invalid_calls += 1
            elif name == "submit_answer":
                res.n_submit_attempts += 1
                ranking, err = _validate_submission(tools, args)
                if ranking is not None:
                    entry["result"] = "accepted"
                    res.tool_log.append(entry)
                    res.ok = True
                    res.ranking = ranking
                    res.reasoning = str(args.get("reasoning", ""))
                    res.forced_submit = budget_done
                    return finish(None)
                result = {"error": f"submission rejected: {err}"}
                if res.n_submit_attempts >= max_submit_attempts:
                    entry["result"] = result
                    res.tool_log.append(entry)
                    return finish("invalid_submission")
            elif used >= max_tool_calls:
                result = {"error": "tool budget exhausted; call submit_answer"}
                res.n_invalid_calls += 1
                budget_done = True
            else:
                t0 = time.time()
                result = tools.call(name, args)
                entry["compute_s"] = round(time.time() - t0, 2)
                if "error" in result:
                    res.n_invalid_calls += 1
                used += 1
                result["calls_remaining"] = max_tool_calls - used
                budget_done = used >= max_tool_calls
            entry["result"] = result
            res.tool_log.append(entry)
            send({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)})

        if budget_done:
            send({"role": "user", "content": BUDGET_DONE})

    return finish("max_turns")


def dump_prompt_and_tools(path: str):
    """Write the literal system prompt template, kickoff/nudge messages and
    tool definitions the model receives (python -m agents.llm_investigator_agent)."""
    with open(path, "w") as f:
        f.write("# LLM investigator agent: literal prompt and tool definitions\n\n"
                "Generated from `src/agents/llm_investigator_agent.py`. `{...}` fields are filled per trial "
                "from the panel's own value lists, the window dates, and the Sentinel's whole-panel "
                "JS / L-inf scores; nothing else is substituted.\n\n## System prompt template\n\n```\n"
                + SYSTEM_PROMPT_TEMPLATE + "\n```\n\n## Other messages\n\n"
                f"- first user message: `{USER_KICKOFF}`\n- if the model replies without a tool call: `{NUDGE}`\n"
                f"- when the tool budget is used up (only submit_answer is then offered, and forced): `{BUDGET_DONE}`\n\n"
                f"Request settings: provider {DEFAULT_PROVIDER} ({PROVIDERS[DEFAULT_PROVIDER]['model']}), "
                f"temperature 0, seed 0, tool_choice auto, max {MAX_TOOL_CALLS} investigation calls, "
                "screening returns top " f"{SCREEN_TOP_N} values.\n\n## Tool definitions (sent verbatim)\n\n```json\n"
                + json.dumps(TOOL_DEFS, indent=2) + "\n```\n")


if __name__ == "__main__":
    dump_prompt_and_tools("docs/agent_prompt_and_tools.md")
