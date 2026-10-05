"""Targeted evaluation: inject drift on the INTERSECTION of two dimensions
(e.g. cat_id='HOBBIES' AND state_id='CA') -- something the base Investigator
structurally cannot name correctly, since it only ever scores one column at
a time. Compares the base Investigator against the AgenticInvestigator's
combination search on exactly this scenario.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from agents.sentinel import Sentinel, DriftAlert
from agents.investigator import Investigator
from agents.agentic_investigator import AgenticInvestigator
from agents.hierarchy import Hierarchy
from evaluation.evaluate import wilson_interval

COMBO_DIMENSIONS = [("cat_id", "state_id"), ("dept_id", "state_id"), ("cat_id", "store_id")]


def run_intersection_evaluation(df, reference_window, current_window_pool, n_trials=10,
                                 current_window_len_days=30, magnitude=0.8, seed=99):
    hierarchy = Hierarchy(df)
    sentinel = Sentinel()
    base_inv = Investigator()
    agentic_inv = AgenticInvestigator()

    pool_start, pool_end = current_window_pool
    max_start_offset = (pool_end - pool_start).days - current_window_len_days

    rows = []
    base_exact, agentic_exact, base_hier_both, agentic_hier_both = 0, 0, 0, 0
    n_run = 0

    for trial in range(n_trials):
        rng = np.random.default_rng(seed * 7 + trial)
        col_a, col_b = COMBO_DIMENSIONS[trial % len(COMBO_DIMENSIONS)]
        offset = int(rng.integers(0, max(max_start_offset, 1)))
        cur_start = pool_start + pd.Timedelta(days=offset)
        cur_end = cur_start + pd.Timedelta(days=current_window_len_days)
        current_window = (cur_start, cur_end)

        cur_slice = df[(df["date"] >= cur_start) & (df["date"] <= cur_end)]
        val_a = rng.choice(cur_slice[col_a].dropna().unique())
        val_b = rng.choice(cur_slice[col_b].dropna().unique())

        d2 = df.copy()
        if pd.api.types.is_integer_dtype(d2["sales"]):
            d2["sales"] = d2["sales"].astype(float)
        mask = (d2[col_a] == val_a) & (d2[col_b] == val_b) & (d2["date"] >= cur_start) & (d2["date"] <= cur_end)
        d2.loc[mask, "sales"] = d2.loc[mask, "sales"] * (1 + magnitude) + 1.0

        alerts = sentinel.scan(d2, reference_window, current_window)
        sales_alert = next(a for a in alerts if a.feature == "sales")
        if not sales_alert.is_drift:
            within = sentinel.scan_within_slices(d2, "sales", col_a, reference_window, current_window)
            if not within:
                continue
            sales_alert = DriftAlert("sales", within[0].js_divergence, within[0].l_inf_distance, True,
                                      reference_window, current_window)
        n_run += 1

        base_cands = base_inv.investigate(d2, sales_alert, top_k=1)
        base_top = base_cands[0] if base_cands else None
        base_is_exact = base_top is not None and (
            (str(base_top.slice_col) == col_a and str(base_top.slice_val) == str(val_a)) or
            (str(base_top.slice_col) == col_b and str(base_top.slice_val) == str(val_b))
        )
        base_is_hier_both = base_top is not None and (
            hierarchy.is_related_slice(str(base_top.slice_col), str(base_top.slice_val), col_a, str(val_a)) or
            hierarchy.is_related_slice(str(base_top.slice_col), str(base_top.slice_val), col_b, str(val_b))
        )

        agentic_cands = agentic_inv.investigate(d2, sales_alert)
        agentic_top = agentic_cands[0] if agentic_cands else None
        agentic_is_exact = False
        agentic_is_hier_both = False
        if agentic_top is not None:
            pred_conds = dict(agentic_top.conditions)
            exact_a = pred_conds.get(col_a) == val_a
            exact_b = pred_conds.get(col_b) == val_b
            agentic_is_exact = exact_a and exact_b
            hier_a = any(hierarchy.is_related_slice(c, v, col_a, str(val_a)) for c, v in agentic_top.conditions)
            hier_b = any(hierarchy.is_related_slice(c, v, col_b, str(val_b)) for c, v in agentic_top.conditions)
            agentic_is_hier_both = hier_a and hier_b

        base_exact += int(base_is_exact)
        agentic_exact += int(agentic_is_exact)
        base_hier_both += int(base_is_hier_both)
        agentic_hier_both += int(agentic_is_hier_both)

        rows.append(dict(
            trial=trial, true_col_a=col_a, true_val_a=str(val_a), true_col_b=col_b, true_val_b=str(val_b),
            base_top_col=str(base_top.slice_col) if base_top else None,
            base_top_val=str(base_top.slice_val) if base_top else None,
            base_exact_both_dims=base_is_exact, base_hier_both_dims=base_is_hier_both,
            agentic_conditions=str(agentic_top.conditions) if agentic_top else None,
            agentic_exact_both_dims=agentic_is_exact, agentic_hier_both_dims=agentic_is_hier_both,
        ))

    trials_df = pd.DataFrame(rows)
    if n_run == 0:
        return pd.DataFrame(), trials_df

    def ci_row(name, k):
        lo, hi = wilson_interval(k, n_run)
        return dict(metric=name, n=n_run, rate=k / n_run, ci_lo=lo, ci_hi=hi)

    summary = pd.DataFrame([
        ci_row("base_investigator_exact_both_dims", base_exact),
        ci_row("agentic_investigator_exact_both_dims", agentic_exact),
        ci_row("base_investigator_hier_both_dims", base_hier_both),
        ci_row("agentic_investigator_hier_both_dims", agentic_hier_both),
    ])
    return summary, trials_df


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    df = pd.read_parquet("data/raw/synthetic_panel_cache.parquet")
    max_date = df["date"].max()
    holdout_start = max_date - pd.Timedelta(days=183)
    reference_window = (df["date"].min(), holdout_start - pd.Timedelta(days=1))
    current_window_pool = (holdout_start, max_date)
    summary, trials = run_intersection_evaluation(df, reference_window, current_window_pool, n_trials=10)
    print(summary.to_string())
    summary.to_csv("outputs/agentic_intersection_summary.csv", index=False)
    trials.to_csv("outputs/agentic_intersection_trials.csv", index=False)
