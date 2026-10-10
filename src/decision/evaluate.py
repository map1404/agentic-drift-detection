"""Phase 9 evaluation: baselines (fit on seed 7 only), metrics, McNemar,
per-drift-type breakdown, sensitivity labels, summary table.

Writes outputs/decision_baseline_fit.csv, decision_metrics.csv,
decision_by_drift_type.csv, decision_mcnemar.csv, decision_summary.csv,
decision_fallbacks.csv.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import binomtest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from evaluation.evaluate import wilson_interval

KEY = ["seed", "drift_type", "trial"]
EVAL_SEEDS = (42, 123)
PRIMARY = "hidden_harmful_1.05"  # AMENDMENT 2026-10-10: primary threshold 1.10 -> 1.05


def bal_acc(y, p):
    y, p = np.asarray(y, bool), np.asarray(p, bool)
    tpr = (p & y).sum() / y.sum() if y.sum() else np.nan
    tnr = (~p & ~y).sum() / (~y).sum() if (~y).sum() else np.nan
    return np.nanmean([tpr, tnr])


def fit_threshold(x, y):
    """Threshold on score x maximising balanced accuracy (predict harmful iff x >= thr);
    candidates are midpoints of the sorted distinct values; ties -> the median candidate."""
    xs = np.unique(x)
    cands = np.concatenate([[xs[0] - 1e-9], (xs[:-1] + xs[1:]) / 2, [xs[-1] + 1e-9]])
    ba = np.array([bal_acc(y, x >= c) for c in cands])
    best = cands[ba == ba.max()]
    return float(np.median(best)), float(ba.max())


def predictions(trials, label):
    """retrain(True)/ignore(False) per method, keyed by trial."""
    fit = trials[trials.seed == 7]
    ev = trials[trials.seed.isin(EVAL_SEEDS)].copy()
    y7 = fit[label].astype(bool).to_numpy()
    thr_a, ba_a = fit_threshold(fit["mae_ratio_cur_ref"].to_numpy(), y7)
    xb = (fit["top_score"] * fit["top_share"]).to_numpy()
    thr_b, ba_b = fit_threshold(xb, y7)
    fits = pd.DataFrame([dict(rule="A: current/reference MAE ratio", threshold=thr_a, seed7_balanced_acc=ba_a, label=label),
                         dict(rule="B: top divergence x top slice share", threshold=thr_b, seed7_balanced_acc=ba_b, label=label)])
    preds = {
        "always_retrain": pd.Series(True, index=ev.index),
        "always_ignore": pd.Series(False, index=ev.index),
        "A_threshold_mae_ratio": ev["mae_ratio_cur_ref"] >= thr_a,
        "B_rule_divergence_x_share": (ev["top_score"] * ev["top_share"]) >= thr_b,
    }
    return ev, preds, fits


def metrics(y, p, cost_missed=1.0, cost_unnecessary=1.0):
    y, p = np.asarray(y, bool), np.asarray(p, bool)
    n = len(y)
    tp, fp, fn, tn = (p & y).sum(), (p & ~y).sum(), (~p & y).sum(), (~p & ~y).sum()
    acc = (tp + tn) / n
    rec = tp / (tp + fn) if tp + fn else np.nan
    spec = tn / (tn + fp) if tn + fp else np.nan
    prec = tp / (tp + fp) if tp + fp else np.nan
    a_lo, a_hi = wilson_interval(int(tp + tn), n)
    r_lo, r_hi = wilson_interval(int(tp), int(tp + fn)) if tp + fn else (np.nan, np.nan)
    s_lo, s_hi = wilson_interval(int(tn), int(tn + fp)) if tn + fp else (np.nan, np.nan)
    rng = np.random.default_rng(0)
    boots = [bal_acc(y[i], p[i]) for i in (rng.integers(0, n, n) for _ in range(2000))]
    return dict(n=n, harmful=int(y.sum()), accuracy=acc, acc_ci_lo=a_lo, acc_ci_hi=a_hi,
                balanced_accuracy=bal_acc(y, p), bal_acc_ci_lo=float(np.nanpercentile(boots, 2.5)),
                bal_acc_ci_hi=float(np.nanpercentile(boots, 97.5)),
                retrain_precision=prec, retrain_recall=rec, recall_ci_lo=r_lo, recall_ci_hi=r_hi,
                specificity=spec, spec_ci_lo=s_lo, spec_ci_hi=s_hi,
                missed_harmful=int(fn), unnecessary_retrains=int(fp),
                cost_weighted_error=float(cost_missed * fn + cost_unnecessary * fp))


def mcnemar(y, pa, pb):
    ca, cb = np.asarray(pa, bool) == y, np.asarray(pb, bool) == y
    only_a, only_b = int((ca & ~cb).sum()), int((~ca & cb).sum())
    p = binomtest(only_a, only_a + only_b, 0.5).pvalue if only_a + only_b else 1.0
    return only_a, only_b, p


def main():
    trials = pd.read_csv("outputs/decision_trials.csv")
    agent = pd.read_csv("outputs/decision_agent.csv") if os.path.exists("outputs/decision_agent.csv") else pd.DataFrame()
    if len(agent):
        agent = agent.drop_duplicates(["regime"] + KEY, keep="last")
        fb = agent.groupby(["regime", "drift_type"]).agg(n=("ok", "size"), fallbacks=("ok", lambda s: int((~s.astype(bool)).sum())))
        fb.to_csv("outputs/decision_fallbacks.csv")
    all_metrics, by_type, mc_rows, fits_all = [], [], [], []
    for label in ("hidden_harmful_1.05", "hidden_harmful_1.10", "hidden_harmful_1.20"):
        ev, preds, fits = predictions(trials, PRIMARY)  # rules fit at the primary label (amendment: 1.05)
        fits_all.append(fits)
        y_all = ev[label].astype(bool)
        lab = label.replace("hidden_harmful_", "")
        for regime, base_names in (("A", ["always_retrain", "always_ignore", "A_threshold_mae_ratio"]),
                                   ("B", ["always_retrain", "always_ignore", "B_rule_divergence_x_share"])):
            methods = {m: preds[m] for m in base_names}
            if len(agent):
                ag = agent[(agent.regime == regime) & agent.ok.astype(bool)].set_index(KEY)
                ok_idx = ev.set_index(KEY).index.isin(ag.index)
                methods["agent"] = pd.Series(ev.set_index(KEY).index.map(
                    lambda k: ag.loc[k, "decision"] == "retrain" if k in ag.index else np.nan), index=ev.index)
            else:
                ok_idx = np.ones(len(ev), bool)
            # all methods scored on the agent-answered trials (paired)
            sub = ev[ok_idx]
            y = sub[label].astype(bool).to_numpy()
            scores = {}
            for m, p in methods.items():
                pm = p[ok_idx].astype(bool).to_numpy()
                r = dict(label_threshold=lab, regime=regime, method=m, **metrics(y, pm))
                all_metrics.append(r)
                scores[m] = r["balanced_accuracy"]
                for dt, g in sub.groupby("drift_type"):
                    gi = sub.index.get_indexer(g.index)
                    by_type.append(dict(label_threshold=lab, regime=regime, method=m, drift_type=dt,
                                        **metrics(y[gi], pm[gi])))
            if "agent" in methods:
                pa = methods["agent"][ok_idx].astype(bool).to_numpy()
                base_only = {m: s for m, s in scores.items() if m != "agent"}
                best = max(base_only, key=base_only.get)
                for m in base_names:
                    oa, ob, p = mcnemar(y, pa, methods[m][ok_idx].astype(bool).to_numpy())
                    mc_rows.append(dict(label_threshold=lab, regime=regime, comparison=f"agent vs {m}",
                                        is_best_baseline=m == best, n_paired=len(y), only_agent_right=oa,
                                        only_baseline_right=ob, mcnemar_exact_p=p,
                                        agent_bal_acc=scores["agent"], baseline_bal_acc=scores[m]))
    pd.concat(fits_all).drop_duplicates().to_csv("outputs/decision_baseline_fit.csv", index=False)
    M = pd.DataFrame(all_metrics)
    M.to_csv("outputs/decision_metrics.csv", index=False)
    pd.DataFrame(by_type).to_csv("outputs/decision_by_drift_type.csv", index=False)
    MC = pd.DataFrame(mc_rows)
    MC.to_csv("outputs/decision_mcnemar.csv", index=False)
    prim = M[M.label_threshold == "1.05"].copy()
    if len(MC):
        best_p = MC[(MC.label_threshold == "1.05") & MC.is_best_baseline].set_index("regime")["mcnemar_exact_p"]
        prim["mcnemar_p_agent_vs_best_baseline"] = prim.apply(
            lambda r: best_p.get(r.regime) if r.method == "agent" else np.nan, axis=1)
    summ = prim[["regime", "method", "n", "balanced_accuracy", "bal_acc_ci_lo", "bal_acc_ci_hi", "retrain_recall",
                 "unnecessary_retrains", "missed_harmful"] + (["mcnemar_p_agent_vs_best_baseline"] if len(MC) else [])]
    summ.to_csv("outputs/decision_summary.csv", index=False)
    pd.set_option("display.width", 250)
    print(pd.concat(fits_all).drop_duplicates().to_string(index=False))
    print(summ.to_string(index=False))
    if len(MC):
        print(MC[MC.label_threshold == "1.05"].to_string(index=False))


if __name__ == "__main__":
    main()
