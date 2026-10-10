"""Covariate-only drift injection (Phase 9). Kept in its own module so the
existing injectors in inject_drift.py stay untouched.

Multiplies `sell_price` by (1 + magnitude) on one randomly chosen slice from
`start_offset_days` into the current window; `sales` is NOT modified, so the
true demand is unchanged and only the model's input moves. `revenue` is
recomputed for the affected rows so the panel stays internally consistent.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .inject_drift import SLICE_COLUMNS, _pick_random_slice


def inject_price_shift(df: pd.DataFrame, current_window: tuple, slice_col: str | None = None,
                       magnitude: float = 0.30, start_offset_days: int = 10,
                       rng: np.random.Generator | None = None):
    rng = rng or np.random.default_rng()
    slice_col = slice_col or rng.choice(SLICE_COLUMNS)
    slice_val = _pick_random_slice(df, slice_col, rng, current_window)
    start = current_window[0] + pd.Timedelta(days=start_offset_days)
    df = df.copy()
    mask = (df[slice_col] == slice_val) & (df["date"] >= start) & (df["date"] <= current_window[1])
    df.loc[mask, "sell_price"] = df.loc[mask, "sell_price"] * (1 + magnitude)
    if "revenue" in df:
        df.loc[mask, "revenue"] = df.loc[mask, "sales"] * df.loc[mask, "sell_price"]
    log = dict(drift_type="covariate_price", slice_col=slice_col, slice_value=str(slice_val),
               target_col="sell_price", magnitude=magnitude,
               affected_start=str(start.date()), affected_end=str(current_window[1].date()))
    return df, log
