"""Non-LLM data-quality checker (Phase 10). Written and committed BEFORE any
agent run. Rules ONLY for stale_feed, zero_fill and duplicated_rows (the
pre-registered "covered" faults); it has no rule for mislabeled_category or
unit_change and must not get one after results are seen.

Per trial it computes, for every item-store series (`id`) in the current
window: the trailing run of zeros, the trailing run of one repeated non-zero
value, and the reference-window mean; plus the (id, date) keys that occur more
than once. Rules (thresholds fit on seed 7 only, see fit()):
  duplicated_rows  any duplicated (id, date) key
  zero_fill        >= n_min series with a trailing zero run >= k_zero days whose
                   reference mean >= m_ref (a series that normally sells went to 0)
  stale_feed       >= n_min series with a trailing constant non-zero run >= k_stale
If several fire: duplicated_rows first, then whichever of zero_fill /
stale_feed flags more series. Location: the (slice column, value) whose
series best match the flagged series (max F1 of flagged vs slice series).
"""
from __future__ import annotations

import itertools
import json
import os
import zlib

import numpy as np
import pandas as pd

from drift.inject_faults import INJECTORS

SLICE_COLS = ["cat_id", "dept_id", "state_id", "store_id", "item_id"]
POOL_DAYS, REF_DAYS, WINDOW_DAYS = 200, 60, 30
N_FAULT, N_CLEAN = 20, 50
COVERED = ["stale_feed", "zero_fill", "duplicated_rows"]
HELD_OUT = ["mislabeled_category", "unit_change"]
GRID = dict(k_zero=[5, 7, 10, 14, 17, 21], m_ref=[0.2, 0.5, 1.0, 2.0, 3.0, 5.0], k_stale=[4, 5, 7, 10, 14], n_min=[1, 3, 5, 8, 10, 15])  # widened once on seed 7 (first fit hit the grid edge)
FIT_PATH = "outputs/faults_checker_fit.json"


def iter_fault_trials(df, seed):
    """(kind, trial, ref, cur, make) for 20 trials per fault + 50 clean windows."""
    pool_end = df["date"].max()
    pool_start = pool_end - pd.Timedelta(days=POOL_DAYS)
    max_off = (pool_end - pool_start).days - WINDOW_DAYS
    for kind, n in [(f, N_FAULT) for f in INJECTORS] + [("clean", N_CLEAN)]:
        for i in range(n):
            rng = np.random.default_rng(seed * 1000 + zlib.crc32(kind.encode()) % 1000 + i)
            cs = pool_start + pd.Timedelta(days=int(rng.integers(0, max(max_off, 1))))
            cur = (cs, cs + pd.Timedelta(days=WINDOW_DAYS))
            ref = (cs - pd.Timedelta(days=REF_DAYS), cs - pd.Timedelta(days=1))
            make = (lambda f=INJECTORS[kind], c=cur, r=rng: f(df, c, r)) if kind != "clean" else \
                (lambda: (df, dict(fault_type="none", column=None, slice_col=None, slice_value=None)))
            yield kind, i, ref, cur, make


def series_stats(df, ref, cur):
    """Per-series statistics the rules use (computed once per trial)."""
    c = df[(df["date"] >= cur[0]) & (df["date"] <= cur[1])]
    r = df[(df["date"] >= ref[0]) & (df["date"] <= ref[1])]
    dup_ids = sorted(c.loc[c.duplicated(["id", "date"], keep=False), "id"].unique())
    c1 = c.drop_duplicates(["id", "date"]).sort_values(["id", "date"])
    piv = c1.pivot(index="id", columns="date", values="sales").to_numpy()[:, ::-1]  # newest first
    first = piv[:, :1]
    same = np.isclose(piv, first)
    run = np.where(same.all(axis=1), piv.shape[1], same.argmin(axis=1))
    last = first[:, 0]
    ids = c1["id"].drop_duplicates().to_numpy()
    st = pd.DataFrame({"id": ids, "trail_run": run, "trail_value": last})
    st["ref_mean"] = st["id"].map(r.groupby("id")["sales"].mean()).fillna(0.0)
    meta = c1.drop_duplicates("id").set_index("id")[SLICE_COLS]
    return st, dup_ids, meta


def locate(flagged, meta):
    flagged = set(flagged)
    best, best_f1 = (None, None), -1.0
    for col in SLICE_COLS:
        for val, ids in meta.groupby(col).groups.items():
            ids = set(ids)
            tp = len(flagged & ids)
            if not tp:
                continue
            p, r = tp / len(ids), tp / len(flagged)
            f1 = 2 * p * r / (p + r)
            if f1 > best_f1 + 1e-12:
                best, best_f1 = (col, str(val)), f1
    return best


def decide(st, dup_ids, meta, k_zero, m_ref, k_stale, n_min):
    if dup_ids:
        return ("duplicated_rows", "rows", *locate(dup_ids, meta))
    zero = st[(st.trail_value == 0) & (st.trail_run >= k_zero) & (st.ref_mean >= m_ref)]["id"]
    stale = st[(st.trail_value != 0) & (st.trail_run >= k_stale)]["id"]
    z, s = len(zero) >= n_min, len(stale) >= n_min
    if z and (not s or len(zero) >= len(stale)):
        return ("zero_fill", "sales", *locate(zero, meta))
    if s:
        return ("stale_feed", "sales", *locate(stale, meta))
    return ("none", None, None, None)


def fit(stats_by_trial):
    """Choose thresholds on seed 7 only: maximise the mean of type-and-location
    accuracy on each covered fault type and specificity on clean windows."""
    best, best_score = None, -1.0
    rows = []
    for combo in itertools.product(*GRID.values()):
        p = dict(zip(GRID, combo))
        per = {k: [] for k in COVERED + ["clean"]}
        for kind, log, st, dup, meta in stats_by_trial:
            if kind not in per:
                continue
            t, _, col, val = decide(st, dup, meta, **p)
            per[kind].append(t == "none" if kind == "clean" else
                             (t == kind and col == log["slice_col"] and val == log["slice_value"]))
        score = float(np.mean([np.mean(v) for v in per.values()]))
        rows.append(dict(p, score=score, **{f"acc_{k}": float(np.mean(v)) for k, v in per.items()}))
        if score > best_score + 1e-12:
            best, best_score = p, score
    return best, best_score, pd.DataFrame(rows)


def load_fit():
    return json.load(open(FIT_PATH))["params"]
