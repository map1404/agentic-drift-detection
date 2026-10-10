"""Convert drafted configs into panels and run the frozen pipeline (Phase 8).

CONVERSION RULES (fixed before any agent output existed):
  dept_id  = the unique parent of the item key (after transitive reduction)
  cat_id   = the unique parent of dept_id
  state_id = the unique parent of the store key
  clip negative sales at 0  iff a negative-sales issue is reported with a
                            handling that removes them (clip/0/zero/drop/...)
  zero-fill omitted days    iff an omitted-zero-days issue is reported
  sell_price = 1.0, snap = 0, event_flag = 0 always (not used for localisation)
  Same sampled items and date window as the hand-written panel.

MANUAL-EDIT POLICY (each application is logged and counted, never silent):
  E1 a level has several candidate parents   -> take the one listed first
                                                in the config's hierarchy_edges
  E2 a level has no parent                   -> constant "ALL" for that level
  E3 a claimed edge used for a level is not  -> drop it and re-resolve
     a true dependency in the raw data
  E4 a required role (item_key, store_key,   -> set it to the raw column the
     date, sales_target) is missing/ambiguous   adapter uses
  E4b (ADDED 2026-10-10 AFTER seeing agent outputs, because the policy above
     did not anticipate it) a required role is assigned to a DIFFERENT raw
     column than the adapter uses -> set it to the adapter's column. Without
     this the panel builder silently used the adapter's columns regardless of
     the config (a bug, caught in review before any downstream agent run).
Obscured configs are translated to original names first (scorer mapping;
mechanical, not an edit).

  python src/onboarding/convert_and_run.py select     # pick best + random repeat
  python src/onboarding/convert_and_run.py run        # build panels, run trials
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.hierarchy import Hierarchy
from evaluation.evaluate_favorita import FROZEN, detect_and_localize, _ranking
from evaluation.evaluate_llm_agent import iter_trials, score
from evaluation.evaluate import wilson_interval
from onboarding import score as S

FAV = "data/raw/favorita"
SEL = "outputs/onboarding_downstream_selection.csv"
EDITS = "outputs/onboarding_manual_edits.csv"
TRIALS = "outputs/onboarding_downstream_trials.csv"
REQUIRED = {"item_key": S.ITEM, "store_key": S.STORE, "date": "date", "sales_target": S.SALES}


def _reduce(edges):
    return {(p, c) for p, c in edges if not any((p, m) in S.closure(edges) and (m, c) in edges
                                                for m in {n for e in edges for n in e} if m not in (p, c))}


def to_spec(cfg, label):
    edits = []
    holds = S.true_fds()

    def edit(kind, what, choice):
        edits.append(dict(label=label, edit_type=kind, problem=what, applied=choice))

    roles = {}
    for role, ref_col in REQUIRED.items():
        cand = sorted({r["column"] for r in cfg["column_roles"] if r["role"] == role and r["file"] == "train.csv"})
        if len(cand) == 1 and cand[0] == ref_col:
            roles[role] = cand[0]
        elif len(cand) == 1:
            roles[role] = ref_col
            edit("E4b", f"{role}: config says {cand[0]!r}, adapter uses {ref_col!r}", ref_col)
        else:
            roles[role] = ref_col
            edit("E4", f"{role}: {cand or 'missing'} in train.csv", ref_col)
    order = [(e["parent"]["column"], e["child"]["column"]) for e in cfg["hierarchy_edges"]]
    edges = set(order)

    def parent_of(child, level):
        nonlocal edges
        while True:
            red = _reduce(edges)
            cands = [p for p, c in order if c == child and (p, c) in red]
            cands = list(dict.fromkeys(cands))
            if not cands:
                edit("E2", f"{level}: no parent of {child}", "ALL")
                return "ALL"
            bad = [p for p in cands if not holds(p, child)]
            if bad:
                for p in bad:
                    edit("E3", f"{level}: claimed {p}->{child} is not a true dependency", f"dropped {p}->{child}")
                    edges.discard((p, child))
                continue
            if len(cands) > 1:
                edit("E1", f"{level}: several parents of {child}: {cands}", cands[0])
            return cands[0]

    dept = parent_of(roles["item_key"], "dept_id")
    if dept == "ALL":
        edit("E2", "cat_id: no dept level to take a parent of", "ALL")
        cat = "ALL"
    else:
        cat = parent_of(dept, "cat_id")
    state = parent_of(roles["store_key"], "state_id")
    texts = [S.issue_text(i) for i in cfg["data_quality_issues"]]
    neg = [t for t in texts if S._matches("negative_sales", t)]
    clip = any(re.search(r"clip|\b0\b|zero|drop|remov|floor|exclude|set to|filter", t) for t in neg)
    zero_fill = any(S._matches("omitted_zero_days", t) for t in texts)
    return dict(label=label, roles=roles, dept=dept, cat=cat, state=state, clip_negative=clip, zero_fill=zero_fill), edits


def build_panel(spec):
    hw = pd.read_parquet("data/raw/favorita_panel_cache.parquet", columns=["item_id", "date"])
    items_sel = set(hw["item_id"].astype(int).unique())
    start, end = hw["date"].min(), hw["date"].max()
    it = pd.read_csv(f"{FAV}/items.csv")
    st = pd.read_csv(f"{FAV}/stores.csv")
    parts = []
    r = spec["roles"]  # after any logged edits; the builder never substitutes columns itself
    src = {r["item_key"]: "item_nbr", r["store_key"]: "store_nbr", r["date"]: "date", r["sales_target"]: "unit_sales"}
    if len(src) != 4:
        raise ValueError(f"roles map several required roles to one column: {r}")
    for ch in pd.read_csv(f"{FAV}/train.csv", usecols=list(src), chunksize=5_000_000):
        ch = ch.rename(columns=src)
        ch = ch[(ch["date"] >= str(start.date())) & ch["item_nbr"].isin(items_sel)]
        if len(ch):
            parts.append(ch)
    tr = pd.concat(parts, ignore_index=True)
    tr["unit_sales"] = tr["unit_sales"].astype("float32")
    tr["date"] = pd.to_datetime(tr["date"])
    if spec["clip_negative"]:
        tr["unit_sales"] = tr["unit_sales"].clip(lower=0)
    if spec["zero_fill"]:
        pairs = tr[["item_nbr", "store_nbr"]].drop_duplicates()
        grid = pairs.merge(pd.DataFrame({"date": pd.date_range(start, end)}), how="cross")
        tr = grid.merge(tr, on=["item_nbr", "store_nbr", "date"], how="left").fillna({"unit_sales": 0.0})

    def attr(frame, key, col):
        return frame.set_index(key)[col] if col != "ALL" else None
    df = pd.DataFrame({"item_id": tr["item_nbr"].astype(str), "store_id": tr["store_nbr"].astype(str),
                       "date": tr["date"], "sales": tr["unit_sales"].astype(float)})
    for name, frame, key, col in (("dept_id", it, "item_nbr", spec["dept"]), ("state_id", st, "store_nbr", spec["state"])):
        df[name] = "ALL" if col == "ALL" else tr[key].map(attr(frame, key, col)).astype(str).to_numpy()
    if spec["cat"] == "ALL":
        df["cat_id"] = "ALL"
    else:
        dept_to_cat = it.drop_duplicates(spec["dept"]).set_index(spec["dept"])[spec["cat"]]
        df["cat_id"] = tr["item_nbr"].map(it.set_index("item_nbr")[spec["dept"]]).map(dept_to_cat).astype(str).to_numpy()
    df.insert(0, "id", df["item_id"] + "_" + df["store_id"])
    df["wday"] = ((df["date"].dt.dayofweek + 2) % 7 + 1).astype(int)
    df["is_weekend"] = df["wday"].isin([1, 2]).astype(int)
    df["sell_price"], df["snap"], df["event_flag"] = 1.0, 0, 0
    df["revenue"] = df["sales"]
    df = df[["id", "item_id", "dept_id", "cat_id", "store_id", "state_id", "date", "wday", "sales",
             "sell_price", "snap", "is_weekend", "event_flag", "revenue"]].sort_values(["id", "date"]).reset_index(drop=True)
    df.attrs["dataset"] = "favorita"
    return df


def select():
    sc = pd.read_csv("outputs/onboarding_scores.csv")
    ag = sc[(sc.actor == "agent") & sc.ok.astype(bool)]
    rows = []
    for cond, g in ag.groupby("condition"):
        best = g.sort_values(["edge_f1", "flag_accuracy", "issue_recall", "repeat"],
                             ascending=[False, False, False, True]).iloc[0]
        rnd = int(np.random.default_rng(2026).choice(sorted(g["repeat"].astype(int))))
        rows += [dict(label=best.label, condition=cond, repeat=int(best["repeat"]), why="best-scoring"),
                 dict(label=f"agent_{cond}_rep{rnd}", condition=cond, repeat=rnd, why="random (rng 2026)")]
    rows.append(dict(label="baseline_named", condition="named", repeat=None, why="non-LLM baseline (extra)"))
    pd.DataFrame(rows).to_csv(SEL, index=False)
    print(pd.DataFrame(rows).to_string(index=False))


def _run_trials(df, label, n=20, seed=42):
    done = set()
    if os.path.exists(TRIALS):
        p = pd.read_csv(TRIALS)
        done = set(zip(p.label, p.drift_type, p.trial))
    hierarchy = Hierarchy(df)
    for dt, trial, ref, cur, make in iter_trials(df, seed, n):
        if (label, dt, trial) in done:
            continue
        drifted, log = make()
        detected, cands = detect_and_localize(drifted, ref, cur, FROZEN)
        rk = _ranking(cands)
        row = dict(label=label, seed=seed, drift_type=dt, trial=trial, true_slice=f"{log['slice_col']}={log['slice_value']}",
                   detected=detected, ranking="|".join(f"{c}={v}" for c, v in rk[:3]), **score(rk, log, hierarchy))
        pd.DataFrame([row]).to_csv(TRIALS, mode="a", index=False, header=not os.path.exists(TRIALS))
    print(f"[{label}] trials done", flush=True)


def run():
    mapping = json.load(open(S.MAPPING))
    sel = pd.read_csv(SEL)
    specs = []
    hw = pd.read_parquet("data/raw/favorita_panel_cache.parquet")
    hw.attrs["dataset"] = "favorita"
    _run_trials(hw, "hand_written")
    del hw
    all_edits = []
    for _, s in sel.drop_duplicates("label").iterrows():
        cfg = json.load(open(f"outputs/onboarding/raw/{s.label}.json"))["config"]
        if s.condition == "obscured":
            cfg = S.translate(cfg, mapping)
        spec, edits = to_spec(cfg, s.label)
        specs.append(dict(spec, roles=json.dumps(spec["roles"]), n_manual_edits=len(edits)))
        all_edits += edits or [dict(label=s.label, edit_type="none", problem="", applied="")]
        pd.DataFrame(all_edits).to_csv(EDITS, index=False)
        pd.DataFrame(specs).to_csv("outputs/onboarding_downstream_specs.csv", index=False)
        t0 = time.time()
        df = build_panel(spec)
        Hierarchy(df)  # raises if the converted hierarchy is not a hierarchy
        print(f"[{s.label}] panel {df.shape}, dept={spec['dept']} cat={spec['cat']} state={spec['state']} "
              f"zero_fill={spec['zero_fill']} clip={spec['clip_negative']} edits={len(edits)} ({time.time()-t0:.0f}s)", flush=True)
        _run_trials(df, s.label)
        del df
    pd.DataFrame(specs).to_csv("outputs/onboarding_downstream_specs.csv", index=False)
    summarize()


def summarize():
    t = pd.read_csv(TRIALS)
    rows = []
    for label, g in t.groupby("label", sort=False):
        for grp, sub in list(g.groupby("drift_type")) + [("ALL", g)]:
            for m in ("strict_top1", "hier_top1", "strict_top3"):
                k, n = int(sub[m].sum()), len(sub)
                lo, hi = wilson_interval(k, n)
                rows.append(dict(label=label, group=grp, metric=m, k=k, n=n, rate=k / n, ci_lo=lo, ci_hi=hi))
    out = pd.DataFrame(rows)
    out.to_csv("outputs/onboarding_downstream_summary.csv", index=False)
    print(out[out.group == "ALL"].to_string(index=False))


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    {"select": select, "run": run, "summarize": summarize}[sys.argv[1]]()
