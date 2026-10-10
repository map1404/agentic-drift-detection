"""Summarize the Favorita transfer arms into outputs/favorita_*.csv.

  (a) frozen deterministic   outputs/favorita_trials_frozen.csv
  (b) frozen LLM agent       outputs/llm_agent/trials_fav_val.csv
                             (copied to outputs/favorita_trials_llm_agent.csv)
  (c) retuned deterministic  outputs/favorita_trials_retuned.csv

Accuracy: strict/hier top-1 and strict top-3 (+ hier top-3), 95% Wilson
CIs, per drift type and pooled. Agent fallbacks are excluded from the
agent's rate and counted separately; (a) is also reported on exactly the
agent-answered trials so the McNemar test is paired. Works on partial
files (whatever has finished so far).
"""
from __future__ import annotations

import os
import sys

import pandas as pd
from scipy.stats import binomtest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from evaluation.evaluate import wilson_interval

KEY = ["seed", "drift_type", "trial"]
METRICS = ["strict_top1", "hier_top1", "strict_top3", "hier_top3"]
GROUPS = ["sudden", "gradual", "intermittent", "ALL"]


def load():
    arms = {}
    if os.path.exists("outputs/favorita_trials_frozen.csv"):
        arms["a_frozen_det"] = pd.read_csv("outputs/favorita_trials_frozen.csv")
    if os.path.exists("outputs/favorita_trials_retuned.csv"):
        arms["c_retuned_det"] = pd.read_csv("outputs/favorita_trials_retuned.csv")
    p = "outputs/llm_agent/trials_fav_val.csv"
    if os.path.exists(p):
        b = pd.read_csv(p).drop_duplicates(KEY, keep="last")
        b.to_csv("outputs/favorita_trials_llm_agent.csv", index=False)
        b["agent_ok"] = b["agent_ok"].astype(bool)
        ren = {f"agent_{m}": m for m in METRICS}
        arms["b_llm_agent"] = b.rename(columns=ren)
    return arms


def _groups(t):
    for g in GROUPS:
        sub = t if g == "ALL" else t[t["drift_type"] == g]
        if len(sub):
            yield g, sub


def accuracy(arms):
    rows = []
    for arm, t in arms.items():
        ok = t[t["agent_ok"]] if "agent_ok" in t else t
        for g, sub in _groups(ok):
            n_all = len(t) if g == "ALL" else int((t["drift_type"] == g).sum())
            for m in METRICS:
                k = int(sub[m].astype(bool).sum())
                lo, hi = wilson_interval(k, len(sub))
                rows.append(dict(arm=arm, group=g, metric=m, k=k, n=len(sub), n_trials=n_all,
                                 n_fallbacks=n_all - len(sub), rate=k / len(sub), ci_lo=lo, ci_hi=hi))
    return pd.DataFrame(rows)


def mcnemar(arms, x, y):
    if x not in arms or y not in arms:
        return pd.DataFrame()
    tx, ty = arms[x], arms[y]
    if "agent_ok" in ty:
        ty = ty[ty["agent_ok"]]
    m = tx.merge(ty, on=KEY, suffixes=("_x", "_y"))
    rows = []
    for g, sub in _groups(m):
        for met in METRICS:
            a, b = sub[f"{met}_x"].astype(bool), sub[f"{met}_y"].astype(bool)
            only_x, only_y = int((a & ~b).sum()), int((~a & b).sum())
            p = binomtest(only_x, only_x + only_y, 0.5).pvalue if only_x + only_y else 1.0
            rows.append(dict(comparison=f"{x} vs {y}", group=g, metric=met, n_paired=len(sub),
                             both_right=int((a & b).sum()), only_x=only_x, only_y=only_y,
                             both_wrong=int((~a & ~b).sum()), rate_x=a.mean(), rate_y=b.mean(),
                             mcnemar_exact_p=p))
    return pd.DataFrame(rows)


def main():
    arms = load()
    acc = accuracy(arms)
    acc.to_csv("outputs/favorita_summary.csv", index=False)
    mc = pd.concat([mcnemar(arms, "a_frozen_det", "b_llm_agent"),
                    mcnemar(arms, "a_frozen_det", "c_retuned_det")], ignore_index=True)
    mc.to_csv("outputs/favorita_mcnemar.csv", index=False)
    if "b_llm_agent" in arms:
        b = arms["b_llm_agent"]
        tc = b.groupby("drift_type").agg(n=("trial", "size"), mean_calls=("n_tool_calls", "mean"),
                                         median_calls=("n_tool_calls", "median"),
                                         forced_submit=("forced_submit", "sum"),
                                         fallbacks=("agent_ok", lambda s: int((~s).sum())))
        tc.to_csv("outputs/favorita_llm_tool_calls.csv")
    pd.set_option("display.width", 200)
    for arm in acc.arm.unique():
        a = acc[acc.arm == arm]
        print(f"\n== {arm}")
        print(a.assign(cell=lambda d: [f"{k}/{n}={r:.0%} [{lo:.0%},{hi:.0%}]" for k, n, r, lo, hi in
                                       zip(d.k, d.n, d.rate, d.ci_lo, d.ci_hi)])
              .pivot(index="group", columns="metric", values="cell").reindex(
                  [g for g in GROUPS if g in set(a.group)])[METRICS].to_string())
    if len(mc):
        print("\n", mc[mc.metric.isin(["strict_top1", "hier_top1", "strict_top3"])].to_string(index=False))


if __name__ == "__main__":
    main()
