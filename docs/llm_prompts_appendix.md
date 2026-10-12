# Appendix: LLM prompts used in this project (verbatim)

Generated 2026-10-13 from the repository at commit `8fccba9` and from the raw run logs in `outputs/`. **No prompt text in this file was retyped or edited**: string constants and tool definitions are dumped from the imported modules; prompts built inside functions (f-strings) are shown as their exact source code, so `{...}` expressions are the runtime placeholders; rendered examples are copied from the raw logs where the run logged them, and otherwise marked **re-rendered** (produced now by calling the unchanged builder on a real trial). Code blocks use four-backtick fences. API keys and personal data are excluded (the file was scanned for key patterns and e-mail addresses before committing).

## Overview

| # | prompt | file | final commit | provider / model | temperature | seed | max steps | used in a scored run | rendered example |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Synthesizer LLM backends (Claude, Groq, xAI) | `src/agents/synthesizer.py` | `0885eb5` | Anthropic claude-sonnet-4-6 / Groq llama-3.3-70b-versatile / xAI grok-2-latest | not set (provider default) | not set | 1 call | no (rule-based Synthesizer used everywhere) | re-rendered |
| 2a | Groq re-ranker v1 | `src/agents/groq_reranker.py` | `0885eb5` | Groq openai/gpt-oss-20b | 0 | not set | 1 call | yes, Task 5 (2026-09-29) | re-rendered |
| 2b | Groq re-ranker v2 | `src/agents/groq_reranker_v2.py` | `0885eb5` | Groq openai/gpt-oss-120b (final run; code default gpt-oss-20b) | 0 | not set | 1 call | yes, Task 5 follow-up (2026-10-03) | re-rendered |
| 3a | AgenticInvestigator LLM controller | `src/agents/agentic_investigator.py` | `0885eb5` | Anthropic claude-sonnet-4-6 (default) / Groq llama-3.3-70b-versatile / xAI grok-2-latest | not set | not set | 6 tool calls | no (never run) | re-rendered |
| 3b | Tool-using investigator (M5 + Favorita validation) | `src/agents/llm_investigator_agent.py` | `ce1c9bb` | Cerebras gpt-oss-120b | 0 | 0 | 10 tool calls (24 turns) | yes, Task 6 (M5) and Phase 7 (Favorita) | from logs (M5, Favorita) |
| 4 | Onboarding agent (Phase 8) | `src/onboarding/agent.py` | `5f914b7` | Cerebras gpt-oss-120b | 0 | repeat index 0-4 | 30 tool calls (60 turns) | yes, Phase 8 | from logs (Favorita named + obscured) |
| 5 | Retrain-vs-ignore agent (Phase 9) | `src/decision/agent.py` | `8b46ce6` | Cerebras gpt-oss-120b | 0 | 0 | 1 forced tool call | yes, Phase 9 | rebuilt exactly from logged payload + committed prompt (M5) |
| 6 | Fault-diagnosis agent (Phase 10) | `src/faults/agent.py` | `8fccba9` | Groq openai/gpt-oss-120b (amendment; Cerebras in the 3 unscored smoke trials) | 0 | 0 | 12 tool calls (30 turns) | yes, Phase 10 | from logs (M5) |

Full commit history of every file is listed in its section. Rendered examples for M5 use seed 42 unless stated; Favorita examples are given only where the prompt was used on Favorita (3b, 4).

## Shared request settings (entries 3b, 4, 5, 6)

Entries 3b, 4, 5 and 6 send requests through the same `ChatClient` in `src/agents/llm_investigator_agent.py` (provider table and request payload, verbatim):

````python
PROVIDERS = {
    # Cerebras free trial for gpt-oss-120b, as reported by the API's own
    # x-ratelimit-* headers (2026-10-04): 5 requests/min, 150/hour, 2400/day;
    # 30K tokens/min, 1M/hour, 1M/day. The daily token counter drops by
    # uncached prompt + completion tokens (cached prompt tokens are free).
    # 150 requests/hour is the binding limit: one request per 24.5s = 147/h.
    # A request is rejected up front if prompt + max_completion_tokens
    # exceeds the remaining bucket, so max_completion_tokens is kept moderate.
    "cerebras": dict(url="https://api.cerebras.ai/v1/chat/completions", key_env="CEREBRAS_API_KEY",
                     model="gpt-oss-120b", min_interval_s=24.5,
                     extra={"reasoning_format": "parsed"}),
    "groq": dict(url="https://api.groq.com/openai/v1/chat/completions", key_env="GROQ_API_KEY",
                 model="openai/gpt-oss-120b", min_interval_s=0.0, extra={}),
}
````

````python
        payload = {"model": self.model, "messages": messages, "tools": tools, "tool_choice": tool_choice,
                   "temperature": 0, "seed": self.seed, "max_completion_tokens": self.max_completion_tokens,
                   "reasoning_effort": self.reasoning_effort, **self.extra}
````

`max_completion_tokens` defaults to 4096. The frozen M5 run and the Favorita run used provider `cerebras`; Phase 10 used `groq` (`extra={}`, so no `reasoning_format`).

## 1. Synthesizer LLM backends (Claude, Groq, xAI)

| field | value |
|---|---|
| file | `src/agents/synthesizer.py` |
| commits | `0885eb5 2026-10-05 first commit` |
| models | Claude `claude-sonnet-4-6` (Anthropic Messages API); Groq `llama-3.3-70b-versatile`; xAI `grok-2-latest` |
| temperature / seed | not set (provider defaults) |
| max tokens | 500 |
| max steps | 1 call, no tools |
| used | Never in a scored run. Every pipeline run in this project used the default rule-based Synthesizer; the LLM path was only checked to fall back to rule-based when no key is set (README). |

### System prompt (identical for all three backends; class attribute `_LLMSynthesizerBase.system_prompt`)

````
You are the Synthesizer agent in a model-drift monitoring system. You are given a statistical drift alert and the top candidate slices that a root-cause agent identified as explaining it. Write a concise, operationally useful explanation and a short list of recommended next actions for an ML engineer. Respond with STRICT JSON ONLY, no markdown fences, no preamble, in exactly this shape: {"narrative": "...", "recommended_actions": ["...", "..."]}
````

### User prompt template (source of `_build_user_message`; the message is this JSON object)

````python
    def _build_user_message(self, alert, candidates, calendar_context) -> str:
        payload = {
            "feature": alert.feature,
            "js_divergence": alert.js_divergence,
            "l_inf_distance": alert.l_inf_distance,
            "top_candidates": [
                {"slice_col": c.slice_col, "slice_val": str(c.slice_val), "score": c.score, "coverage_frac": c.coverage_frac}
                for c in candidates[:3]
            ],
            "calendar_context": calendar_context or {},
        }
        return json.dumps(payload)

````

### Tools
None.

### Output schema
Stated in the system prompt: `{"narrative": "...", "recommended_actions": ["...", "..."]}`.

### Request construction (verbatim source, per backend)

````python
    def _call_llm(self, alert, candidates, calendar_context):
        import requests
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        user_msg = self._build_user_message(alert, candidates, calendar_context)
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 500,
                "system": self.system_prompt,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        parsed = self._parse_json_response(text)
        return parsed["narrative"], parsed["recommended_actions"]

````

````python
    def _call_llm(self, alert, candidates, calendar_context):
        import requests
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY not set")
        user_msg = self._build_user_message(alert, candidates, calendar_context)
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 500,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        parsed = self._parse_json_response(text)
        return parsed["narrative"], parsed["recommended_actions"]

````

````python
    def _call_llm(self, alert, candidates, calendar_context):
        import requests
        if not self.api_key:
            raise RuntimeError("XAI_API_KEY not set")
        user_msg = self._build_user_message(alert, candidates, calendar_context)
        resp = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 500,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        parsed = self._parse_json_response(text)
        return parsed["narrative"], parsed["recommended_actions"]

````

## 2a. Groq re-ranker v1

| field | value |
|---|---|
| file | `src/agents/groq_reranker.py` |
| commits | `0885eb5 2026-10-05 first commit` |
| model / provider | `openai/gpt-oss-20b` on Groq (`https://api.groq.com/openai/v1/chat/completions`) |
| temperature / seed | 0 / not set |
| other | `reasoning_effort="low"`, `max_tokens=500`, single user message, no system prompt |
| max steps | 1 call |
| used | Task 5 (2026-09-29): 240 calls, seeds 42/123, 2 repeats |

### Prompt (single user message; verbatim builder source)

````python
def _build_prompt(alert_context: dict, candidates: list) -> str:
    lines = [
        "A drift-detection system flagged a possible distribution shift in an "
        "e-commerce demand dataset (M5: item/store/dept/category/state hierarchy).",
        f"Monitored feature: {alert_context.get('feature')}",
        f"Drift-type hint from the detector run (NOT ground truth, just context): "
        f"{alert_context.get('drift_type', 'unknown')}",
        f"Whole-window detector scores: JS divergence={alert_context.get('js_divergence'):.6f}, "
        f"L-infinity distance={alert_context.get('l_inf_distance'):.6f}",
        "",
        "A deterministic statistical scorer (within-slice Jensen-Shannon "
        "divergence: each candidate slice's OWN reference-window distribution "
        "vs. its OWN current-window distribution) already ranked the top 5 "
        "candidate root-cause slices below. `score` is that divergence (higher "
        "= bigger within-slice distributional shift); `coverage` is the "
        "fraction of the current window's rows that slice covers.",
        "",
    ]
    for i, c in enumerate(candidates, start=1):
        lines.append(
            f"{i}. slice_col={c.slice_col}, slice_val={c.slice_val}, "
            f"score={c.score:.6f}, coverage={c.coverage_frac:.4f}"
        )
    lines += [
        "",
        "Re-rank these 5 candidates by how likely each is to be the TRUE "
        "root-cause slice of the drift. You may reorder them, but you must "
        "choose ONLY from the 5 listed above -- do not invent a new slice.",
        'Respond with STRICT JSON ONLY, no other text, exactly in this form: '
        '{"ranking": [a, b, c]} where a, b, c are the 1-based indices (1-5) '
        "of your top 3 picks from the list above, most-likely first.",
    ]
    return "\n".join(lines)

````

**Note:** `alert_context['drift_type']` was the injector's TRUE drift type (`src/evaluation/evaluate_groq_rerank.py`), passed as a "hint"; this leak is documented in CLAUDE_CODE_HANDOFF.md (Task 5 follow-up) and was removed in v2.

### Request payload (verbatim)

````python
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": _build_prompt(alert_context, candidates)}],
        "temperature": 0,
        "max_tokens": 500,
        "reasoning_effort": "low",
    }
````

### Output schema
Strict JSON `{"ranking": [a, b, c]}` (1-based indices into the 5 listed candidates), validated by `_parse_ranking`.

## 2b. Groq re-ranker v2

| field | value |
|---|---|
| file | `src/agents/groq_reranker_v2.py` |
| commits | `0885eb5 2026-10-05 first commit` |
| model / provider | Groq; final validated run `openai/gpt-oss-120b` (`src/evaluation/evaluate_groq_rerank_v2.py`); code default `openai/gpt-oss-20b` |
| temperature / seed | 0 / not set |
| other | `reasoning_effort="low"`, `max_tokens=900`, pool_mode `hybrid`, single user message |
| max steps | 1 call |
| used | Task 5 follow-up (2026-10-03), seeds 42/123, 2 repeats |

### Constant `DOMAIN_CONTEXT`

````
Methodological context from prior calibration on this dataset (true in general, not specific to this one alert -- treat as background knowledge, the same way a human analyst would be briefed before judging):
- `coarse_score`: within-slice Jensen-Shannon divergence at the department/store/state/category level (few, large-sample candidates). Typical range ~0-0.2. The most stable signal.
- `item_divergence_score`: the SAME divergence statistic, but computed per individual item. Typical range similar to coarse, but individual items carry much more natural, non-drift demand volatility (products get delisted, ramp up, etc.) -- a high item_divergence_score ALONE is weak evidence, because ordinary item-level noise routinely produces scores this large even with no real anomaly.
- `burst_score`: looks specifically for an unusually large SHORT (1-2 day) spike in an item's daily total, relative to its own history. Typical range can be tens to low hundreds; NOT comparable in magnitude to the other two scores. A high burst_score is more specific evidence of a short, deliberate shock than a high item_divergence_score, because it requires a concentrated spike shape rather than general noisiness.
- A candidate may have one, two, or all three scores computed; a missing score is reported as null, not zero.

````

### Constant `DECISION_RULE_MIXED`

````

DECISION RULE (apply this; item-level scores are seductive but usually wrong): start from the single best coarse_score candidate as your default #1 pick. Only replace it with an item-level candidate (by item_divergence_score or burst_score) if that item's evidence is NOT just the single biggest number in the list, but is dramatically isolated from every other item-level candidate too (e.g. burst_score > 60 and clearly larger than the next item's burst_score, or item_divergence_score more than ~3x the next-highest item's score) -- a merely-highest-of-many noisy item scores is NOT enough, because ordinary non-drift item volatility routinely produces the single largest number in any list of many items by chance alone.

````

### Constant `DECISION_RULE_ITEM_ONLY`

````

DECISION RULE: every candidate below is an individual item. These are shown INSTEAD OF coarse candidates because the best coarse (dept/store/state/category) score for this specific alert was too weak to trust (below this pipeline's own significance bar) -- NOT because the drift type is known in advance to be item-level; it may simply be a case with weak signal everywhere. Prefer burst_score over item_divergence_score when both are present (a short, concentrated spike is more specific evidence than general distributional noise). Pick the candidate that is a clear OUTLIER relative to the REST OF THIS LIST (e.g. its burst_score is much larger than the second-highest burst_score here), not merely whichever happens to be numerically largest by a small margin --  ordinary items routinely produce the single largest score in a list purely by chance, so a narrow win is weak evidence.

````

### Prompt builder (verbatim source; uses the constants above)

````python
def _build_prompt(alert_context: dict, pool: list[ExtCandidate]) -> str:
    lines = [
        "A drift-detection system flagged a possible distribution shift in an "
        "e-commerce demand dataset (M5: item/store/dept/category/state hierarchy).",
        f"Monitored feature: {alert_context.get('feature')}",
        # NOTE: no drift-type label is given here on purpose. An earlier
        # version of this prompt (and v1's `groq_reranker.py`) passed the
        # ground-truth injector type through as a "hint" -- that's real
        # information leakage: the deterministic baseline this is compared
        # against never gets to see it, and no real deployment would know
        # it in advance either. Only data-derived signal belongs here.
        f"Whole-window detector scores: JS divergence={alert_context.get('js_divergence'):.6f}, "
        f"L-infinity distance={alert_context.get('l_inf_distance'):.6f}",
        "",
        DOMAIN_CONTEXT,
        DECISION_RULE_MIXED if any(c.coarse_score is not None for c in pool) else DECISION_RULE_ITEM_ONLY,
        "Candidate root-cause slices (merged from multiple scoring methods -- "
        "see context above):",
        "",
    ]
    for i, c in enumerate(pool, start=1):
        lines.append(
            f"{i}. slice_col={c.slice_col}, slice_val={c.slice_val}, coverage={c.coverage_frac:.4f}, "
            f"coarse_score={'null' if c.coarse_score is None else f'{c.coarse_score:.6f}'}, "
            f"item_divergence_score={'null' if c.item_divergence_score is None else f'{c.item_divergence_score:.6f}'}, "
            f"burst_score={'null' if c.burst_score is None else f'{c.burst_score:.2f}'}"
        )
    lines += [
        "",
        f"Rank your top 3 picks for the TRUE root-cause slice from the {len(pool)} "
        "candidates above, most-likely first. Choose ONLY from the list -- do not "
        "invent a new slice.",
        'Respond with STRICT JSON ONLY, no other text: {"ranking": [a, b, c]} where '
        f"a, b, c are 1-based indices (1-{len(pool)}) of your top 3 picks.",
    ]
    return "\n".join(lines)

````

### Request payload (verbatim)

````python
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _build_prompt(alert_context, pool)}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "reasoning_effort": reasoning_effort,
    }
````

### Output schema
Strict JSON `{"ranking": [a, b, c]}` (1-based indices into the pool), validated by `_parse_ranking`.

### Revisions
Per CLAUDE_CODE_HANDOFF.md (Task 5 follow-up), an earlier v2 prompt passed the true `drift_type` as a hint and the pool was routed on it; both were removed before the final validated run. That earlier text predates the git repository and was not preserved, so it cannot be reproduced verbatim here.

## 3a. AgenticInvestigator LLM controller

| field | value |
|---|---|
| file | `src/agents/agentic_investigator.py` |
| commits | `0885eb5 2026-10-05 first commit` |
| models | Anthropic `claude-sonnet-4-6` (default backend); Groq `llama-3.3-70b-versatile`; xAI `grok-2-latest` |
| temperature / seed | not set |
| max tokens | 600 (Anthropic); not set (Groq/xAI) |
| max steps | `max_steps=6` tool calls |
| used | Never run in this project (no recorded call); the deterministic controller was used for the intersection benchmark. |

### System prompt (verbatim source of `_investigate_llm`; the f-string is the template)

````python
    def _investigate_llm(self, df: pd.DataFrame, alert):
        if not self.api_key:
            raise RuntimeError(f"{self.backend} API key not set")

        ref = df[(df["date"] >= alert.reference_window[0]) & (df["date"] <= alert.reference_window[1])]
        cur = df[(df["date"] >= alert.current_window[0]) & (df["date"] <= alert.current_window[1])]
        feature = alert.feature
        is_cat = feature in ("cat_id", "state_id", "store_id", "dept_id", "item_id")
        scorer = _FixedBinScorer(ref[feature], cur[feature], is_cat)

        def score_tool(col_a, val_a, col_b=None, val_b=None):
            mask = (cur[col_a] == val_a).to_numpy()
            if col_b and val_b:
                mask &= (cur[col_b] == val_b).to_numpy()
            score, coverage = scorer.score_mask(cur[feature], mask)
            return {"score": score, "coverage_frac": coverage, "below_min_coverage": coverage < self.min_coverage}

        system_prompt = (
            "You are the root-cause investigator for a model-drift alert. You may call "
            "score_combination repeatedly to test (column, value) or (column1,value1) AND "
            "(column2,value2) hypotheses for what slice of the data explains the drift in "
            f"feature '{feature}'. A good answer has a high score AND is not trivially tiny "
            f"(candidates covering under {self.min_coverage:.0%} of the window are unreliable). "
            "When confident, call submit_answer with your best (column, value) or combined "
            f"answer. You have at most {self.max_steps} tool calls total. "
            f"Available slice columns: {[c for c in SLICE_COLUMNS if c != feature]}."
        )

        if self.backend == "anthropic":
            return self._loop_anthropic(system_prompt, score_tool)
        else:
            return self._loop_openai_compat(system_prompt, score_tool)

````

### User prompt
The first user message is the literal `"Begin your investigation."` (see `_loop_anthropic` / `_loop_openai_compat`).

### Tool definitions (Anthropic format, `TOOL_SCHEMA_ANTHROPIC`)

````json
[
  {
    "name": "score_combination",
    "description": "Score how well the intersection of up to two (column, value) conditions explains the drift, as a divergence-removal score and the fraction of the current window it covers.",
    "input_schema": {
      "type": "object",
      "properties": {
        "col_a": {
          "type": "string"
        },
        "val_a": {
          "type": "string"
        },
        "col_b": {
          "type": "string",
          "description": "optional second column"
        },
        "val_b": {
          "type": "string",
          "description": "optional second value"
        }
      },
      "required": [
        "col_a",
        "val_a"
      ]
    }
  },
  {
    "name": "submit_answer",
    "description": "Submit your final answer for the root cause of the drift.",
    "input_schema": {
      "type": "object",
      "properties": {
        "col": {
          "type": "string"
        },
        "val": {
          "type": "string"
        },
        "col2": {
          "type": "string"
        },
        "val2": {
          "type": "string"
        },
        "reasoning": {
          "type": "string"
        }
      },
      "required": [
        "col",
        "val",
        "reasoning"
      ]
    }
  }
]
````

### Tool definitions (OpenAI-compatible format, `TOOL_SCHEMA_OPENAI_COMPAT`, for Groq/xAI)

````json
[
  {
    "type": "function",
    "function": {
      "name": "score_combination",
      "description": "Score how well the intersection of up to two (column, value) conditions explains the drift, as a divergence-removal score and the fraction of the current window it covers.",
      "parameters": {
        "type": "object",
        "properties": {
          "col_a": {
            "type": "string"
          },
          "val_a": {
            "type": "string"
          },
          "col_b": {
            "type": "string",
            "description": "optional second column"
          },
          "val_b": {
            "type": "string",
            "description": "optional second value"
          }
        },
        "required": [
          "col_a",
          "val_a"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "submit_answer",
      "description": "Submit your final answer for the root cause of the drift.",
      "parameters": {
        "type": "object",
        "properties": {
          "col": {
            "type": "string"
          },
          "val": {
            "type": "string"
          },
          "col2": {
            "type": "string"
          },
          "val2": {
            "type": "string"
          },
          "reasoning": {
            "type": "string"
          }
        },
        "required": [
          "col",
          "val",
          "reasoning"
        ]
      }
    }
  }
]
````

### Output schema
The `submit_answer` tool: `col`, `val`, optional `col2`, `val2`, `reasoning`.

## 3b. Tool-using investigator (M5 and Favorita validation runs)

| field | value |
|---|---|
| file | `src/agents/llm_investigator_agent.py` |
| commits | `ce1c9bb 2026-10-10 Favorita cross-dataset transfer (Phase 7)`; `0885eb5 2026-10-05 first commit` |
| version used for the M5 validation | commit `0885eb5` (tag `m5-frozen` on `98a9635`; SHA-256 in `outputs/llm_agent/FROZEN_val_v2.sha256`) |
| version used for the Favorita validation | commit `ce1c9bb` (dataset-specific prompt fields; fingerprint `outputs/llm_agent/FROZEN_fav_val.sha256`) |
| model / provider | `gpt-oss-120b` on Cerebras |
| temperature / seed | 0 / 0 |
| other | `reasoning_effort="medium"`, `reasoning_format="parsed"`, `max_completion_tokens=4096` |
| max steps | `MAX_TOOL_CALLS = 10` investigation calls (submit not counted), `max_turns=24`; submit forced once the budget is used |
| used | Task 6 (M5, 120 trials) and Phase 7 (Favorita, 120 trials) |

### System prompt template (current; `SYSTEM_PROMPT_TEMPLATE`)

````
You are the root-cause investigator for a retail demand-forecasting monitor.

DATA: daily unit sales for {n_items} products in {n_stores} stores ({dataset_name}). One row = one item in one store on one day.
Hierarchy: item_id -> dept_id -> cat_id, and store_id -> state_id.
- cat_id: {cat_vals}
- dept_id: {dept_vals}
- state_id: {state_vals}
- store_id: {store_vals}
- item_id: {n_items} items {item_id_format}. Screen item_id (omit val) to find specific item ids.

SITUATION: the monitor compared the current window ({cur_start} to {cur_end}) with the reference window ({ref_start} to {ref_end}, the days immediately before).
Whole-panel sales shift: JS divergence = {js:.6f}, L-infinity distance = {linf:.6f}; alert threshold exceeded: {is_drift}.

TASK: find the single slice (one column = one value) that most plausibly contains an anomalous change in sales in the current window, and submit a ranked top 3.
- The anomaly can sit at any level of the hierarchy, from a whole category or state down to one item. Do not assume a level; check several.
- Real slices also change organically (seasonality, launches, delistings, trends). Judge a change against the slice's own history, not just its raw size.
- The anomaly may be a lasting level shift, a ramp, or a few short bursts; the tools measure these differently.
- Rank first the level where the change actually lives, and verify it with get_slice_history's where_the_change_lives section rather than assuming it:
  - If most sub-groups across the other hierarchy move together (high n_groups_moving_with_slice), the change is not specific to one store or one department on that side.
  - If one child carries most of a parent's change while its siblings stay flat, the child is the better answer than the parent.
  - A child with a large % change but a small share_of_parent_change is not what drives its parent; large % swings in low-volume items are common and usually organic.

BUDGET: at most {max_calls} investigation tool calls; each result reports how many remain. Then call submit_answer with exactly 3 distinct slices (most likely first) and your reasoning. Only submit slices that exist in the data.
````

### Dataset descriptor fields (`DATASET_PROMPT_FIELDS`; template and filled-in text)

````json
{
  "m5": {
    "dataset_name": "Walmart M5 sample",
    "item_id_format": "named <dept_id>_<3-digit number> (e.g. FOODS_3_NNN)"
  },
  "favorita": {
    "dataset_name": "Corporacion Favorita sample, Ecuador",
    "item_id_format": "identified by integer item numbers (e.g. NNNNNN)"
  }
}
````

**m5** — the two template lines as rendered:

````
DATA: daily unit sales for {n_items} products in {n_stores} stores (Walmart M5 sample). One row = one item in one store on one day.
- item_id: {n_items} items named <dept_id>_<3-digit number> (e.g. FOODS_3_NNN). Screen item_id (omit val) to find specific item ids.
````

**favorita** — the two template lines as rendered:

````
DATA: daily unit sales for {n_items} products in {n_stores} stores (Corporacion Favorita sample, Ecuador). One row = one item in one store on one day.
- item_id: {n_items} items identified by integer item numbers (e.g. NNNNNN). Screen item_id (omit val) to find specific item ids.
````

### System prompt template as committed for the M5 validation (`0885eb5`, before the dataset fields)

````
You are the root-cause investigator for a retail demand-forecasting monitor.

DATA: daily unit sales for {n_items} products in {n_stores} stores (Walmart M5 sample). One row = one item in one store on one day.
Hierarchy: item_id -> dept_id -> cat_id, and store_id -> state_id.
- cat_id: {cat_vals}
- dept_id: {dept_vals}
- state_id: {state_vals}
- store_id: {store_vals}
- item_id: {n_items} items named <dept_id>_<3-digit number> (e.g. FOODS_3_NNN). Screen item_id (omit val) to find specific item ids.

SITUATION: the monitor compared the current window ({cur_start} to {cur_end}) with the reference window ({ref_start} to {ref_end}, the days immediately before).
Whole-panel sales shift: JS divergence = {js:.6f}, L-infinity distance = {linf:.6f}; alert threshold exceeded: {is_drift}.

TASK: find the single slice (one column = one value) that most plausibly contains an anomalous change in sales in the current window, and submit a ranked top 3.
- The anomaly can sit at any level of the hierarchy, from a whole category or state down to one item. Do not assume a level; check several.
- Real slices also change organically (seasonality, launches, delistings, trends). Judge a change against the slice's own history, not just its raw size.
- The anomaly may be a lasting level shift, a ramp, or a few short bursts; the tools measure these differently.
- Rank first the level where the change actually lives, and verify it with get_slice_history's where_the_change_lives section rather than assuming it:
  - If most sub-groups across the other hierarchy move together (high n_groups_moving_with_slice), the change is not specific to one store or one department on that side.
  - If one child carries most of a parent's change while its siblings stay flat, the child is the better answer than the parent.
  - A child with a large % change but a small share_of_parent_change is not what drives its parent; large % swings in low-volume items are common and usually organic.

BUDGET: at most {max_calls} investigation tool calls; each result reports how many remain. Then call submit_answer with exactly 3 distinct slices (most likely first) and your reasoning. Only submit slices that exist in the data.
````

Diff of the whole file between the M5 and Favorita versions (`git diff 0885eb5 ce1c9bb`), verbatim:

````diff
diff --git a/src/agents/llm_investigator_agent.py b/src/agents/llm_investigator_agent.py
index 9059390..0f7a95b 100644
--- a/src/agents/llm_investigator_agent.py
+++ b/src/agents/llm_investigator_agent.py
@@ -71,6 +71,7 @@ class SliceTools:
         hist_start = self.cur_start - pd.Timedelta(days=HISTORY_DAYS + 90)
         self.df = df[(df["date"] >= hist_start) & (df["date"] <= self.cur_end)]
         self.alert = DriftAlert("sales", float("nan"), float("nan"), True, reference_window, current_window)
+        self.dataset = df.attrs.get("dataset", "m5")  # selects DATASET_PROMPT_FIELDS only
         self.values = {c: sorted(self.df[c].dropna().astype(str).unique()) for c in SLICE_COLUMNS}
         self._daily_mean: dict[str, pd.DataFrame] = {}
         self._daily_sum: dict[str, pd.DataFrame] = {}
@@ -410,13 +411,13 @@ TOOL_DEFS = [
 SYSTEM_PROMPT_TEMPLATE = """\
 You are the root-cause investigator for a retail demand-forecasting monitor.
 
-DATA: daily unit sales for {n_items} products in {n_stores} stores (Walmart M5 sample). One row = one item in one store on one day.
+DATA: daily unit sales for {n_items} products in {n_stores} stores ({dataset_name}). One row = one item in one store on one day.
 Hierarchy: item_id -> dept_id -> cat_id, and store_id -> state_id.
 - cat_id: {cat_vals}
 - dept_id: {dept_vals}
 - state_id: {state_vals}
 - store_id: {store_vals}
-- item_id: {n_items} items named <dept_id>_<3-digit number> (e.g. FOODS_3_NNN). Screen item_id (omit val) to find specific item ids.
+- item_id: {n_items} items {item_id_format}. Screen item_id (omit val) to find specific item ids.
 
 SITUATION: the monitor compared the current window ({cur_start} to {cur_end}) with the reference window ({ref_start} to {ref_end}, the days immediately before).
 Whole-panel sales shift: JS divergence = {js:.6f}, L-infinity distance = {linf:.6f}; alert threshold exceeded: {is_drift}.
@@ -433,9 +434,20 @@ TASK: find the single slice (one column = one value) that most plausibly contain
 BUDGET: at most {max_calls} investigation tool calls; each result reports how many remain. Then call submit_answer with exactly 3 distinct slices (most likely first) and your reasoning. Only submit slices that exist in the data."""
 
 
+# Dataset-specific wording (prompt-audit hits 1 and 2, 2026-10-07). The M5
+# entry is the exact text of the frozen M5 prompt, so M5 renders unchanged.
+DATASET_PROMPT_FIELDS = {
+    "m5": dict(dataset_name="Walmart M5 sample",
+               item_id_format="named <dept_id>_<3-digit number> (e.g. FOODS_3_NNN)"),
+    "favorita": dict(dataset_name="Corporacion Favorita sample, Ecuador",
+                     item_id_format="identified by integer item numbers (e.g. NNNNNN)"),
+}
+
+
 def build_system_prompt(tools: SliceTools, js: float, linf: float, is_drift: bool, max_calls: int) -> str:
     v = tools.values
     return SYSTEM_PROMPT_TEMPLATE.format(
+        **DATASET_PROMPT_FIELDS[tools.dataset],
         n_items=len(v["item_id"]), n_stores=len(v["store_id"]),
         cat_vals=", ".join(v["cat_id"]), dept_vals=", ".join(v["dept_id"]),
         state_vals=", ".join(v["state_id"]), store_vals=", ".join(v["store_id"]),
@@ -777,7 +789,9 @@ def dump_prompt_and_tools(path: str):
                 "Generated from `src/agents/llm_investigator_agent.py`. `{...}` fields are filled per trial "
                 "from the panel's own value lists, the window dates, and the Sentinel's whole-panel "
                 "JS / L-inf scores; nothing else is substituted.\n\n## System prompt template\n\n```\n"
-                + SYSTEM_PROMPT_TEMPLATE + "\n```\n\n## Other messages\n\n"
+                + SYSTEM_PROMPT_TEMPLATE + "\n```\n\n## Dataset-specific fields\n\n"
+                "`{dataset_name}` and `{item_id_format}` are filled from the panel's dataset tag:\n\n```json\n"
+                + json.dumps(DATASET_PROMPT_FIELDS, indent=2) + "\n```\n\n## Other messages\n\n"
                 f"- first user message: `{USER_KICKOFF}`\n- if the model replies without a tool call: `{NUDGE}`\n"
                 f"- when the tool budget is used up (only submit_answer is then offered, and forced): `{BUDGET_DONE}`\n\n"
                 f"Request settings: provider {DEFAULT_PROVIDER} ({PROVIDERS[DEFAULT_PROVIDER]['model']}), "
````

### Other messages (current constants)

- `USER_KICKOFF`: `Begin the investigation.`
- `NUDGE`: `Continue: call an investigation tool, or call submit_answer.`
- `BUDGET_DONE`: `Tool budget exhausted. Call submit_answer now with your best ranked top 3.`

### Tool definitions (`TOOL_DEFS`, sent with every request; unchanged between the M5 and Favorita versions)

````json
[
  {
    "type": "function",
    "function": {
      "name": "get_slice_history",
      "description": "Long-run daily sales summary for a slice: twelve 30-day means before the current window, reference vs current mean, the current window split into six ~5-day blocks (a step shows as a jump between blocks, a ramp as a steady climb, short bursts as one or two high blocks), how unusual the current change is relative to this slice's own past 30-day changes over the last year, and where the change lives: the slice's share of its parent's change, and how its change splits across its children (cat->dept, dept->item, state->store) and across the other hierarchy (product slices by store, location slices by dept). Omit `val` to screen a column, ranked by change relative to own history.",
      "parameters": {
        "type": "object",
        "properties": {
          "col": {
            "type": "string",
            "enum": [
              "cat_id",
              "dept_id",
              "state_id",
              "store_id",
              "item_id"
            ],
            "description": "Hierarchy column: cat_id, dept_id, state_id, store_id or item_id."
          },
          "val": {
            "type": "string",
            "description": "One value of `col`. Omit to screen every value of the column and get the top ones."
          }
        },
        "required": [
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "compare_windows",
      "description": "Reference vs current distribution shift for a slice: within-slice Jensen-Shannon divergence of daily item-store sales (and its rank among the column's values), mean change, share of zero-sales rows, and the slice's share of the current window. Omit `val` to screen a column, ranked by JS divergence.",
      "parameters": {
        "type": "object",
        "properties": {
          "col": {
            "type": "string",
            "enum": [
              "cat_id",
              "dept_id",
              "state_id",
              "store_id",
              "item_id"
            ],
            "description": "Hierarchy column: cat_id, dept_id, state_id, store_id or item_id."
          },
          "val": {
            "type": "string",
            "description": "One value of `col`. Omit to screen every value of the column and get the top ones."
          }
        },
        "required": [
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "check_spikes",
      "description": "Short-window burst statistic for a slice: the largest 2-day surge in the current window vs the largest in the reference window, scaled by the reference daily level (and its rank in the column), the top three surge dates, and how many current days exceed the reference window's maximum day. Omit `val` to screen a column, ranked by burst score.",
      "parameters": {
        "type": "object",
        "properties": {
          "col": {
            "type": "string",
            "enum": [
              "cat_id",
              "dept_id",
              "state_id",
              "store_id",
              "item_id"
            ],
            "description": "Hierarchy column: cat_id, dept_id, state_id, store_id or item_id."
          },
          "val": {
            "type": "string",
            "description": "One value of `col`. Omit to screen every value of the column and get the top ones."
          }
        },
        "required": [
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "submit_answer",
      "description": "Submit the final answer: exactly 3 distinct slices, most likely root cause first.",
      "parameters": {
        "type": "object",
        "properties": {
          "ranking": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
              "type": "object",
              "properties": {
                "col": {
                  "type": "string",
                  "enum": [
                    "cat_id",
                    "dept_id",
                    "state_id",
                    "store_id",
                    "item_id"
                  ],
                  "description": "Hierarchy column: cat_id, dept_id, state_id, store_id or item_id."
                },
                "val": {
                  "type": "string"
                }
              },
              "required": [
                "col",
                "val"
              ]
            }
          },
          "reasoning": {
            "type": "string",
            "description": "Why this ranking, citing the tool evidence."
          }
        },
        "required": [
          "ranking",
          "reasoning"
        ]
      }
    }
  }
]
````

### Output schema
The `submit_answer` tool above: `ranking` (exactly 3 `{col, val}` objects) and `reasoning`.

### Rendered example from the raw log: M5 (validation seed 42, first trial)
Source `outputs/llm_agent/transcripts_val_v2.jsonl`, seed=42, sudden, trial=0. System and user messages exactly as sent:

**system:**

````
You are the root-cause investigator for a retail demand-forecasting monitor.

DATA: daily unit sales for 198 products in 10 stores (Walmart M5 sample). One row = one item in one store on one day.
Hierarchy: item_id -> dept_id -> cat_id, and store_id -> state_id.
- cat_id: FOODS, HOBBIES, HOUSEHOLD
- dept_id: FOODS_1, FOODS_2, FOODS_3, HOBBIES_1, HOBBIES_2, HOUSEHOLD_1, HOUSEHOLD_2
- state_id: CA, TX, WI
- store_id: CA_1, CA_2, CA_3, CA_4, TX_1, TX_2, TX_3, WI_1, WI_2, WI_3
- item_id: 198 items named <dept_id>_<3-digit number> (e.g. FOODS_3_NNN). Screen item_id (omit val) to find specific item ids.

SITUATION: the monitor compared the current window (2015-12-26 to 2016-01-25) with the reference window (2015-10-27 to 2015-12-25, the days immediately before).
Whole-panel sales shift: JS divergence = 0.000549, L-infinity distance = 0.010275; alert threshold exceeded: no.

TASK: find the single slice (one column = one value) that most plausibly contains an anomalous change in sales in the current window, and submit a ranked top 3.
- The anomaly can sit at any level of the hierarchy, from a whole category or state down to one item. Do not assume a level; check several.
- Real slices also change organically (seasonality, launches, delistings, trends). Judge a change against the slice's own history, not just its raw size.
- The anomaly may be a lasting level shift, a ramp, or a few short bursts; the tools measure these differently.
- Rank first the level where the change actually lives, and verify it with get_slice_history's where_the_change_lives section rather than assuming it:
  - If most sub-groups across the other hierarchy move together (high n_groups_moving_with_slice), the change is not specific to one store or one department on that side.
  - If one child carries most of a parent's change while its siblings stay flat, the child is the better answer than the parent.
  - A child with a large % change but a small share_of_parent_change is not what drives its parent; large % swings in low-volume items are common and usually organic.

BUDGET: at most 10 investigation tool calls; each result reports how many remain. Then call submit_answer with exactly 3 distinct slices (most likely first) and your reasoning. Only submit slices that exist in the data.
````

**user:**

````
Begin the investigation.
````

### Rendered example from the raw log: Favorita (validation seed 42, first trial)
Source `outputs/llm_agent/transcripts_fav_val.jsonl`, seed=42, sudden, trial=0. System and user messages exactly as sent:

**system:**

````
You are the root-cause investigator for a retail demand-forecasting monitor.

DATA: daily unit sales for 181 products in 54 stores (Corporacion Favorita sample, Ecuador). One row = one item in one store on one day.
Hierarchy: item_id -> dept_id -> cat_id, and store_id -> state_id.
- cat_id: NON_PERISHABLE, PERISHABLE
- dept_id: AUTOMOTIVE, BABY CARE, BEAUTY, BEVERAGES, BOOKS, BREAD/BAKERY, CELEBRATION, CLEANING, DAIRY, DELI, EGGS, FROZEN FOODS, GROCERY I, GROCERY II, HARDWARE, HOME AND KITCHEN I, HOME AND KITCHEN II, HOME APPLIANCES, HOME CARE, LADIESWEAR, LAWN AND GARDEN, LINGERIE, LIQUOR,WINE,BEER, MAGAZINES, MEATS, PERSONAL CARE, PET SUPPLIES, PLAYERS AND ELECTRONICS, POULTRY, PREPARED FOODS, PRODUCE, SCHOOL AND OFFICE SUPPLIES, SEAFOOD
- state_id: Azuay, Bolivar, Chimborazo, Cotopaxi, El Oro, Esmeraldas, Guayas, Imbabura, Loja, Los Rios, Manabi, Pastaza, Pichincha, Santa Elena, Santo Domingo de los Tsachilas, Tungurahua
- store_id: 1, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 2, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 3, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 4, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 5, 50, 51, 52, 53, 54, 6, 7, 8, 9
- item_id: 181 items identified by integer item numbers (e.g. NNNNNN). Screen item_id (omit val) to find specific item ids.

SITUATION: the monitor compared the current window (2017-04-17 to 2017-05-17) with the reference window (2017-02-16 to 2017-04-16, the days immediately before).
Whole-panel sales shift: JS divergence = 0.000070, L-infinity distance = 0.006080; alert threshold exceeded: no.

TASK: find the single slice (one column = one value) that most plausibly contains an anomalous change in sales in the current window, and submit a ranked top 3.
- The anomaly can sit at any level of the hierarchy, from a whole category or state down to one item. Do not assume a level; check several.
- Real slices also change organically (seasonality, launches, delistings, trends). Judge a change against the slice's own history, not just its raw size.
- The anomaly may be a lasting level shift, a ramp, or a few short bursts; the tools measure these differently.
- Rank first the level where the change actually lives, and verify it with get_slice_history's where_the_change_lives section rather than assuming it:
  - If most sub-groups across the other hierarchy move together (high n_groups_moving_with_slice), the change is not specific to one store or one department on that side.
  - If one child carries most of a parent's change while its siblings stay flat, the child is the better answer than the parent.
  - A child with a large % change but a small share_of_parent_change is not what drives its parent; large % swings in low-volume items are common and usually organic.

BUDGET: at most 10 investigation tool calls; each result reports how many remain. Then call submit_answer with exactly 3 distinct slices (most likely first) and your reasoning. Only submit slices that exist in the data.
````

**user:**

````
Begin the investigation.
````

### Revisions during seed-7 tuning (Task 6)

The tuning rounds ran before the repository existed; none of the intermediate versions was committed, and the source snapshots kept during the session no longer exist. The **system prompt** of each round is preserved verbatim in the raw logs and is diffed below. The **tool-description** changes were never logged; they are reproduced from the exact edit strings applied during the session (not verifiable against any file). v2 is the version validated on M5 (`0885eb5`).

**System prompt, tune_v1 -> tune_v2** (rendered seed-7 sudden trial 0; only template lines differ):

````diff
--- tune_v1
+++ tune_v2
@@ -18 +18,4 @@
-- If a parent and one of its children both look shifted, check whether the change is spread across the parent or concentrated in the child, and rank the level where it actually lives first.
+- Rank first the level where the change actually lives, and verify it with get_slice_history's where_the_change_lives section rather than assuming it:
+  - If most sub-groups across the other hierarchy move together (high n_groups_moving_with_slice), the change is not specific to one store or one department on that side.
+  - If one child carries most of a parent's change while its siblings stay flat, the child is the better answer than the parent.
+  - A child with a large % change but a small share_of_parent_change is not what drives its parent; large % swings in low-volume items are common and usually organic.
````

**System prompt, tune_v2 -> tune_v3** (rendered seed-7 sudden trial 0; only template lines differ):

````diff
--- tune_v2
+++ tune_v3
@@ -16,2 +16,3 @@
-- Real slices also change organically (seasonality, launches, delistings, trends). Judge a change against the slice's own history, not just its raw size.
-- The anomaly may be a lasting level shift, a ramp, or a few short bursts; the tools measure these differently.
+- Real slices also change organically (seasonality, launches, delistings, trends). Judge a change against the slice's own history, not just its raw size. A slice whose monthly means were near zero, or already climbing or collapsing, before the reference window is showing a launch, relaunch or delisting, which is organic.
+- The anomaly may be a lasting level shift, a ramp, or a few short bursts; the tools measure these differently, so screen with more than one tool (including check_spikes) before concluding.
+- A column with many values always has some extreme-looking values by chance (e.g. among 198 items). Screens report how high the column's top score typically was in past windows; a top value that does not clearly exceed that typical top is ordinary for that column, however large it looks next to other columns.
@@ -21 +22 @@
-  - A child with a large % change but a small share_of_parent_change is not what drives its parent; large % swings in low-volume items are common and usually organic.
+  - Compare parent_pct_change with parent_pct_change_without_this_slice: if the parent still moves about as much without the slice, the change is spread across the parent and the slice is not its driver (a large child can carry a big share of the change just because it sells a lot). If the parent barely moved at all, the slice is not driving it either, whatever share_of_parent_change says.
````

tune_v3 was not better than tune_v2 on seed 7 and was reverted; the reverted code reproduced all 12 sampled tune_v2 prompts and all 106 tool outputs byte for byte.

**Tool-description changes (from the session's edit strings):**

- v1 -> v2, `get_slice_history` description. v1 ended:

````
... and how unusual the current change is relative to this slice's own past 30-day changes over the last year. Omit `val` to screen a column, ranked by change relative to own history.
````

  v2 (final) ends:

````
..., how unusual the current change is relative to this slice's own past 30-day changes over the last year, and where the change lives: the slice's share of its parent's change, and how its change splits across its children (cat->dept, dept->item, state->store) and across the other hierarchy (product slices by store, location slices by dept). Omit `val` to screen a column, ranked by change relative to own history.
````

- v2 -> v3 (reverted): `get_slice_history` gained `; the screen also reports how high the column's top score typically was in past windows.` after `ranked by change relative to own history`, and `check_spikes` gained `; the screen also reports how high the column's top burst score typically was in past windows.` after `ranked by burst score`.

## 4. Onboarding agent (Phase 8)

| field | value |
|---|---|
| file | `src/onboarding/agent.py` |
| commits | `5f914b7 2026-10-10 Phase 8: LLM adapter-config drafting on Favorita (named + obscured), non-LLM baseline, scorer, downstream test` |
| model / provider | `gpt-oss-120b` on Cerebras (`ChatClient` above) |
| temperature / seed | 0 / repeat index 0-4 |
| other | `reasoning_effort="medium"`, `reasoning_format="parsed"` |
| max steps | `MAX_TOOL_CALLS = 30` profiling calls, `max_turns=60`; submit forced once the budget is used |
| used | Phase 8: 5 repeats x {named, obscured} on Favorita |
| note | The file was first committed with the results (`5f914b7`); the prompt was not edited during or after the runs, and every raw transcript's system prompt equals the committed `SYSTEM_PROMPT` with the budget filled in (checked when generating this appendix). |

### System prompt template (`SYSTEM_PROMPT`)

````
You are onboarding a raw retail dataset into a drift-monitoring pipeline. You can only inspect the raw files through the tools; you cannot read anything else.

WHAT THE PIPELINE NEEDS (its input contract):
- A daily panel with one row per item x store x day: an item key, a store key, the date, and the sales quantity to monitor.
- A product hierarchy with two levels above item (item -> dept -> category) and a location hierarchy with one level above store (store -> region). Each child value must map to exactly one parent value.
- Numeric features are binned for divergence tests in one of two ways: the "integer" path (one bin per integer value; only valid if every value is an integer) or the "quantile" path (equal-frequency bins).

YOUR JOB: draft the adapter config by profiling the files, then call submit_config once. Base every claim on tool evidence. Report in data_quality_issues anything in the raw data that the panel builder must handle to produce a correct daily panel. Give a short rationale for every non-trivial choice.

BUDGET: at most {max_calls} profiling tool calls (submit_config not counted); each result reports how many remain.
````

### Other messages (verbatim from `run`)
- first user message: `Begin profiling the dataset.`
- nudge: `Continue: call a profiling tool or submit_config.`
- budget exhausted: `Tool budget exhausted. Call submit_config now.`

### Tool definitions (`TOOL_DEFS`)

````json
[
  {
    "type": "function",
    "function": {
      "name": "list_files",
      "description": "List the raw files with row and column counts.",
      "parameters": {
        "type": "object",
        "properties": {},
        "required": []
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "list_columns",
      "description": "Column names and dtypes of a file.",
      "parameters": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string",
            "description": "a file name from list_files"
          }
        },
        "required": [
          "file"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "head",
      "description": "First n rows of a file (n <= 20).",
      "parameters": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string",
            "description": "a file name from list_files"
          },
          "n": {
            "type": "integer"
          }
        },
        "required": [
          "file"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "value_counts",
      "description": "Top 15 values of a column with counts, plus its number of distinct values.",
      "parameters": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string",
            "description": "a file name from list_files"
          },
          "col": {
            "type": "string"
          }
        },
        "required": [
          "file",
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "null_rate",
      "description": "Share of missing values in a column.",
      "parameters": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string",
            "description": "a file name from list_files"
          },
          "col": {
            "type": "string"
          }
        },
        "required": [
          "file",
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "n_unique",
      "description": "Number of distinct values in a column.",
      "parameters": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string",
            "description": "a file name from list_files"
          },
          "col": {
            "type": "string"
          }
        },
        "required": [
          "file",
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "is_functionally_determined",
      "description": "Does col_a determine col_b in every row of the file (each col_a value maps to exactly one col_b value)?",
      "parameters": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string",
            "description": "a file name from list_files"
          },
          "col_a": {
            "type": "string"
          },
          "col_b": {
            "type": "string"
          }
        },
        "required": [
          "file",
          "col_a",
          "col_b"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "numeric_summary",
      "description": "Mean, std and quantiles of a numeric column.",
      "parameters": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string",
            "description": "a file name from list_files"
          },
          "col": {
            "type": "string"
          }
        },
        "required": [
          "file",
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "fraction_zero",
      "description": "Share of rows equal to 0 in a numeric column.",
      "parameters": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string",
            "description": "a file name from list_files"
          },
          "col": {
            "type": "string"
          }
        },
        "required": [
          "file",
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "has_fractional",
      "description": "Whether a numeric column has non-integer values, and their share.",
      "parameters": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string",
            "description": "a file name from list_files"
          },
          "col": {
            "type": "string"
          }
        },
        "required": [
          "file",
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "has_negative",
      "description": "Whether a numeric column has negative values, and how many.",
      "parameters": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string",
            "description": "a file name from list_files"
          },
          "col": {
            "type": "string"
          }
        },
        "required": [
          "file",
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "date_gaps",
      "description": "Date coverage of a date column: overall range and, per group of group_cols, the share of days missing between the group's first and last date.",
      "parameters": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string",
            "description": "a file name from list_files"
          },
          "date_col": {
            "type": "string"
          },
          "group_cols": {
            "type": "array",
            "items": {
              "type": "string"
            }
          }
        },
        "required": [
          "file",
          "date_col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "submit_config",
      "description": "Submit the drafted adapter config (strict JSON).",
      "parameters": {
        "type": "object",
        "properties": {
          "column_roles": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "file": {
                  "type": "string"
                },
                "column": {
                  "type": "string"
                },
                "role": {
                  "type": "string",
                  "enum": [
                    "date",
                    "sales_target",
                    "item_key",
                    "store_key",
                    "item_attribute",
                    "store_attribute",
                    "promotion",
                    "calendar_event",
                    "row_id",
                    "other"
                  ]
                },
                "rationale": {
                  "type": "string"
                }
              },
              "required": [
                "file",
                "column",
                "role"
              ]
            }
          },
          "hierarchy_edges": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "parent": {
                  "type": "object",
                  "properties": {
                    "file": {
                      "type": "string"
                    },
                    "column": {
                      "type": "string"
                    }
                  },
                  "required": [
                    "file",
                    "column"
                  ]
                },
                "child": {
                  "type": "object",
                  "properties": {
                    "file": {
                      "type": "string"
                    },
                    "column": {
                      "type": "string"
                    }
                  },
                  "required": [
                    "file",
                    "column"
                  ]
                },
                "rationale": {
                  "type": "string"
                }
              },
              "required": [
                "parent",
                "child"
              ]
            }
          },
          "zero_inflated": {
            "type": "object",
            "properties": {
              "value": {
                "type": "boolean"
              },
              "rationale": {
                "type": "string"
              }
            },
            "required": [
              "value",
              "rationale"
            ]
          },
          "fractional_sales": {
            "type": "object",
            "properties": {
              "value": {
                "type": "boolean"
              },
              "rationale": {
                "type": "string"
              }
            },
            "required": [
              "value",
              "rationale"
            ]
          },
          "bin_strategy_per_feature": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "file": {
                  "type": "string"
                },
                "column": {
                  "type": "string"
                },
                "path": {
                  "type": "string",
                  "enum": [
                    "integer",
                    "quantile"
                  ]
                },
                "rationale": {
                  "type": "string"
                }
              },
              "required": [
                "file",
                "column",
                "path"
              ]
            }
          },
          "data_quality_issues": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "issue": {
                  "type": "string"
                },
                "evidence": {
                  "type": "string"
                },
                "handling": {
                  "type": "string"
                }
              },
              "required": [
                "issue",
                "evidence",
                "handling"
              ]
            }
          },
          "rationale": {
            "type": "string"
          }
        },
        "required": [
          "column_roles",
          "hierarchy_edges",
          "zero_inflated",
          "fractional_sales",
          "bin_strategy_per_feature",
          "data_quality_issues",
          "rationale"
        ]
      }
    }
  }
]
````

### Output schema (`SUBMIT_SCHEMA`, the parameters of `submit_config`)

````json
{
  "type": "object",
  "properties": {
    "column_roles": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string"
          },
          "column": {
            "type": "string"
          },
          "role": {
            "type": "string",
            "enum": [
              "date",
              "sales_target",
              "item_key",
              "store_key",
              "item_attribute",
              "store_attribute",
              "promotion",
              "calendar_event",
              "row_id",
              "other"
            ]
          },
          "rationale": {
            "type": "string"
          }
        },
        "required": [
          "file",
          "column",
          "role"
        ]
      }
    },
    "hierarchy_edges": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "parent": {
            "type": "object",
            "properties": {
              "file": {
                "type": "string"
              },
              "column": {
                "type": "string"
              }
            },
            "required": [
              "file",
              "column"
            ]
          },
          "child": {
            "type": "object",
            "properties": {
              "file": {
                "type": "string"
              },
              "column": {
                "type": "string"
              }
            },
            "required": [
              "file",
              "column"
            ]
          },
          "rationale": {
            "type": "string"
          }
        },
        "required": [
          "parent",
          "child"
        ]
      }
    },
    "zero_inflated": {
      "type": "object",
      "properties": {
        "value": {
          "type": "boolean"
        },
        "rationale": {
          "type": "string"
        }
      },
      "required": [
        "value",
        "rationale"
      ]
    },
    "fractional_sales": {
      "type": "object",
      "properties": {
        "value": {
          "type": "boolean"
        },
        "rationale": {
          "type": "string"
        }
      },
      "required": [
        "value",
        "rationale"
      ]
    },
    "bin_strategy_per_feature": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "file": {
            "type": "string"
          },
          "column": {
            "type": "string"
          },
          "path": {
            "type": "string",
            "enum": [
              "integer",
              "quantile"
            ]
          },
          "rationale": {
            "type": "string"
          }
        },
        "required": [
          "file",
          "column",
          "path"
        ]
      }
    },
    "data_quality_issues": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "issue": {
            "type": "string"
          },
          "evidence": {
            "type": "string"
          },
          "handling": {
            "type": "string"
          }
        },
        "required": [
          "issue",
          "evidence",
          "handling"
        ]
      }
    },
    "rationale": {
      "type": "string"
    }
  },
  "required": [
    "column_roles",
    "hierarchy_edges",
    "zero_inflated",
    "fractional_sales",
    "bin_strategy_per_feature",
    "data_quality_issues",
    "rationale"
  ]
}
````

### Rendered example from the raw log: Favorita, named condition, repeat 0
Source `outputs/onboarding/raw/agent_named_rep0.json` (model `gpt-oss-120b`, provider `cerebras`, temperature 0, seed 0):

**system:**

````
You are onboarding a raw retail dataset into a drift-monitoring pipeline. You can only inspect the raw files through the tools; you cannot read anything else.

WHAT THE PIPELINE NEEDS (its input contract):
- A daily panel with one row per item x store x day: an item key, a store key, the date, and the sales quantity to monitor.
- A product hierarchy with two levels above item (item -> dept -> category) and a location hierarchy with one level above store (store -> region). Each child value must map to exactly one parent value.
- Numeric features are binned for divergence tests in one of two ways: the "integer" path (one bin per integer value; only valid if every value is an integer) or the "quantile" path (equal-frequency bins).

YOUR JOB: draft the adapter config by profiling the files, then call submit_config once. Base every claim on tool evidence. Report in data_quality_issues anything in the raw data that the panel builder must handle to produce a correct daily panel. Give a short rationale for every non-trivial choice.

BUDGET: at most 30 profiling tool calls (submit_config not counted); each result reports how many remain.
````

**user:**

````
Begin profiling the dataset.
````

### Rendered example from the raw log: Favorita, obscured condition, repeat 0
Source `outputs/onboarding/raw/agent_obscured_rep0.json` (model `gpt-oss-120b`, provider `cerebras`, temperature 0, seed 0):

**system:**

````
You are onboarding a raw retail dataset into a drift-monitoring pipeline. You can only inspect the raw files through the tools; you cannot read anything else.

WHAT THE PIPELINE NEEDS (its input contract):
- A daily panel with one row per item x store x day: an item key, a store key, the date, and the sales quantity to monitor.
- A product hierarchy with two levels above item (item -> dept -> category) and a location hierarchy with one level above store (store -> region). Each child value must map to exactly one parent value.
- Numeric features are binned for divergence tests in one of two ways: the "integer" path (one bin per integer value; only valid if every value is an integer) or the "quantile" path (equal-frequency bins).

YOUR JOB: draft the adapter config by profiling the files, then call submit_config once. Base every claim on tool evidence. Report in data_quality_issues anything in the raw data that the panel builder must handle to produce a correct daily panel. Give a short rationale for every non-trivial choice.

BUDGET: at most 30 profiling tool calls (submit_config not counted); each result reports how many remain.
````

**user:**

````
Begin profiling the dataset.
````

No M5 run was made with this prompt (Phase 8 used Favorita only).

## 5. Retrain-vs-ignore agent (Phase 9), regimes A and B

| field | value |
|---|---|
| file | `src/decision/agent.py` |
| commits | `8b46ce6 2026-10-10 Phase 9: fix contradictory regime-B prompt wording before any agent call`; `c61739e 2026-10-10 Phase 9 amendment (primary harm threshold 1.05) before any agent/baseline run; trial table and code` |
| model / provider | `gpt-oss-120b` on Cerebras |
| temperature / seed | 0 / 0 |
| other | `reasoning_effort="medium"`, `reasoning_format="parsed"` |
| max steps | 1 call per trial per regime; `submit_decision` forced; no retries |
| used | Phase 9: 260 trials x 2 regimes (seeds 42/123), M5 only |

### System prompt (verbatim source of the constants and the assembling function)

````python
MODEL_DESC = (
    "The monitored model is a gradient-boosted demand forecaster that predicts daily unit sales for every "
    "item x store x day of a retail panel (about 200 items in 10 stores). Its inputs are the item's sell price, "
    "calendar features (weekday, weekend, month, SNAP day, event day) and the item/department/category/store/"
    "state identifiers. It does NOT use recent sales as an input. A drift monitor compared the current 30-day "
    "window with the preceding 60-day reference window and raised the alert below; a localiser ranked the "
    "slices that changed most.")
````

````python
DECISION_DEF = (
    "Decide whether this drift has measurably degraded the model. 'retrain' means: the drift is likely to make "
    "the model's mean absolute error over the whole current window at least 5% higher than it would have been "
    "without the drift. 'ignore' means: it is not (for example, the change is small, confined to a small slice, "
    "or does not affect what the model gets wrong). Retraining has a cost, and so does missing a harmful drift.")
````

````python
REGIME_NOTE = {
    "A": "You also see the model's mean absolute error on the reference window and on the current window.",
    "B": ("You are NOT shown the model's error on the current window; decide from the drift evidence alone. 'mean_sales_pct_change' is the slice's observed mean-sales change "
          "from reference to current; 'score' is its within-slice divergence; 'share_of_window' is the share of "
          "rows the slice covers."),
}
````

````python
def system_prompt(regime):
    return f"{MODEL_DESC}\n\n{DECISION_DEF}\n\n{REGIME_NOTE[regime]}\n\nCall submit_decision exactly once."

````

### Rendered system prompts

**Regime A:**

````
The monitored model is a gradient-boosted demand forecaster that predicts daily unit sales for every item x store x day of a retail panel (about 200 items in 10 stores). Its inputs are the item's sell price, calendar features (weekday, weekend, month, SNAP day, event day) and the item/department/category/store/state identifiers. It does NOT use recent sales as an input. A drift monitor compared the current 30-day window with the preceding 60-day reference window and raised the alert below; a localiser ranked the slices that changed most.

Decide whether this drift has measurably degraded the model. 'retrain' means: the drift is likely to make the model's mean absolute error over the whole current window at least 5% higher than it would have been without the drift. 'ignore' means: it is not (for example, the change is small, confined to a small slice, or does not affect what the model gets wrong). Retraining has a cost, and so does missing a harmful drift.

You also see the model's mean absolute error on the reference window and on the current window.

Call submit_decision exactly once.
````

**Regime B:**

````
The monitored model is a gradient-boosted demand forecaster that predicts daily unit sales for every item x store x day of a retail panel (about 200 items in 10 stores). Its inputs are the item's sell price, calendar features (weekday, weekend, month, SNAP day, event day) and the item/department/category/store/state identifiers. It does NOT use recent sales as an input. A drift monitor compared the current 30-day window with the preceding 60-day reference window and raised the alert below; a localiser ranked the slices that changed most.

Decide whether this drift has measurably degraded the model. 'retrain' means: the drift is likely to make the model's mean absolute error over the whole current window at least 5% higher than it would have been without the drift. 'ignore' means: it is not (for example, the change is small, confined to a small slice, or does not affect what the model gets wrong). Retraining has a cost, and so does missing a harmful drift.

You are NOT shown the model's error on the current window; decide from the drift evidence alone. 'mean_sales_pct_change' is the slice's observed mean-sales change from reference to current; 'score' is its within-slice divergence; 'share_of_window' is the share of rows the slice covers.

Call submit_decision exactly once.
````

### User prompt template (from `decide`)

````python
{"role": "user", "content": "Drift evidence (JSON):\n" + payload_json}
````

### Tool definition (`TOOL`)

````json
{
  "type": "function",
  "function": {
    "name": "submit_decision",
    "description": "Submit the decision.",
    "parameters": {
      "type": "object",
      "properties": {
        "decision": {
          "type": "string",
          "enum": [
            "retrain",
            "ignore"
          ]
        },
        "confidence": {
          "type": "number",
          "minimum": 0,
          "maximum": 1
        },
        "rationale": {
          "type": "string"
        }
      },
      "required": [
        "decision",
        "confidence",
        "rationale"
      ]
    }
  }
}
````

### Output schema
The `submit_decision` parameters above: `decision` in {retrain, ignore}, `confidence` in [0, 1], `rationale`.

### Rendered example, M5 seed 42, sudden, trial 0
The Phase 9 raw log stores the payload (`outputs/decision_trials.csv`, columns `payload_A` / `payload_B`) but not the assembled messages; the messages below are rebuilt exactly from that logged payload and the prompt at the run's commit (`8b46ce6`).

**Regime A — system:**

````
The monitored model is a gradient-boosted demand forecaster that predicts daily unit sales for every item x store x day of a retail panel (about 200 items in 10 stores). Its inputs are the item's sell price, calendar features (weekday, weekend, month, SNAP day, event day) and the item/department/category/store/state identifiers. It does NOT use recent sales as an input. A drift monitor compared the current 30-day window with the preceding 60-day reference window and raised the alert below; a localiser ranked the slices that changed most.

Decide whether this drift has measurably degraded the model. 'retrain' means: the drift is likely to make the model's mean absolute error over the whole current window at least 5% higher than it would have been without the drift. 'ignore' means: it is not (for example, the change is small, confined to a small slice, or does not affect what the model gets wrong). Retraining has a cost, and so does missing a harmful drift.

You also see the model's mean absolute error on the reference window and on the current window.

Call submit_decision exactly once.
````

**Regime A — user:**

````
Drift evidence (JSON):
{"window": {"reference": ["2015-10-27", "2015-12-25"], "current": ["2015-12-26", "2016-01-25"]}, "drift_alert": {"sales": {"js_divergence": 0.000549, "l_inf": 0.010275, "fired": false}, "sell_price": {"js_divergence": 0.000257, "l_inf": 0.004203, "fired": false}, "revenue": {"js_divergence": 0.000228, "l_inf": 0.011389, "fired": false}, "cat_id": {"js_divergence": 0.0, "l_inf": 0.0, "fired": false}, "state_id": {"js_divergence": 0.0, "l_inf": 0.0, "fired": false}, "pipeline_detected": true}, "top_slices": [{"slice": "store_id=CA_2", "score": 0.018574, "share_of_window": 0.1}, {"slice": "dept_id=HOBBIES_2", "score": 0.007436, "share_of_window": 0.0859}, {"slice": "dept_id=FOODS_3", "score": 0.003038, "share_of_window": 0.202}], "reference_mae": 0.887, "current_mae": 0.9754}
````

**Regime B — system:**

````
The monitored model is a gradient-boosted demand forecaster that predicts daily unit sales for every item x store x day of a retail panel (about 200 items in 10 stores). Its inputs are the item's sell price, calendar features (weekday, weekend, month, SNAP day, event day) and the item/department/category/store/state identifiers. It does NOT use recent sales as an input. A drift monitor compared the current 30-day window with the preceding 60-day reference window and raised the alert below; a localiser ranked the slices that changed most.

Decide whether this drift has measurably degraded the model. 'retrain' means: the drift is likely to make the model's mean absolute error over the whole current window at least 5% higher than it would have been without the drift. 'ignore' means: it is not (for example, the change is small, confined to a small slice, or does not affect what the model gets wrong). Retraining has a cost, and so does missing a harmful drift.

You are NOT shown the model's error on the current window; decide from the drift evidence alone. 'mean_sales_pct_change' is the slice's observed mean-sales change from reference to current; 'score' is its within-slice divergence; 'share_of_window' is the share of rows the slice covers.

Call submit_decision exactly once.
````

**Regime B — user:**

````
Drift evidence (JSON):
{"window": {"reference": ["2015-10-27", "2015-12-25"], "current": ["2015-12-26", "2016-01-25"]}, "drift_alert": {"sales": {"js_divergence": 0.000549, "l_inf": 0.010275, "fired": false}, "sell_price": {"js_divergence": 0.000257, "l_inf": 0.004203, "fired": false}, "revenue": {"js_divergence": 0.000228, "l_inf": 0.011389, "fired": false}, "cat_id": {"js_divergence": 0.0, "l_inf": 0.0, "fired": false}, "state_id": {"js_divergence": 0.0, "l_inf": 0.0, "fired": false}, "pipeline_detected": true}, "top_slices": [{"slice": "store_id=CA_2", "score": 0.018574, "share_of_window": 0.1, "mean_sales_pct_change": 0.5656}, {"slice": "dept_id=HOBBIES_2", "score": 0.007436, "share_of_window": 0.0859, "mean_sales_pct_change": -0.3688}, {"slice": "dept_id=FOODS_3", "score": 0.003038, "share_of_window": 0.202, "mean_sales_pct_change": 0.2568}]}
````

### Revisions (all before any agent call)

1. Original `DECISION_DEF` (written at pre-registration, never committed; replaced before commit `c61739e` by the threshold amendment). Verbatim from the session's edit string:

````python
    "Decide whether this drift warrants retraining the model. 'retrain' means: the drift is likely to make the "
    "model's mean absolute error over the whole current window at least 10% higher than it would have been "
    "without the drift. 'ignore' means: it is not (for example, the change is small, confined to a small slice, "
    "or does not affect what the model gets wrong). Retraining has a cost, and so does missing a harmful drift.")
````

2. Regime B note, `c61739e` -> `8b46ce6` (`git diff`, verbatim):

````diff
diff --git a/src/decision/agent.py b/src/decision/agent.py
index 17fb6a9..0ff3567 100644
--- a/src/decision/agent.py
+++ b/src/decision/agent.py
@@ -47,8 +47,7 @@ DECISION_DEF = (
 
 REGIME_NOTE = {
     "A": "You also see the model's mean absolute error on the reference window and on the current window.",
-    "B": ("You do NOT see the model's error on the current window (actual sales for it are not available yet); "
-          "decide from the drift evidence alone. 'mean_sales_pct_change' is the slice's observed mean-sales change "
+    "B": ("You are NOT shown the model's error on the current window; decide from the drift evidence alone. 'mean_sales_pct_change' is the slice's observed mean-sales change "
           "from reference to current; 'score' is its within-slice divergence; 'share_of_window' is the share of "
           "rows the slice covers."),
 }
````

No seed-7 prompt tuning was done in Phase 9 (pre-registered).

## 6. Fault-diagnosis agent (Phase 10)

| field | value |
|---|---|
| file | `src/faults/agent.py` |
| commits | `8fccba9 2026-10-12 Phase 10 results: fault-diagnosis agent NOT SUPPORTED (held-out pass vs no-rule baselines; significantly worse than the checker on covered faults)`; `0494405 2026-10-12 Phase 10 amendment: agent provider Cerebras -> Groq (same model), before any scored agent result; set aside Cerebras smoke trials`; `357d2fc 2026-10-12 Phase 10: fault injectors, non-LLM checker (fit on seed 7) and agent code, committed before any agent run` |
| model / provider | `openai/gpt-oss-120b` on Groq (amendment `0494405`); the 3 unscored smoke trials used Cerebras `gpt-oss-120b` |
| temperature / seed | 0 / 0 |
| other | `reasoning_effort="medium"`; Groq: no `reasoning_format` |
| max steps | `MAX_CALLS = 12` tool calls, `max_turns=30`; submit forced once the budget is used; up to `MAX_RETRIES = 2` retries of a turn Groq rejects (added `8fccba9`) |
| used | Phase 10: 300 trials (seeds 42/123), M5 only |

### System prompt template (`SYSTEM_PROMPT`)

````
You are a data-quality engineer. A daily retail sales panel feeds a demand-forecasting model. One row = one item in one store on one day; columns include id (item_store series), item_id, dept_id, cat_id, store_id, state_id, date, sales, sell_price, revenue. Hierarchy: item -> dept -> cat, store -> state.

TASK: decide whether the CURRENT window contains a data-pipeline fault, using the REFERENCE window (the days just before) as normal behaviour. Possible answers:
- stale_feed: a feed stops updating and keeps repeating its last values.
- mislabeled_category: rows carry a wrong category or department label.
- unit_change: a column's values switch units (a constant multiplicative factor).
- zero_fill: missing data was filled with zeros.
- duplicated_rows: some rows were ingested twice.
- none: no data-pipeline fault.
Real retail data also contains genuine demand changes, sparse items with long zero runs, and normal price changes; those are not faults. If there is a fault, give the affected column and the slice (one column and one value) where it occurs.

BUDGET: at most {max_calls} tool calls, then call submit_diagnosis.
````

### Other messages (verbatim from `run_trial`)
- first user message: `Begin.`
- nudge: `Call a tool or submit_diagnosis.`
- budget exhausted: `Tool budget exhausted. Call submit_diagnosis now.`

### Tool definitions (`TOOL_DEFS`)

````json
[
  {
    "type": "function",
    "function": {
      "name": "list_columns",
      "description": "Columns, dtypes, row counts and dates of both windows.",
      "parameters": {
        "type": "object",
        "properties": {},
        "required": []
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "value_counts",
      "description": "Top values of a column with row counts in one window.",
      "parameters": {
        "type": "object",
        "properties": {
          "col": {
            "type": "string"
          },
          "window": {
            "type": "string",
            "enum": [
              "reference",
              "current"
            ]
          }
        },
        "required": [
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "null_rate",
      "description": "Share of missing values in a column in one window.",
      "parameters": {
        "type": "object",
        "properties": {
          "col": {
            "type": "string"
          },
          "window": {
            "type": "string",
            "enum": [
              "reference",
              "current"
            ]
          }
        },
        "required": [
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "numeric_summary",
      "description": "Mean, share of zeros and quantiles of a numeric column in one window.",
      "parameters": {
        "type": "object",
        "properties": {
          "col": {
            "type": "string"
          },
          "window": {
            "type": "string",
            "enum": [
              "reference",
              "current"
            ]
          }
        },
        "required": [
          "col"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "slice_history",
      "description": "For rows where col == val: weekly mean of feature (and rows per day) in the reference window, daily mean and row count in the current window.",
      "parameters": {
        "type": "object",
        "properties": {
          "col": {
            "type": "string"
          },
          "val": {
            "type": "string"
          },
          "feature": {
            "type": "string"
          }
        },
        "required": [
          "col",
          "val",
          "feature"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "compare_windows",
      "description": "For every value of col: mean of feature and rows per day in reference vs current; the most changed values.",
      "parameters": {
        "type": "object",
        "properties": {
          "col": {
            "type": "string"
          },
          "feature": {
            "type": "string"
          }
        },
        "required": [
          "col",
          "feature"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "duplicate_check",
      "description": "Rows whose (id, date) key occurs more than once, per window, with the slices they fall in.",
      "parameters": {
        "type": "object",
        "properties": {},
        "required": []
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "constant_run_check",
      "description": "Series whose feature value stays constant over the last min_run (or more) days of each window, split into zero and non-zero runs, with the slices they fall in.",
      "parameters": {
        "type": "object",
        "properties": {
          "feature": {
            "type": "string"
          },
          "min_run": {
            "type": "integer"
          }
        },
        "required": []
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "label_consistency",
      "description": "Does col_a map to a single col_b value in each window? Lists values that map to several, with dates.",
      "parameters": {
        "type": "object",
        "properties": {
          "col_a": {
            "type": "string"
          },
          "col_b": {
            "type": "string"
          }
        },
        "required": [
          "col_a",
          "col_b"
        ]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "submit_diagnosis",
      "description": "Submit the diagnosis.",
      "parameters": {
        "type": "object",
        "properties": {
          "fault_type": {
            "type": "string",
            "enum": [
              "stale_feed",
              "mislabeled_category",
              "unit_change",
              "zero_fill",
              "duplicated_rows",
              "none"
            ]
          },
          "column": {
            "type": [
              "string",
              "null"
            ]
          },
          "slice_col": {
            "type": [
              "string",
              "null"
            ]
          },
          "slice_value": {
            "type": [
              "string",
              "null"
            ]
          },
          "rationale": {
            "type": "string"
          }
        },
        "required": [
          "fault_type",
          "column",
          "slice_col",
          "slice_value",
          "rationale"
        ]
      }
    }
  }
]
````

### Output schema
The `submit_diagnosis` parameters above: `fault_type` (one of the five faults or `none`), `column`, `slice_col`, `slice_value`, `rationale`.

### Rendered example from the raw log: M5 seed 42, stale_feed, trial 0
Source `outputs/faults_agent_raw.jsonl` (model `openai/gpt-oss-120b`, provider `groq`, temperature 0):

**system:**

````
You are a data-quality engineer. A daily retail sales panel feeds a demand-forecasting model. One row = one item in one store on one day; columns include id (item_store series), item_id, dept_id, cat_id, store_id, state_id, date, sales, sell_price, revenue. Hierarchy: item -> dept -> cat, store -> state.

TASK: decide whether the CURRENT window contains a data-pipeline fault, using the REFERENCE window (the days just before) as normal behaviour. Possible answers:
- stale_feed: a feed stops updating and keeps repeating its last values.
- mislabeled_category: rows carry a wrong category or department label.
- unit_change: a column's values switch units (a constant multiplicative factor).
- zero_fill: missing data was filled with zeros.
- duplicated_rows: some rows were ingested twice.
- none: no data-pipeline fault.
Real retail data also contains genuine demand changes, sparse items with long zero runs, and normal price changes; those are not faults. If there is a fault, give the affected column and the slice (one column and one value) where it occurs.

BUDGET: at most 12 tool calls, then call submit_diagnosis.
````

**user:**

````
Begin.
````

### Revisions
The prompt and tools are unchanged since the first commit (`357d2fc`); later commits changed only the provider option (`0494405`) and the retry policy (`8fccba9`). No seed-7 prompt tuning was done.

`git diff 357d2fc 8fccba9` restricted to prompt/tool lines is empty; full diff for reference:

````diff
diff --git a/src/faults/agent.py b/src/faults/agent.py
index c270b45..0b606f6 100644
--- a/src/faults/agent.py
+++ b/src/faults/agent.py
@@ -1,6 +1,7 @@
-"""Phase 10 fault-diagnosis agent: same model/provider as the frozen
-investigator (reuses its ChatClient unchanged: Cerebras gpt-oss-120b,
-temperature 0, seed 0, reasoning medium). Tools are computed on the trial's
+"""Phase 10 fault-diagnosis agent: same model as the frozen investigator,
+reusing its ChatClient unchanged (gpt-oss-120b, temperature 0, seed 0,
+reasoning medium). Provider: Groq by AMENDMENT (2026-10-12, before any
+scored agent result) -- the pre-registered provider was Cerebras. Tools are computed on the trial's
 panel restricted to its reference + current windows; the fault log is never
 passed in. Strict-JSON answer through a forced `submit_diagnosis` call.
 
@@ -27,6 +28,7 @@ OUT_CSV = "outputs/faults_agent_trials.csv"
 OUT_RAW = "outputs/faults_agent_raw.jsonl"
 OUT_CALLS = "outputs/faults_agent_toolcalls.jsonl"
 MAX_CALLS = 12
+MAX_RETRIES = 2  # retries of a turn Groq rejects as tool_use_failed / http_400
 FAULTS = ["stale_feed", "mislabeled_category", "unit_change", "zero_fill", "duplicated_rows", "none"]
 NUMERIC = ["sales", "sell_price", "revenue"]
 MAX_CHARS = 3000
@@ -221,12 +223,19 @@ def run_trial(tools, chat, ctx, max_calls=MAX_CALLS, max_turns=30):
                 {"role": "user", "content": "Begin."}]
     transcript = [dict(m) for m in messages]
     used, budget_done, tok = 0, False, dict(prompt=0, completion=0, cached=0)
+    retries = 0
     for _ in range(max_turns):
         choice = {"type": "function", "function": {"name": "submit_diagnosis"}} if budget_done else "auto"
         out = chat(messages, [TOOL_DEFS[-1]] if budget_done else TOOL_DEFS, choice)
         tok["prompt"] += out.prompt_tokens; tok["completion"] += out.completion_tokens; tok["cached"] += out.cached_tokens
         if not out.ok:
-            return dict(ok=False, fallback=out.error, n_tool_calls=used, transcript=transcript, tokens=tok)
+            transcript.append({"role": "_harness", "content": f"{out.error}: {(out.detail or '')[:1500]}"})
+            # Same policy as the frozen investigator loop (added 2026-10-12 after the first
+            # Groq pass): Groq intermittently rejects a malformed tool call; retry the turn.
+            if out.error in ("tool_use_failed", "http_400") and retries < MAX_RETRIES:
+                retries += 1
+                continue
+            return dict(ok=False, fallback=out.error, n_tool_calls=used, retries=retries, transcript=transcript, tokens=tok)
         msg = out.message
         clean = {"role": "assistant", "content": msg.get("content") or ""}
         if msg.get("tool_calls"):
@@ -246,7 +255,7 @@ def run_trial(tools, chat, ctx, max_calls=MAX_CALLS, max_turns=30):
                 args = None
             if name == "submit_diagnosis":
                 if isinstance(args, dict) and args.get("fault_type") in FAULTS and isinstance(args.get("rationale"), str):
-                    return dict(ok=True, fallback=None, diagnosis=args, n_tool_calls=used, forced=budget_done,
+                    return dict(ok=True, fallback=None, diagnosis=args, n_tool_calls=used, forced=budget_done, retries=retries,
                                 transcript=transcript, tokens=tok)
                 return dict(ok=False, fallback="invalid_json", raw=args, n_tool_calls=used, transcript=transcript, tokens=tok)
             if args is None:
@@ -273,6 +282,8 @@ def main(argv=None):
     ap = argparse.ArgumentParser()
     ap.add_argument("--seeds", type=int, nargs="+", default=[42, 123])
     ap.add_argument("--max-trials", type=int, default=None)
+    ap.add_argument("--provider", default="groq", choices=["groq", "cerebras"],
+                    help="AMENDMENT 2026-10-12: Groq (same open-weight gpt-oss-120b) instead of Cerebras")
     a = ap.parse_args(argv)
     assert 7 not in a.seeds or a.max_trials is not None, "seed 7 only for (logged) tuning"
     load_dotenv(".env", override=True)
@@ -281,7 +292,7 @@ def main(argv=None):
     if os.path.exists(OUT_CSV):
         p = pd.read_csv(OUT_CSV)
         done = set(zip(p.seed, p.kind, p.trial))
-    chat = ChatClient(provider="cerebras", reasoning_effort="medium", seed=0)
+    chat = ChatClient(provider=a.provider, reasoning_effort="medium", seed=0)
     n = 0
     for seed in a.seeds:
         for kind, i, ref, cur, make in iter_fault_trials(df, seed):
````

## Re-rendered examples for prompts whose runs did not log the prompt

Produced now by calling each unchanged builder on a real M5 trial (seed 42, sudden, trial 0: true slice `store_id=CA_2`), and for the Synthesizer also on the matching Favorita trial. They are faithful renderings of the code, not copies of a logged request.

### 2a. Re-ranker v1 (M5)
The deterministic top-5 re-rendered here matches the logged candidates for this trial in `outputs/groq_rerank_trials.csv` (store_id=CA_2:0.018574|dept_id=HOBBIES_2:0.007436|dept_id=FOODS_3:0.003038|cat_id=FOODS:0.002346|dept_id=FOODS_2:0.002035).

````
A drift-detection system flagged a possible distribution shift in an e-commerce demand dataset (M5: item/store/dept/category/state hierarchy).
Monitored feature: sales
Drift-type hint from the detector run (NOT ground truth, just context): sudden
Whole-window detector scores: JS divergence=0.000549, L-infinity distance=0.010275

A deterministic statistical scorer (within-slice Jensen-Shannon divergence: each candidate slice's OWN reference-window distribution vs. its OWN current-window distribution) already ranked the top 5 candidate root-cause slices below. `score` is that divergence (higher = bigger within-slice distributional shift); `coverage` is the fraction of the current window's rows that slice covers.

1. slice_col=store_id, slice_val=CA_2, score=0.018574, coverage=0.1000
2. slice_col=dept_id, slice_val=HOBBIES_2, score=0.007436, coverage=0.0859
3. slice_col=dept_id, slice_val=FOODS_3, score=0.003038, coverage=0.2020
4. slice_col=cat_id, slice_val=FOODS, score=0.002346, coverage=0.3333
5. slice_col=dept_id, slice_val=FOODS_2, score=0.002035, coverage=0.0758

Re-rank these 5 candidates by how likely each is to be the TRUE root-cause slice of the drift. You may reorder them, but you must choose ONLY from the 5 listed above -- do not invent a new slice.
Respond with STRICT JSON ONLY, no other text, exactly in this form: {"ranking": [a, b, c]} where a, b, c are the 1-based indices (1-5) of your top 3 picks from the list above, most-likely first.
````

### 2b. Re-ranker v2 (M5, hybrid pool)

````
A drift-detection system flagged a possible distribution shift in an e-commerce demand dataset (M5: item/store/dept/category/state hierarchy).
Monitored feature: sales
Whole-window detector scores: JS divergence=0.000549, L-infinity distance=0.010275

Methodological context from prior calibration on this dataset (true in general, not specific to this one alert -- treat as background knowledge, the same way a human analyst would be briefed before judging):
- `coarse_score`: within-slice Jensen-Shannon divergence at the department/store/state/category level (few, large-sample candidates). Typical range ~0-0.2. The most stable signal.
- `item_divergence_score`: the SAME divergence statistic, but computed per individual item. Typical range similar to coarse, but individual items carry much more natural, non-drift demand volatility (products get delisted, ramp up, etc.) -- a high item_divergence_score ALONE is weak evidence, because ordinary item-level noise routinely produces scores this large even with no real anomaly.
- `burst_score`: looks specifically for an unusually large SHORT (1-2 day) spike in an item's daily total, relative to its own history. Typical range can be tens to low hundreds; NOT comparable in magnitude to the other two scores. A high burst_score is more specific evidence of a short, deliberate shock than a high item_divergence_score, because it requires a concentrated spike shape rather than general noisiness.
- A candidate may have one, two, or all three scores computed; a missing score is reported as null, not zero.


DECISION RULE (apply this; item-level scores are seductive but usually wrong): start from the single best coarse_score candidate as your default #1 pick. Only replace it with an item-level candidate (by item_divergence_score or burst_score) if that item's evidence is NOT just the single biggest number in the list, but is dramatically isolated from every other item-level candidate too (e.g. burst_score > 60 and clearly larger than the next item's burst_score, or item_divergence_score more than ~3x the next-highest item's score) -- a merely-highest-of-many noisy item scores is NOT enough, because ordinary non-drift item volatility routinely produces the single largest number in any list of many items by chance alone.

Candidate root-cause slices (merged from multiple scoring methods -- see context above):

1. slice_col=store_id, slice_val=CA_2, coverage=0.1000, coarse_score=0.018574, item_divergence_score=null, burst_score=null
2. slice_col=dept_id, slice_val=HOBBIES_2, coverage=0.0859, coarse_score=0.007436, item_divergence_score=null, burst_score=null
3. slice_col=dept_id, slice_val=FOODS_3, coverage=0.2020, coarse_score=0.003038, item_divergence_score=null, burst_score=null
4. slice_col=cat_id, slice_val=FOODS, coverage=0.3333, coarse_score=0.002346, item_divergence_score=null, burst_score=null
5. slice_col=dept_id, slice_val=FOODS_2, coverage=0.0758, coarse_score=0.002035, item_divergence_score=null, burst_score=null

Rank your top 3 picks for the TRUE root-cause slice from the 5 candidates above, most-likely first. Choose ONLY from the list -- do not invent a new slice.
Respond with STRICT JSON ONLY, no other text: {"ranking": [a, b, c]} where a, b, c are 1-based indices (1-5) of your top 3 picks.
````

### 3a. AgenticInvestigator LLM controller (M5; rendered from the f-string with the class defaults)

````
You are the root-cause investigator for a model-drift alert. You may call score_combination repeatedly to test (column, value) or (column1,value1) AND (column2,value2) hypotheses for what slice of the data explains the drift in feature 'sales'. A good answer has a high score AND is not trivially tiny (candidates covering under 5% of the window are unreliable). When confident, call submit_answer with your best (column, value) or combined answer. You have at most 6 tool calls total. Available slice columns: ['cat_id', 'dept_id', 'state_id', 'store_id', 'item_id'].
````

First user message: `Begin your investigation.`

### 1. Synthesizer user message (m5; system prompt as in section 1)

````
{"feature": "sales", "js_divergence": 0.0005494761956449655, "l_inf_distance": 0.010275062452481887, "top_candidates": [{"slice_col": "store_id", "slice_val": "CA_2", "score": 0.018574245461880416, "coverage_frac": 0.1}, {"slice_col": "dept_id", "slice_val": "HOBBIES_2", "score": 0.007436376015965526, "coverage_frac": 0.08585858585858586}, {"slice_col": "dept_id", "slice_val": "FOODS_3", "score": 0.003038093232622667, "coverage_frac": 0.20202020202020202}], "calendar_context": {}}
````

### 1. Synthesizer user message (favorita; system prompt as in section 1)

````
{"feature": "sales", "js_divergence": 6.96835014920356e-05, "l_inf_distance": 0.0060801725400396744, "top_candidates": [{"slice_col": "store_id", "slice_val": "52", "score": 0.35579249974066557, "coverage_frac": 0.021048287247214196}, {"slice_col": "dept_id", "slice_val": "PREPARED FOODS", "score": 0.08708854058407271, "coverage_frac": 0.03714403631861329}, {"slice_col": "state_id", "slice_val": "Manabi", "score": 0.04000299959094936, "coverage_frac": 0.055578483973036184}], "calendar_context": {}}
````

