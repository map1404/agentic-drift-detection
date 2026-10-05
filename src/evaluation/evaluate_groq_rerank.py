"""Task 5: does an optional Groq re-ranking step over the Investigator's
deterministic top-5 candidates improve root-cause localization accuracy
over the deterministic-only baseline, on the real M5 panel?

Protocol (matching Tasks 0/1/3/4's trusted methodology, see
CLAUDE_CODE_HANDOFF.md and investigator.py's module docstring): real cached
M5 panel, 60-day ROLLING reference window, 30-day current window, the same
three injectors (sudden/gradual/intermittent) at the same magnitudes, n=20
trials/drift-type, seed in {42, 123}. This script does NOT modify
`evaluate.py`'s trusted `_detect_and_localize`/`run_evaluation` -- it calls
`_detect_and_localize` (the exact function Tasks 0/1/3/4 already trust) to
get the deterministic top-5, then ADDS an optional Groq re-ranking step on
top, so the deterministic arm of this comparison is provably identical to
what was already validated, not a re-implementation that could silently
diverge.

Each trial's deterministic top-5 is computed ONCE (it's already proven
reproducible -- see `_stable_seed_component`), then sent to Groq
`n_repeats` times (independent live API calls, temperature=0) to check
whether Groq's answer is actually stable call-to-call, not just that the
code doesn't crash.

HONESTY GATE: `used_groq` is False whenever the call fell back for ANY
reason (no key, network, timeout, non-200, malformed JSON, out-of-range
index) -- see groq_reranker.py. All accuracy metrics are reported BOTH
"whatever happened" (fallback rows keep the deterministic top-3, same as
what the live pipeline would actually show a user) and restricted to
`used_groq == True` rows only, so a run dominated by silent fallbacks
cannot be mistaken for a real Groq result.
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.sentinel import Sentinel
from agents.investigator import Investigator
from agents.hierarchy import Hierarchy
from agents.groq_reranker import groq_rerank
from drift.inject_drift import inject_sudden_drift, inject_gradual_drift, inject_intermittent_drift
from evaluation.evaluate import _detect_and_localize, _stable_seed_component, wilson_interval

INJECTORS = {
    "sudden": (inject_sudden_drift, dict(magnitude=0.6, start_offset_days=10)),
    "gradual": (inject_gradual_drift, dict(magnitude=0.7)),
    "intermittent": (inject_intermittent_drift, dict(magnitude=3.0, n_spikes=4, spike_len_days=2, slice_col="item_id")),
}


def run_trials(
    df: pd.DataFrame,
    reference_window: tuple,
    current_window_pool: tuple,
    seed: int,
    n_trials: int = 20,
    current_window_len_days: int = 30,
    n_repeats: int = 2,
    request_interval_s: float = 1.0,
) -> pd.DataFrame:
    hierarchy = Hierarchy(df)
    sentinel = Sentinel()
    investigator = Investigator()

    pool_start, pool_end = current_window_pool
    max_start_offset = (pool_end - pool_start).days - current_window_len_days
    reference_window_days = (reference_window[1] - reference_window[0]).days

    rows = []
    for drift_type, (inject_fn, kwargs) in INJECTORS.items():
        for trial in range(n_trials):
            rng = np.random.default_rng(seed * 1000 + _stable_seed_component(drift_type) + trial)
            offset = int(rng.integers(0, max(max_start_offset, 1)))
            cur_start = pool_start + pd.Timedelta(days=offset)
            cur_end = cur_start + pd.Timedelta(days=current_window_len_days)
            current_window = (cur_start, cur_end)
            trial_reference_window = (
                cur_start - pd.Timedelta(days=reference_window_days),
                cur_start - pd.Timedelta(days=1),
            )

            drifted_df, log = inject_fn(df, current_window, rng=rng, **kwargs)
            detected, candidates, elapsed = _detect_and_localize(
                drifted_df, trial_reference_window, current_window, sentinel, investigator
            )

            def is_match(col, val):
                return str(col) == log["slice_col"] and str(val) == log["slice_value"]

            def is_related(col, val):
                return is_match(col, val) or hierarchy.is_related_slice(
                    str(col), str(val), log["slice_col"], log["slice_value"]
                )

            det_top = candidates[0] if candidates else None
            det_strict = bool(det_top is not None and is_match(det_top.slice_col, det_top.slice_val))
            det_hier = bool(det_top is not None and is_related(det_top.slice_col, det_top.slice_val))
            det_strict3 = bool(any(is_match(c.slice_col, c.slice_val) for c in candidates[:3]))
            det_hier3 = bool(any(is_related(c.slice_col, c.slice_val) for c in candidates[:3]))
            det_top_repr = f"{det_top.slice_col}={det_top.slice_val}" if det_top else None
            det_candidates_repr = "|".join(
                f"{c.slice_col}={c.slice_val}:{c.score:.6f}" for c in candidates
            )

            base_row = dict(
                seed=seed, drift_type=drift_type, trial=trial, detected=detected,
                true_slice_col=log["slice_col"], true_slice_value=log["slice_value"],
                det_top_slice=det_top_repr, det_candidates=det_candidates_repr,
                det_strict=det_strict, det_hier=det_hier,
                det_strict_top3=det_strict3, det_hier_top3=det_hier3,
                time_to_insight_s=elapsed,
            )

            if not detected or not candidates:
                for repeat in range(n_repeats):
                    rows.append(dict(
                        base_row, repeat=repeat, used_groq=False,
                        fallback_reason=("not_detected" if not detected else "no_candidates"),
                        groq_strict=False, groq_hier=False, groq_strict_top3=False, groq_hier_top3=False,
                        groq_top_slice=None, groq_ranking="",
                        prompt_tokens=0, completion_tokens=0, latency_s=0.0, http_status=None,
                    ))
                continue

            # recompute the sales alert purely for prompt context (js/l_inf
            # scores) -- same df/windows _detect_and_localize already scanned,
            # so this is a cheap, side-effect-free re-derivation, not a second
            # independent measurement
            alerts = sentinel.scan(drifted_df, trial_reference_window, current_window)
            sales_alert = next(a for a in alerts if a.feature == "sales")
            alert_context = dict(
                feature="sales", drift_type=drift_type,
                js_divergence=sales_alert.js_divergence, l_inf_distance=sales_alert.l_inf_distance,
            )

            for repeat in range(n_repeats):
                result = groq_rerank(alert_context, candidates)
                # Pacing note: this account's Groq rate limit is 8000
                # tokens/min (checked directly via response headers), and a
                # single call here uses ~500-700 tokens -- sustained 1
                # call/sec would blow that budget and turn "rate_limit"
                # fallbacks into a pacing artifact rather than a real signal
                # about Groq. `request_interval_s` paces calls to stay under
                # budget; an EXTRA backoff on an actual rate_limit hit is a
                # response to real, observed 429s, not tuning toward a
                # result -- any rate_limit that still occurs is still logged
                # in `fallback_reason`, never retried/hidden.
                if result.fallback_reason == "rate_limit":
                    time.sleep(request_interval_s + 5.0)
                else:
                    time.sleep(request_interval_s)

                g_top3 = result.ranked_candidates
                g_top = g_top3[0] if g_top3 else None
                groq_strict = bool(g_top is not None and is_match(g_top.slice_col, g_top.slice_val))
                groq_hier = bool(g_top is not None and is_related(g_top.slice_col, g_top.slice_val))
                groq_strict3 = bool(any(is_match(c.slice_col, c.slice_val) for c in g_top3))
                groq_hier3 = bool(any(is_related(c.slice_col, c.slice_val) for c in g_top3))
                groq_top_repr = f"{g_top.slice_col}={g_top.slice_val}" if g_top else None
                ranking_repr = "|".join(f"{c.slice_col}={c.slice_val}" for c in g_top3)

                rows.append(dict(
                    base_row, repeat=repeat, used_groq=result.used_groq,
                    fallback_reason=result.fallback_reason,
                    groq_strict=groq_strict, groq_hier=groq_hier,
                    groq_strict_top3=groq_strict3, groq_hier_top3=groq_hier3,
                    groq_top_slice=groq_top_repr, groq_ranking=ranking_repr,
                    prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
                    latency_s=result.latency_s, http_status=result.http_status,
                ))

    return pd.DataFrame(rows)


def _ci(k, n):
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    lo, hi = wilson_interval(k, n)
    return (k / n, lo, hi)


def summarize_accuracy(trials: pd.DataFrame) -> pd.DataFrame:
    """Top-1/top-3, strict/hier, deterministic-only vs. deterministic+Groq,
    broken out per (seed, repeat, drift_type) AND pooled. Denominator is
    always `detected` trials (matches run_evaluation()'s convention).
    Reports the Groq arm twice: "asfired" (whatever the live pipeline would
    show -- fallback rows keep the deterministic answer) and "used_groq_only"
    (restricted to rows where Groq actually returned a valid answer), so a
    fallback-dominated run cannot be mischaracterized as a Groq result.
    """
    rows = []
    groups = [("per_seed_repeat", ["seed", "repeat", "drift_type"]),
              ("pooled_by_drift_type", ["drift_type"]),
              ("pooled_overall", [])]
    for group_name, keys in groups:
        it = trials.groupby(keys) if keys else [((), trials)]
        for key, g in it:
            det = g[g["detected"]]
            n = len(det)
            used = det[det["used_groq"]]
            n_used = len(used)

            row = dict(zip(keys, key if isinstance(key, tuple) else (key,)))
            row["group"] = group_name
            row["n_detected"] = n
            row["n_used_groq"] = n_used
            row["groq_success_rate"] = n_used / n if n else float("nan")

            for label, k in (("strict", "det_strict"), ("hier", "det_hier"),
                              ("strict_top3", "det_strict_top3"), ("hier_top3", "det_hier_top3")):
                rate, lo, hi = _ci(int(det[k].sum()), n)
                row[f"det_{label}_rate"] = rate
                row[f"det_{label}_ci_lo"] = lo
                row[f"det_{label}_ci_hi"] = hi

            for label, k in (("strict", "groq_strict"), ("hier", "groq_hier"),
                              ("strict_top3", "groq_strict_top3"), ("hier_top3", "groq_hier_top3")):
                rate, lo, hi = _ci(int(det[k].sum()), n)
                row[f"groq_asfired_{label}_rate"] = rate
                row[f"groq_asfired_{label}_ci_lo"] = lo
                row[f"groq_asfired_{label}_ci_hi"] = hi

                rate_u, lo_u, hi_u = _ci(int(used[k].sum()), n_used)
                row[f"groq_used_only_{label}_rate"] = rate_u
                row[f"groq_used_only_{label}_ci_lo"] = lo_u
                row[f"groq_used_only_{label}_ci_hi"] = hi_u

            rows.append(row)
    return pd.DataFrame(rows)


def summarize_fallbacks(trials: pd.DataFrame) -> pd.DataFrame:
    return (
        trials.groupby(["seed", "repeat", "fallback_reason"], dropna=False)
        .size().rename("n").reset_index()
        .sort_values(["seed", "repeat", "n"], ascending=[True, True, False])
    )


def summarize_tokens_and_latency(trials: pd.DataFrame) -> pd.DataFrame:
    used = trials[trials["used_groq"]]
    attempted = trials[trials["fallback_reason"] != "not_detected"]
    attempted = attempted[attempted["fallback_reason"] != "no_candidates"]
    return pd.DataFrame([dict(
        n_attempted_calls=len(attempted),
        n_successful_calls=len(used),
        total_prompt_tokens=int(attempted["prompt_tokens"].sum()),
        total_completion_tokens=int(attempted["completion_tokens"].sum()),
        mean_prompt_tokens_per_call=float(attempted["prompt_tokens"].mean()) if len(attempted) else float("nan"),
        mean_completion_tokens_per_call=float(attempted["completion_tokens"].mean()) if len(attempted) else float("nan"),
        mean_latency_s=float(attempted["latency_s"].mean()) if len(attempted) else float("nan"),
        n_rate_limit_errors=int((trials["fallback_reason"] == "rate_limit").sum()),
        n_timeout_errors=int((trials["fallback_reason"] == "timeout").sum()),
        n_network_errors=int((trials["fallback_reason"] == "network_error").sum()),
        n_malformed_responses=int((trials["fallback_reason"] == "malformed_response").sum()),
        n_auth_errors=int((trials["fallback_reason"] == "auth_error").sum()),
        n_http_errors=int((trials["fallback_reason"] == "http_error").sum()),
    )])


def summarize_stability(trials: pd.DataFrame) -> pd.DataFrame:
    """Among trials with >=2 repeats where Groq actually succeeded in BOTH
    repeats, how often is the returned top-3 ranking byte-identical between
    repeat 0 and repeat 1? This is the "is Groq's answer stable run-to-run"
    check, distinct from accuracy."""
    rows = []
    for (seed, drift_type, trial), g in trials.groupby(["seed", "drift_type", "trial"]):
        g = g.sort_values("repeat")
        if len(g) < 2:
            continue
        r0, r1 = g.iloc[0], g.iloc[1]
        if not (r0["used_groq"] and r1["used_groq"]):
            continue
        rows.append(dict(
            seed=seed, drift_type=drift_type, trial=trial,
            ranking_repeat0=r0["groq_ranking"], ranking_repeat1=r1["groq_ranking"],
            identical=(r0["groq_ranking"] == r1["groq_ranking"]),
            top1_identical=(r0["groq_top_slice"] == r1["groq_top_slice"]),
        ))
    out = pd.DataFrame(rows)
    return out


def summarize_contradictions(trials: pd.DataFrame) -> pd.DataFrame:
    """Among Groq calls that actually succeeded (used_groq==True) on a
    detected trial: did Groq's top-1 answer FIX a wrong deterministic
    answer, BREAK a correct one, or leave it unchanged (both right / both
    wrong)? Reported for strict matching."""
    used = trials[trials["detected"] & trials["used_groq"]]
    fixed = int(((~used["det_strict"]) & used["groq_strict"]).sum())
    broke = int((used["det_strict"] & (~used["groq_strict"])).sum())
    both_right = int((used["det_strict"] & used["groq_strict"]).sum())
    both_wrong = int(((~used["det_strict"]) & (~used["groq_strict"])).sum())
    n = len(used)
    return pd.DataFrame([dict(
        n_groq_success_trials=n, fixed_wrong_to_right=fixed, broke_right_to_wrong=broke,
        both_right=both_right, both_wrong=both_wrong,
        fix_rate=fixed / n if n else float("nan"), break_rate=broke / n if n else float("nan"),
        net_fixed_minus_broken=fixed - broke,
    )])


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")

    df = pd.read_parquet("data/raw/real_m5_panel_cache.parquet")
    pool_end = df["date"].max()
    pool_start = pool_end - pd.Timedelta(days=200)
    reference_window = (pool_start, pool_start + pd.Timedelta(days=60))
    current_window_pool = (pool_start, pool_end)

    all_trials = []
    for seed in (42, 123):
        print(f"=== seed={seed} ===", flush=True)
        t = run_trials(df, reference_window, current_window_pool, seed=seed,
                        n_trials=20, n_repeats=2, request_interval_s=5.0)
        all_trials.append(t)

    trials = pd.concat(all_trials, ignore_index=True)
    trials.to_csv("outputs/groq_rerank_trials.csv", index=False)

    accuracy = summarize_accuracy(trials)
    accuracy.to_csv("outputs/groq_rerank_accuracy_summary.csv", index=False)

    fallbacks = summarize_fallbacks(trials)
    fallbacks.to_csv("outputs/groq_rerank_fallback_breakdown.csv", index=False)

    tokens = summarize_tokens_and_latency(trials)
    tokens.to_csv("outputs/groq_rerank_token_usage.csv", index=False)

    stability = summarize_stability(trials)
    stability.to_csv("outputs/groq_rerank_stability.csv", index=False)

    contradictions = summarize_contradictions(trials)
    contradictions.to_csv("outputs/groq_rerank_contradictions.csv", index=False)

    print(accuracy[accuracy["group"] == "pooled_by_drift_type"].to_string())
    print(accuracy[accuracy["group"] == "pooled_overall"].to_string())
    print(fallbacks.to_string())
    print(tokens.to_string())
    print("stability: identical rate =", stability["identical"].mean() if len(stability) else float("nan"))
    print(contradictions.to_string())
