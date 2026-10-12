"""Phase 10 scoring: frozen pipeline (location only) vs non-LLM checker vs
agent, on seeds 42/123. Primary metric: type-and-location accuracy (type
correct AND strict location correct). Clean windows: false alarm = any answer
other than "none" (the pipeline alarms whenever it detects drift).

Writes outputs/faults_metrics.csv, faults_by_type.csv, faults_mcnemar.csv,
faults_summary.csv, faults_fallbacks.csv.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import binomtest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.hierarchy import Hierarchy
from evaluation.evaluate import wilson_interval
from faults.checker import COVERED, HELD_OUT

KEY = ["seed", "kind", "trial"]


def load():
    b = pd.read_csv("outputs/faults_baseline_trials.csv")
    a = pd.read_csv("outputs/faults_agent_trials.csv").drop_duplicates(KEY, keep="last")
    pd.DataFrame(a.groupby("kind").agg(n=("ok", "size"), fallbacks=("ok", lambda s: int((~s.astype(bool)).sum())),
                                       reasons=("fallback", lambda s: ";".join(sorted(set(s.dropna())))))
                 ).to_csv("outputs/faults_fallbacks.csv")
    m = b.merge(a, on=KEY, how="left")
    m["ok"] = m["ok"].fillna(False).astype(bool)
    return m


def score(m):
    h = Hierarchy(pd.read_parquet("data/raw/real_m5_panel_cache.parquet"))
    rows = []
    for _, r in m.iterrows():
        clean = r.kind == "clean"
        for meth, t, col, sc, sv in (
                ("pipeline", None, None, r.pipeline_slice_col, r.pipeline_slice_value),
                ("checker", r.checker_type, r.checker_column, r.checker_slice_col, r.checker_slice_value),
                ("agent", r.agent_type, r.agent_column, r.agent_slice_col, r.agent_slice_value)):
            if meth == "agent" and not r.ok:
                continue
            if clean:
                alarm = bool(r.pipeline_detected) if meth == "pipeline" else (t != "none")
                rows.append(dict(seed=r.seed, kind=r.kind, trial=r.trial, method=meth, clean=True, false_alarm=alarm))
                continue
            sc = None if pd.isna(sc) else str(sc)
            sv = None if pd.isna(sv) else str(sv)
            strict = sc == r.true_slice_col and sv == str(r.true_slice_value)
            hier = strict or (sc is not None and sv is not None and
                              h.is_related_slice(sc, sv, r.true_slice_col, str(r.true_slice_value)))
            type_ok = (t == r.true_type) if meth != "pipeline" else False
            rows.append(dict(seed=r.seed, kind=r.kind, trial=r.trial, method=meth, clean=False,
                             type_correct=type_ok, loc_strict=strict, loc_hier=hier,
                             column_correct=(str(col) == str(r.true_column)) if meth != "pipeline" and r.kind != "duplicated_rows" else np.nan,
                             both_correct=type_ok and strict, both_correct_hier=type_ok and hier))
    return pd.DataFrame(rows)


def _rate(s):
    s = s.dropna().astype(bool)
    k, n = int(s.sum()), len(s)
    lo, hi = wilson_interval(k, n) if n else (np.nan, np.nan)
    return k, n, (k / n if n else np.nan), lo, hi


def main():
    m = load()
    s = score(m)
    ok_keys = set(map(tuple, m[m.ok][KEY].values))
    s = s[s[KEY].apply(tuple, axis=1).isin(ok_keys)]  # paired: trials the agent answered
    strata = {**{k: [k] for k in COVERED + HELD_OUT}, "HELD_OUT": HELD_OUT, "COVERED": COVERED,
              "ALL_FAULTS": COVERED + HELD_OUT}
    rows = []
    for meth, g in s.groupby("method"):
        for name, kinds in strata.items():
            f = g[g.kind.isin(kinds) & ~g.clean]
            r = dict(method=meth, stratum=name)
            for met in ("both_correct", "type_correct", "loc_strict", "loc_hier", "column_correct", "both_correct_hier"):
                k, n, rate, lo, hi = _rate(f[met])
                r.update({met: rate, f"{met}_k": k, f"{met}_ci_lo": lo, f"{met}_ci_hi": hi})
            r["n"] = len(f)
            rows.append(r)
        k, n, rate, lo, hi = _rate(g[g.clean]["false_alarm"])
        rows.append(dict(method=meth, stratum="clean", n=n, false_alarm_rate=rate, false_alarm_k=k,
                         false_alarm_ci_lo=lo, false_alarm_ci_hi=hi))
    M = pd.DataFrame(rows)
    M.to_csv("outputs/faults_metrics.csv", index=False)

    mc = []
    for name in ("HELD_OUT", "COVERED", "ALL_FAULTS", *COVERED, *HELD_OUT):
        sub = M[(M.stratum == name) & (M.method != "agent")]
        best = sub.sort_values("both_correct", ascending=False).iloc[0].method
        w = s[s.kind.isin(strata[name]) & ~s.clean].pivot_table(index=KEY, columns="method", values="both_correct", aggfunc="first")
        a, b = w["agent"].astype(bool), w[best].astype(bool)
        oa, ob = int((a & ~b).sum()), int((~a & b).sum())
        p = binomtest(oa, oa + ob, 0.5).pvalue if oa + ob else 1.0
        mc.append(dict(stratum=name, best_non_llm=best, n_paired=len(w), agent_rate=a.mean(), best_rate=b.mean(),
                       only_agent_right=oa, only_best_right=ob, mcnemar_exact_p=p))
    # clean windows: false alarms, agent vs checker (pipeline alarms always)
    w = s[s.clean].pivot_table(index=KEY, columns="method", values="false_alarm", aggfunc="first")
    a, b = ~w["agent"].astype(bool), ~w["checker"].astype(bool)
    oa, ob = int((a & ~b).sum()), int((~a & b).sum())
    mc.append(dict(stratum="clean (correct = no alarm)", best_non_llm="checker", n_paired=len(w), agent_rate=a.mean(),
                   best_rate=b.mean(), only_agent_right=oa, only_best_right=ob,
                   mcnemar_exact_p=binomtest(oa, oa + ob, 0.5).pvalue if oa + ob else 1.0))
    MC = pd.DataFrame(mc)
    MC.to_csv("outputs/faults_mcnemar.csv", index=False)

    by = []
    for meth, g in s.groupby("method"):
        for kind, f in g.groupby("kind"):
            if kind == "clean":
                k, n, rate, lo, hi = _rate(f["false_alarm"])
                by.append(dict(method=meth, fault=kind, n=n, false_alarm=f"{k}/{n} = {rate:.0%} [{lo:.0%}, {hi:.0%}]"))
                continue
            cell = {}
            for met in ("both_correct", "type_correct", "loc_strict", "loc_hier"):
                k, n, rate, lo, hi = _rate(f[met])
                cell[met] = f"{k}/{n} = {rate:.0%} [{lo:.0%}, {hi:.0%}]"
            by.append(dict(method=meth, fault=kind, n=len(f), group="held-out" if kind in HELD_OUT else "covered", **cell))
    BY = pd.DataFrame(by)
    BY.to_csv("outputs/faults_by_type.csv", index=False)
    summary = BY.pivot_table(index="fault", columns="method", values="both_correct", aggfunc="first")
    fa = BY[BY.fault == "clean"].set_index("method")["false_alarm"]
    summary.loc["clean (false-alarm rate)"] = fa
    summary.to_csv("outputs/faults_summary.csv")
    pd.set_option("display.width", 250, "display.max_colwidth", 40)
    print(summary.to_string())
    print(MC.to_string(index=False))
    print(pd.read_csv("outputs/faults_fallbacks.csv").to_string(index=False))


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    main()
