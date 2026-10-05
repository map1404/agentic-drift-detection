"""Task 5: optional Groq-based re-ranking of the Investigator's deterministic
top-5 candidates.

This module is a thin, isolated wrapper around a single Groq chat-completions
call. It NEVER invents new candidate slices -- it only re-orders (or
subsets to 3 of) the deterministic top-5 it's given. If anything at all
goes wrong (no API key, network error, timeout, non-200 response, malformed
JSON, an out-of-range or duplicate index in the model's answer), it returns
`used_groq=False` with an explicit `fallback_reason` and the deterministic
top-3 unchanged as `ranked_candidates` -- callers MUST check `used_groq`
before treating a trial as a real Groq result, per this project's honesty
norm (see CLAUDE_CODE_HANDOFF.md): a silent fallback must never be counted
as "Groq was used."

Model choice: this account's `/v1/models` endpoint (checked directly, see
Task 5 conversation) does not expose any `llama-3.x` model -- only
`openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `openai/gpt-oss-safeguard-20b`,
`qwen/qwen3.8-27b`, `allam-2-7b`, plus audio/guard models. `gpt-oss-20b` is
used here as a fast, cheap default. NOTE: gpt-oss is a reasoning model --
it spends completion tokens on a hidden `reasoning` field before emitting
`content`, so a small `max_tokens` (e.g. 20) truncates before any real
answer (`finish_reason="length"`, empty `content`) even though the HTTP
call succeeded. Confirmed directly: `max_tokens=20` with no
`reasoning_effort` -> empty content; `max_tokens=200` + `reasoning_effort=
"low"` -> a real, short "pong" answer. This module sets `reasoning_effort=
"low"` and a comfortably large `max_tokens` for exactly this reason -- an
empty/truncated `content` is treated as `malformed_response`, not silently
retried with more tokens (a fixed budget keeps trial-to-trial cost and
latency comparable, and a trial that runs out of budget is real evidence
the model needed more room, worth logging honestly rather than papering
over).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import requests

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-20b"

FALLBACK_REASONS = (
    "no_api_key", "auth_error", "network_error", "timeout",
    "rate_limit", "http_error", "malformed_response",
)


@dataclass
class RerankResult:
    used_groq: bool
    fallback_reason: str | None
    ranked_candidates: list = field(default_factory=list)  # list[SliceCandidate], len<=3
    raw_content: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    http_status: int | None = None


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


def _parse_ranking(content: str, n_candidates: int) -> list[int]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
        text = text.strip()
    parsed = json.loads(text)
    ranking = parsed["ranking"]
    if not isinstance(ranking, list) or not ranking:
        raise ValueError("ranking is empty or not a list")
    idxs = [int(x) for x in ranking]
    if any(i < 1 or i > n_candidates for i in idxs):
        raise ValueError(f"index out of range 1..{n_candidates}: {idxs}")
    if len(set(idxs)) != len(idxs):
        raise ValueError(f"duplicate index in ranking: {idxs}")
    return idxs[:3]


def groq_rerank(alert_context: dict, candidates: list, timeout: float = 20.0) -> RerankResult:
    """Re-rank `candidates` (the Investigator's top-5 `SliceCandidate`s) via
    Groq, given `alert_context` (feature/drift_type/js_divergence/
    l_inf_distance). Returns a `RerankResult`; `used_groq=False` on ANY
    failure, with `ranked_candidates` falling back to the deterministic
    top-3 unchanged so callers can still compute a metric for that trial,
    but MUST gate "did Groq actually help" analysis on `used_groq`.
    """
    if not candidates:
        return RerankResult(False, "malformed_response", [])

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return RerankResult(False, "no_api_key", candidates[:3])

    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": _build_prompt(alert_context, candidates)}],
        "temperature": 0,
        "max_tokens": 500,
        "reasoning_effort": "low",
    }

    t0 = time.time()
    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return RerankResult(False, "timeout", candidates[:3], latency_s=time.time() - t0)
    except requests.exceptions.RequestException as e:
        return RerankResult(False, "network_error", candidates[:3], raw_content=str(e), latency_s=time.time() - t0)

    latency = time.time() - t0

    if resp.status_code in (401, 403):
        return RerankResult(False, "auth_error", candidates[:3], raw_content=resp.text[:500],
                             latency_s=latency, http_status=resp.status_code)
    if resp.status_code == 429:
        return RerankResult(False, "rate_limit", candidates[:3], raw_content=resp.text[:500],
                             latency_s=latency, http_status=resp.status_code)
    if resp.status_code != 200:
        return RerankResult(False, "http_error", candidates[:3], raw_content=resp.text[:500],
                             latency_s=latency, http_status=resp.status_code)

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        return RerankResult(False, "malformed_response", candidates[:3], raw_content=resp.text[:500],
                             latency_s=latency, http_status=resp.status_code)

    if not content:
        return RerankResult(False, "malformed_response", candidates[:3], raw_content=content,
                             prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                             latency_s=latency, http_status=resp.status_code)

    try:
        idxs = _parse_ranking(content, len(candidates))
    except Exception:
        return RerankResult(False, "malformed_response", candidates[:3], raw_content=content,
                             prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                             latency_s=latency, http_status=resp.status_code)

    ranked = [candidates[i - 1] for i in idxs]
    return RerankResult(True, None, ranked, raw_content=content,
                         prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                         latency_s=latency, http_status=resp.status_code)
