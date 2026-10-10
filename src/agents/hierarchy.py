"""Slice hierarchy lookup, parameterised by dataset.

Both supported datasets are loaded into the same panel schema
(item_id -> dept_id -> cat_id, store_id -> state_id), so the parent/child
EDGES are declared per dataset in HIERARCHY_SPECS while the value-level
lookups (which dept an item belongs to, etc.) are always built from the
panel itself. This scores whether a predicted slice is an ancestor or
descendant of the true slice, so evaluation can report both strict and
hierarchy-aware accuracy.

The dataset is taken from the `dataset` argument, else `df.attrs["dataset"]`
(set by the Favorita loader), else "m5" -- so existing callers that pass
only `df` keep the exact M5 behavior they had before this was parameterised.
"""
from __future__ import annotations
import pandas as pd

HIERARCHY_SPECS = {
    # M5: item -> dept -> category; store -> state (CA/TX/WI). Unchanged from
    # the original hard-coded chain.
    "m5": {
        "parent_of": {"item_id": "dept_id", "dept_id": "cat_id", "store_id": "state_id"},
    },
    # Corporacion Favorita (see src/data/load_favorita.py for the column
    # mapping): item -> family (dept_id) -> perishable group (cat_id);
    # store -> Ecuadorian state/province (state_id).
    "favorita": {
        "parent_of": {"item_id": "dept_id", "dept_id": "cat_id", "store_id": "state_id"},
    },
}


class Hierarchy:
    def __init__(self, df: pd.DataFrame, dataset: str | None = None):
        self.dataset = dataset or df.attrs.get("dataset", "m5")
        if self.dataset not in HIERARCHY_SPECS:
            raise ValueError(f"unknown dataset {self.dataset!r}; known: {sorted(HIERARCHY_SPECS)}")
        # child -> parent chains
        self.parent_of = dict(HIERARCHY_SPECS[self.dataset]["parent_of"])
        self.child_of = {v: k for k, v in self.parent_of.items()}
        # child column -> {child value: parent value}, built from the panel
        self.lookup = {}
        for child, parent in self.parent_of.items():
            pairs = df[[child, parent]].drop_duplicates()
            dup = pairs[pairs[child].duplicated(keep=False)]
            if len(dup):
                raise ValueError(f"{self.dataset}: {child} -> {parent} is not a hierarchy; "
                                 f"{dup[child].nunique()} {child} values map to several {parent} values, "
                                 f"e.g. {dup.head(4).values.tolist()}")
            self.lookup[child] = dict(pairs.values)
        # backward-compatible names
        self.item_to_dept = self.lookup.get("item_id", {})
        self.dept_to_cat = self.lookup.get("dept_id", {})
        self.store_to_state = self.lookup.get("store_id", {})

    def ancestors(self, col: str, val: str) -> list[tuple]:
        """Return [(col, val), (parent_col, parent_val), ...] up the chain."""
        chain = [(col, val)]
        cur_col, cur_val = col, val
        while cur_col in self.parent_of:
            parent_val = self.lookup[cur_col].get(cur_val)
            if parent_val is None:
                break
            parent_col = self.parent_of[cur_col]
            chain.append((parent_col, parent_val))
            cur_col, cur_val = parent_col, parent_val
        return chain

    def is_related_slice(self, pred_col: str, pred_val: str, true_col: str, true_val: str) -> bool:
        """True if predicted slice is an ancestor OR descendant of the true
        slice on the hierarchy (or an exact match)."""
        if pred_col == true_col:
            return pred_val == true_val
        pred_chain = dict(self.ancestors(pred_col, pred_val))
        true_chain = dict(self.ancestors(true_col, true_val))
        # pred is ancestor of true?
        if pred_col in true_chain and true_chain[pred_col] == pred_val:
            return True
        # true is ancestor of pred?
        if true_col in pred_chain and pred_chain[true_col] == true_val:
            return True
        return False
