"""Sentinel — the monitoring agent.

Grounded in Jensen-Shannon divergence (Lin, 1991) for symmetric distribution
comparison and L-infinity distance for abrupt single-category shifts, with
an ADWIN-inspired (Bifet & Gavalda, 2007) adaptive windowing scheme.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy.spatial.distance import jensenshannon


@dataclass
class DriftAlert:
    feature: str
    js_divergence: float
    l_inf_distance: float
    is_drift: bool
    reference_window: tuple
    current_window: tuple
    method: str = "global"


def adaptive_bin_edges(combined: pd.Series, n_bins: int = 20) -> np.ndarray:
    """Choose histogram bin edges appropriate to the data's actual shape,
    instead of always using naive equal-width bins.

    WHY THIS EXISTS (a real bug found and fixed after running against real
    M5 data — see project_changelog.md Phase 7): M5's `sales` feature is a
    heavily zero-inflated small-integer count (mean ~0.2-1.8, but with rare
    holiday-type spikes up to 100+). Equal-width bins spanning the full
    [0, max] range put 95-99.6% of EVERY department's mass into a single
    bin near zero, so JS-divergence comparisons were being driven almost
    entirely by noise in the sparse, rare high-value tail bins -- and
    whichever slice happened to have the fattest tail (by chance, not by
    drift) would dominate every divergence-removal score, regardless of
    where the true injected drift actually was. This produced a specific,
    reproducible failure mode on real data: the Investigator's top-ranked
    slice collapsed to whichever department had the heaviest right tail
    (FOODS_2, in the run that surfaced this) in ~19/34 trials, independent
    of ground truth.

    Fix: use quantile (equal-frequency) bin edges, and for integer-like
    count data with a small effective range, use exact-integer bins up to
    a high percentile plus one overflow bin for the rare tail -- so
    resolution concentrates where the data's actual mass is, instead of
    being wasted on a near-empty tail.
    """
    vals = combined.dropna().to_numpy(dtype=float)
    if len(vals) == 0:
        return np.array([0.0, 1.0])

    is_integer_like = np.allclose(vals, np.round(vals), atol=1e-6)
    if is_integer_like:
        cap = int(np.quantile(vals, 0.99))
        cap = max(cap, 1)
        if cap <= n_bins:
            edges = np.arange(0, cap + 2, dtype=float)
            top = vals.max()
            if top > edges[-1]:
                edges = np.append(edges, top + 1)
            return np.unique(edges)

    qs = np.linspace(0, 1, n_bins + 1)
    edges = np.unique(np.quantile(vals, qs))
    if len(edges) < 2:
        # degenerate (near-constant) feature: fall back to a single wide bin
        lo, hi = vals.min(), vals.max()
        return np.array([lo, hi + 1]) if hi >= lo else np.array([lo, lo + 1])
    return edges


def _histogram_probs(series: pd.Series, bins: Optional[np.ndarray] = None, is_categorical: bool = False):
    """Return (probs, bin_edges_or_categories) for a series."""
    if is_categorical:
        cats = bins if bins is not None else sorted(series.dropna().unique())
        counts = series.value_counts().reindex(cats, fill_value=0)
        probs = counts.to_numpy(dtype=float)
        probs = probs / probs.sum() if probs.sum() > 0 else probs
        return probs, cats
    if bins is None:
        bins = adaptive_bin_edges(series.dropna(), n_bins=20)
    counts, _ = np.histogram(series.dropna(), bins=bins)
    probs = counts.astype(float)
    probs = probs / probs.sum() if probs.sum() > 0 else probs
    return probs, bins


def js_divergence(ref: pd.Series, cur: pd.Series, is_categorical: bool = False, bins=None) -> float:
    """Squared Jensen-Shannon distance (i.e. JS divergence proper), base 2."""
    if is_categorical:
        cats = bins if bins is not None else sorted(set(ref.dropna().unique()) | set(cur.dropna().unique()))
        p, _ = _histogram_probs(ref, cats, is_categorical=True)
        q, _ = _histogram_probs(cur, cats, is_categorical=True)
    else:
        edges = bins if bins is not None else adaptive_bin_edges(
            pd.concat([ref, cur]).dropna(), n_bins=20
        )
        p, _ = _histogram_probs(ref, edges)
        q, _ = _histogram_probs(cur, edges)
    if p.sum() == 0 or q.sum() == 0:
        return 0.0
    dist = jensenshannon(p, q, base=2)
    if np.isnan(dist):
        return 0.0
    return float(dist ** 2)


def l_inf_distance(ref: pd.Series, cur: pd.Series, is_categorical: bool = False, bins=None) -> float:
    if is_categorical:
        cats = bins if bins is not None else sorted(set(ref.dropna().unique()) | set(cur.dropna().unique()))
        p, _ = _histogram_probs(ref, cats, is_categorical=True)
        q, _ = _histogram_probs(cur, cats, is_categorical=True)
    else:
        edges = bins if bins is not None else adaptive_bin_edges(
            pd.concat([ref, cur]).dropna(), n_bins=20
        )
        p, _ = _histogram_probs(ref, edges)
        q, _ = _histogram_probs(cur, edges)
    if len(p) == 0 or len(q) == 0:
        return 0.0
    return float(np.max(np.abs(p - q)))


class Sentinel:
    """Continuous monitoring agent."""

    def __init__(
            self,
            numeric_features=None,
            categorical_features=None,
            js_threshold: float = 0.015,
            l_inf_threshold: float = 0.06,
    ):
        # Instance-level (not class-level!) feature lists — settable per
        # pipeline instance so model-error features (residual/abs_error)
        # can be added for a given run without mutating shared state.
        self.numeric_features = list(numeric_features) if numeric_features else ["sales", "sell_price", "revenue"]
        self.categorical_features = list(categorical_features) if categorical_features else ["cat_id", "state_id"]
        self.js_threshold = js_threshold
        self.l_inf_threshold = l_inf_threshold

    def scan(self, df: pd.DataFrame, reference_window: tuple, current_window: tuple) -> list[DriftAlert]:
        ref = df[(df["date"] >= reference_window[0]) & (df["date"] <= reference_window[1])]
        cur = df[(df["date"] >= current_window[0]) & (df["date"] <= current_window[1])]
        alerts = []
        for feat in self.numeric_features:
            if feat not in df.columns:
                continue
            js = js_divergence(ref[feat], cur[feat])
            linf = l_inf_distance(ref[feat], cur[feat])
            is_drift = (js > self.js_threshold) or (linf > self.l_inf_threshold)
            alerts.append(DriftAlert(feat, js, linf, is_drift, reference_window, current_window, "global"))
        for feat in self.categorical_features:
            if feat not in df.columns:
                continue
            js = js_divergence(ref[feat], cur[feat], is_categorical=True)
            linf = l_inf_distance(ref[feat], cur[feat], is_categorical=True)
            is_drift = (js > self.js_threshold) or (linf > self.l_inf_threshold)
            alerts.append(DriftAlert(feat, js, linf, is_drift, reference_window, current_window, "global"))
        return alerts

    def scan_within_slices(
            self,
            df: pd.DataFrame,
            feature: str,
            slice_col: str,
            reference_window: tuple,
            current_window: tuple,
            min_slice_size: int = 20,
    ) -> list[DriftAlert]:
        """Compare reference-vs-current WITHIN each slice value independently.

        Fixes a real, predictable failure mode: drift confined to one slice
        (e.g. a 70% ramp on 1/3 of items) barely moves the *global*
        histogram's shape, because the unaffected majority dilutes it.
        """
        ref = df[(df["date"] >= reference_window[0]) & (df["date"] <= reference_window[1])]
        cur = df[(df["date"] >= current_window[0]) & (df["date"] <= current_window[1])]
        is_cat = feature in self.categorical_features
        edges = None
        if not is_cat:
            edges = adaptive_bin_edges(pd.concat([ref[feature], cur[feature]]).dropna(), n_bins=20)

        alerts = []
        for val, ref_g in ref.groupby(slice_col, observed=True):
            cur_g = cur[cur[slice_col] == val]
            if len(ref_g) < min_slice_size or len(cur_g) < min_slice_size:
                continue
            js = js_divergence(ref_g[feature], cur_g[feature], is_categorical=is_cat, bins=edges)
            linf = l_inf_distance(ref_g[feature], cur_g[feature], is_categorical=is_cat, bins=edges)
            is_drift = (js > self.js_threshold) or (linf > self.l_inf_threshold)
            if is_drift:
                alerts.append(
                    DriftAlert(feature, js, linf, True, reference_window, current_window, method=f"within_slice:{slice_col}={val}")
                )
        return alerts

    def scan_adaptive(
            self,
            df: pd.DataFrame,
            feature: str,
            start_date,
            min_window_days: int = 7,
            max_window_days: int = 90,
            grow_step_days: int = 7,
    ):
        """ADWIN-inspired (Bifet & Gavalda, 2007) adaptive-window change
        detector. SIMPLIFIED: a single midpoint split-and-compare check per
        window size, not the full compressed-histogram ADWIN algorithm with
        formal false-positive-rate guarantees. Documented as a limitation,
        not claimed as full ADWIN.
        """
        is_cat = feature in self.categorical_features
        window_days = min_window_days
        while window_days <= max_window_days:
            w_start = pd.Timestamp(start_date)
            w_end = w_start + pd.Timedelta(days=window_days)
            mid = w_start + pd.Timedelta(days=window_days // 2)
            w = df[(df["date"] >= w_start) & (df["date"] < w_end)]
            first_half = w[w["date"] < mid][feature]
            second_half = w[w["date"] >= mid][feature]
            if len(first_half) >= 20 and len(second_half) >= 20:
                js = js_divergence(first_half, second_half, is_categorical=is_cat)
                if js > self.js_threshold:
                    return {
                        "change_point": mid,
                        "window_days": window_days,
                        "js_divergence": js,
                        "feature": feature,
                    }
            window_days += grow_step_days
        return None