"""Save sample Synthesizer reports from the full pipeline (Sentinel ->
Investigator -> Synthesizer, `pipeline.DriftDetectionPipeline`, unchanged,
default rule-based Synthesizer, no calendar context).

Trials come from the same generator/seeds as the evaluations (seed 42): the
first N of each drift type plus a few drift-free windows, on M5 and Favorita.
Every report the pipeline returns (one per alerted feature) is saved with the
injected ground truth next to it.

Outputs: outputs/synthesizer_reports.csv, outputs/synthesizer_reports.md
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import DriftDetectionPipeline
from evaluation.evaluate_llm_agent import PANELS, POOL_DAYS, REF_DAYS, WINDOW_DAYS, iter_trials

N_PER_TYPE, N_FREE, SEED = 3, 2, 42


def trials(df):
    seen = {}
    for dt, trial, ref, cur, make in iter_trials(df, SEED, N_PER_TYPE):
        yield dt, trial, ref, cur, make
    pool_end = df["date"].max()
    pool_start = pool_end - pd.Timedelta(days=POOL_DAYS)
    for i in range(N_FREE):
        off = int(np.linspace(0, (pool_end - pool_start).days - WINDOW_DAYS, N_FREE)[i])
        cs = pool_start + pd.Timedelta(days=off)
        yield ("drift_free", i, (cs - pd.Timedelta(days=REF_DAYS), cs - pd.Timedelta(days=1)),
               (cs, cs + pd.Timedelta(days=WINDOW_DAYS)), lambda: (df, {"slice_col": None, "slice_value": None}))


def main():
    pipe = DriftDetectionPipeline()
    rows = []
    for dataset in ("m5", "favorita"):
        df = pd.read_parquet(PANELS[dataset])
        df.attrs["dataset"] = dataset
        for dt, trial, ref, cur, make in trials(df):
            drifted, log = make()
            reports = pipe.run(drifted, ref, cur)
            truth = f"{log['slice_col']}={log['slice_value']}" if log.get("slice_col") else "(none: drift-free)"
            for r in reports:
                top = [f"{c.slice_col}={c.slice_val}" for c in r.top_slices[:3]]
                rows.append(dict(dataset=dataset, drift_type=dt, trial=trial,
                                 current_window=f"{cur[0].date()}..{cur[1].date()}", true_slice=truth,
                                 feature=r.feature, backend=r.backend, top_slices="|".join(top),
                                 top1_is_true_slice=bool(top) and top[0] == truth,
                                 narrative=r.narrative, recommended_actions=" || ".join(r.recommended_actions)))
            print(f"{dataset} {dt:12s} t={trial} true={truth:28s} reports={len(reports)} "
                  f"features={[r.feature for r in reports]}", flush=True)
    out = pd.DataFrame(rows)
    out.to_csv("outputs/synthesizer_reports.csv", index=False)
    md = ["# Synthesizer sample reports\n",
          "Full pipeline (`DriftDetectionPipeline`: Sentinel -> Investigator -> rule-based Synthesizer), "
          f"unchanged, seed {SEED}, no calendar context. One report per alerted feature. The true slice is "
          "shown for the reader only; the pipeline never sees it.\n"]
    for (dataset, dt, trial), g in out.groupby(["dataset", "drift_type", "trial"], sort=False):
        md.append(f"\n## {dataset} / {dt} / trial {trial} ({g.current_window.iloc[0]})\n\n**True slice:** `{g.true_slice.iloc[0]}`\n")
        for _, r in g.iterrows():
            md.append(f"\n### Report: `{r.feature}` ({r.backend})\n\n- Top slices: `{r.top_slices}`"
                      f"{' (top-1 = true slice)' if r.top1_is_true_slice else ''}\n\n{r.narrative}\n\n"
                      "**Recommended actions:**\n" + "\n".join(f"- {a}" for a in r.recommended_actions.split(" || ")) + "\n")
    with open("outputs/synthesizer_reports.md", "w") as f:
        f.write("\n".join(md))
    print(f"\n{len(out)} reports from {out.groupby(['dataset','drift_type','trial']).ngroups} trials -> "
          "outputs/synthesizer_reports.csv, outputs/synthesizer_reports.md")


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    main()
