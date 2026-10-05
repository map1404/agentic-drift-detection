"""M5's schema is a real hierarchy: item_id -> dept_id -> cat_id,
store_id -> state_id. This module builds that lookup from the panel itself
and scores whether a predicted slice is an ancestor/descendant of the true
slice, so evaluation can report both strict and hierarchy-aware accuracy.
"""
from __future__ import annotations
import pandas as pd


class Hierarchy:
    def __init__(self, df: pd.DataFrame):
        self.dept_to_cat = dict(df[["dept_id", "cat_id"]].drop_duplicates().values)
        self.item_to_dept = dict(df[["item_id", "dept_id"]].drop_duplicates().values)
        self.store_to_state = dict(df[["store_id", "state_id"]].drop_duplicates().values)
        # child -> parent chains
        self.parent_of = {"item_id": "dept_id", "dept_id": "cat_id", "store_id": "state_id"}
        self.child_of = {v: k for k, v in self.parent_of.items()}

    def ancestors(self, col: str, val: str) -> list[tuple]:
        """Return [(col, val), (parent_col, parent_val), ...] up the chain."""
        chain = [(col, val)]
        cur_col, cur_val = col, val
        while cur_col in self.parent_of:
            parent_col = self.parent_of[cur_col]
            if cur_col == "item_id":
                parent_val = self.item_to_dept.get(cur_val)
            elif cur_col == "dept_id":
                parent_val = self.dept_to_cat.get(cur_val)
            elif cur_col == "store_id":
                parent_val = self.store_to_state.get(cur_val)
            else:
                parent_val = None
            if parent_val is None:
                break
            chain.append((parent_col, parent_val))
            cur_col, cur_val = parent_col, parent_val
        return chain

    def is_related_slice(self, pred_col: str, pred_val: str, true_col: str, true_val: str) -> bool:
        """True if predicted slice is an ancestor OR descendant of the true
        slice on the M5 hierarchy (or an exact match)."""
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
