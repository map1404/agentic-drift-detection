"""Non-LLM baseline profiler (Phase 8): the same output schema as the LLM
agent, from functional-dependency checks and the same numeric tests, using
the same sandboxed ProfileTools. Name-agnostic: it never looks at column or
file names, only at values, so it behaves identically in both conditions.

Rules:
- date column: >= 95% of a 1000-row sample parses as a date.
- fact file: the largest file with a date column. Its integer columns that
  are unique-per-row in some smaller file are keys; the key with more
  distinct values is the item key, the other the store key. A column unique
  per row in the fact file is a row id. The non-key numeric column with the
  most distinct values is the sales target.
- dimension files: the smaller file in which a key is unique; their other
  columns are item/store attributes.
- calendar file: a date-bearing file with no key column.
- hierarchy: within each dimension file, a -> b whenever a determines b and
  n_unique(a) > n_unique(b) > 1; then transitive reduction. Edge = (b parent, a child).
- zero_inflated: fraction_zero(target) > 0.3 OR mean per-(item,store) share of
  missing days > 0.3 (omitted rows become zeros in a daily panel).
- fractional_sales = has_fractional(target); bin path: integer iff not fractional.
- issues: negative target values; per-group date gaps; calendar dates missing
  for every group; nulls in fact columns; no non-target fractional numeric
  column in the fact or item file (no price-like column).
"""
from __future__ import annotations

import itertools
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from onboarding.profile_tools import ProfileTools


def _j(s):
    return json.loads(s)


def profile(sandbox_dir, log_path=None, context=None):
    T = ProfileTools(sandbox_dir, log_path=log_path, log_context=context)
    files = {f["file"]: f["n_rows"] for f in _j(T.call("list_files", {}))["files"]}
    cols = {f: [c["name"] for c in _j(T.call("list_columns", {"file": f}))["columns"]] for f in files}

    def is_date(f, c):
        s = T._df(f)[c].dropna().astype(str).head(1000)
        return len(s) > 0 and pd.to_datetime(s, errors="coerce", format="mixed").notna().mean() >= 0.95 and \
            s.str.contains(r"\d{4}-\d{2}-\d{2}").mean() > 0.9

    date_cols = {f: [c for c in cols[f] if is_date(f, c)] for f in files}
    fact = max((f for f in files if date_cols[f]), key=lambda f: files[f])
    nuniq = {(f, c): _j(T.call("n_unique", {"file": f, "col": c}))["n_unique"] for f in files for c in cols[f]}

    roles, rationale = [], {}
    keys = []
    for c in cols[fact]:
        if c in date_cols[fact]:
            continue
        dims = [f for f in files if f != fact and c in cols[f] and nuniq[(f, c)] == files[f]]
        if dims:
            keys.append((c, dims[0]))
    keys.sort(key=lambda kc: -nuniq[(fact, kc[0])])
    item_key, item_dim = keys[0] if keys else (None, None)
    store_key, store_dim = keys[1] if len(keys) > 1 else (None, None)
    numeric = [c for c in cols[fact] if pd.api.types.is_numeric_dtype(T._df(fact)[c])]
    row_ids = [c for c in numeric if nuniq[(fact, c)] == files[fact]]
    target = max((c for c in numeric if c not in {item_key, store_key, *row_ids}),
                 key=lambda c: nuniq[(fact, c)], default=None)
    for c in cols[fact]:
        role = ("date" if c in date_cols[fact] else "item_key" if c == item_key else "store_key" if c == store_key
                else "row_id" if c in row_ids else "sales_target" if c == target else "other")
        roles.append(dict(file=fact, column=c, role=role))
    for f, key, kind in ((item_dim, item_key, "item_attribute"), (store_dim, store_key, "store_attribute")):
        if f:
            roles += [dict(file=f, column=c, role="item_key" if c == item_key else "store_key" if c == store_key else kind)
                      for c in cols[f]]
    for f in files:
        if f not in (fact, item_dim, store_dim):
            has_key = any(k in cols[f] for k in (item_key, store_key))
            for c in cols[f]:
                role = ("calendar_event" if not has_key else "date" if c in date_cols[f]
                        else "store_key" if c == store_key else "item_key" if c == item_key else "other")
                roles.append(dict(file=f, column=c, role=role))

    # hierarchy: functional dependencies within each dimension file, transitive reduction
    edges = []
    for f, key in ((item_dim, item_key), (store_dim, store_key)):
        if not f:
            continue
        cs = [c for c in cols[f] if nuniq[(f, c)] > 1]
        fds = set()
        for a, b in itertools.permutations(cs, 2):
            if nuniq[(f, a)] > nuniq[(f, b)] and \
                    _j(T.call("is_functionally_determined", {"file": f, "col_a": a, "col_b": b}))["a_determines_b"]:
                fds.add((a, b))
        reduced = {(a, b) for a, b in fds if not any((a, m) in fds and (m, b) in fds for m in cs)}
        edges += [dict(parent=dict(file=f, column=b), child=dict(file=f, column=a)) for a, b in sorted(reduced)]

    zero = _j(T.call("fraction_zero", {"file": fact, "col": target}))["fraction_zero"]
    gaps = _j(T.call("date_gaps", {"file": fact, "date_col": date_cols[fact][0],
                                   "group_cols": [k for k in (store_key, item_key) if k]}))
    miss = gaps["per_group_fraction_of_days_missing_between_first_and_last_date"]["mean"]
    frac = _j(T.call("has_fractional", {"file": fact, "col": target}))
    neg = _j(T.call("has_negative", {"file": fact, "col": target}))

    issues = []
    if neg["has_negative"]:
        issues.append(dict(issue="negative values in the sales target", evidence=f"{neg['n_negative']} negative rows",
                           handling="clip to 0 or treat as returns"))
    if miss > 0:
        issues.append(dict(issue="per-group date gaps: rows absent for some item-store-days",
                           evidence=f"mean share of days missing per group {miss:.3f}",
                           handling="reindex to a full daily grid and fill absent days with 0"))
    if gaps["n_distinct_dates"] < gaps["n_days_in_range"]:
        issues.append(dict(issue="calendar dates absent for every group",
                           evidence=f"{gaps['n_days_in_range'] - gaps['n_distinct_dates']} dates missing from the whole file",
                           handling="decide whether to fill these dates"))
    for c in cols[fact]:
        nr = _j(T.call("null_rate", {"file": fact, "col": c}))["null_rate"]
        if nr and nr > 0:
            issues.append(dict(issue=f"nulls in fact column {c}", evidence=f"null rate {nr:.3f}", handling="fill or drop"))
    price_like = [c for f in (fact, item_dim) if f for c in cols[f] if c != target and
                  pd.api.types.is_numeric_dtype(T._df(f)[c]) and
                  _j(T.call("has_fractional", {"file": f, "col": c}))["has_fractional"]]
    if not price_like:
        issues.append(dict(issue="no price-like column (no non-target fractional numeric column)",
                           evidence="checked fact and item files", handling="fill price with a neutral constant"))

    return dict(
        column_roles=roles, hierarchy_edges=edges,
        zero_inflated=dict(value=bool((zero or 0) > 0.3 or miss > 0.3),
                           rationale=f"fraction_zero={zero}, mean per-group missing-day share={miss:.3f}"),
        fractional_sales=dict(value=bool(frac["has_fractional"]), rationale=f"fraction fractional={frac['fraction_fractional']}"),
        bin_strategy_per_feature=[dict(file=fact, column=target, path="quantile" if frac["has_fractional"] else "integer",
                                       rationale="integer path only if every value is an integer")],
        data_quality_issues=issues,
        rationale="rule-based baseline; see module docstring")


def main():
    os.makedirs("outputs/onboarding/raw", exist_ok=True)
    for cond in ("named", "obscured"):
        cfg = profile(f"outputs/onboarding/sandbox/{cond}", "outputs/onboarding/toolcalls_baseline.jsonl",
                      dict(condition=cond, actor="baseline"))
        json.dump(dict(meta=dict(condition=cond, actor="baseline", llm=None), ok=True, config=cfg),
                  open(f"outputs/onboarding/raw/baseline_{cond}.json", "w"), indent=1)
        print(cond, "edges:", [(e["parent"]["column"], e["child"]["column"]) for e in cfg["hierarchy_edges"]],
              "| zero", cfg["zero_inflated"]["value"], "| frac", cfg["fractional_sales"]["value"],
              "| issues", [i["issue"][:40] for i in cfg["data_quality_issues"]])


if __name__ == "__main__":
    main()
