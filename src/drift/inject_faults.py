"""Data-pipeline fault injectors (Phase 10). New module; inject_drift.py is
untouched. Definitions are the pre-registered ones (docs/project_changelog.md,
Phase 10 addendum). Each fault starts on a random day d0 = 5..15 of the
current window and lasts to its end, on one random slice. Each returns
(df, log) with fault type, column, slice and dates.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FAULT_TYPES = ["stale_feed", "mislabeled_category", "unit_change", "zero_fill", "duplicated_rows"]


def _start(rng, current_window):
    return current_window[0] + pd.Timedelta(days=int(rng.integers(5, 16)))


def _slice(df, rng, current_window, cols):
    col = str(rng.choice(cols))
    cur = df[(df["date"] >= current_window[0]) & (df["date"] <= current_window[1])]
    return col, str(rng.choice(sorted(cur[col].astype(str).unique())))


def _log(fault, column, col, val, d0, cw, **extra):
    return dict(fault_type=fault, column=column, slice_col=col, slice_value=val,
                start=str(d0.date()), end=str(cw[1].date()), **extra)


def _mask(df, col, val, d0, cw):
    return (df[col].astype(str) == val) & (df["date"] >= d0) & (df["date"] <= cw[1])


def inject_stale_feed(df, current_window, rng):
    col, val = _slice(df, rng, current_window, ["store_id", "item_id"])
    d0 = _start(rng, current_window)
    df = df.copy()
    df["sales"] = df["sales"].astype(float)
    in_slice = df[col].astype(str) == val
    before = df[in_slice & (df["date"] < d0)].sort_values("date").groupby("id")["sales"].last()
    m = _mask(df, col, val, d0, current_window)
    df.loc[m, "sales"] = df.loc[m, "id"].map(before).fillna(0.0).to_numpy()
    df.loc[m, "revenue"] = df.loc[m, "sales"] * df.loc[m, "sell_price"]
    return df, _log("stale_feed", "sales", col, val, d0, current_window)


def inject_zero_fill(df, current_window, rng):
    col, val = _slice(df, rng, current_window, ["store_id", "dept_id", "item_id"])
    d0 = _start(rng, current_window)
    df = df.copy()
    df["sales"] = df["sales"].astype(float)
    m = _mask(df, col, val, d0, current_window)
    df.loc[m, ["sales", "revenue"]] = 0.0
    return df, _log("zero_fill", "sales", col, val, d0, current_window)


def inject_duplicated_rows(df, current_window, rng):
    col, val = _slice(df, rng, current_window, ["store_id", "state_id"])
    d0 = _start(rng, current_window)
    m = _mask(df, col, val, d0, current_window)
    df = pd.concat([df, df[m]], ignore_index=True).sort_values(["id", "date"], kind="stable").reset_index(drop=True)
    return df, _log("duplicated_rows", "rows", col, val, d0, current_window, n_duplicated=int(m.sum()))


def inject_unit_change(df, current_window, rng):
    col, val = _slice(df, rng, current_window, ["store_id", "dept_id"])
    d0 = _start(rng, current_window)
    column, factor = (("sales", 12.0) if rng.random() < 0.5 else ("sell_price", 100.0))
    df = df.copy()
    df["sales"] = df["sales"].astype(float)
    m = _mask(df, col, val, d0, current_window)
    df.loc[m, column] = df.loc[m, column] * factor
    df.loc[m, "revenue"] = df.loc[m, "sales"] * df.loc[m, "sell_price"]
    return df, _log("unit_change", column, col, val, d0, current_window, factor=factor)


def inject_mislabeled_category(df, current_window, rng):
    d0 = _start(rng, current_window)
    cur = df[(df["date"] >= current_window[0]) & (df["date"] <= current_window[1])]
    df = df.copy()
    if rng.random() < 0.5:
        col, column = "dept_id", "cat_id"
        val = str(rng.choice(sorted(cur["dept_id"].unique())))
        true_cat = df.loc[df["dept_id"] == val, "cat_id"].iloc[0]
        new = str(rng.choice(sorted(c for c in df["cat_id"].unique() if c != true_cat)))
    else:
        col, column = "item_id", "dept_id"
        val = str(rng.choice(sorted(cur["item_id"].unique())))
        true_cat = df.loc[df["item_id"] == val, "cat_id"].iloc[0]
        new = str(rng.choice(sorted(df.loc[df["cat_id"] != true_cat, "dept_id"].unique())))
    m = _mask(df, col, val, d0, current_window)
    df.loc[m, column] = new
    return df, _log("mislabeled_category", column, col, val, d0, current_window, wrong_label=new)


INJECTORS = {
    "stale_feed": inject_stale_feed, "mislabeled_category": inject_mislabeled_category,
    "unit_change": inject_unit_change, "zero_fill": inject_zero_fill, "duplicated_rows": inject_duplicated_rows,
}
