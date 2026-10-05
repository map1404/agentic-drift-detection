"""Evaluation harness: n=20 trials/drift-type with real confidence intervals,
not just point estimates. Wilson score intervals for proportions (several
observed rates sit at or near 0%/100%, where the normal approximation
misbehaves), a t-distribution interval for the mean time-to-insight.

Trial independence: every trial randomizes WHICH slice value is drifted
(not just timing), via a fresh rng per trial, so trials are genuine
independent draws.
"""
from __future__ import annotations

import time
import sys
import os
import zlib

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.sentinel import Sentinel, DriftAlert
from agents.investigator import Investigator, COARSE_SCORE_BAR, BURST_SCORE_BAR
from agents.hierarchy import Hierarchy
from drift.inject_drift import inject_sudden_drift, inject_gradual_drift, inject_intermittent_drift


def _stable_seed_component(s: str) -> int:
    """Deterministic replacement for `hash(s) % 1000`.

    Python's built-in `hash()` on strings is randomized PER-PROCESS by
    default (PYTHONHASHSEED) as a security hardening measure -- it is not a
    stable function of the string across separate runs. Using it to derive
    an RNG seed silently defeats the whole point of passing `seed=42`:
    every fresh `python` invocation picks a different `hash(drift_type)`,
    so it draws a genuinely different set of trials (which slice/offset
    gets drifted) even with the same seed argument, while looking
    reproducible. Confirmed directly: three back-to-back runs of the exact
    same call gave three different RCA-strict rates. `zlib.crc32` is a
    fixed, well-defined function of the bytes, so this actually reproduces.
    """
    return zlib.crc32(s.encode()) % 1000


def wilson_interval(successes: int, n: int, confidence: float = 0.95):
    if n == 0:
        return (float("nan"), float("nan"))
    z = stats.norm.ppf(1 - (1 - confidence) / 2)
    p_hat = successes / n
    denom = 1 + z ** 2 / n
    center = (p_hat + z ** 2 / (2 * n)) / denom
    half = (z * np.sqrt((p_hat * (1 - p_hat) / n) + (z ** 2 / (4 * n ** 2)))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def t_interval(values: list[float], confidence: float = 0.95):
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n < 2:
        return (float("nan"), float("nan"), float(values.mean()) if n else float("nan"))
    mean = values.mean()
    sem = stats.sem(values)
    t = stats.t.ppf(1 - (1 - confidence) / 2, df=n - 1)
    return (mean - t * sem, mean + t * sem, mean)


def _detect_and_localize(df, reference_window, current_window, sentinel, investigator,
                          fallback_slice_cols=("cat_id", "item_id")):
    """Run Sentinel(+slice fallback) -> Investigator for the 'sales' feature
    only (what every injector targets), and time it. Returns
    (detected: bool, ranked_candidates: list[SliceCandidate], elapsed_seconds).
    `ranked_candidates` is empty if not detected; callers that only want the
    single best answer use `ranked_candidates[0] if ranked_candidates else None`
    -- this is unchanged from the previous single-candidate return, just not
    truncated to length 1 anymore, so top-3/top-5 metrics can be computed
    without re-running the pipeline.

    Two-stage localization (see investigator.py's module docstring,
    "FOLLOW-UP" section, for the full measured account): the default
    coarse-only `investigate()` structurally can't find intermittent drift
    (always item_id by injector construction). When its top score is below
    `COARSE_SCORE_BAR` -- too weak to trust -- we also try
    `investigate_bursts()`, a statistic purpose-built for short spikes
    (intermittent's shape), and use IT instead only if its own top score
    clears `BURST_SCORE_BAR`. Both bars were empirically calibrated against
    the real cached M5 panel; trusting the burst candidate unconditionally
    (no coarse-weakness gate) was tried and measurably regressed sudden from
    30% to 0% strict RCA, because plenty of non-intermittent windows also
    contain one real, non-drift-related bursty item.

    When burst wins the gate, `ranked_candidates` is the burst method's own
    top-5 (NOT the coarse list with position 1 swapped out) -- once burst
    has won, positions 2-5 should be "next most burst-like," not "next most
    coarse-like," to stay a coherent ranking from one method rather than a
    hybrid of two differently-scaled statistics.
    """
    t0 = time.time()
    alerts = sentinel.scan(df, reference_window, current_window)
    sales_alert = next(a for a in alerts if a.feature == "sales")

    if not sales_alert.is_drift:
        found = None
        for slice_col in fallback_slice_cols:
            within = sentinel.scan_within_slices(df, "sales", slice_col, reference_window, current_window)
            if within:
                found = within[0]
                break
        if found is not None:
            sales_alert = DriftAlert("sales", found.js_divergence, found.l_inf_distance, True,
                                      reference_window, current_window, method=found.method)

    if not sales_alert.is_drift:
        elapsed = time.time() - t0
        return False, [], elapsed

    candidates = investigator.investigate(df, sales_alert, top_k=5)
    top = candidates[0] if candidates else None

    if top is None or top.score < COARSE_SCORE_BAR:
        burst_candidates = investigator.investigate_bursts(df, sales_alert, top_k=5)
        if burst_candidates and burst_candidates[0].score > BURST_SCORE_BAR:
            candidates = burst_candidates

    elapsed = time.time() - t0
    return True, candidates, elapsed


def run_evaluation(
    df: pd.DataFrame,
    reference_window: tuple,
    current_window_pool: tuple,
    n_trials: int = 20,
    current_window_len_days: int = 30,
    seed: int = 42,
):
    """Run n_trials for each of sudden/gradual/intermittent drift, scoring
    detection rate, RCA accuracy (strict + hierarchy-aware), and
    time-to-insight, each with a 95% CI.

    `reference_window` sets the reference period LENGTH (its span in days),
    not a fixed pair of dates: each trial gets its own reference window of
    that length, ROLLING to end immediately before that trial's current
    window. This matters a lot on real data -- see investigator.py's module
    docstring, "FOLLOW-UP" section: a single fixed, temporally distant
    reference window (e.g. the panel's first ~700 days) picks up real
    secular trend (specific departments/stores genuinely growing over a
    ~2-year gap) as false drift, which competes with and often beats the
    actual injected signal, especially for gradual drift's weaker average
    effect. A rolling recent reference window measurably fixes most of
    this: verified on the real cached M5 panel (coarse-only method both
    times, fixed-distant reference -> rolling), sudden 25%->30% strict,
    gradual 5%->30% strict.

    Also reports top-3 accuracy (strict: true slice is ANY of the top-3
    ranked candidates; hier: any of the top-3 is an exact match or a
    hierarchy relative of the true slice) alongside the existing top-1
    numbers, plus the mean rank of the true slice among trials where it
    appears anywhere in the top 5 (a near-miss-distance measure -- it says
    nothing about the trials where it doesn't appear at all).
    """
    hierarchy = Hierarchy(df)
    sentinel = Sentinel()
    investigator = Investigator()

    injectors = {
        "sudden": (inject_sudden_drift, dict(magnitude=0.6, start_offset_days=10)),
        "gradual": (inject_gradual_drift, dict(magnitude=0.7)),
        "intermittent": (inject_intermittent_drift, dict(magnitude=3.0, n_spikes=4, spike_len_days=2, slice_col="item_id")),
    }

    pool_start, pool_end = current_window_pool
    max_start_offset = (pool_end - pool_start).days - current_window_len_days
    reference_window_days = (reference_window[1] - reference_window[0]).days

    all_results = []
    per_trial_rows = []

    for drift_type, (inject_fn, kwargs) in injectors.items():
        detections = 0
        strict_hits = 0
        hier_hits = 0
        strict_top3_hits = 0
        hier_top3_hits = 0
        true_ranks = []  # rank (1-5) of the true slice, only when it appears in the top 5
        times = []
        for trial in range(n_trials):
            rng = np.random.default_rng(seed * 1000 + _stable_seed_component(drift_type) + trial)
            offset = int(rng.integers(0, max(max_start_offset, 1)))
            cur_start = pool_start + pd.Timedelta(days=offset)
            cur_end = cur_start + pd.Timedelta(days=current_window_len_days)
            current_window = (cur_start, cur_end)
            trial_reference_window = (cur_start - pd.Timedelta(days=reference_window_days), cur_start - pd.Timedelta(days=1))

            drifted_df, log = inject_fn(df, current_window, rng=rng, **kwargs)

            detected, candidates, elapsed = _detect_and_localize(drifted_df, trial_reference_window, current_window, sentinel, investigator)
            top = candidates[0] if candidates else None
            times.append(elapsed)
            strict_hit = False
            hier_hit = False
            strict_top3_hit = False
            hier_top3_hit = False
            true_rank = None
            if detected:
                detections += 1
                if top is not None:
                    strict_hit = (str(top.slice_col) == log["slice_col"]) and (str(top.slice_val) == log["slice_value"])
                    hier_hit = strict_hit or hierarchy.is_related_slice(
                        str(top.slice_col), str(top.slice_val), log["slice_col"], log["slice_value"]
                    )
                    strict_hits += int(strict_hit)
                    hier_hits += int(hier_hit)

                for rank, c in enumerate(candidates[:5], start=1):
                    is_exact = (str(c.slice_col) == log["slice_col"]) and (str(c.slice_val) == log["slice_value"])
                    if is_exact and true_rank is None:
                        true_rank = rank
                    if rank <= 3:
                        if is_exact:
                            strict_top3_hit = True
                        if is_exact or hierarchy.is_related_slice(str(c.slice_col), str(c.slice_val), log["slice_col"], log["slice_value"]):
                            hier_top3_hit = True
                strict_top3_hits += int(strict_top3_hit)
                hier_top3_hits += int(hier_top3_hit)
                if true_rank is not None:
                    true_ranks.append(true_rank)

            per_trial_rows.append(dict(
                drift_type=drift_type, trial=trial, detected=detected,
                strict_hit=strict_hit, hier_hit=hier_hit,
                strict_top3_hit=strict_top3_hit, hier_top3_hit=hier_top3_hit,
                true_rank_in_top5=true_rank, time_to_insight_s=elapsed,
                true_slice_col=log["slice_col"], true_slice_value=log["slice_value"],
                pred_slice_col=str(top.slice_col) if top else None,
                pred_slice_value=str(top.slice_val) if top else None,
            ))

        det_lo, det_hi = wilson_interval(detections, n_trials)
        strict_lo, strict_hi = wilson_interval(strict_hits, detections) if detections else (float("nan"), float("nan"))
        hier_lo, hier_hi = wilson_interval(hier_hits, detections) if detections else (float("nan"), float("nan"))
        strict3_lo, strict3_hi = wilson_interval(strict_top3_hits, detections) if detections else (float("nan"), float("nan"))
        hier3_lo, hier3_hi = wilson_interval(hier_top3_hits, detections) if detections else (float("nan"), float("nan"))
        t_lo, t_hi, t_mean = t_interval(times)

        all_results.append(dict(
            drift_type=drift_type, n_trials=n_trials,
            detection_rate=detections / n_trials, detection_ci_lo=det_lo, detection_ci_hi=det_hi,
            rca_strict_rate=(strict_hits / detections) if detections else float("nan"),
            rca_strict_ci_lo=strict_lo, rca_strict_ci_hi=strict_hi,
            rca_hier_rate=(hier_hits / detections) if detections else float("nan"),
            rca_hier_ci_lo=hier_lo, rca_hier_ci_hi=hier_hi,
            rca_strict_top3_rate=(strict_top3_hits / detections) if detections else float("nan"),
            rca_strict_top3_ci_lo=strict3_lo, rca_strict_top3_ci_hi=strict3_hi,
            rca_hier_top3_rate=(hier_top3_hits / detections) if detections else float("nan"),
            rca_hier_top3_ci_lo=hier3_lo, rca_hier_top3_ci_hi=hier3_hi,
            mean_rank_if_in_top5=(float(np.mean(true_ranks)) if true_ranks else float("nan")),
            n_appeared_in_top5=len(true_ranks),
            mean_time_to_insight_s=t_mean, time_ci_lo=t_lo, time_ci_hi=t_hi,
        ))

    return pd.DataFrame(all_results), pd.DataFrame(per_trial_rows)


def run_false_alarm_evaluation(
    df: pd.DataFrame,
    reference_window: tuple,
    current_window_pool: tuple,
    n_windows: int = 50,
    current_window_len_days: int = 30,
):
    """Specificity check: how often does Sentinel (and the full detection
    pipeline it feeds) fire on HEALTHY data -- no injected drift at all?

    Every trial in `run_evaluation()` has drift injected, so that harness
    only ever measures the true-positive rate. It says nothing about how
    often the system cries wolf on ordinary, undrifted windows -- the other
    half of a confusion matrix that was otherwise never reported. This
    function measures exactly that, using the SAME rolling reference window
    convention as `run_evaluation()` (`reference_window`'s span sets the
    length, rolling to end right before each window).

    Window placement is DETERMINISTIC (evenly spaced across the holdout
    period via `np.linspace`), not randomly sampled: this measures a rate
    across the period, not trial-to-trial variance, so deliberate, even
    coverage is more informative here than a random draw of the same size
    -- and it sidesteps needing any RNG-seeding decision for a new use case.
    Windows overlap (30-day windows spaced ~4 days apart across ~200 days),
    which is intentional: it lets nearby windows be compared directly to
    check whether a false alarm is a persistent signal or flickers from one
    highly overlapping window to the next (the latter is a sign of noise,
    not a real, stable non-stationarity).

    Returns (summary_df, per_window_df). summary_df has one row per
    monitored feature (from `Sentinel`'s default numeric + categorical
    feature lists) plus one row for `sales_full_pipeline`. The pipeline row
    and the Investigator output reuse `_detect_and_localize` VERBATIM --
    same global-scan-then-slice-fallback logic, same gated coarse/burst
    routing in the Investigator -- so this measures exactly what a real
    trial would see, not a simplified stand-in for it. per_window_df
    additionally reports, for every window where the full pipeline fired,
    the top candidate and its score, so a human can judge whether false
    alarms "look" like real findings or not.
    """
    sentinel = Sentinel()
    investigator = Investigator()

    pool_start, pool_end = current_window_pool
    max_start_offset = (pool_end - pool_start).days - current_window_len_days
    reference_window_days = (reference_window[1] - reference_window[0]).days

    offsets = sorted(set(np.linspace(0, max(max_start_offset, 0), n_windows).astype(int).tolist()))

    per_window_rows = []
    for offset in offsets:
        cur_start = pool_start + pd.Timedelta(days=int(offset))
        cur_end = cur_start + pd.Timedelta(days=current_window_len_days)
        current_window = (cur_start, cur_end)
        trial_reference_window = (cur_start - pd.Timedelta(days=reference_window_days), cur_start - pd.Timedelta(days=1))

        # per-feature global rates, reported independently of the pipeline's
        # own fallback logic (which only ever concerns the "sales" feature)
        alerts = sentinel.scan(df, trial_reference_window, current_window)
        row = dict(offset=int(offset), window_start=str(cur_start.date()), window_end=str(cur_end.date()))
        for a in alerts:
            row[f"{a.feature}_fired"] = a.is_drift
            row[f"{a.feature}_js"] = a.js_divergence
            row[f"{a.feature}_linf"] = a.l_inf_distance

        # the pipeline's actual behavior -- same function real trials use,
        # so this is not a simplified stand-in
        detected, candidates, _elapsed = _detect_and_localize(df, trial_reference_window, current_window, sentinel, investigator)
        top = candidates[0] if candidates else None
        global_sales_alert = next(a for a in alerts if a.feature == "sales")
        fallback_method = None
        if detected and not global_sales_alert.is_drift:
            # the global scan missed it, so _detect_and_localize's fallback
            # must have caught it -- recover which one for reporting
            for slice_col in ("cat_id", "item_id"):
                within = sentinel.scan_within_slices(df, "sales", slice_col, trial_reference_window, current_window)
                if within:
                    fallback_method = within[0].method
                    break
        row["sales_full_pipeline_fired"] = detected
        row["sales_fallback_method"] = fallback_method
        row["investigator_top_score"] = top.score if top is not None else None
        row["investigator_top_slice"] = f"{top.slice_col}={top.slice_val}" if top is not None else None

        per_window_rows.append(row)

    per_window_df = pd.DataFrame(per_window_rows)
    n = len(per_window_df)

    feature_names = sentinel.numeric_features + sentinel.categorical_features
    summary_rows = []
    for feat in feature_names:
        col = f"{feat}_fired"
        k = int(per_window_df[col].sum())
        lo, hi = wilson_interval(k, n)
        summary_rows.append(dict(feature=feat, n_windows=n, false_alarm_count=k,
                                  false_alarm_rate=k / n, ci_lo=lo, ci_hi=hi))
    k = int(per_window_df["sales_full_pipeline_fired"].sum())
    lo, hi = wilson_interval(k, n)
    summary_rows.append(dict(feature="sales_full_pipeline", n_windows=n, false_alarm_count=k,
                              false_alarm_rate=k / n, ci_lo=lo, ci_hi=hi))

    return pd.DataFrame(summary_rows), per_window_df


def run_shap_comparison(
    df: pd.DataFrame,
    reference_window: tuple,
    current_window_pool: tuple,
    model,
    n_trials: int = 10,
    current_window_len_days: int = 30,
    seed: int = 123,
):
    """Head-to-head: Investigator's answer vs. the SHAP-based localization
    baseline, for sudden and gradual drift (intermittent is excluded here;
    see note below -- this exclusion predates the recent fixes and its
    original justification is now partly stale).

    `reference_window`'s span sets the rolling reference LENGTH, same
    convention as `run_evaluation()` -- each trial's reference window rolls
    to end right before that trial's current window. `model` is reused
    as-is across all trials (an already-trained, already-deployed demand
    model being monitored over time, not retrained per trial -- retraining
    per trial would be both unrealistic, real deployed models aren't
    retrained every time you check for drift, and prohibitively slow for
    20+ trials).

    Reports BOTH top-1 and top-3 (strict + hierarchy-aware) for each
    method, with Wilson CIs on top-1.

    STALE-NOTE on excluding intermittent: the original reason given was
    "base detection already fails for it in run_evaluation." That was true
    of an early synthetic-data run but is no longer true of the current
    pipeline on real data -- intermittent's detection rate is 100% (see
    Task 0-3 results), it's RCA-strict/top-3 accuracy that's weak, not
    detection. Left excluded here anyway, since expanding this comparison's
    scope wasn't asked for and SHAP's own behavior on spike-shaped drift
    hasn't been characterized.
    """
    from model.demand_model import compute_shap_frame, shap_localize

    hierarchy = Hierarchy(df)
    sentinel = Sentinel()
    investigator = Investigator()

    injectors = {
        "sudden": (inject_sudden_drift, dict(magnitude=0.6, start_offset_days=10)),
        "gradual": (inject_gradual_drift, dict(magnitude=0.7)),
    }
    pool_start, pool_end = current_window_pool
    max_start_offset = (pool_end - pool_start).days - current_window_len_days
    reference_window_days = (reference_window[1] - reference_window[0]).days

    rows = []
    for drift_type, (inject_fn, kwargs) in injectors.items():
        inv_strict, inv_hier, shap_strict, shap_hier = 0, 0, 0, 0
        inv_strict3, inv_hier3, shap_strict3, shap_hier3 = 0, 0, 0, 0
        n_detected = 0
        for trial in range(n_trials):
            rng = np.random.default_rng(seed * 1000 + _stable_seed_component(drift_type) + trial)
            offset = int(rng.integers(0, max(max_start_offset, 1)))
            cur_start = pool_start + pd.Timedelta(days=offset)
            cur_end = cur_start + pd.Timedelta(days=current_window_len_days)
            current_window = (cur_start, cur_end)
            trial_reference_window = (cur_start - pd.Timedelta(days=reference_window_days), cur_start - pd.Timedelta(days=1))

            drifted_df, log = inject_fn(df, current_window, rng=rng, **kwargs)
            detected, candidates, _ = _detect_and_localize(drifted_df, trial_reference_window, current_window, sentinel, investigator)
            top = candidates[0] if candidates else None
            if not detected:
                continue
            n_detected += 1

            def is_match(col, val):
                return str(col) == log["slice_col"] and str(val) == log["slice_value"]

            def is_related(col, val):
                return is_match(col, val) or hierarchy.is_related_slice(str(col), str(val), log["slice_col"], log["slice_value"])

            inv_strict_hit = top is not None and is_match(top.slice_col, top.slice_val)
            inv_hier_hit = top is not None and is_related(top.slice_col, top.slice_val)
            inv_strict += int(inv_strict_hit)
            inv_hier += int(inv_hier_hit)
            inv_strict3_hit = any(is_match(c.slice_col, c.slice_val) for c in candidates[:3])
            inv_hier3_hit = any(is_related(c.slice_col, c.slice_val) for c in candidates[:3])
            inv_strict3 += int(inv_strict3_hit)
            inv_hier3 += int(inv_hier3_hit)

            shap_df = compute_shap_frame(drifted_df, model, sample_n=25_000, random_state=trial)
            shap_ranked = shap_localize(shap_df, "sell_price", trial_reference_window, current_window, top_k=5)
            shap_hit_strict = bool(shap_ranked) and is_match(shap_ranked[0][0], shap_ranked[0][1])
            shap_hit_hier = bool(shap_ranked) and is_related(shap_ranked[0][0], shap_ranked[0][1])
            shap_strict += int(shap_hit_strict)
            shap_hier += int(shap_hit_hier)
            shap_hit_strict3 = any(is_match(c[0], c[1]) for c in shap_ranked[:3])
            shap_hit_hier3 = any(is_related(c[0], c[1]) for c in shap_ranked[:3])
            shap_strict3 += int(shap_hit_strict3)
            shap_hier3 += int(shap_hit_hier3)

            rows.append(dict(drift_type=drift_type, trial=trial,
                              investigator_strict=inv_strict_hit, investigator_hier=inv_hier_hit,
                              investigator_strict_top3=inv_strict3_hit, investigator_hier_top3=inv_hier3_hit,
                              shap_strict=shap_hit_strict, shap_hier=shap_hit_hier,
                              shap_strict_top3=shap_hit_strict3, shap_hier_top3=shap_hit_hier3))

        def fmt(k, n):
            if n == 0:
                return (float("nan"), float("nan"), float("nan"))
            lo, hi = wilson_interval(k, n)
            return (k / n, lo, hi)

        inv_s_rate, inv_s_lo, inv_s_hi = fmt(inv_strict, n_detected)
        inv_h_rate, inv_h_lo, inv_h_hi = fmt(inv_hier, n_detected)
        inv_s3_rate, inv_s3_lo, inv_s3_hi = fmt(inv_strict3, n_detected)
        inv_h3_rate, inv_h3_lo, inv_h3_hi = fmt(inv_hier3, n_detected)
        shap_s_rate, shap_s_lo, shap_s_hi = fmt(shap_strict, n_detected)
        shap_h_rate, shap_h_lo, shap_h_hi = fmt(shap_hier, n_detected)
        shap_s3_rate, shap_s3_lo, shap_s3_hi = fmt(shap_strict3, n_detected)
        shap_h3_rate, shap_h3_lo, shap_h3_hi = fmt(shap_hier3, n_detected)

        rows.append(dict(
            drift_type=f"{drift_type}_SUMMARY", n_detected=n_detected,
            investigator_strict_rate=inv_s_rate, investigator_strict_ci_lo=inv_s_lo, investigator_strict_ci_hi=inv_s_hi,
            investigator_hier_rate=inv_h_rate, investigator_hier_ci_lo=inv_h_lo, investigator_hier_ci_hi=inv_h_hi,
            investigator_strict_top3_rate=inv_s3_rate, investigator_strict_top3_ci_lo=inv_s3_lo, investigator_strict_top3_ci_hi=inv_s3_hi,
            investigator_hier_top3_rate=inv_h3_rate, investigator_hier_top3_ci_lo=inv_h3_lo, investigator_hier_top3_ci_hi=inv_h3_hi,
            shap_strict_rate=shap_s_rate, shap_strict_ci_lo=shap_s_lo, shap_strict_ci_hi=shap_s_hi,
            shap_hier_rate=shap_h_rate, shap_hier_ci_lo=shap_h_lo, shap_hier_ci_hi=shap_h_hi,
            shap_strict_top3_rate=shap_s3_rate, shap_strict_top3_ci_lo=shap_s3_lo, shap_strict_top3_ci_hi=shap_s3_hi,
            shap_hier_top3_rate=shap_h3_rate, shap_hier_top3_ci_lo=shap_h3_lo, shap_hier_top3_ci_hi=shap_h3_hi,
        ))

    return pd.DataFrame(rows)
