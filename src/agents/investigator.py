"""Investigator — root-cause localization agent.

Ranks candidate slices by WITHIN-SLICE divergence: does *this slice's own*
reference-vs-current distribution differ? This is the default scoring
method (`method="within_slice"`).

VECTORIZED: bins reference and current windows ONCE per (target_col,
slice_col) into slice-value x histogram-bin count matrices, so scoring
every candidate slice value is an O(n_bins) per-value comparison rather
than an O(n_rows) rescan per candidate.

HONEST HISTORY (see project_changelog.md Phase 7 for the full account):
the original scoring method here was "divergence removal" -- score a slice
by how much removing it from the aggregate current-window distribution
lowers divergence back toward the reference. That method worked fine on
the synthetic panel this project was originally built and evaluated
against, but FAILED on real M5 data: real departments have genuinely
different baseline sales distributions from each other (not drift, just
structural heterogeneity -- FOODS departments sell in much higher volume
than HOBBIES/HOUSEHOLD, for instance). Divergence-removal is confounded
by that heterogeneity -- removing a structurally different-from-average
department shifts the aggregate distribution a lot regardless of whether
real drift happened there, so the method kept finding whichever
department was already the most different from the rest, even with ZERO
injected drift. On a real-data-matched benchmark this produced ~30%
top-1 accuracy, heavily biased toward one or two default answers.

Within-slice comparison is immune to that confound BY CONSTRUCTION: it
never compares one slice against another, only each slice against its
OWN reference-window history. On the same benchmark this raised accuracy
to ~85%, with the remaining misses concentrated in the single lowest-
volume department -- a genuine detection-floor limitation (not enough
signal in a very sparse slice), not a systematic bias.

The old divergence-removal method is kept as `method="removal"` for
comparison/documentation purposes -- it is a real, working, legitimately
weaker baseline now, not deleted history.

REAL-SCALE REGRESSION (see CLAUDE_CODE_HANDOFF.md for the full account):
re-running the n=20/drift-type evaluation harness against the real cached
M5 panel (198 items x 10 stores x 913 days) after the within-slice fix
landed made things WORSE, not better -- RCA-strict fell to 0-5% across all
three drift types. The initial guess was item-level sparsity (too few rows
per item_id slice for a stable histogram): REFUTED -- an item_id slice at
this scale still has ~300 rows in a 30-day window (10 stores x 30 days),
comfortably above `min_slice_size`; raising the floor further didn't change
the diagnosis.

The real cause, confirmed by injecting ZERO drift and finding the *same*
failure mode: individual real M5 items carry large genuine, organic (i.e.
NOT drift-related) demand swings between any reference and current window
-- products get delisted (sales collapse to ~0), or ramp up several-fold --
and in raw JS-divergence terms this organic churn routinely EXCEEDS the
injected drift magnitudes (0.6-3.0x, confined to one slice). Combined with
item_id having ~20-60x more candidates than any other slice_col (198 items
vs. 3-10 cat/dept/state/store values), the single largest score across all
candidates is almost always a real-but-irrelevant, structurally volatile
item, regardless of where drift was actually injected -- confirmed: the
top-ranked slice was `item_id` in effectively every one of 60 real trials.

Fixes that were tried and measurably did NOT work, in the interest of
reporting negative results honestly rather than only the one that stuck:
  - Per-column robust z-score normalization (score vs. that column's own
    median/MAD): doesn't help, because the offending items are genuine
    outliers within item_id's own distribution too, not merely a
    variance-scale artifact of having more candidates.
  - Weighting the score by `coverage_frac` (a slice's share of the current
    window): dampens but does not eliminate the effect -- the worst
    offending items still win even after weighting.
  - A historical-noise ratio (dividing the real ref-vs-cur score by a null
    score computed from two historical chunks drawn from within the
    reference window itself): made it WORSE -- a single-draw null is
    itself noisy for sparse per-item data (often ~0 by chance), so the
    ratio produces even more extreme, spurious values for item-level
    candidates.
  - `method="removal"` (legacy): scored slightly better in a head-to-head
    on the same 60 trials (5% vs. 0% strict) only because its handful of
    default wrong answers (a structurally large department/state -- the
    ORIGINAL confound this method has, see above) happened to coincide
    with the true answer slightly more often by chance. Verified still
    broken: with ZERO injected drift it keeps naming the same
    structurally-largest department/state, exactly as originally
    documented. Not a real fix.

What actually measurably helped, verified end-to-end via the real
evaluation harness: excluding item_id from the default candidate pool
(`COARSE_SLICE_COLUMNS`, i.e. only cat_id/dept_id/state_id/store_id) raised
strict RCA to ~10% and hierarchy-aware RCA to ~27% overall on the same real
panel (vs. 0/5/0% strict on the regression), which restores and modestly
exceeds the ~15/5/5% strict numbers seen before the within-slice fix ever
landed. This is a real but PARTIAL fix: it works because those four columns
have far fewer, far larger-sample, far more stable candidates, so they
aren't swamped by real item-level churn the way item_id is. It does NOT
solve item-level localization -- when the true drifted slice is an
individual item_id (this is the true slice in ~45% of trials, and ALWAYS
true for intermittent drift by injector construction), this configuration
cannot find it by design, and honestly can't currently be made to: no
scoring approach tried here can reliably separate "this one item was
deliberately shocked" from "this one item's demand naturally swung a lot,"
because at the magnitudes used by these injectors, real M5 item-level
non-stationarity is comparable to or larger than the injected signal.
Pass `slice_columns=SLICE_COLUMNS` to `Investigator(...)` to opt back into
ranking item_id candidates, with that limitation in mind.

FOLLOW-UP (improving gradual and intermittent specifically): two further,
verified improvements on top of the coarse-only default above.

(1) The reference window matters a lot, and a single FIXED, temporally
distant reference window (e.g. the first 700 days of a 913-day panel,
compared against current windows drawn from the last ~200 days) picks up
REAL secular trend as false drift: e.g. dept_id `FOODS_2` genuinely grew
~57% (mean sales 1.4 -> 2.2) over that 2-year gap in the real cached panel,
and store_id `CA_2` grew similarly -- both comparable to or larger than
gradual's injected magnitude (0.7, averaging ~0.35 over a linear ramp), so
these real trends out-competed the actual injected slice much of the time.
Using a ROLLING reference window -- the N days immediately preceding each
trial's current window, not a fixed historical baseline -- removes most of
this secular-trend confound (standard practice: compare against the recent
past believed stable, not an arbitrary distant one). Verified on the real
cached panel with a 60-day rolling reference (fixed-distant baseline ->
rolling, coarse-only method both times): sudden 25%->30% strict (55%->80%
hier), gradual 5%->30% strict (10%->55% hier), intermittent unchanged at 0%
(still structurally excluded -- see above). `store_id`'s trend confound
mostly disappeared at this length; `dept_id` FOODS_2's did not fully (still
the most common wrong answer), but the net effect, especially for gradual,
is a clear, real improvement.

(2) For intermittent specifically (drift confined to a few 1-2 day spikes
in a single item, ALWAYS item_id by injector construction -- see
`inject_drift.py`), even coarse-only ranking can't win since it structurally
excludes item_id. `investigate_bursts()` (below) is a second, item-level
statistic purpose-built for this spike shape, gated behind `COARSE_SCORE_BAR`
/ `BURST_SCORE_BAR` so it only overrides the coarse answer when the coarse
signal is weak AND the burst signal is strong. Naively always preferring
burst whenever its own raw score cleared a threshold (no coarse-weakness
gate) was tried first and regressed sudden badly -- 30%->0% strict, 80%->
20% hier -- because plenty of real, non-intermittent windows also contain
one bursty real item unrelated to any injected drift; gating it behind
"coarse is weak" fixes that. Net measured effect combined with (1), rolling
reference + gated burst vs. rolling reference alone: sudden 30%/75%
(essentially unchanged, only 1-2 trials ever route through burst), gradual
25%/50% (a small, honestly-reported give-back from rolling-alone's 30%/55%,
because a few weak-coarse gradual trials now get overridden by a wrong
burst guess), intermittent 0%->10% strict (10%->15% hier). A genuine net
improvement, not a clean win on every axis -- and note `investigate_bursts`
used entirely on its own (no coarse method or gating at all), just ranking
items by burst score for known-intermittent trials, picks the true item at
rank 1 in ~45% of trials -- confirming the statistic itself is informative,
even though the gating needed to safely combine it with the coarse method
gives up some of that on the way to a single top-1 answer.

REPRODUCIBILITY NOTE: every percentage above was re-measured after fixing a
real bug in `evaluate.py` -- trial RNG seeds were derived from
`hash(drift_type) % 1000`, and Python randomizes string `hash()` per
process by default, so `seed=42` was silently NOT reproducible run to run
(confirmed: three back-to-back runs of the identical call gave three
different RCA-strict rates, e.g. sudden ranged 20-35% across runs before
the fix). Fixed via `_stable_seed_component` (a real, fixed hash of the
string). All numbers here are from that fixed, now-reproducible seeding --
confirmed identical across repeated runs -- not from the original,
non-reproducible ad hoc runs used earlier while iterating.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from .sentinel import DriftAlert, _histogram_probs, adaptive_bin_edges
from scipy.spatial.distance import jensenshannon

SLICE_COLUMNS = ["cat_id", "dept_id", "state_id", "store_id", "item_id"]
# Default candidate pool for ranking (see module docstring's "real-scale
# regression" section): item_id is excluded by default because, at real M5
# scale, individual items carry genuine organic (non-drift) demand swings
# -- products get delisted, ramp up, etc. -- that routinely exceed the
# injected drift magnitude, and with ~20-60x more item_id candidates than
# any other column, its noisiest candidate wins the top-1 slot almost
# regardless of where drift was actually injected.
COARSE_SLICE_COLUMNS = ["cat_id", "dept_id", "state_id", "store_id"]

# Calibrated significance bars for the two-stage coarse-then-burst routing
# used by evaluate.py's `_detect_and_localize` (see its docstring for the
# full account). Chosen by sweeping both bars against the real cached M5
# panel across all three drift types and picking the point that gains the
# most on intermittent while leaving sudden unchanged and costing gradual
# the least. Not derived from first principles -- an empirical trade-off
# point, same spirit as Sentinel's js_threshold/l_inf_threshold. (The exact
# calibration sweep that picked these two numbers ran under an unpinned
# `hash()`-seeded harness -- see `_stable_seed_component` in evaluate.py --
# so its specific percentages aren't reproducible and aren't repeated here;
# only the chosen bar VALUES are load-bearing, and re-sweeping with the now
# fixed, reproducible seeding is a reasonable follow-up if these ever need
# re-justifying.)
COARSE_SCORE_BAR = 0.005   # below this, the coarse within-slice answer is
                            # treated as too weak to trust on its own
BURST_SCORE_BAR = 60.0     # above this, a burst candidate is trusted enough
                            # to override a weak coarse answer


@dataclass
class SliceCandidate:
    slice_col: str
    slice_val: str
    score: float
    coverage_frac: float  # fraction of the current window this slice covers


def _js_from_probs(p: np.ndarray, q: np.ndarray) -> float:
    if p.sum() == 0 or q.sum() == 0:
        return 0.0
    d = jensenshannon(p, q, base=2)
    return float(d ** 2) if not np.isnan(d) else 0.0


class Investigator:
    def __init__(self, slice_columns=None, min_slice_size: int = 20):
        self.slice_columns = slice_columns if slice_columns is not None else COARSE_SLICE_COLUMNS
        self.min_slice_size = min_slice_size

    def investigate(
            self,
            df: pd.DataFrame,
            alert: DriftAlert,
            top_k: int = 5,
            method: str = "within_slice",
    ) -> list[SliceCandidate]:
        """Return top-k candidate slices explaining `alert`, across all
        candidate slice columns.

        method="within_slice" (default): rank by each slice's OWN
        reference-vs-current JS divergence. Immune to inter-slice
        heterogeneity confounds -- use this unless you specifically want
        the legacy divergence-removal baseline for comparison.

        method="removal": legacy divergence-removal scoring. Kept for
        comparison; see module docstring for why it's no longer the
        default.

        NOTE on `slice_columns`: by default this class only ranks
        cat_id/dept_id/state_id/store_id (see COARSE_SLICE_COLUMNS and the
        module docstring's "real-scale regression" section for why item_id
        is excluded by default). Pass `slice_columns=SLICE_COLUMNS`
        explicitly to also rank item_id, understanding that its results are
        measurably unreliable at real M5 scale.
        """
        ref = df[(df["date"] >= alert.reference_window[0]) & (df["date"] <= alert.reference_window[1])]
        cur = df[(df["date"] >= alert.current_window[0]) & (df["date"] <= alert.current_window[1])]
        feature = alert.feature
        is_categorical = df[feature].dtype == object or feature in ("cat_id", "state_id", "store_id", "dept_id", "item_id")

        if is_categorical:
            bins = sorted(set(ref[feature].dropna().unique()) | set(cur[feature].dropna().unique()))
        else:
            bins = adaptive_bin_edges(pd.concat([ref[feature], cur[feature]]).dropna(), n_bins=20)

        n_cur_total = len(cur)

        all_candidates: list[SliceCandidate] = []
        for slice_col in self.slice_columns:
            if slice_col == feature:
                continue
            if method == "within_slice":
                scores = self._score_column_within_slice(ref, cur, feature, slice_col, bins, is_categorical, n_cur_total)
            elif method == "removal":
                ref_p, _ = _histogram_probs(ref[feature], bins, is_categorical=is_categorical)
                scores = self._score_column_removal(cur, feature, slice_col, bins, is_categorical, ref_p, n_cur_total)
            else:
                raise ValueError(f"unknown method {method!r}")
            all_candidates.extend(scores)

        all_candidates.sort(key=lambda c: c.score, reverse=True)
        return all_candidates[:top_k]

    def _score_column_within_slice(self, ref, cur, feature, slice_col, bins, is_categorical, n_cur_total):
        """Vectorized within-slice scoring: build ref and cur slice-value x
        bin count matrices ONCE, then compare each slice value's own ref
        histogram against its own cur histogram. No cross-slice comparison
        anywhere -- immune to inter-slice heterogeneity confounds."""
        n_bins = len(bins) if is_categorical else len(bins) - 1

        def build_matrix(frame):
            work = frame[[slice_col, feature]].dropna()
            if is_categorical:
                bin_index = {b: i for i, b in enumerate(bins)}
                work = work[work[feature].isin(bin_index)]
                work = work.assign(_bin_idx=work[feature].map(bin_index))
            else:
                work = work.assign(_bin_idx=np.clip(np.digitize(work[feature].to_numpy(), bins[1:-1]), 0, len(bins) - 2))
            mat = work.groupby([slice_col, "_bin_idx"], observed=True).size().unstack(fill_value=0)
            return mat.reindex(columns=range(n_bins), fill_value=0)

        ref_mat = build_matrix(ref)
        cur_mat = build_matrix(cur)

        common_vals = ref_mat.index.intersection(cur_mat.index)
        candidates = []
        for val in common_vals:
            ref_counts = ref_mat.loc[val].to_numpy(dtype=float)
            cur_counts = cur_mat.loc[val].to_numpy(dtype=float)
            ref_n, cur_n = ref_counts.sum(), cur_counts.sum()
            if ref_n < self.min_slice_size or cur_n < self.min_slice_size:
                continue
            ref_p = ref_counts / ref_n
            cur_p = cur_counts / cur_n
            score = _js_from_probs(ref_p, cur_p)
            coverage = cur_n / n_cur_total if n_cur_total else 0.0
            candidates.append(SliceCandidate(slice_col, val, float(score), float(coverage)))
        return candidates

    def investigate_bursts(
            self,
            df: pd.DataFrame,
            alert: DriftAlert,
            slice_col: str = "item_id",
            spike_len_days: int = 2,
            top_k: int = 5,
    ) -> list[SliceCandidate]:
        """Rank `slice_col` values (default item_id) by a BURST statistic
        instead of whole-window JS divergence: for each value, compare the
        largest `spike_len_days`-day rolling SUM of `alert.feature` in the
        current window against the same value's own rolling max in the
        reference window, relative to its reference-window daily mean.

        WHY THIS EXISTS: `investigate()`'s within-slice JS divergence
        compares the ENTIRE current window's distribution shape against the
        entire reference window. A short (1-2 day) spike confined to a few
        days out of a 30-day window barely moves that whole-window shape --
        it gets diluted by the other ~26-28 unaffected days -- so intermittent
        (spike-shaped) drift is structurally hard for `investigate()` to see
        at the item level, on top of the item-level noise problem documented
        in the module docstring. This statistic looks specifically for an
        unusually large SHORT burst, which is exactly intermittent drift's
        shape, instead of an overall distributional shift.

        CALIBRATED, NOT JUST RANKED: like Sentinel's js_threshold/
        l_inf_threshold, the caller should compare the top score against a
        significance bar before trusting it (see BURST_SCORE_BAR and
        `evaluate.py`'s use of it) -- this statistic has its own heavy-tailed
        real-data noise floor (real one-off item events can also produce a
        large burst), so an unfiltered top-1 answer is not automatically
        reliable. Measured on the real cached M5 panel: used alone, this
        picks the true intermittent-drifted item at rank 1 in ~45% of
        trials and within the top 3 in ~80% -- much better than raw
        within-slice divergence's ~0%, but still an unreliable answer on its
        own without a significance gate, since other real items can produce
        even larger spurious bursts (see module docstring's calibration
        notes).
        """
        ref = df[(df["date"] >= alert.reference_window[0]) & (df["date"] <= alert.reference_window[1])]
        cur = df[(df["date"] >= alert.current_window[0]) & (df["date"] <= alert.current_window[1])]
        feature = alert.feature

        def rolling_max_and_mean(frame, val):
            g = frame[frame[slice_col] == val].groupby("date")[feature].sum().sort_index()
            if len(g) < spike_len_days:
                return 0.0, float(g.mean()) if len(g) else 0.0
            roll = g.rolling(spike_len_days).sum().dropna()
            return (float(roll.max()) if len(roll) else 0.0), float(g.mean())

        candidates = []
        common_vals = set(ref[slice_col].dropna().unique()) & set(cur[slice_col].dropna().unique())
        for val in common_vals:
            cur_max, cur_mean = rolling_max_and_mean(cur, val)
            ref_max, ref_mean = rolling_max_and_mean(ref, val)
            n_cur = (cur[slice_col] == val).sum()
            if n_cur < self.min_slice_size:
                continue
            score = (cur_max - ref_max) / (ref_mean + 0.5)
            coverage = float(n_cur) / len(cur) if len(cur) else 0.0
            candidates.append(SliceCandidate(slice_col, val, float(score), coverage))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:top_k]

    def _score_column_removal(self, cur, feature, slice_col, bins, is_categorical, ref_p, n_cur_total):
        """LEGACY divergence-removal scoring. See module docstring for why
        this is no longer the default -- kept for comparison."""
        work = cur[[slice_col, feature]].dropna()
        if is_categorical:
            work = work.assign(_bin=work[feature])
            bin_index = {b: i for i, b in enumerate(bins)}
            work = work[work["_bin"].isin(bin_index)]
            work["_bin_idx"] = work["_bin"].map(bin_index)
        else:
            work["_bin_idx"] = np.clip(np.digitize(work[feature].to_numpy(), bins[1:-1]), 0, len(bins) - 2)

        n_bins = len(bins) if is_categorical else len(bins) - 1
        mat = (
            work.groupby([slice_col, "_bin_idx"], observed=True).size().unstack(fill_value=0)
        )
        mat = mat.reindex(columns=range(n_bins), fill_value=0)

        total_hist = mat.sum(axis=0).to_numpy(dtype=float)
        total_count = total_hist.sum()
        if total_count == 0:
            return []

        baseline_cur_p = total_hist / total_count
        baseline_div = _js_from_probs(ref_p, baseline_cur_p)

        candidates = []
        for val, row in mat.iterrows():
            row_counts = row.to_numpy(dtype=float)
            slice_size = row_counts.sum()
            if slice_size == 0 or slice_size == total_count:
                continue
            remaining_hist = total_hist - row_counts
            remaining_total = remaining_hist.sum()
            if remaining_total <= 0:
                continue
            remaining_p = remaining_hist / remaining_total
            div_without_slice = _js_from_probs(ref_p, remaining_p)
            score = baseline_div - div_without_slice  # divergence removal
            coverage = slice_size / n_cur_total
            candidates.append(SliceCandidate(slice_col, val, float(score), float(coverage)))
        return candidates