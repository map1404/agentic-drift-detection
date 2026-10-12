"""Phase 10 fault-diagnosis agent: same model/provider as the frozen
investigator (reuses its ChatClient unchanged: Cerebras gpt-oss-120b,
temperature 0, seed 0, reasoning medium). Tools are computed on the trial's
panel restricted to its reference + current windows; the fault log is never
passed in. Strict-JSON answer through a forced `submit_diagnosis` call.

  python src/faults/agent.py --seeds 42 123
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

from agents.llm_investigator_agent import AuthFailed, ChatClient, QuotaExhausted
from faults.checker import SLICE_COLS, iter_fault_trials

OUT_CSV = "outputs/faults_agent_trials.csv"
OUT_RAW = "outputs/faults_agent_raw.jsonl"
OUT_CALLS = "outputs/faults_agent_toolcalls.jsonl"
MAX_CALLS = 12
FAULTS = ["stale_feed", "mislabeled_category", "unit_change", "zero_fill", "duplicated_rows", "none"]
NUMERIC = ["sales", "sell_price", "revenue"]
MAX_CHARS = 3000


def _r(x, nd=4):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), nd)


class FaultTools:
    def __init__(self, df, ref, cur):
        self.ref, self.cur = ref, cur
        d = df[(df["date"] >= ref[0]) & (df["date"] <= cur[1])]
        self.w = {"reference": d[d["date"] <= ref[1]], "current": d[d["date"] >= cur[0]]}
        self.cols = list(df.columns)

    def _win(self, window):
        if window not in self.w:
            raise ValueError("window must be 'reference' or 'current'")
        return self.w[window]

    def _col(self, col):
        if col not in self.cols:
            raise ValueError(f"unknown column {col!r}; columns: {self.cols}")
        return col

    def list_columns(self):
        return {"columns": [{"name": c, "dtype": str(t)} for c, t in self.w["current"].dtypes.items()],
                "rows": {k: int(len(v)) for k, v in self.w.items()},
                "windows": {"reference": [str(self.ref[0].date()), str(self.ref[1].date())],
                            "current": [str(self.cur[0].date()), str(self.cur[1].date())]}}

    def value_counts(self, col, window="current"):
        s = self._win(window)[self._col(col)]
        return {"col": col, "window": window, "n_unique": int(s.nunique()),
                "top": [{"value": str(k), "rows": int(v)} for k, v in s.value_counts().head(15).items()]}

    def null_rate(self, col, window="current"):
        s = self._win(window)[self._col(col)]
        return {"col": col, "window": window, "null_rate": _r(s.isna().mean())}

    def numeric_summary(self, col, window="current"):
        s = pd.to_numeric(self._win(window)[self._col(col)], errors="coerce").dropna()
        q = s.quantile([0, .25, .5, .75, .99, 1]).tolist()
        return {"col": col, "window": window, "n": int(len(s)), "mean": _r(s.mean()), "share_zero": _r((s == 0).mean()),
                "min": _r(q[0]), "p25": _r(q[1]), "median": _r(q[2]), "p75": _r(q[3]), "p99": _r(q[4]), "max": _r(q[5])}

    def slice_history(self, col, val, feature):
        self._col(col), self._col(feature)
        out = {"slice": f"{col}={val}", "feature": feature}
        for name, w in self.w.items():
            s = w[w[col].astype(str) == str(val)]
            g = s.groupby("date").agg(mean=(feature, "mean"), rows=(feature, "size"))
            if name == "reference":
                wk = g.resample("7D").mean()
                out["reference_weekly"] = [{"week_start": str(i.date()), "mean": _r(r["mean"]), "rows_per_day": _r(r["rows"], 1)}
                                           for i, r in wk.iterrows()]
            else:
                out["current_daily"] = [{"date": str(i.date()), "mean": _r(r["mean"]), "rows": int(r["rows"])}
                                        for i, r in g.iterrows()]
        if not len(out.get("current_daily", [])):
            out["error"] = "no rows for this slice in the current window"
        return out

    def compare_windows(self, col, feature):
        self._col(col), self._col(feature)
        rows = []
        for name, w in self.w.items():
            days = max(w["date"].nunique(), 1)
            g = w.groupby(col)[feature].agg(["mean", "size"])
            g["rows_per_day"] = g["size"] / days
            rows.append(g.add_prefix(name + "_"))
        t = pd.concat(rows, axis=1)
        t["mean_ratio"] = t["current_mean"] / t["reference_mean"].replace(0, np.nan)
        t["rows_ratio"] = t["current_rows_per_day"] / t["reference_rows_per_day"].replace(0, np.nan)
        key = np.abs(np.log(t["mean_ratio"].clip(lower=1e-6).fillna(1))) + np.abs(np.log(t["rows_ratio"].fillna(1)))
        t = t.loc[key.sort_values(ascending=False).index[:12]]
        return {"col": col, "feature": feature, "n_values": int(len(rows[0])),
                "most_changed": [{"value": str(i), "ref_mean": _r(r.reference_mean), "cur_mean": _r(r.current_mean),
                                  "mean_ratio": _r(r.mean_ratio), "ref_rows_per_day": _r(r.reference_rows_per_day, 1),
                                  "cur_rows_per_day": _r(r.current_rows_per_day, 1)} for i, r in t.iterrows()]}

    def duplicate_check(self):
        out = {}
        for name, w in self.w.items():
            dup = w[w.duplicated(["id", "date"], keep=False)]
            out[name] = {"duplicated_id_date_rows": int(len(dup)),
                         "by_slice": {c: {str(k): int(v) for k, v in dup[c].value_counts().head(5).items()}
                                      for c in ("state_id", "store_id", "dept_id")} if len(dup) else {}}
        return out

    def constant_run_check(self, feature="sales", min_run=7):
        self._col(feature)
        min_run = int(min_run)
        out = {"feature": feature, "min_run_days": min_run, "note": "trailing run = run ending on the window's last day"}
        for name, w in self.w.items():
            p = w.drop_duplicates(["id", "date"]).pivot(index="id", columns="date", values=feature).to_numpy()[:, ::-1]
            same = np.isclose(p, p[:, :1])
            run = np.where(same.all(axis=1), p.shape[1], same.argmin(axis=1))
            ids = w.drop_duplicates(["id", "date"]).sort_values(["id", "date"])["id"].drop_duplicates().to_numpy()
            flag = run >= min_run
            zero = flag & (p[:, 0] == 0)
            nonzero = flag & (p[:, 0] != 0)
            meta = w.drop_duplicates("id").set_index("id")
            res = {"n_series": int(len(ids)), "series_with_trailing_zero_run": int(zero.sum()),
                   "series_with_trailing_constant_nonzero_run": int(nonzero.sum())}
            for label, m in (("zero", zero), ("nonzero", nonzero)):
                if m.sum():
                    sel = meta.loc[ids[m]]
                    res[f"top_slices_{label}"] = {c: {str(k): int(v) for k, v in sel[c].value_counts().head(4).items()}
                                                  for c in ("store_id", "dept_id", "item_id")}
            out[name] = res
        return out

    def label_consistency(self, col_a, col_b):
        self._col(col_a), self._col(col_b)
        out = {"col_a": col_a, "col_b": col_b}
        for name, w in self.w.items():
            g = w.groupby(col_a)[col_b].nunique()
            bad = g[g > 1]
            ex = []
            for v in bad.index[:5]:
                s = w[w[col_a] == v]
                ex.append({col_a: str(v), f"{col_b}_values": {str(k): int(n) for k, n in s[col_b].value_counts().items()},
                           "dates_per_value": {str(k): [str(s[s[col_b] == k]["date"].min().date()),
                                                        str(s[s[col_b] == k]["date"].max().date())]
                                               for k in s[col_b].unique()}})
            out[name] = {"n_col_a_values": int(len(g)), "n_mapping_to_multiple": int(len(bad)), "examples": ex}
        return out

    NAMES = ["list_columns", "value_counts", "null_rate", "numeric_summary", "slice_history", "compare_windows",
             "duplicate_check", "constant_run_check", "label_consistency"]

    def call(self, name, args):
        try:
            if name not in self.NAMES:
                raise ValueError(f"unknown tool {name!r}")
            out = getattr(self, name)(**(args or {}))
        except (ValueError, TypeError, KeyError) as e:
            out = {"error": str(e)[:300]}
        text = json.dumps(out, default=str)
        return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + '..."[truncated]'


SYSTEM_PROMPT = """\
You are a data-quality engineer. A daily retail sales panel feeds a demand-forecasting model. One row = one item in one store on one day; columns include id (item_store series), item_id, dept_id, cat_id, store_id, state_id, date, sales, sell_price, revenue. Hierarchy: item -> dept -> cat, store -> state.

TASK: decide whether the CURRENT window contains a data-pipeline fault, using the REFERENCE window (the days just before) as normal behaviour. Possible answers:
- stale_feed: a feed stops updating and keeps repeating its last values.
- mislabeled_category: rows carry a wrong category or department label.
- unit_change: a column's values switch units (a constant multiplicative factor).
- zero_fill: missing data was filled with zeros.
- duplicated_rows: some rows were ingested twice.
- none: no data-pipeline fault.
Real retail data also contains genuine demand changes, sparse items with long zero runs, and normal price changes; those are not faults. If there is a fault, give the affected column and the slice (one column and one value) where it occurs.

BUDGET: at most {max_calls} tool calls, then call submit_diagnosis."""

_C = {"type": "string"}
_W = {"type": "string", "enum": ["reference", "current"]}


def _fn(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}}}


TOOL_DEFS = [
    _fn("list_columns", "Columns, dtypes, row counts and dates of both windows.", {}, []),
    _fn("value_counts", "Top values of a column with row counts in one window.", {"col": _C, "window": _W}, ["col"]),
    _fn("null_rate", "Share of missing values in a column in one window.", {"col": _C, "window": _W}, ["col"]),
    _fn("numeric_summary", "Mean, share of zeros and quantiles of a numeric column in one window.", {"col": _C, "window": _W}, ["col"]),
    _fn("slice_history", "For rows where col == val: weekly mean of feature (and rows per day) in the reference window, daily mean and row count in the current window.",
        {"col": _C, "val": _C, "feature": _C}, ["col", "val", "feature"]),
    _fn("compare_windows", "For every value of col: mean of feature and rows per day in reference vs current; the most changed values.",
        {"col": _C, "feature": _C}, ["col", "feature"]),
    _fn("duplicate_check", "Rows whose (id, date) key occurs more than once, per window, with the slices they fall in.", {}, []),
    _fn("constant_run_check", "Series whose feature value stays constant over the last min_run (or more) days of each window, split into zero and non-zero runs, with the slices they fall in.",
        {"feature": _C, "min_run": {"type": "integer"}}, []),
    _fn("label_consistency", "Does col_a map to a single col_b value in each window? Lists values that map to several, with dates.",
        {"col_a": _C, "col_b": _C}, ["col_a", "col_b"]),
    {"type": "function", "function": {"name": "submit_diagnosis", "description": "Submit the diagnosis.",
     "parameters": {"type": "object", "properties": {
         "fault_type": {"type": "string", "enum": FAULTS}, "column": {"type": ["string", "null"]},
         "slice_col": {"type": ["string", "null"]}, "slice_value": {"type": ["string", "null"]},
         "rationale": {"type": "string"}}, "required": ["fault_type", "column", "slice_col", "slice_value", "rationale"]}}},
]


def run_trial(tools, chat, ctx, max_calls=MAX_CALLS, max_turns=30):
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(max_calls=max_calls)},
                {"role": "user", "content": "Begin."}]
    transcript = [dict(m) for m in messages]
    used, budget_done, tok = 0, False, dict(prompt=0, completion=0, cached=0)
    for _ in range(max_turns):
        choice = {"type": "function", "function": {"name": "submit_diagnosis"}} if budget_done else "auto"
        out = chat(messages, [TOOL_DEFS[-1]] if budget_done else TOOL_DEFS, choice)
        tok["prompt"] += out.prompt_tokens; tok["completion"] += out.completion_tokens; tok["cached"] += out.cached_tokens
        if not out.ok:
            return dict(ok=False, fallback=out.error, n_tool_calls=used, transcript=transcript, tokens=tok)
        msg = out.message
        clean = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            clean["tool_calls"] = msg["tool_calls"]
        messages.append(clean)
        transcript.append(dict(clean, reasoning=msg.get("reasoning")))
        calls = msg.get("tool_calls") or []
        if not calls:
            m = {"role": "user", "content": "Call a tool or submit_diagnosis."}
            messages.append(m); transcript.append(m)
            continue
        for tc in calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = None
            if name == "submit_diagnosis":
                if isinstance(args, dict) and args.get("fault_type") in FAULTS and isinstance(args.get("rationale"), str):
                    return dict(ok=True, fallback=None, diagnosis=args, n_tool_calls=used, forced=budget_done,
                                transcript=transcript, tokens=tok)
                return dict(ok=False, fallback="invalid_json", raw=args, n_tool_calls=used, transcript=transcript, tokens=tok)
            if args is None:
                result = json.dumps({"error": "arguments were not valid JSON"})
            elif used >= max_calls:
                result = json.dumps({"error": "tool budget exhausted; call submit_diagnosis"})
                budget_done = True
            else:
                used += 1
                result = tools.call(name, args)
                with open(OUT_CALLS, "a") as f:
                    f.write(json.dumps(dict(ctx, ts=datetime.now(timezone.utc).isoformat(), tool=name, args=args,
                                            result=result[:500])) + "\n")
                budget_done = used >= max_calls
            m = {"role": "tool", "tool_call_id": tc["id"], "content": result}
            messages.append(m); transcript.append(m)
        if budget_done:
            m = {"role": "user", "content": "Tool budget exhausted. Call submit_diagnosis now."}
            messages.append(m); transcript.append(m)
    return dict(ok=False, fallback="max_turns", n_tool_calls=used, transcript=transcript, tokens=tok)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 123])
    ap.add_argument("--max-trials", type=int, default=None)
    a = ap.parse_args(argv)
    assert 7 not in a.seeds or a.max_trials is not None, "seed 7 only for (logged) tuning"
    load_dotenv(".env", override=True)
    df = pd.read_parquet("data/raw/real_m5_panel_cache.parquet")
    done = set()
    if os.path.exists(OUT_CSV):
        p = pd.read_csv(OUT_CSV)
        done = set(zip(p.seed, p.kind, p.trial))
    chat = ChatClient(provider="cerebras", reasoning_effort="medium", seed=0)
    n = 0
    for seed in a.seeds:
        for kind, i, ref, cur, make in iter_fault_trials(df, seed):
            if (seed, kind, i) in done:
                continue
            if a.max_trials is not None and n >= a.max_trials:
                print("STOP (max_trials)", flush=True)
                return 0
            d, log = make()  # the log stays here; only the panel goes to the tools
            ctx = dict(seed=seed, kind=kind, trial=i)
            try:
                res = run_trial(FaultTools(d, ref, cur), chat, ctx)
            except (QuotaExhausted, AuthFailed) as e:
                print(f"STOP ({type(e).__name__}: {str(e)[:160]})", flush=True)
                return 3
            n += 1
            dg = res.get("diagnosis") or {}
            with open(OUT_RAW, "a") as f:
                f.write(json.dumps(dict(ctx, provider=chat.provider, model=chat.model, temperature=0, seed_llm=0,
                                        **{k: v for k, v in res.items()}), default=str) + "\n")
            pd.DataFrame([dict(ctx, ok=res["ok"], fallback=res["fallback"], agent_type=dg.get("fault_type"),
                               agent_column=dg.get("column"), agent_slice_col=dg.get("slice_col"),
                               agent_slice_value=None if dg.get("slice_value") is None else str(dg.get("slice_value")),
                               n_tool_calls=res["n_tool_calls"], forced=res.get("forced", False),
                               model=chat.model, temperature=0, **{f"tok_{k}": v for k, v in res["tokens"].items()})]
                         ).to_csv(OUT_CSV, mode="a", index=False, header=not os.path.exists(OUT_CSV))
            print(f"seed={seed} {kind:20s} t={i:2d} true={log['fault_type']}:{log['slice_col']}={log['slice_value']} "
                  f"-> {dg.get('fault_type', 'FALLBACK:' + str(res['fallback']))}:{dg.get('slice_col')}={dg.get('slice_value')} "
                  f"calls={res['n_tool_calls']}", flush=True)
    print("COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    sys.exit(main())
