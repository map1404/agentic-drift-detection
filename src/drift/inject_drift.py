"""Synthetic drift injection, matching the standard drift taxonomy: sudden,
gradual, intermittent. Each returns (drifted_df, log_dict), where log_dict
records the exact ground truth the evaluation harness scores against.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SLICE_COLUMNS = ["cat_id", "dept_id", "state_id", "store_id", "item_id"]


def _pick_random_slice(df: pd.DataFrame, slice_col: str, rng: np.random.Generator, current_window: tuple):
    cur = df[(df["date"] >= current_window[0]) & (df["date"] <= current_window[1])]
    vals = cur[slice_col].dropna().unique()
    return rng.choice(vals)


def inject_sudden_drift(
    df: pd.DataFrame,
    current_window: tuple,
    target_col: str = "sales",
    slice_col: str | None = None,
    magnitude: float = 0.6,
    start_offset_days: int = 10,
    rng: np.random.Generator | None = None,
):
    """Abrupt shock to target_col, confined to one randomly-chosen slice,
    starting `start_offset_days` into the current window."""
    rng = rng or np.random.default_rng()
    slice_col = slice_col or rng.choice(SLICE_COLUMNS)
    slice_val = _pick_random_slice(df, slice_col, rng, current_window)
    shock_start = current_window[0] + pd.Timedelta(days=start_offset_days)

    df = df.copy()
    if pd.api.types.is_integer_dtype(df[target_col]):
        df[target_col] = df[target_col].astype(float)
    mask = (df[slice_col] == slice_val) & (df["date"] >= shock_start) & (df["date"] <= current_window[1])
    df.loc[mask, target_col] = df.loc[mask, target_col] * (1 + magnitude)

    log = dict(
        drift_type="sudden", slice_col=slice_col, slice_value=str(slice_val),
        target_col=target_col, magnitude=magnitude,
        affected_start=str(shock_start.date()), affected_end=str(current_window[1].date()),
    )
    return df, log


def inject_gradual_drift(
    df: pd.DataFrame,
    current_window: tuple,
    target_col: str = "sales",
    slice_col: str | None = None,
    magnitude: float = 0.7,
    rng: np.random.Generator | None = None,
):
    """Ramp from 0 to `magnitude` over the current window, confined to one
    randomly-chosen slice."""
    rng = rng or np.random.default_rng()
    slice_col = slice_col or rng.choice(SLICE_COLUMNS)
    slice_val = _pick_random_slice(df, slice_col, rng, current_window)

    df = df.copy()
    if pd.api.types.is_integer_dtype(df[target_col]):
        df[target_col] = df[target_col].astype(float)
    mask = (df[slice_col] == slice_val) & (df["date"] >= current_window[0]) & (df["date"] <= current_window[1])
    idx = df.index[mask]
    days_in = (df.loc[idx, "date"] - current_window[0]).dt.days
    total_days = max((current_window[1] - current_window[0]).days, 1)
    ramp = magnitude * (days_in / total_days)
    df.loc[idx, target_col] = df.loc[idx, target_col] * (1 + ramp.to_numpy())

    log = dict(
        drift_type="gradual", slice_col=slice_col, slice_value=str(slice_val),
        target_col=target_col, magnitude=magnitude,
        affected_start=str(current_window[0].date()), affected_end=str(current_window[1].date()),
    )
    return df, log


def inject_intermittent_drift(
    df: pd.DataFrame,
    current_window: tuple,
    target_col: str = "sales",
    slice_col: str = "item_id",
    magnitude: float = 3.0,
    n_spikes: int = 4,
    spike_len_days: int = 2,
    rng: np.random.Generator | None = None,
):
    """Several short (1-2 day) spikes at random points in the window,
    confined to one slice (typically a single item_id). Each trial should
    sample a DIFFERENT random slice_val for proper statistical independence
    across trials (the caller passes a fresh rng per trial)."""
    rng = rng or np.random.default_rng()
    slice_val = _pick_random_slice(df, slice_col, rng, current_window)

    total_days = (current_window[1] - current_window[0]).days
    spike_starts = rng.choice(max(total_days - spike_len_days, 1), size=n_spikes, replace=False)
    spike_windows = [
        (current_window[0] + pd.Timedelta(days=int(s)), current_window[0] + pd.Timedelta(days=int(s) + spike_len_days))
        for s in spike_starts
    ]

    df = df.copy()
    if pd.api.types.is_integer_dtype(df[target_col]):
        df[target_col] = df[target_col].astype(float)
    mask = (df[slice_col] == slice_val) & pd.Series(False, index=df.index)
    for w_start, w_end in spike_windows:
        m = (df[slice_col] == slice_val) & (df["date"] >= w_start) & (df["date"] < w_end)
        mask = mask | m
        df.loc[m, target_col] = df.loc[m, target_col] * (1 + magnitude) + magnitude

    log = dict(
        drift_type="intermittent", slice_col=slice_col, slice_value=str(slice_val),
        target_col=target_col, magnitude=magnitude,
        affected_windows=[(str(s.date()), str(e.date())) for s, e in spike_windows],
    )
    return df, log


INJECTORS = {
    "sudden": inject_sudden_drift,
    "gradual": inject_gradual_drift,
    "intermittent": inject_intermittent_drift,
}
