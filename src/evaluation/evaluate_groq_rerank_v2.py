"""Task 5 v2 evaluation harness: Groq re-ranking over the EXPANDED
(coarse + item-divergence + item-burst) candidate pool, vs. the exact same
deterministic baseline Tasks 0/1/3/4 and Task 5 already trust.

Methodology discipline, so this isn't prompt-tuned against the numbers
being reported: `TUNING_SEED` (7) is used for ALL iteration -- picking
`reasoning_effort`, trying the plain expanded pool vs. self-consistency
voting, etc. -- and is NEVER reported as a final result. Only the
already-committed-to seeds 42 and 123 (same ones Task 5 and Tasks 0/1/3/4
used) are used for the final reported comparison, run exactly once per
seed per repeat after a configuration is chosen on the tuning seed.

LEAKAGE FIX (caught mid-iteration, see conversation): the first version of
`pool_mode="hybrid"` chose the candidate pool (coarse-only vs. item-only)
by branching on the TRUE injector `drift_type` from the evaluation loop --
real ground-truth leakage, since the deterministic baseline being compared
against never sees that label and no real deployment would have it in
advance either. Fixed: pool choice now branches on the coarse pool's OWN
top score vs. `COARSE_SCORE_BAR` -- the exact same data-derived signal
`_detect_and_localize` already legitimately uses internally to decide
coarse-vs-burst. The `drift_type` ground-truth label was also removed from
the prompt text Groq receives (see groq_reranker_v2.py) for the same
reason.
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.sentinel import Sentinel
from agents.investigator import Investigator, COARSE_SLICE_COLUMNS, COARSE_SCORE_BAR
from agents.hierarchy import Hierarchy
from agents.groq_reranker_v2 import build_expanded_pool, groq_rerank_v2
from drift.inject_drift import inject_sudden_drift, inject_gradual_drift, inject_intermittent_drift
from evaluation.evaluate import _detect_and_localize, _stable_seed_component, wilson_interval

TUNING_SEED = 7  # held out from Task 5's reported seeds (42, 123); iteration-only

INJECTORS = {
    "sudden": (inject_sudden_drift, dict(magnitude=0.6, start_offset_days=10)),
    "gradual": (inject_gradual_drift, dict(magnitude=0.7)),
    "intermittent": (inject_intermittent_drift, dict(magnitude=3.0, n_spikes=4, spike_len_days=2, slice_col="item_id")),
}


def _vote_ranking(rankings: list[list[int]], n_pool: int) -> list[int]:
    """Majority-vote aggregation across self-consistency samples: rank pool
    indices by how often (and how highly) they appeared, ties broken by
    best (lowest) mean rank among those who mentioned it."""
    appearances = Counter()
    rank_sum = Counter()
    for ranking in rankings:
        for pos, idx in enumerate(ranking):
            appearances[idx] += 1
            rank_sum[idx] += pos
    scored = sorted(appearances.keys(), key=lambda i: (-appearances[i], rank_sum[i] / appearances[i]))
    return scored[:3]


def run_trials_v2(
    df: pd.DataFrame,
    reference_window: tuple,
    current_window_pool: tuple,
    seed: int,
    n_trials: int = 20,
    current_window_len_days: int = 30,
    n_repeats: int = 1,
    n_votes: int = 1,
    model: str = "openai/gpt-oss-20b",
    reasoning_effort: str = "low",
    request_interval_s: float = 5.0,
    pool_mode: str = "hybrid",
) -> pd.DataFrame:
    """`pool_mode`:
      - "expanded": coarse + item-divergence + burst, every trial (the
        original v2 attempt -- measurably WORSE than deterministic on the
        tuning seed, see Task 5 v2 notes; kept for comparison).
      - "coarse_only": identical pool to v1 (sanity baseline).
      - "hybrid": per-trial, DATA-DERIVED gate (not ground-truth drift_type
        -- see module docstring's "LEAKAGE FIX"): coarse-only when the
        coarse pool's own top score clears `COARSE_SCORE_BAR`, item-only
        (no coarse) when it doesn't. Same signal `_detect_and_localize`
        already legitimately uses for its own coarse-vs-burst gating, so
        this is a fair comparison -- Groq gets no information the
        deterministic baseline doesn't also have.
    """
    hierarchy = Hierarchy(df)
    sentinel = Sentinel()
    investigator = Investigator()  # default coarse, same as the trusted baseline
    item_investigator = Investigator(slice_columns=["item_id"])

    pool_start, pool_end = current_window_pool
    max_start_offset = (pool_end - pool_start).days - current_window_len_days
    reference_window_days = (reference_window[1] - reference_window[0]).days

    rows = []
    aborted_early = False
    for drift_type, (inject_fn, kwargs) in INJECTORS.items():
        if aborted_early:
            break
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

            base_row = dict(
                seed=seed, drift_type=drift_type, trial=trial, detected=detected,
                true_slice_col=log["slice_col"], true_slice_value=log["slice_value"],
                det_top_slice=det_top_repr,
                det_strict=det_strict, det_hier=det_hier,
                det_strict_top3=det_strict3, det_hier_top3=det_hier3,
            )

            if not detected or not candidates:
                for repeat in range(n_repeats):
                    rows.append(dict(
                        base_row, repeat=repeat, used_groq=False,
                        fallback_reason=("not_detected" if not detected else "no_candidates"),
                        groq_strict=False, groq_hier=False, groq_strict_top3=False, groq_hier_top3=False,
                        groq_top_slice=None, pool_size=0,
                        prompt_tokens=0, completion_tokens=0, latency_s=0.0,
                    ))
                continue

            alerts = sentinel.scan(drifted_df, trial_reference_window, current_window)
            sales_alert = next(a for a in alerts if a.feature == "sales")
            alert_context = dict(
                feature="sales",
                js_divergence=sales_alert.js_divergence, l_inf_distance=sales_alert.l_inf_distance,
            )

            if pool_mode == "coarse_only":
                include_coarse, include_item = True, False
            elif pool_mode == "hybrid":
                # data-derived gate, NOT ground-truth drift_type (see module
                # docstring's "LEAKAGE FIX"): same signal
                # `_detect_and_localize` already legitimately uses.
                raw_coarse = investigator.investigate(drifted_df, sales_alert, top_k=5)
                coarse_is_strong = bool(raw_coarse) and raw_coarse[0].score >= COARSE_SCORE_BAR
                include_coarse, include_item = coarse_is_strong, not coarse_is_strong
            else:  # "expanded"
                include_coarse, include_item = True, True
            ext_pool = build_expanded_pool(drifted_df, sales_alert, investigator, item_investigator, top_k=5,
                                            include_coarse=include_coarse, include_item=include_item)

            for repeat in range(n_repeats):
                vote_rankings = []
                last_result = None
                total_prompt_tokens = 0
                total_completion_tokens = 0
                total_latency = 0.0
                for vote in range(n_votes):
                    result = groq_rerank_v2(alert_context, ext_pool, model=model, reasoning_effort=reasoning_effort)
                    if result.fallback_reason == "daily_quota_exceeded":
                        # Stop NOW, don't grind through remaining trials
                        # hitting the same wall -- the per-minute bucket
                        # looks healthy forever, so without this check the
                        # loop would burn the rest of its wall-clock time
                        # on calls that cannot succeed today (see
                        # groq_reranker_v2.py's 429-handling comment).
                        # Break out cleanly (don't raise) so whatever rows
                        # were already collected -- for THIS seed and any
                        # earlier one -- are still returned and can be
                        # saved by the caller, instead of being lost to an
                        # uncaught exception (that really happened once,
                        # see conversation: a fully-valid 120-row seed=42
                        # result was silently discarded this way).
                        aborted_early = True
                        break
                    last_result = result  # noqa: only reached when NOT aborting this vote
                    total_prompt_tokens += result.prompt_tokens
                    total_completion_tokens += result.completion_tokens
                    total_latency += result.latency_s
                    # Adaptive pacing using Groq's REAL rate-limit headers
                    # (checked directly, see Task 5 v2 notes): a fixed
                    # interval guess is what caused a 61% fallback rate on
                    # the first validation attempt (mostly rate_limit +
                    # downstream network_error cascading from it) --
                    # that run's accuracy numbers were an artifact of
                    # pacing, not a real measurement, and were discarded.
                    # If the server says few tokens remain, wait its OWN
                    # stated reset time (+ margin) instead of guessing.
                    if result.fallback_reason == "rate_limit":
                        wait = (result.reset_tokens_s or 15.0) + 3.0
                    elif result.remaining_tokens is not None and result.remaining_tokens < 1800:
                        wait = (result.reset_tokens_s or request_interval_s) + 1.0
                    else:
                        wait = request_interval_s
                    # Hard safety cap: the token bucket is a ~1-minute
                    # window, so no legitimate wait should ever exceed
                    # ~75s. A parsing bug in a header duration (one really
                    # happened -- see groq_reranker_v2._parse_duration's
                    # docstring, a ~9.6-hour stuck sleep from misreading
                    # "577ms" as minutes) should never again be able to
                    # stall a run silently for hours.
                    wait = min(wait, 75.0)
                    time.sleep(wait)
                    if result.used_groq:
                        idxs = [ext_pool.index(e) for e in result.ranked_ext]
                        vote_rankings.append(idxs)

                if aborted_early:
                    break  # don't record a bogus row for the vote that triggered the abort

                if not vote_rankings:
                    rows.append(dict(
                        base_row, repeat=repeat, used_groq=False, fallback_reason=last_result.fallback_reason,
                        groq_strict=False, groq_hier=False, groq_strict_top3=False, groq_hier_top3=False,
                        groq_top_slice=None, groq_ranking="", pool_size=len(ext_pool),
                        prompt_tokens=total_prompt_tokens, completion_tokens=total_completion_tokens,
                        latency_s=total_latency,
                    ))
                    continue

                winning_idxs = _vote_ranking(vote_rankings, len(ext_pool)) if n_votes > 1 else vote_rankings[0]
                g_top3 = [ext_pool[i] for i in winning_idxs]
                g_top = g_top3[0] if g_top3 else None
                groq_strict = bool(g_top is not None and is_match(g_top.slice_col, g_top.slice_val))
                groq_hier = bool(g_top is not None and is_related(g_top.slice_col, g_top.slice_val))
                groq_strict3 = bool(any(is_match(c.slice_col, c.slice_val) for c in g_top3))
                groq_hier3 = bool(any(is_related(c.slice_col, c.slice_val) for c in g_top3))
                groq_top_repr = f"{g_top.slice_col}={g_top.slice_val}" if g_top else None
                ranking_repr = "|".join(f"{c.slice_col}={c.slice_val}" for c in g_top3)

                rows.append(dict(
                    base_row, repeat=repeat, used_groq=True, fallback_reason=None,
                    groq_strict=groq_strict, groq_hier=groq_hier,
                    groq_strict_top3=groq_strict3, groq_hier_top3=groq_hier3,
                    groq_top_slice=groq_top_repr, groq_ranking=ranking_repr, pool_size=len(ext_pool),
                    prompt_tokens=total_prompt_tokens, completion_tokens=total_completion_tokens,
                    latency_s=total_latency,
                ))

            if aborted_early:
                break  # out of the trial loop; the drift_type loop checks aborted_early at its top

    out = pd.DataFrame(rows)
    out.attrs["aborted_early"] = aborted_early  # non-breaking: callers that ignore .attrs see no change
    return out


def _ci(k, n):
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    lo, hi = wilson_interval(k, n)
    return (k / n, lo, hi)


def summarize(trials: pd.DataFrame) -> pd.DataFrame:
    det = trials[trials["detected"]]
    n = len(det)
    used = det[det["used_groq"]]
    n_used = len(used)
    row = dict(n_detected=n, n_used_groq=n_used, groq_success_rate=n_used / n if n else float("nan"))
    for label, k in (("strict", "det_strict"), ("hier", "det_hier"),
                      ("strict_top3", "det_strict_top3"), ("hier_top3", "det_hier_top3")):
        rate, lo, hi = _ci(int(det[k].sum()), n)
        row[f"det_{label}_rate"], row[f"det_{label}_ci_lo"], row[f"det_{label}_ci_hi"] = rate, lo, hi
    for label, k in (("strict", "groq_strict"), ("hier", "groq_hier"),
                      ("strict_top3", "groq_strict_top3"), ("hier_top3", "groq_hier_top3")):
        rate, lo, hi = _ci(int(det[k].sum()), n)
        row[f"groq_{label}_rate"], row[f"groq_{label}_ci_lo"], row[f"groq_{label}_ci_hi"] = rate, lo, hi
    return pd.DataFrame([row])


def summarize_by_drift_type(trials: pd.DataFrame) -> pd.DataFrame:
    return pd.concat([summarize(g).assign(drift_type=dt) for dt, g in trials.groupby("drift_type")], ignore_index=True)


def summarize_by_seed_repeat_drift_type(trials: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (seed, repeat, dt), g in trials.groupby(["seed", "repeat", "drift_type"]):
        row = summarize(g).iloc[0].to_dict()
        row.update(seed=seed, repeat=repeat, drift_type=dt)
        rows.append(row)
    return pd.DataFrame(rows)


def summarize_stability(trials: pd.DataFrame) -> pd.DataFrame:
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
            identical=(r0["groq_ranking"] == r1["groq_ranking"]),
            top1_identical=(r0["groq_top_slice"] == r1["groq_top_slice"]),
        ))
    return pd.DataFrame(rows)


def summarize_contradictions(trials: pd.DataFrame) -> pd.DataFrame:
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


def summarize_tokens(trials: pd.DataFrame) -> pd.DataFrame:
    attempted = trials[~trials["fallback_reason"].isin(["not_detected", "no_candidates"])]
    used = trials[trials["used_groq"]]
    return pd.DataFrame([dict(
        n_attempted_calls=len(attempted), n_successful_calls=len(used),
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
        t = run_trials_v2(df, reference_window, current_window_pool, seed=seed,
                           n_trials=20, n_repeats=2, model="openai/gpt-oss-120b",
                           request_interval_s=15.0, pool_mode="hybrid")
        all_trials.append(t)
        # Save after EVERY seed, not just at the end: a quota abort on a
        # later seed must never discard an earlier seed's already-valid,
        # fully-computed rows (this really happened once, see conversation
        # -- a clean 120-row seed=42 result was lost to an uncaught
        # exception before any CSV was ever written).
        pd.concat(all_trials, ignore_index=True).to_csv("outputs/groq_rerank_v2_trials.csv", index=False)
        print(f"=== saved {sum(len(x) for x in all_trials)} rows so far ===", flush=True)
        if t.attrs.get("aborted_early"):
            print(f"=== seed={seed} aborted early (daily quota) -- stopping, not attempting further seeds ===", flush=True)
            break

    trials = pd.concat(all_trials, ignore_index=True)

    summarize_by_seed_repeat_drift_type(trials).to_csv("outputs/groq_rerank_v2_accuracy_by_seed_repeat.csv", index=False)
    summarize_by_drift_type(trials).to_csv("outputs/groq_rerank_v2_accuracy_pooled.csv", index=False)
    summarize(trials).to_csv("outputs/groq_rerank_v2_accuracy_overall.csv", index=False)
    summarize_stability(trials).to_csv("outputs/groq_rerank_v2_stability.csv", index=False)
    summarize_contradictions(trials).to_csv("outputs/groq_rerank_v2_contradictions.csv", index=False)
    summarize_tokens(trials).to_csv("outputs/groq_rerank_v2_token_usage.csv", index=False)

    pd.set_option("display.width", 220)
    print(summarize_by_drift_type(trials).to_string())
    print(summarize(trials).to_string())
    print("DONE")
