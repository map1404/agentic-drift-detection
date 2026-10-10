"""LLM agent that drafts a dataset adapter config from raw files (Phase 8).

Same model and provider as the frozen investigator agent: it reuses that
module's ChatClient unchanged (Cerebras gpt-oss-120b, temperature 0,
reasoning_effort medium); the seed is the repeat index. The agent sees only
the profiling tools bound to one sandbox directory (profile_tools.py), and
the prompt describes only the drift pipeline's input contract -- nothing
about Favorita, M5 or the data-quality issues being scored.
"""
from __future__ import annotations

import json
import os
import time

from agents.llm_investigator_agent import ChatClient, QuotaExhausted, AuthFailed  # noqa: F401 (re-exported)
from onboarding.profile_tools import ProfileTools

MAX_TOOL_CALLS = 30
ROLES = ["date", "sales_target", "item_key", "store_key", "item_attribute", "store_attribute",
         "promotion", "calendar_event", "row_id", "other"]

SYSTEM_PROMPT = """\
You are onboarding a raw retail dataset into a drift-monitoring pipeline. You can only inspect the raw files through the tools; you cannot read anything else.

WHAT THE PIPELINE NEEDS (its input contract):
- A daily panel with one row per item x store x day: an item key, a store key, the date, and the sales quantity to monitor.
- A product hierarchy with two levels above item (item -> dept -> category) and a location hierarchy with one level above store (store -> region). Each child value must map to exactly one parent value.
- Numeric features are binned for divergence tests in one of two ways: the "integer" path (one bin per integer value; only valid if every value is an integer) or the "quantile" path (equal-frequency bins).

YOUR JOB: draft the adapter config by profiling the files, then call submit_config once. Base every claim on tool evidence. Report in data_quality_issues anything in the raw data that the panel builder must handle to produce a correct daily panel. Give a short rationale for every non-trivial choice.

BUDGET: at most {max_calls} profiling tool calls (submit_config not counted); each result reports how many remain."""

_FILECOL = {"type": "object", "properties": {"file": {"type": "string"}, "column": {"type": "string"}},
            "required": ["file", "column"]}

SUBMIT_SCHEMA = {
    "type": "object",
    "properties": {
        "column_roles": {"type": "array", "items": {"type": "object", "properties": {
            "file": {"type": "string"}, "column": {"type": "string"}, "role": {"type": "string", "enum": ROLES},
            "rationale": {"type": "string"}}, "required": ["file", "column", "role"]}},
        "hierarchy_edges": {"type": "array", "items": {"type": "object", "properties": {
            "parent": _FILECOL, "child": _FILECOL, "rationale": {"type": "string"}}, "required": ["parent", "child"]}},
        "zero_inflated": {"type": "object", "properties": {"value": {"type": "boolean"}, "rationale": {"type": "string"}},
                          "required": ["value", "rationale"]},
        "fractional_sales": {"type": "object", "properties": {"value": {"type": "boolean"}, "rationale": {"type": "string"}},
                             "required": ["value", "rationale"]},
        "bin_strategy_per_feature": {"type": "array", "items": {"type": "object", "properties": {
            "file": {"type": "string"}, "column": {"type": "string"},
            "path": {"type": "string", "enum": ["integer", "quantile"]}, "rationale": {"type": "string"}},
            "required": ["file", "column", "path"]}},
        "data_quality_issues": {"type": "array", "items": {"type": "object", "properties": {
            "issue": {"type": "string"}, "evidence": {"type": "string"}, "handling": {"type": "string"}},
            "required": ["issue", "evidence", "handling"]}},
        "rationale": {"type": "string"},
    },
    "required": ["column_roles", "hierarchy_edges", "zero_inflated", "fractional_sales",
                 "bin_strategy_per_feature", "data_quality_issues", "rationale"],
}


def _fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}}


_F = {"file": {"type": "string", "description": "a file name from list_files"}}
_C = {"col": {"type": "string"}}
TOOL_DEFS = [
    _fn("list_files", "List the raw files with row and column counts.", {}, []),
    _fn("list_columns", "Column names and dtypes of a file.", _F, ["file"]),
    _fn("head", "First n rows of a file (n <= 20).", {**_F, "n": {"type": "integer"}}, ["file"]),
    _fn("value_counts", "Top 15 values of a column with counts, plus its number of distinct values.", {**_F, **_C}, ["file", "col"]),
    _fn("null_rate", "Share of missing values in a column.", {**_F, **_C}, ["file", "col"]),
    _fn("n_unique", "Number of distinct values in a column.", {**_F, **_C}, ["file", "col"]),
    _fn("is_functionally_determined", "Does col_a determine col_b in every row of the file (each col_a value maps to exactly one col_b value)?",
        {**_F, "col_a": {"type": "string"}, "col_b": {"type": "string"}}, ["file", "col_a", "col_b"]),
    _fn("numeric_summary", "Mean, std and quantiles of a numeric column.", {**_F, **_C}, ["file", "col"]),
    _fn("fraction_zero", "Share of rows equal to 0 in a numeric column.", {**_F, **_C}, ["file", "col"]),
    _fn("has_fractional", "Whether a numeric column has non-integer values, and their share.", {**_F, **_C}, ["file", "col"]),
    _fn("has_negative", "Whether a numeric column has negative values, and how many.", {**_F, **_C}, ["file", "col"]),
    _fn("date_gaps", "Date coverage of a date column: overall range and, per group of group_cols, the share of days missing between the group's first and last date.",
        {**_F, "date_col": {"type": "string"}, "group_cols": {"type": "array", "items": {"type": "string"}}}, ["file", "date_col"]),
    {"type": "function", "function": {"name": "submit_config", "description": "Submit the drafted adapter config (strict JSON).",
                                      "parameters": SUBMIT_SCHEMA}},
]


def _validate(cfg):
    """Minimal strict check of the submitted JSON against SUBMIT_SCHEMA."""
    for k in SUBMIT_SCHEMA["required"]:
        if k not in cfg:
            return f"missing key {k}"
    for r in cfg["column_roles"]:
        if r.get("role") not in ROLES:
            return f"invalid role {r.get('role')!r}; allowed {ROLES}"
    for b in cfg["bin_strategy_per_feature"]:
        if b.get("path") not in ("integer", "quantile"):
            return f"invalid bin path {b.get('path')!r}"
    for k in ("zero_inflated", "fractional_sales"):
        if not isinstance(cfg[k], dict) or not isinstance(cfg[k].get("value"), bool):
            return f"{k}.value must be a boolean"
    for e in cfg["hierarchy_edges"]:
        if not all(isinstance(e.get(s), dict) and "file" in e[s] and "column" in e[s] for s in ("parent", "child")):
            return "each hierarchy edge needs parent{file,column} and child{file,column}"
    return None


def run(sandbox_dir, chat, log_path, context, max_calls=MAX_TOOL_CALLS, max_turns=60):
    """Returns dict(ok, config, fallback_reason, n_tool_calls, transcript, tokens)."""
    tools = ProfileTools(sandbox_dir, log_path=log_path, log_context=context)
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(max_calls=max_calls)},
                {"role": "user", "content": "Begin profiling the dataset."}]
    transcript = [dict(m) for m in messages]
    used = submits = nudges = 0
    tokens = dict(prompt=0, completion=0, cached=0)
    budget_done = False
    for _ in range(max_turns):
        choice = {"type": "function", "function": {"name": "submit_config"}} if budget_done else "auto"
        out = chat(messages, [TOOL_DEFS[-1]] if budget_done else TOOL_DEFS, choice)
        tokens["prompt"] += out.prompt_tokens
        tokens["completion"] += out.completion_tokens
        tokens["cached"] += out.cached_tokens
        if not out.ok:
            transcript.append({"role": "_harness", "content": f"{out.error}: {out.detail}"})
            return dict(ok=False, config=None, fallback_reason=out.error, n_tool_calls=used, transcript=transcript, tokens=tokens)
        msg = out.message
        clean = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            clean["tool_calls"] = msg["tool_calls"]
        messages.append(clean)
        transcript.append(dict(clean, reasoning=msg.get("reasoning")))
        calls = msg.get("tool_calls") or []
        if not calls:
            nudges += 1
            if nudges > 2:
                return dict(ok=False, config=None, fallback_reason="no_tool_call", n_tool_calls=used, transcript=transcript, tokens=tokens)
            m = {"role": "user", "content": "Continue: call a profiling tool or submit_config."}
            messages.append(m)
            transcript.append(m)
            continue
        for tc in calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = None
            if name == "submit_config" and args is not None:
                submits += 1
                err = _validate(args)
                if err is None:
                    return dict(ok=True, config=args, fallback_reason=None, n_tool_calls=used,
                                forced=budget_done, transcript=transcript, tokens=tokens)
                result = json.dumps({"error": f"config rejected: {err}"})
                if submits >= 3:
                    return dict(ok=False, config=args, fallback_reason="invalid_config", n_tool_calls=used, transcript=transcript, tokens=tokens)
            elif args is None:
                result = json.dumps({"error": "arguments were not valid JSON"})
            elif used >= max_calls:
                result = json.dumps({"error": "tool budget exhausted; call submit_config"})
                budget_done = True
            else:
                used += 1
                result = tools.call(name, args)
                result = result[:-1] + f', "calls_remaining": {max_calls - used}}}' if result.endswith("}") else result
                budget_done = used >= max_calls
            m = {"role": "tool", "tool_call_id": tc["id"], "content": result}
            messages.append(m)
            transcript.append(m)
        if budget_done:
            m = {"role": "user", "content": "Tool budget exhausted. Call submit_config now."}
            messages.append(m)
            transcript.append(m)
    return dict(ok=False, config=None, fallback_reason="max_turns", n_tool_calls=used, transcript=transcript, tokens=tokens)
