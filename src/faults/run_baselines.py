"""Phase 10 non-LLM arms: fit the checker on seed 7, then run the checker and
the frozen pipeline on seeds 42/123. Rows appended per trial; resumable.

  python src/faults/run_baselines.py fit          # seed 7 only
  python src/faults/run_baselines.py run --seeds 42 123
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.investigator import Investigator
from agents.sentinel import Sentinel
from evaluation.evaluate import _detect_and_localize
from faults.checker import FIT_PATH, GRID, decide, fit, iter_fault_trials, load_fit, series_stats

OUT = "outputs/faults_baseline_trials.csv"


def _panel():
    return pd.read_parquet("data/raw/real_m5_panel_cache.parquet")


def do_fit():
    df = _panel()
    stats = []
    for kind, i, ref, cur, make in iter_fault_trials(df, 7):
        d, log = make()
        stats.append((kind, log, *series_stats(d, ref, cur)))
        print(f"[fit] seed=7 {kind:20s} t={i}", flush=True)
    params, score, grid = fit(stats)
    grid.sort_values("score", ascending=False).to_csv("outputs/faults_checker_grid_seed7.csv", index=False)
    json.dump(dict(params=params, seed7_score=score, grid=GRID, tuned_on="seed 7 only",
                   objective="mean of covered-fault type-and-location accuracy and clean-window specificity"),
              open(FIT_PATH, "w"), indent=1)
    print("chosen:", params, "seed-7 score:", round(score, 3))


def do_run(seeds):
    df = _panel()
    params = load_fit()
    sentinel, inv = Sentinel(), Investigator()
    done = set()
    if os.path.exists(OUT):
        p = pd.read_csv(OUT)
        done = set(zip(p.seed, p.kind, p.trial))
    for seed in seeds:
        for kind, i, ref, cur, make in iter_fault_trials(df, seed):
            if (seed, kind, i) in done:
                continue
            d, log = make()
            t, column, col, val = decide(*series_stats(d, ref, cur), **params)
            detected, cands, _ = _detect_and_localize(d, ref, cur, sentinel, inv)
            top = cands[0] if cands else None
            row = dict(seed=seed, kind=kind, trial=i, cur_start=str(cur[0].date()),
                       true_type=log["fault_type"], true_column=log["column"], true_slice_col=log["slice_col"],
                       true_slice_value=log["slice_value"], fault_start=log.get("start"),
                       checker_type=t, checker_column=column, checker_slice_col=col, checker_slice_value=val,
                       pipeline_detected=detected, pipeline_slice_col=str(top.slice_col) if top else None,
                       pipeline_slice_value=str(top.slice_val) if top else None)
            pd.DataFrame([row]).to_csv(OUT, mode="a", index=False, header=not os.path.exists(OUT))
            print(f"seed={seed} {kind:20s} t={i:2d} true={log['slice_col']}={log['slice_value']} "
                  f"checker={t}:{col}={val} pipeline={row['pipeline_slice_col']}={row['pipeline_slice_value']}", flush=True)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fit", "run"])
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 123])
    a = ap.parse_args()
    assert a.cmd == "fit" or 7 not in a.seeds, "seed 7 is tuning only"
    do_fit() if a.cmd == "fit" else do_run(a.seeds)
