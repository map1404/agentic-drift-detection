"""Profiling tools for adapter-config drafting (Phase 8), shared by the LLM
agent and the non-LLM baseline so both see the same evidence.

SANDBOX: a ProfileTools instance is bound to ONE directory and can only read
files that are listed directly in it (no paths, no '..', no other
directories). It never imports or opens project code, docs or outputs. Every
call is appended to a JSONL log with a timestamp.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

MAX_CHARS = 2500  # cap on a single tool result shown to the model


def _js(x):
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return None if not np.isfinite(x) else round(float(x), 6)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, float):
        return None if not np.isfinite(x) else round(x, 6)
    return x


class SandboxError(Exception):
    pass


class ProfileTools:
    def __init__(self, sandbox_dir: str, log_path: str | None = None, log_context: dict | None = None):
        self.dir = os.path.realpath(sandbox_dir)
        self.files = sorted(f for f in os.listdir(self.dir) if os.path.isfile(os.path.join(self.dir, f)))
        self.log_path = log_path
        self.log_context = log_context or {}
        self._cache: dict[str, pd.DataFrame] = {}

    # -- sandbox ----------------------------------------------------------
    def _df(self, file):
        if not isinstance(file, str) or file not in self.files or "/" in file or ".." in file:
            raise SandboxError(f"unknown file {file!r}; available: {self.files}")
        if file not in self._cache:
            path = os.path.realpath(os.path.join(self.dir, file))
            if os.path.dirname(path) != self.dir:
                raise SandboxError("path escapes sandbox")
            self._cache[file] = pd.read_csv(path, low_memory=False)
        return self._cache[file]

    def _col(self, file, col):
        d = self._df(file)
        if col not in d.columns:
            raise SandboxError(f"unknown column {col!r} in {file}; columns: {list(d.columns)}")
        return d[col]

    # -- tools ------------------------------------------------------------
    def list_files(self):
        return {"files": [{"file": f, "n_rows": int(len(self._df(f))), "n_columns": int(self._df(f).shape[1])}
                          for f in self.files]}

    def list_columns(self, file):
        d = self._df(file)
        return {"file": file, "n_rows": int(len(d)), "columns": [{"name": c, "dtype": str(t)} for c, t in d.dtypes.items()]}

    def head(self, file, n=5):
        n = max(1, min(int(n), 20))
        return {"file": file, "rows": json.loads(self._df(file).head(n).to_json(orient="records"))}

    def value_counts(self, file, col):
        s = self._col(file, col)
        vc = s.value_counts(dropna=False).head(15)
        return {"file": file, "col": col, "n_unique": int(s.nunique(dropna=True)), "n_rows": int(len(s)),
                "top": [{"value": _js(k) if not (isinstance(k, float) and np.isnan(k)) else None, "count": int(v)}
                        for k, v in vc.items()]}

    def null_rate(self, file, col):
        s = self._col(file, col)
        return {"file": file, "col": col, "null_rate": _js(s.isna().mean()), "n_null": int(s.isna().sum())}

    def n_unique(self, file, col):
        s = self._col(file, col)
        return {"file": file, "col": col, "n_unique": int(s.nunique(dropna=True)), "n_rows": int(len(s))}

    def is_functionally_determined(self, file, col_a, col_b):
        a, b = self._col(file, col_a), self._col(file, col_b)
        g = pd.DataFrame({"a": a, "b": b}).dropna().drop_duplicates().groupby("a")["b"].nunique()
        bad = g[g > 1]
        return {"file": file, "col_a": col_a, "col_b": col_b, "a_determines_b": bool(len(bad) == 0),
                "n_a_values": int(len(g)), "n_a_values_with_multiple_b": int(len(bad)),
                "example_violations": [_js(x) for x in bad.index[:3]]}

    def numeric_summary(self, file, col):
        s = pd.to_numeric(self._col(file, col), errors="coerce")
        if s.notna().sum() == 0:
            return {"file": file, "col": col, "error": "column is not numeric"}
        q = s.quantile([0, .01, .25, .5, .75, .99, 1]).tolist()
        return {"file": file, "col": col, "n_numeric": int(s.notna().sum()), "mean": _js(s.mean()), "std": _js(s.std()),
                "min": _js(q[0]), "p01": _js(q[1]), "p25": _js(q[2]), "median": _js(q[3]),
                "p75": _js(q[4]), "p99": _js(q[5]), "max": _js(q[6])}

    def fraction_zero(self, file, col):
        s = pd.to_numeric(self._col(file, col), errors="coerce").dropna()
        return {"file": file, "col": col, "fraction_zero": _js((s == 0).mean()) if len(s) else None}

    def has_fractional(self, file, col):
        s = pd.to_numeric(self._col(file, col), errors="coerce").dropna()
        frac = (s % 1 != 0)
        return {"file": file, "col": col, "has_fractional": bool(frac.any()), "fraction_fractional": _js(frac.mean())}

    def has_negative(self, file, col):
        s = pd.to_numeric(self._col(file, col), errors="coerce").dropna()
        neg = s < 0
        return {"file": file, "col": col, "has_negative": bool(neg.any()), "n_negative": int(neg.sum()),
                "fraction_negative": _js(neg.mean())}

    def date_gaps(self, file, date_col, group_cols=None):
        d = self._df(file)
        group_cols = [group_cols] if isinstance(group_cols, str) else list(group_cols or [])
        for c in [date_col] + group_cols:
            self._col(file, c)
        dt = pd.to_datetime(d[date_col], errors="coerce")
        if dt.notna().sum() == 0:
            return {"file": file, "error": f"{date_col} does not parse as dates"}
        work = pd.DataFrame({"_d": dt}).join(d[group_cols]).dropna(subset=["_d"])
        overall = {"min_date": str(dt.min().date()), "max_date": str(dt.max().date()),
                   "n_distinct_dates": int(dt.nunique()),
                   "n_days_in_range": int((dt.max() - dt.min()).days + 1)}
        if not group_cols:
            return {"file": file, "date_col": date_col, **overall,
                    "fraction_of_range_days_missing": _js(1 - overall["n_distinct_dates"] / overall["n_days_in_range"])}
        g = work.groupby(group_cols)["_d"].agg(["min", "max", "nunique"])
        span = (g["max"] - g["min"]).dt.days + 1
        miss = 1 - g["nunique"] / span
        return {"file": file, "date_col": date_col, "group_cols": group_cols, **overall,
                "n_groups": int(len(g)),
                "per_group_fraction_of_days_missing_between_first_and_last_date": {
                    "mean": _js(miss.mean()), "median": _js(miss.median()),
                    "p90": _js(miss.quantile(0.9)), "share_of_groups_with_any_gap": _js((miss > 0).mean())}}

    TOOL_NAMES = ["list_files", "list_columns", "head", "value_counts", "null_rate", "n_unique",
                  "is_functionally_determined", "numeric_summary", "fraction_zero", "has_fractional",
                  "has_negative", "date_gaps"]

    def call(self, name, args):
        t0 = time.time()
        try:
            if name not in self.TOOL_NAMES:
                raise SandboxError(f"unknown tool {name!r}")
            out = getattr(self, name)(**(args or {}))
        except (SandboxError, TypeError, ValueError, KeyError) as e:
            out = {"error": str(e)[:400]}
        text = json.dumps(out, default=str)
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + '..."[truncated]'
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(dict(self.log_context, ts=datetime.now(timezone.utc).isoformat(), tool=name,
                                        args=args, compute_s=round(time.time() - t0, 3), result=text)) + "\n")
        return text
