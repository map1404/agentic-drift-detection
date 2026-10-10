"""Favorita cross-dataset transfer: deterministic arms and false-alarm test.

  (a) frozen    -- the M5-frozen pipeline (`evaluate._detect_and_localize`,
                   tag m5-frozen) run unchanged on Favorita.
  (c) retuned   -- the same pipeline with parameters re-chosen on Favorita
                   seed 7 ONLY (`tune`), then run once on seeds 42/123.
  false-alarm   -- `evaluate.run_false_alarm_evaluation` (frozen) on 50
                   drift-free Favorita windows.
The LLM agent arm (b) runs through evaluate_llm_agent.py --dataset favorita.

Trials are generated exactly as in the M5 runs (`iter_trials`: same seeds,
200-day pool, 60-day rolling reference, 30-day window, same injectors).
Rows are appended after every trial; reruns skip finished trials.

Usage:
  python src/evaluation/evaluate_favorita.py arm --arm frozen --seeds 42 123
  python src/evaluation/evaluate_favorita.py tune            # seed 7 only
  python src/evaluation/evaluate_favorita.py arm --arm retuned --seeds 42 123
  python src/evaluation/evaluate_favorita.py false-alarm
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field, replace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.hierarchy import Hierarchy
from agents.investigator import COARSE_SCORE_BAR, BURST_SCORE_BAR, COARSE_SLICE_COLUMNS, Investigator
from agents.sentinel import DriftAlert, Sentinel
from evaluation.evaluate import run_false_alarm_evaluation
from evaluation.evaluate_llm_agent import PANELS, POOL_DAYS, REF_DAYS, WINDOW_DAYS, iter_trials, score

OUT = "outputs"
TUNED_CONFIG = f"{OUT}/favorita_retuned_config.json"


@dataclass(frozen=True)
class PipelineConfig:
    """Every parameter `_detect_and_localize` depends on. Defaults are the
    M5-frozen values (tag m5-frozen)."""
    js_threshold: float = 0.015
    l_inf_threshold: float = 0.06
    slice_columns: tuple = tuple(COARSE_SLICE_COLUMNS)
    min_slice_size: int = 20
    coarse_score_bar: float = COARSE_SCORE_BAR
    burst_score_bar: float = BURST_SCORE_BAR
    fallback_slice_cols: tuple = ("cat_id", "item_id")


FROZEN = PipelineConfig()


def load_panel():
    df = pd.read_parquet(PANELS["favorita"])
    df.attrs["dataset"] = "favorita"
    return df


def _alert(df, ref, cur, cfg, sentinel):
    """Sentinel global scan with within-slice fallback -- same logic as
    evaluate._detect_and_localize."""
    alerts = sentinel.scan(df, ref, cur)
    a = next(x for x in alerts if x.feature == "sales")
    if not a.is_drift:
        for col in cfg.fallback_slice_cols:
            within = sentinel.scan_within_slices(df, "sales", col, ref, cur, min_slice_size=cfg.min_slice_size)
            if within:
                f = within[0]
                return DriftAlert("sales", f.js_divergence, f.l_inf_distance, True, ref, cur, method=f.method)
    return a


def detect_and_localize(df, ref, cur, cfg: PipelineConfig):
    """Parameterised copy of evaluate._detect_and_localize. With FROZEN it
    returns identical candidates (checked in `check_equivalence`)."""
    sentinel = Sentinel(js_threshold=cfg.js_threshold, l_inf_threshold=cfg.l_inf_threshold)
    inv = Investigator(slice_columns=list(cfg.slice_columns), min_slice_size=cfg.min_slice_size)
    a = _alert(df, ref, cur, cfg, sentinel)
    if not a.is_drift:
        return False, []
    cands = inv.investigate(df, a, top_k=5)
    if not cands or cands[0].score < cfg.coarse_score_bar:
        burst = inv.investigate_bursts(df, a, top_k=5)
        if burst and burst[0].score > cfg.burst_score_bar:
            cands = burst
    return True, cands


def _ranking(cands):
    return [(str(c.slice_col), str(c.slice_val)) for c in cands[:5]]


def run_arm(df, arm, cfg, seeds, n):
    path = f"{OUT}/favorita_trials_{arm}.csv"
    done = set()
    if os.path.exists(path):
        prev = pd.read_csv(path)
        done = set(zip(prev.seed, prev.drift_type, prev.trial))
    hierarchy = Hierarchy(df)
    for seed in seeds:
        for dt, trial, ref, cur, make in iter_trials(df, seed, n):
            if (seed, dt, trial) in done:
                continue
            drifted, log = make()
            t0 = time.time()
            detected, cands = detect_and_localize(drifted, ref, cur, cfg)
            rk = _ranking(cands)
            row = dict(arm=arm, seed=seed, drift_type=dt, trial=trial, cur_start=str(cur[0].date()),
                       true_slice=f"{log['slice_col']}={log['slice_value']}", detected=detected,
                       ranking="|".join(f"{c}={v}" for c, v in rk[:3]), time_s=round(time.time() - t0, 1),
                       config=json.dumps(asdict(cfg)), **score(rk, log, hierarchy))
            pd.DataFrame([row]).to_csv(path, mode="a", index=False, header=not os.path.exists(path))
            print(f"[{arm}] seed={seed} {dt:12s} t={trial:2d} true={row['true_slice']:28s} "
                  f"{'Y' if row['strict_top1'] else '.'} [{row['ranking']}] {row['time_s']}s", flush=True)
    return pd.read_csv(path)


# ---------------------------------------------------------------------------
# Re-tuning on seed 7 only
# ---------------------------------------------------------------------------

GRID = dict(
    slice_set=["coarse", "coarse+item"],
    coarse_score_bar=[0.0, 0.001, 0.0025, 0.005, 0.01, 0.02, 0.05, float("inf")],
    burst_score_bar=[10.0, 20.0, 40.0, 60.0, 100.0, 150.0, 250.0, float("inf")],
)
SELECTION_RULE = ("maximise pooled strict top-1 on seed 7 (60 trials); ties broken by pooled hier "
                  "top-1, then strict top-3, then fewest parameters changed from M5-frozen")


def _precompute(df, seed, n):
    """Per trial, the raw candidate lists every grid point is assembled from
    (detection does not depend on the grid parameters)."""
    rows = []
    hierarchy = Hierarchy(df)
    sentinel = Sentinel()
    coarse_inv = Investigator()
    item_inv = Investigator(slice_columns=["item_id"])
    for dt, trial, ref, cur, make in iter_trials(df, seed, n):
        drifted, log = make()
        a = _alert(drifted, ref, cur, FROZEN, sentinel)
        rec = dict(drift_type=dt, trial=trial, log=log, detected=a.is_drift)
        if a.is_drift:
            rec["coarse"] = coarse_inv.investigate(drifted, a, top_k=5)
            rec["item"] = item_inv.investigate(drifted, a, top_k=5)
            rec["burst"] = coarse_inv.investigate_bursts(drifted, a, top_k=5)
        rows.append(rec)
        print(f"[tune precompute] seed={seed} {dt:12s} t={trial:2d}", flush=True)
    return rows, hierarchy


def _evaluate_point(rows, hierarchy, slice_set, cbar, bbar):
    hits = []
    for r in rows:
        if not r["detected"]:
            hits.append(dict(drift_type=r["drift_type"], strict_top1=False, hier_top1=False, strict_top3=False))
            continue
        cands = r["coarse"] if slice_set == "coarse" else \
            sorted(r["coarse"] + r["item"], key=lambda c: c.score, reverse=True)[:5]
        if not cands or cands[0].score < cbar:
            if r["burst"] and r["burst"][0].score > bbar:
                cands = r["burst"]
        s = score(_ranking(cands), r["log"], hierarchy)
        hits.append(dict(drift_type=r["drift_type"], **{k: s[k] for k in ("strict_top1", "hier_top1", "strict_top3")}))
    return pd.DataFrame(hits)


def tune(df, n=20):
    rows, hierarchy = _precompute(df, 7, n)
    results = []
    for slice_set, cbar, bbar in itertools.product(*GRID.values()):
        h = _evaluate_point(rows, hierarchy, slice_set, cbar, bbar)
        rec = dict(slice_set=slice_set, coarse_score_bar=cbar, burst_score_bar=bbar,
                   n_changed=int(slice_set != "coarse") + int(cbar != FROZEN.coarse_score_bar)
                   + int(bbar != FROZEN.burst_score_bar))
        for m in ("strict_top1", "hier_top1", "strict_top3"):
            rec[f"{m}_all"] = int(h[m].sum())
            for dt, g in h.groupby("drift_type"):
                rec[f"{m}_{dt}"] = int(g[m].sum())
        results.append(rec)
    grid = pd.DataFrame(results).sort_values(
        ["strict_top1_all", "hier_top1_all", "strict_top3_all", "n_changed"],
        ascending=[False, False, False, True]).reset_index(drop=True)
    grid.to_csv(f"{OUT}/favorita_tuning_grid_seed7.csv", index=False)
    best = grid.iloc[0]
    frozen_row = grid[(grid.slice_set == "coarse") & (grid.coarse_score_bar == FROZEN.coarse_score_bar)
                      & (grid.burst_score_bar == FROZEN.burst_score_bar)].iloc[0]
    cfg = replace(FROZEN,
                  slice_columns=tuple(COARSE_SLICE_COLUMNS) + (("item_id",) if best.slice_set == "coarse+item" else ()),
                  coarse_score_bar=float(best.coarse_score_bar), burst_score_bar=float(best.burst_score_bar))
    changes = []
    for name in ("slice_columns", "coarse_score_bar", "burst_score_bar"):
        old, new = getattr(FROZEN, name), getattr(cfg, name)
        if old != new:
            changes.append(dict(parameter=name, m5_frozen=str(old), favorita_retuned=str(new)))
    record = dict(selection_rule=SELECTION_RULE, tuned_on="Favorita seed 7, n=20 per drift type",
                  grid={k: [str(x) for x in v] for k, v in GRID.items()},
                  frozen_seed7=frozen_row.to_dict(), chosen_seed7=best.to_dict(),
                  changes=changes, config=asdict(cfg),
                  not_tuned=dict(js_threshold="detection is evaluated separately; see note in changelog",
                                 l_inf_threshold="same", min_slice_size="unchanged",
                                 fallback_slice_cols="unchanged"))
    json.dump(record, open(TUNED_CONFIG, "w"), indent=1, default=str)
    pd.DataFrame(changes or [dict(parameter="(none)", m5_frozen="", favorita_retuned="")]).to_csv(
        f"{OUT}/favorita_tuning_changes.csv", index=False)
    print(json.dumps(record, indent=1, default=str))
    return cfg


def load_tuned():
    rec = json.load(open(TUNED_CONFIG))
    c = rec["config"]
    c["slice_columns"] = tuple(c["slice_columns"])
    c["fallback_slice_cols"] = tuple(c["fallback_slice_cols"])
    return PipelineConfig(**c)


def false_alarm(df):
    pool_end = df["date"].max()
    pool_start = pool_end - pd.Timedelta(days=POOL_DAYS)
    summary, windows = run_false_alarm_evaluation(
        df, (pool_start, pool_start + pd.Timedelta(days=REF_DAYS)), (pool_start, pool_end),
        n_windows=50, current_window_len_days=WINDOW_DAYS)
    summary.to_csv(f"{OUT}/favorita_false_alarm_summary.csv", index=False)
    windows.to_csv(f"{OUT}/favorita_false_alarm_windows.csv", index=False)
    print(summary.to_string())


def check_equivalence(df, n_trials=3):
    """FROZEN config == evaluate._detect_and_localize on a few trials."""
    from evaluation.evaluate import _detect_and_localize
    s, inv = Sentinel(), Investigator()
    for dt, trial, ref, cur, make in itertools.islice(iter_trials(df, 7, n_trials), 0, None):
        d, _ = make()
        a = _detect_and_localize(d, ref, cur, s, inv)
        b = detect_and_localize(d, ref, cur, FROZEN)
        assert (a[0], _ranking(a[1])) == (b[0], _ranking(b[1])), (dt, trial)
    print("FROZEN config matches evaluate._detect_and_localize")


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["arm", "tune", "false-alarm", "check"])
    ap.add_argument("--arm", choices=["frozen", "retuned"])
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 123])
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()
    df = load_panel()
    if args.cmd == "check":
        check_equivalence(df)
    elif args.cmd == "tune":
        tune(df, args.n)
    elif args.cmd == "false-alarm":
        false_alarm(df)
    else:
        assert 7 not in args.seeds, "seed 7 is the tuning seed; arms are reported on 42/123 only"
        run_arm(df, args.arm, FROZEN if args.arm == "frozen" else load_tuned(), args.seeds, args.n)
