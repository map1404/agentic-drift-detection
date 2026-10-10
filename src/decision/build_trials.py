"""Phase 9 trial builder (no LLM): per trial, the MAEs, labels, detection
output and the exact payload each decision regime will see.

Writes one row per trial to outputs/decision_trials.csv (appended after every
trial; reruns skip finished trials). Columns prefixed `hidden_` (injection
log, counterfactual MAE, labels) are for scoring only and never enter a
payload; `payload_A` / `payload_B` are the literal JSON the agent receives.

  python src/decision/build_trials.py --seeds 7 42 123
  python src/decision/build_trials.py --balance      # class balance report
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zlib

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.investigator import Investigator
from agents.sentinel import Sentinel
from drift.inject_covariate import inject_price_shift
from evaluation.evaluate import _detect_and_localize
from evaluation.evaluate_llm_agent import POOL_DAYS, REF_DAYS, WINDOW_DAYS, iter_trials
from model.demand_model import _prep_features

OUT = "outputs/decision_trials.csv"
THRESHOLDS = (1.05, 1.10, 1.20)
N_INJECTED, N_FREE = 20, 50
FEATURES = ["sales", "sell_price", "revenue", "cat_id", "state_id"]


def _seed(seed, name, i):
    return seed * 1000 + zlib.crc32(name.encode()) % 1000 + i


def iter_all(df, seed):
    """sudden/gradual/intermittent via the existing generator; covariate and
    drift-free with the same pattern and their own seed component."""
    for dt, trial, ref, cur, make in iter_trials(df, seed, N_INJECTED):
        yield dt, trial, ref, cur, make
    pool_end = df["date"].max()
    pool_start = pool_end - pd.Timedelta(days=POOL_DAYS)
    max_off = (pool_end - pool_start).days - WINDOW_DAYS
    for name, n in (("covariate_price", N_INJECTED), ("drift_free", N_FREE)):
        for i in range(n):
            rng = np.random.default_rng(_seed(seed, name, i))
            off = int(rng.integers(0, max(max_off, 1)))
            cs = pool_start + pd.Timedelta(days=off)
            cur = (cs, cs + pd.Timedelta(days=WINDOW_DAYS))
            ref = (cs - pd.Timedelta(days=REF_DAYS), cs - pd.Timedelta(days=1))
            if name == "covariate_price":
                make = (lambda c=cur, r=rng: inject_price_shift(df, c, rng=r))
            else:
                make = (lambda: (df, dict(drift_type="drift_free", slice_col=None, slice_value=None)))
            yield name, i, ref, cur, make


def _mae(frame, model, window):
    w = frame[(frame["date"] >= window[0]) & (frame["date"] <= window[1])]
    pred = model.predict(_prep_features(w))
    return float(np.mean(np.abs(w["sales"].to_numpy() - pred)))


def _slice_change(frame, col, val, ref, cur):
    s = frame[frame[col].astype(str) == str(val)]
    r = s[(s["date"] >= ref[0]) & (s["date"] <= ref[1])]["sales"].mean()
    c = s[(s["date"] >= cur[0]) & (s["date"] <= cur[1])]["sales"].mean()
    return float((c - r) / r) if r else None


def build(seeds):
    df = pd.read_parquet("data/raw/real_m5_panel_cache.parquet")
    model = joblib.load("outputs/demand_model_real.joblib")
    sentinel, inv = Sentinel(), Investigator()
    done = set()
    if os.path.exists(OUT):
        p = pd.read_csv(OUT)
        done = set(zip(p.seed, p.drift_type, p.trial))
    for seed in seeds:
        for dt, trial, ref, cur, make in iter_all(df, seed):
            if (seed, dt, trial) in done:
                continue
            drifted, log = make()
            mae_ref = _mae(drifted, model, ref)
            mae_cur = _mae(drifted, model, cur)
            mae_clean = _mae(df, model, cur)
            ratio = mae_cur / mae_clean
            alerts = {a.feature: a for a in sentinel.scan(drifted, ref, cur)}
            detected, cands, _ = _detect_and_localize(drifted, ref, cur, sentinel, inv)
            alert = {f: dict(js_divergence=round(alerts[f].js_divergence, 6), l_inf=round(alerts[f].l_inf_distance, 6),
                             fired=bool(alerts[f].is_drift)) for f in FEATURES if f in alerts}
            alert["pipeline_detected"] = bool(detected)
            slices = [dict(slice=f"{c.slice_col}={c.slice_val}", score=round(c.score, 6),
                           share_of_window=round(c.coverage_frac, 4)) for c in cands[:3]]
            slices_b = [dict(s, mean_sales_pct_change=None if (m := _slice_change(drifted, *s["slice"].split("=", 1), ref, cur)) is None
                             else round(m, 4)) for s in slices]
            payload_a = dict(window=dict(reference=[str(ref[0].date()), str(ref[1].date())],
                                         current=[str(cur[0].date()), str(cur[1].date())]),
                             drift_alert=alert, top_slices=slices,
                             reference_mae=round(mae_ref, 4), current_mae=round(mae_cur, 4))
            payload_b = dict(window=payload_a["window"], drift_alert=alert, top_slices=slices_b)
            top = cands[0] if cands else None
            row = dict(seed=seed, drift_type=dt, trial=trial, cur_start=str(cur[0].date()),
                       mae_ref=mae_ref, mae_cur=mae_cur, mae_ratio_cur_ref=mae_cur / mae_ref,
                       top_score=top.score if top else 0.0, top_share=top.coverage_frac if top else 0.0,
                       top_mag=slices_b[0]["mean_sales_pct_change"] if slices_b else None,
                       payload_A=json.dumps(payload_a), payload_B=json.dumps(payload_b),
                       hidden_true_slice=f"{log.get('slice_col')}={log.get('slice_value')}",
                       hidden_mae_clean=mae_clean, hidden_harm_ratio=ratio,
                       **{f"hidden_harmful_{t:.2f}": bool(ratio >= t) for t in THRESHOLDS})
            pd.DataFrame([row]).to_csv(OUT, mode="a", index=False, header=not os.path.exists(OUT))
            print(f"seed={seed} {dt:16s} t={trial:2d} ratio={ratio:.3f} harmful@1.10={ratio >= 1.10} "
                  f"top={slices[0]['slice'] if slices else None}", flush=True)


def balance():
    t = pd.read_csv(OUT)
    rows = []
    for (seed, dt), g in t.groupby(["seed", "drift_type"]):
        r = dict(seed=seed, drift_type=dt, n=len(g))
        for th in THRESHOLDS:
            r[f"harmful_share_{th:.2f}"] = g[f"hidden_harmful_{th:.2f}"].mean()
        rows.append(r)
    b = pd.DataFrame(rows)
    b.to_csv("outputs/decision_class_balance.csv", index=False)
    print(b.to_string(index=False))
    viol = b[(b.drift_type != "drift_free") & (b.seed != 7) &
             ((b["harmful_share_1.10"] < 0.15) | (b["harmful_share_1.10"] > 0.85))]
    print("\nSTOP CONDITION (a class < 15% at 1.10, injected types, seeds 42/123):",
          "TRIGGERED for " + str(viol[["seed", "drift_type"]].values.tolist()) if len(viol) else "not triggered")


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[7, 42, 123])
    ap.add_argument("--balance", action="store_true")
    a = ap.parse_args()
    balance() if a.balance else build(a.seeds)
