"""AgenticInvestigator — the one component in this project that is agentic
in the strict sense (Wooldridge 2020): a controller making judgment calls
about what to test next, not a fixed script.

The base Investigator only ever scores ONE slice column at a time (e.g.
"is this cat_id='HOBBIES'?"). It structurally cannot express "is this
SPECIFICALLY HOBBIES in California?" — a two-dimensional intersection.
This module adds a controller that decides which slice *combinations* are
worth testing and stops once nothing improves.

Explicit scope boundary: the Sentinel's core divergence math is
deliberately NOT made agentic. It runs on every feature, every scan, and
needs to stay cheap, deterministic, and auditable — an LLM call for that
would be slower, costlier, and harder to audit than a fixed statistical
threshold. The EU AI Act's (2024) transparency/robustness expectations for
high-stakes ML monitoring are the regulatory reason this boundary matters
in production, not just an engineering preference. "Agentic" is scoped
here to the DECISION of what to investigate further, not the arithmetic.

Development history worth keeping visible (see project_changelog.md Phase 3
for the full narrative): getting this right required fixing three real bugs
in sequence, not just writing the "obvious" version:
  1. Raw divergence-removal score is biased toward LARGER slices (removing
     more rows mechanically lowers divergence, independent of true cause).
  2. The naive fix -- ranking by score/coverage ("efficiency") -- overcorrects
     toward tiny slices, because dividing by a near-zero coverage denominator
     amplifies noise.
  3. The real fix: a minimum-size floor (a candidate must cover at least
     `min_coverage` of the window) BEFORE ranking by efficiency, mirroring
     Slice Finder's own significance-floor design.
Also: all candidates in one scoring PASS must share ONE fixed set of
histogram bin edges (computed once from the full reference+current data),
or scores across different candidates become non-comparable because a
different subset changes the data range and therefore the bins.
"""
from __future__ import annotations

from dataclasses import dataclass
import itertools
import json
import os

import numpy as np
import pandas as pd
from scipy.spatial.distance import jensenshannon

from .sentinel import _histogram_probs
from .investigator import SliceCandidate, SLICE_COLUMNS


@dataclass
class CombinedCandidate:
    conditions: tuple  # ((col, val), (col, val), ...)
    score: float
    coverage_frac: float
    reasoning: str = ""


def _js_from_probs(p, q):
    if p.sum() == 0 or q.sum() == 0:
        return 0.0
    d = jensenshannon(p, q, base=2)
    return float(d ** 2) if not np.isnan(d) else 0.0


class _FixedBinScorer:
    """Computes JS-divergence-removal scores for arbitrary boolean masks
    against ONE fixed set of bin edges, fixing the bin-consistency bug
    described in the module docstring."""

    def __init__(self, ref: pd.Series, cur_feature: pd.Series, is_categorical: bool):
        self.is_categorical = is_categorical
        if is_categorical:
            self.bins = sorted(set(ref.dropna().unique()) | set(cur_feature.dropna().unique()))
        else:
            self.bins = np.histogram_bin_edges(pd.concat([ref, cur_feature]).dropna(), bins=20)
        self.ref_p, _ = _histogram_probs(ref, self.bins, is_categorical=is_categorical)

    def score_mask(self, cur_feature: pd.Series, mask: np.ndarray) -> tuple[float, float]:
        """Return (divergence_removal_score, coverage_frac) for removing
        the rows selected by `mask` from cur_feature."""
        total = cur_feature
        remaining = cur_feature[~mask]
        n_total = len(total)
        if n_total == 0 or mask.sum() == 0 or mask.sum() == n_total:
            return 0.0, float(mask.sum()) / max(n_total, 1)
        total_p, _ = _histogram_probs(total, self.bins, is_categorical=self.is_categorical)
        remaining_p, _ = _histogram_probs(remaining, self.bins, is_categorical=self.is_categorical)
        baseline_div = _js_from_probs(self.ref_p, total_p)
        div_without = _js_from_probs(self.ref_p, remaining_p)
        return float(baseline_div - div_without), float(mask.sum()) / n_total


class AgenticInvestigator:
    """Deterministic best-first controller (default). No API key required."""

    def __init__(self, slice_columns=None, min_coverage: float = 0.05, max_combo_size: int = 2):
        self.slice_columns = slice_columns or SLICE_COLUMNS
        self.min_coverage = min_coverage
        self.max_combo_size = max_combo_size

    def investigate(self, df: pd.DataFrame, alert, top_k_seed: int = 5) -> list[CombinedCandidate]:
        ref = df[(df["date"] >= alert.reference_window[0]) & (df["date"] <= alert.reference_window[1])]
        cur = df[(df["date"] >= alert.current_window[0]) & (df["date"] <= alert.current_window[1])]
        feature = alert.feature
        is_cat = feature in ("cat_id", "state_id", "store_id", "dept_id", "item_id")
        scorer = _FixedBinScorer(ref[feature], cur[feature], is_cat)

        # Step 1 (cheap, not agentic): seed with best single-column candidates,
        # from the top slice_columns, filtered by the min-coverage floor.
        seeds: list[CombinedCandidate] = []
        for slice_col in self.slice_columns:
            if slice_col == feature:
                continue
            for val in cur[slice_col].dropna().unique():
                mask = (cur[slice_col] == val).to_numpy()
                score, coverage = scorer.score_mask(cur[feature], mask)
                if coverage < self.min_coverage:
                    continue
                seeds.append(CombinedCandidate(((slice_col, val),), score, coverage))
        seeds.sort(key=lambda c: c.score / max(c.coverage_frac, 1e-6), reverse=True)
        top_seeds = seeds[: top_k_seed * 3]  # a few per distinct column
        # keep only the best per distinct slice column, up to top_k_seed columns
        seen_cols = {}
        for s in top_seeds:
            col = s.conditions[0][0]
            if col not in seen_cols and len(seen_cols) < top_k_seed:
                seen_cols[col] = s
            elif col in seen_cols and s.score > seen_cols[col].score:
                seen_cols[col] = s
        frontier = list(seen_cols.values())

        # Step 2 (the agentic decision): try pairwise combinations across
        # DIFFERENT slice columns from the seed frontier; keep whichever
        # improves on both parents; stop when nothing does.
        best = max(frontier, key=lambda c: c.score / max(c.coverage_frac, 1e-6)) if frontier else None
        improved = True
        tested_combos = set()
        current_frontier = frontier
        while improved and self.max_combo_size >= 2:
            improved = False
            new_candidates = []
            for a, b in itertools.combinations(current_frontier, 2):
                if a.conditions[0][0] == b.conditions[0][0]:
                    continue  # same column, skip
                combo_key = tuple(sorted(a.conditions + b.conditions))
                if combo_key in tested_combos:
                    continue
                tested_combos.add(combo_key)
                mask = np.ones(len(cur), dtype=bool)
                for col, val in combo_key:
                    mask &= (cur[col] == val).to_numpy()
                score, coverage = scorer.score_mask(cur[feature], mask)
                if coverage < self.min_coverage:
                    continue
                cand = CombinedCandidate(combo_key, score, coverage,
                                          reasoning=f"tested intersection of {combo_key}")
                new_candidates.append(cand)

            if new_candidates:
                new_candidates.sort(key=lambda c: c.score / max(c.coverage_frac, 1e-6), reverse=True)
                best_new = new_candidates[0]
                cur_best_eff = best.score / max(best.coverage_frac, 1e-6) if best else -1
                if best_new.score / max(best_new.coverage_frac, 1e-6) > cur_best_eff:
                    best = best_new
                    current_frontier = current_frontier + new_candidates[:top_k_seed]
                    improved = True

        all_ranked = sorted(
            (frontier if not tested_combos else current_frontier),
            key=lambda c: c.score / max(c.coverage_frac, 1e-6), reverse=True,
        )
        return all_ranked[:5] if all_ranked else []


# ---------------------------------------------------------------------------
# LLM tool-calling controller (opt-in). Uses the deterministic controller's
# scoring primitive as a tool the model can call, so the model decides which
# combinations to test and when to stop, rather than following a fixed
# best-first script.
# ---------------------------------------------------------------------------

TOOL_SCHEMA_ANTHROPIC = [
    {
        "name": "score_combination",
        "description": "Score how well the intersection of up to two (column, value) "
                        "conditions explains the drift, as a divergence-removal score "
                        "and the fraction of the current window it covers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "col_a": {"type": "string"},
                "val_a": {"type": "string"},
                "col_b": {"type": "string", "description": "optional second column"},
                "val_b": {"type": "string", "description": "optional second value"},
            },
            "required": ["col_a", "val_a"],
        },
    },
    {
        "name": "submit_answer",
        "description": "Submit your final answer for the root cause of the drift.",
        "input_schema": {
            "type": "object",
            "properties": {
                "col": {"type": "string"},
                "val": {"type": "string"},
                "col2": {"type": "string"},
                "val2": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["col", "val", "reasoning"],
        },
    },
]

TOOL_SCHEMA_OPENAI_COMPAT = [
    {
        "type": "function",
        "function": {
            "name": "score_combination",
            "description": TOOL_SCHEMA_ANTHROPIC[0]["description"],
            "parameters": TOOL_SCHEMA_ANTHROPIC[0]["input_schema"],
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_answer",
            "description": TOOL_SCHEMA_ANTHROPIC[1]["description"],
            "parameters": TOOL_SCHEMA_ANTHROPIC[1]["input_schema"],
        },
    },
]


class LLMAgenticInvestigator:
    """LLM tool-calling controller. Opt-in; falls back to the deterministic
    controller on ANY error (missing key, network failure, malformed tool
    call, exhausted step budget without a submit_answer call).

    Backend request formats differ and are NOT interchangeable:
      - Anthropic: `tools=[{name, description, input_schema}]`; tool
        results go back as a `role: "user"` message containing
        `content: [{"type": "tool_result", "tool_use_id": ..., "content": ...}]`.
      - OpenAI-compatible (Groq, xAI): `tools=[{"type": "function",
        "function": {name, description, parameters}}]` with
        `tool_choice: "auto"`; tool results go back as `role: "tool"`
        messages keyed by `tool_call_id`.
    """

    def __init__(self, backend: str = "anthropic", model: str | None = None,
                 api_key: str | None = None, max_steps: int = 6,
                 slice_columns=None, min_coverage: float = 0.05):
        self.backend = backend
        self.max_steps = max_steps
        self.deterministic = AgenticInvestigator(slice_columns=slice_columns, min_coverage=min_coverage)
        self.min_coverage = min_coverage
        if backend == "anthropic":
            self.model = model or "claude-sonnet-4-6"
            self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        elif backend == "groq":
            self.model = model or "llama-3.3-70b-versatile"
            self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        elif backend == "xai":
            self.model = model or "grok-2-latest"
            self.api_key = api_key or os.environ.get("XAI_API_KEY")
        else:
            raise ValueError(f"unknown backend {backend}")

    def investigate(self, df: pd.DataFrame, alert, top_k_seed: int = 3):
        try:
            return self._investigate_llm(df, alert)
        except Exception as e:  # noqa: BLE001 - deliberate, documented fallback
            print(f"[LLMAgenticInvestigator:{self.backend}] LLM control loop failed "
                  f"({type(e).__name__}: {e}); falling back to deterministic controller.")
            return self.deterministic.investigate(df, alert, top_k_seed=top_k_seed)

    def _investigate_llm(self, df: pd.DataFrame, alert):
        if not self.api_key:
            raise RuntimeError(f"{self.backend} API key not set")

        ref = df[(df["date"] >= alert.reference_window[0]) & (df["date"] <= alert.reference_window[1])]
        cur = df[(df["date"] >= alert.current_window[0]) & (df["date"] <= alert.current_window[1])]
        feature = alert.feature
        is_cat = feature in ("cat_id", "state_id", "store_id", "dept_id", "item_id")
        scorer = _FixedBinScorer(ref[feature], cur[feature], is_cat)

        def score_tool(col_a, val_a, col_b=None, val_b=None):
            mask = (cur[col_a] == val_a).to_numpy()
            if col_b and val_b:
                mask &= (cur[col_b] == val_b).to_numpy()
            score, coverage = scorer.score_mask(cur[feature], mask)
            return {"score": score, "coverage_frac": coverage, "below_min_coverage": coverage < self.min_coverage}

        system_prompt = (
            "You are the root-cause investigator for a model-drift alert. You may call "
            "score_combination repeatedly to test (column, value) or (column1,value1) AND "
            "(column2,value2) hypotheses for what slice of the data explains the drift in "
            f"feature '{feature}'. A good answer has a high score AND is not trivially tiny "
            f"(candidates covering under {self.min_coverage:.0%} of the window are unreliable). "
            "When confident, call submit_answer with your best (column, value) or combined "
            f"answer. You have at most {self.max_steps} tool calls total. "
            f"Available slice columns: {[c for c in SLICE_COLUMNS if c != feature]}."
        )

        if self.backend == "anthropic":
            return self._loop_anthropic(system_prompt, score_tool)
        else:
            return self._loop_openai_compat(system_prompt, score_tool)

    def _loop_anthropic(self, system_prompt, score_tool):
        import requests
        messages = [{"role": "user", "content": "Begin your investigation."}]
        for step in range(self.max_steps):
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": self.model, "max_tokens": 600, "system": system_prompt,
                    "tools": TOOL_SCHEMA_ANTHROPIC, "messages": messages,
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            messages.append({"role": "assistant", "content": data["content"]})
            tool_use_blocks = [b for b in data["content"] if b.get("type") == "tool_use"]
            if not tool_use_blocks:
                break
            tool_results = []
            for tb in tool_use_blocks:
                if tb["name"] == "submit_answer":
                    return self._answer_to_candidates(tb["input"])
                result = score_tool(**{k: v for k, v in tb["input"].items() if v})
                tool_results.append({"type": "tool_result", "tool_use_id": tb["id"], "content": json.dumps(result)})
            messages.append({"role": "user", "content": tool_results})
        raise RuntimeError("LLM did not submit an answer within max_steps")

    def _loop_openai_compat(self, system_prompt, score_tool):
        import requests
        url = "https://api.groq.com/openai/v1/chat/completions" if self.backend == "groq" \
            else "https://api.x.ai/v1/chat/completions"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Begin your investigation."},
        ]
        for step in range(self.max_steps):
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
                json={"model": self.model, "messages": messages, "tools": TOOL_SCHEMA_OPENAI_COMPAT, "tool_choice": "auto"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data["choices"][0]["message"]
            messages.append(msg)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                break
            for tc in tool_calls:
                args = json.loads(tc["function"]["arguments"])
                if tc["function"]["name"] == "submit_answer":
                    return self._answer_to_candidates(args)
                result = score_tool(**{k: v for k, v in args.items() if v})
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)})
        raise RuntimeError("LLM did not submit an answer within max_steps")

    @staticmethod
    def _answer_to_candidates(answer: dict):
        conditions = [(answer["col"], answer["val"])]
        if answer.get("col2") and answer.get("val2"):
            conditions.append((answer["col2"], answer["val2"]))
        return [CombinedCandidate(tuple(conditions), score=1.0, coverage_frac=float("nan"),
                                   reasoning=answer.get("reasoning", ""))]
