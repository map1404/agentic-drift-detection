"""Score drafted adapter configs against the hand-written Favorita adapter
(Phase 8). The REFERENCE LIVES ONLY HERE: it is derived from
src/data/load_favorita.py (dept_id = items.family, state_id = stores.state,
negative unit_sales clipped, omitted zero-sales days zero-filled, no price
column -> neutral fill) and from the frozen Sentinel's binning on the
hand-written panel. Nothing in this module is reachable by the agent.

Obscured configs are first translated back to original names with the
private mapping (mechanical, not an edit).

Writes outputs/onboarding_scores.csv, onboarding_issue_matches.csv,
onboarding_design_judgements.csv, onboarding_agreement.csv,
onboarding_llm_only_findings.csv.
"""
from __future__ import annotations

import glob
import itertools
import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT = "outputs"
RAW = "outputs/onboarding/raw"
MAPPING = "outputs/onboarding/private_mapping.json"

# --- reference, from load_favorita.py -----------------------------------------
ITEM, STORE, FAMILY, STATE, SALES = "item_nbr", "store_nbr", "family", "state", "unit_sales"
SCORED_NODES = {ITEM, FAMILY, STORE, STATE}
REF_EDGES = {(FAMILY, ITEM), (STATE, STORE)}                  # (parent, child), ground truth
DESIGN_JUDGEMENT_EDGES = {("perishable", FAMILY): "cat_id = perishable group (adapter's choice)"}
REF_FLAGS = {"zero_inflated": True, "fractional_sales": True}
KNOWN_ISSUES = {
    "negative_sales": r"negativ|return",
    "omitted_zero_days": (r"(missing|absent|omit|gap|sparse|not recorded|implicit|unrecorded|no row|not present)"
                          r".{0,60}(day|date|row|record|zero|observation)"
                          r"|zero.{0,40}(missing|omit|absent|not recorded|not stored|implicit)"
                          r"|full (daily )?(grid|calendar|panel)|reindex|densif|fill.{0,30}(\b0\b|zero)"),
    "absent_prices": r"price",
}
# extra categories used only to compare LLM findings with the baseline's
EXTRA_ISSUES = {
    "calendar_dates_missing_all_groups": r"(christmas|dec(ember)?[- ]25|holiday closure|dates? (missing|absent) (for|from|across) (all|every|the whole)|calendar dates)",
    "promotion_nulls": r"(onpromotion|promot).{0,60}(null|nan|missing)|(null|nan|missing).{0,60}(onpromotion|promot)",
}


def reference_bin_path():
    """Path the frozen Sentinel's adaptive_bin_edges takes for sales on the
    hand-written panel: integer path iff the values are integer-like."""
    s = pd.read_parquet("data/raw/favorita_panel_cache.parquet", columns=["sales"])["sales"].to_numpy()
    return "integer" if np.allclose(s, np.round(s), atol=1e-6) else "quantile"


# --- helpers ------------------------------------------------------------------
def translate(cfg, mapping):
    """obscured -> original names (files, columns, values)."""
    fm, cm = mapping["files"], mapping["columns"]
    s = json.dumps(cfg)
    out = json.loads(s)

    def fc(d):
        return {"file": fm.get(d.get("file"), d.get("file")), "column": cm.get(d.get("column"), d.get("column"))}
    for r in out["column_roles"]:
        r.update(fc(r))
    for e in out["hierarchy_edges"]:
        e["parent"], e["child"] = fc(e["parent"]), fc(e["child"])
    for b in out["bin_strategy_per_feature"]:
        b.update(fc(b))
    text_keys = sorted(cm, key=len, reverse=True)  # replace col_zz before col_z
    for i in out["data_quality_issues"]:
        for k in ("issue", "evidence", "handling"):
            t = i.get(k, "")
            for code in text_keys:
                t = re.sub(rf"\b{re.escape(code)}\b", cm[code], t)
            for code, orig in fm.items():
                t = t.replace(code, orig)
            i[k] = t
    return out


def edges_of(cfg):
    return {(e["parent"]["column"], e["child"]["column"]) for e in cfg["hierarchy_edges"]}


def closure(edges):
    anc = {}
    nodes = {n for e in edges for n in e}
    adj = {n: {p for p, c in edges if c == n} for n in nodes}
    for n in nodes:
        seen, stack = set(), list(adj[n])
        while stack:
            p = stack.pop()
            if p not in seen:
                seen.add(p)
                stack += list(adj.get(p, ()))
        anc[n] = seen
    return {(a, n) for n, ps in anc.items() for a in ps}


def edge_prf(pred_edges):
    P = {e for e in closure(pred_edges) if e[0] in SCORED_NODES and e[1] in SCORED_NODES}
    G = closure(REF_EDGES)
    tp = len(P & G)
    prec = tp / len(P) if P else 0.0
    rec = tp / len(G)
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1, sorted(P - G), sorted(G - P)


def true_fds():
    """Which candidate edges actually hold in the full raw dimension files."""
    it = pd.read_csv("data/raw/favorita/items.csv")
    st = pd.read_csv("data/raw/favorita/stores.csv")
    def fd(d, child, parent):
        return child in d and parent in d and bool((d.groupby(child)[parent].nunique() <= 1).all())
    return lambda parent, child: fd(it, child, parent) or fd(st, child, parent)


def _matches(category, text):
    """Keyword match. Tightened 2026-10-10 BEFORE any agent output existed:
    an issue about calendar dates missing for every group (e.g. store-closure
    days) only counts as 'omitted zero-sales days' if it also talks about
    zeros or per item/store/group gaps."""
    if not re.search({**KNOWN_ISSUES, **EXTRA_ISSUES}[category], text):
        return False
    if category == "omitted_zero_days" and re.search(EXTRA_ISSUES["calendar_dates_missing_all_groups"], text):
        return bool(re.search(r"zero|per[- ](group|item|store|series)|item-store|item.store", text))
    return True


def issue_text(i):
    return " ".join(str(i.get(k, "")) for k in ("issue", "evidence", "handling")).lower()


def sales_bin_path(cfg):
    paths = [b["path"] for b in cfg["bin_strategy_per_feature"] if b.get("column") == SALES]
    return paths[0] if paths else None


# --- main ---------------------------------------------------------------------
def load_runs():
    mapping = json.load(open(MAPPING))
    runs = []
    for p in sorted(glob.glob(f"{RAW}/*.json")):
        r = json.load(open(p))
        m = r["meta"]
        actor = m.get("actor", "agent")
        cfg = r.get("config")
        if cfg and m["condition"] == "obscured":
            cfg = translate(cfg, mapping)
        runs.append(dict(actor=actor, condition=m["condition"], repeat=m.get("repeat"), ok=r.get("ok", False),
                         fallback=r.get("fallback_reason"), config=cfg, path=p, meta=m))
    return runs


def main():
    ref_path = reference_bin_path()
    holds = true_fds()
    runs = load_runs()
    rows, matches, judgements, llm_only = [], [], [], []
    for r in runs:
        label = f"{r['actor']}_{r['condition']}" + (f"_rep{r['repeat']}" if r["repeat"] is not None else "")
        base = dict(label=label, actor=r["actor"], condition=r["condition"], repeat=r["repeat"], ok=r["ok"],
                    fallback=r["fallback"])
        cfg = r["config"]
        if not r["ok"] or not cfg:
            rows.append(base)
            continue
        E = edges_of(cfg)
        prec, rec, f1, fp, fn = edge_prf(E)
        flags = {k: cfg[k]["value"] for k in REF_FLAGS}
        flag_acc = np.mean([flags[k] == REF_FLAGS[k] for k in REF_FLAGS])
        bp = sales_bin_path(cfg)
        claimed = next((r["column"] for r in cfg["column_roles"] if r["role"] == "sales_target"), None)
        claimed_bp = next((b["path"] for b in cfg["bin_strategy_per_feature"] if b.get("column") == claimed), None)
        hit = {}
        for k, pat in {**KNOWN_ISSUES, **EXTRA_ISSUES}.items():
            found = [i for i in cfg["data_quality_issues"] if _matches(k, issue_text(i))]
            hit[k] = bool(found)
            for i in found:
                matches.append(dict(label=label, category=k, known=k in KNOWN_ISSUES, issue=i.get("issue"),
                                    evidence=i.get("evidence"), handling=i.get("handling")))
        for i in cfg["data_quality_issues"]:
            if not any(_matches(k, issue_text(i)) for k in {**KNOWN_ISSUES, **EXTRA_ISSUES}):
                llm_only.append(dict(label=label, issue=i.get("issue"), evidence=i.get("evidence"),
                                     handling=i.get("handling"), note="matches no scored or baseline category"))
        invalid = sorted(e for e in E if not holds(*e))
        for e in sorted(E):
            if e not in REF_EDGES and e in DESIGN_JUDGEMENT_EDGES or (e[0] not in SCORED_NODES or e[1] not in SCORED_NODES):
                judgements.append(dict(label=label, parent=e[0], child=e[1], holds_in_data=holds(*e),
                                       note=DESIGN_JUDGEMENT_EDGES.get(e, "edge outside the scored levels "
                                                                          "(not penalised; for human rating)")))
        rows.append(dict(base, edge_precision=prec, edge_recall=rec, edge_f1=f1,
                         edges="; ".join(f"{p}->{c}" for p, c in sorted(E)),
                         false_scored_relations="; ".join(f"{p}->{c}" for p, c in fp),
                         missed_relations="; ".join(f"{p}->{c}" for p, c in fn),
                         edges_not_true_in_data="; ".join(f"{p}->{c}" for p, c in invalid),
                         zero_inflated=flags["zero_inflated"], fractional_sales=flags["fractional_sales"],
                         flag_accuracy=flag_acc, sales_bin_path=bp, reference_bin_path=ref_path,
                         # disclosed extra (added after seeing outputs): the path given to the
                         # column the config itself names as the sales target
                         claimed_target=claimed, claimed_target_bin_path=claimed_bp,
                         claimed_target_is_sales=claimed == SALES,
                         bin_path_correct=bp == ref_path,
                         **{f"issue_{k}": v for k, v in hit.items()},
                         issue_recall=np.mean([hit[k] for k in KNOWN_ISSUES]),
                         n_issues_reported=len(cfg["data_quality_issues"]),
                         n_tool_calls=r["meta"].get("max_tool_calls") and json.load(open(r["path"])).get("n_tool_calls"),
                         model=r["meta"].get("model"), provider=r["meta"].get("provider"),
                         temperature=r["meta"].get("temperature"), seed=r["meta"].get("seed")))
    scores = pd.DataFrame(rows)
    scores.to_csv(f"{OUT}/onboarding_scores.csv", index=False)
    pd.DataFrame(matches).to_csv(f"{OUT}/onboarding_issue_matches.csv", index=False)
    pd.DataFrame(judgements).to_csv(f"{OUT}/onboarding_design_judgements.csv", index=False)
    pd.DataFrame(llm_only).to_csv(f"{OUT}/onboarding_llm_only_findings.csv", index=False)

    # run-to-run agreement across the agent's repeats
    agr = []
    for cond, g in scores[(scores.actor == "agent") & scores.ok.fillna(False).astype(bool)].groupby("condition"):
        cfgs = {r["repeat"]: r["config"] for r in runs if r["actor"] == "agent" and r["condition"] == cond and r["ok"]}
        pairs = list(itertools.combinations(sorted(cfgs), 2))
        jac = [len(edges_of(cfgs[a]) & edges_of(cfgs[b])) / max(len(edges_of(cfgs[a]) | edges_of(cfgs[b])), 1)
               for a, b in pairs]
        def modal_share(col):
            v = g[col].astype(str)
            return v.value_counts().iloc[0] / len(v)
        agr.append(dict(condition=cond, n_ok_repeats=len(g), mean_pairwise_edge_jaccard=np.mean(jac) if jac else np.nan,
                        identical_edge_sets_share=np.mean([j == 1.0 for j in jac]) if jac else np.nan,
                        modal_share_edge_f1=modal_share("edge_f1"), modal_share_zero_flag=modal_share("zero_inflated"),
                        modal_share_fractional_flag=modal_share("fractional_sales"),
                        modal_share_bin_path=modal_share("sales_bin_path"),
                        modal_share_issue_recall=modal_share("issue_recall"),
                        bin_path_correct_count=int(g.bin_path_correct.sum())))
    pd.DataFrame(agr).to_csv(f"{OUT}/onboarding_agreement.csv", index=False)
    pd.set_option("display.width", 250)
    cols = ["label", "ok", "edge_precision", "edge_recall", "edge_f1", "flag_accuracy", "sales_bin_path",
            "bin_path_correct", "issue_recall", "edges_not_true_in_data"]
    print(scores[[c for c in cols if c in scores]].to_string(index=False))
    print(pd.DataFrame(agr).to_string(index=False))


if __name__ == "__main__":
    main()
