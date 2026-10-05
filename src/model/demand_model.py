"""The actual gradient-boosted demand model the whole scenario is about
monitoring, plus a SHAP-style localization baseline the Investigator is
compared against.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import lightgbm as lgb

CAT_COLS = ["cat_id", "dept_id", "store_id", "state_id", "item_id"]
FEATURE_COLS = ["sell_price", "wday", "is_weekend", "snap", "event_flag"] + CAT_COLS


def _prep_features(df: pd.DataFrame) -> pd.DataFrame:
    X = df[FEATURE_COLS].copy()
    X["month"] = df["date"].dt.month
    for c in CAT_COLS:
        X[c] = X[c].astype("category")
    return X


def train_demand_model(df: pd.DataFrame, reference_window: tuple, params: dict | None = None):
    """Train ONLY on the reference window — mirroring a model deployed
    before the monitoring period began."""
    ref = df[(df["date"] >= reference_window[0]) & (df["date"] <= reference_window[1])]
    X = _prep_features(ref)
    y = ref["sales"].to_numpy()

    default_params = dict(
        objective="poisson",
        n_estimators=150,
        num_leaves=31,
        learning_rate=0.08,
        min_child_samples=30,
        verbosity=-1,
    )
    if params:
        default_params.update(params)

    model = lgb.LGBMRegressor(**default_params)
    model.fit(X, y, categorical_feature=CAT_COLS)
    return model


def add_model_predictions(df: pd.DataFrame, model) -> pd.DataFrame:
    """Score the full panel (reference + current), adding y_pred, residual,
    abs_error — wired into the Sentinel as optional monitored features so it
    can distinguish model-performance drift from plain covariate drift."""
    X = _prep_features(df)
    df = df.copy()
    df["y_pred"] = model.predict(X)
    df["residual"] = df["sales"] - df["y_pred"]
    df["abs_error"] = df["residual"].abs()
    return df


def compute_shap_frame(df: pd.DataFrame, model, sample_n: int | None = 200_000, random_state: int = 42) -> pd.DataFrame:
    """SHAP-based drift-localization baseline. Uses LightGBM's native
    pred_contrib=True (exact, fast) rather than the general-purpose `shap`
    package, to stay within a ~1 CPU / ~4GB RAM budget.

    Returns a frame with one shap_<feature> column per input feature (plus
    the base-value column dropped) aligned to df's row order (or a random
    sample of it, if sample_n is set).
    """
    work = df if sample_n is None or len(df) <= sample_n else df.sample(sample_n, random_state=random_state)
    X = _prep_features(work)
    booster = model.booster_
    contribs = booster.predict(X, pred_contrib=True)  # (n, n_features + 1); last col is base value
    feat_names = list(X.columns)
    shap_df = pd.DataFrame(contribs[:, :-1], columns=[f"shap_{c}" for c in feat_names], index=work.index)
    shap_df["date"] = work["date"].values
    for c in CAT_COLS:
        shap_df[c] = work[c].values
    return shap_df


def shap_localize(shap_df: pd.DataFrame, feature: str, reference_window: tuple, current_window: tuple,
                   slice_columns=None, top_k: int = 5):
    """Rank slice values by how much their mean SHAP contribution (for
    `feature`) shifted between reference and current windows.

    This is the baseline the Investigator's own divergence-removal answer is
    compared against in evaluation. SHAP explains feature importance to
    *predictions*, not distributional-shift localization, so it is expected
    (and reported, not hidden) to be a meaningfully weaker localizer for at
    least some drift types.
    """
    slice_columns = slice_columns or ["cat_id", "dept_id", "state_id", "store_id", "item_id"]
    shap_col = f"shap_{feature}"
    if shap_col not in shap_df.columns:
        return []
    ref = shap_df[(shap_df["date"] >= reference_window[0]) & (shap_df["date"] <= reference_window[1])]
    cur = shap_df[(shap_df["date"] >= current_window[0]) & (shap_df["date"] <= current_window[1])]

    results = []
    for slice_col in slice_columns:
        ref_means = ref.groupby(slice_col, observed=True)[shap_col].mean()
        cur_means = cur.groupby(slice_col, observed=True)[shap_col].mean()
        common = ref_means.index.intersection(cur_means.index)
        for val in common:
            shift = abs(cur_means[val] - ref_means[val])
            results.append((slice_col, val, float(shift)))
    results.sort(key=lambda r: r[2], reverse=True)
    return results[:top_k]
