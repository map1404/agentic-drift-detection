"""Task 5, v2: an Groq re-ranker that can actually see MORE than the
deterministic pipeline's single pre-gated top-5, not just reorder it.

v1 (`groq_reranker.py`) gave Groq exactly the same 5 numbers the
deterministic `_detect_and_localize` already committed to (whichever of
coarse-within-slice or burst "won" a hardcoded score-bar gate BEFORE Groq
ever saw anything) -- and measurably, it mostly just agreed with them
(96% of trials, see Task 5 writeup in CLAUDE_CODE_HANDOFF.md). Re-sorting
the same 5 numbers with an LLM has no real information to work with; this
module instead gives Groq the UNION of three deterministic methods' raw
top-5s, un-gated, and the methodological context (documented in
investigator.py's module docstring) a human analyst would get, then lets
the model's own judgment do what the hardcoded gate did before -- decide
which method's evidence to trust for THIS trial.

Methods merged into one candidate pool:
  - "coarse": `Investigator().investigate()` default (cat/dept/state/store,
    within-slice JS divergence) -- the original deterministic top-5.
  - "item_divergence": the SAME within-slice JS divergence method, but
    scored at item_id granularity (`Investigator(slice_columns=["item_id"])`)
    -- excluded from the deterministic default specifically because it's
    noisy (see investigator.py), but not useless; worth showing, not hiding.
  - "item_burst": `Investigator().investigate_bursts()` -- the short-spike
    statistic, item-level, also normally gated.

This is still an honest comparison against the "simplistic approach": the
deterministic baseline it's measured against is the EXACT SAME
`_detect_and_localize` output as Task 5 and as Tasks 0/1/3/4 -- only what
Groq gets to see changed, not what the deterministic pipeline itself does
or outputs on its own.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import requests

from .groq_reranker import GROQ_API_URL  # same endpoint, reused

GROQ_MODEL_DEFAULT = "openai/gpt-oss-20b"


@dataclass
class ExtCandidate:
    slice_col: str
    slice_val: str
    coverage_frac: float
    coarse_score: float | None = None
    item_divergence_score: float | None = None
    burst_score: float | None = None
    # back-reference to whichever original SliceCandidate objects this came
    # from, keyed by source, so callers can recover the exact object for
    # metric scoring without re-deriving it
    sources: dict = field(default_factory=dict)


def build_expanded_pool(df, alert, coarse_investigator, item_investigator, top_k: int = 5,
                         include_coarse: bool = True, include_item: bool = True) -> list[ExtCandidate]:
    """Union of coarse top-k, item-level-divergence top-k, and item-level
    burst top-k, deduplicated by (slice_col, slice_val). `coarse_investigator`
    should use the default COARSE_SLICE_COLUMNS; `item_investigator` should
    be constructed with `slice_columns=["item_id"]`.

    `include_coarse`/`include_item` let a caller build a NARROWER pool --
    e.g. item-only for intermittent (coarse is structurally irrelevant
    there, and empirically just adds noise the model can be distracted by;
    see Task 5 v2 tuning notes) or coarse-only for sudden/gradual (adding
    the noisy item-level pool there measurably made things worse on the
    held-out tuning seed, not better)."""
    coarse = coarse_investigator.investigate(df, alert, top_k=top_k) if include_coarse else []
    item_div = item_investigator.investigate(df, alert, top_k=top_k) if include_item else []
    item_burst = item_investigator.investigate_bursts(df, alert, top_k=top_k) if include_item else []

    pool: dict[tuple, ExtCandidate] = {}

    def upsert(c, field_name):
        key = (c.slice_col, str(c.slice_val))
        if key not in pool:
            pool[key] = ExtCandidate(slice_col=c.slice_col, slice_val=str(c.slice_val), coverage_frac=c.coverage_frac)
        ext = pool[key]
        setattr(ext, field_name, c.score)
        ext.sources[field_name] = c

    for c in coarse:
        upsert(c, "coarse_score")
    for c in item_div:
        upsert(c, "item_divergence_score")
    for c in item_burst:
        upsert(c, "burst_score")

    # stable, readable ordering: coarse first (by coarse score), then the
    # item-level remainder (by whichever item score is present)
    coarse_keys = [(c.slice_col, str(c.slice_val)) for c in coarse]
    ordered = [pool[k] for k in coarse_keys]
    remainder = [v for k, v in pool.items() if k not in set(coarse_keys)]
    remainder.sort(key=lambda e: max(e.item_divergence_score or 0, (e.burst_score or 0) / 100.0), reverse=True)
    ordered += remainder
    return ordered


DOMAIN_CONTEXT = (
    "Methodological context from prior calibration on this dataset (true in "
    "general, not specific to this one alert -- treat as background "
    "knowledge, the same way a human analyst would be briefed before "
    "judging):\n"
    "- `coarse_score`: within-slice Jensen-Shannon divergence at the "
    "department/store/state/category level (few, large-sample candidates). "
    "Typical range ~0-0.2. The most stable signal.\n"
    "- `item_divergence_score`: the SAME divergence statistic, but computed "
    "per individual item. Typical range similar to coarse, but individual "
    "items carry much more natural, non-drift demand volatility (products "
    "get delisted, ramp up, etc.) -- a high item_divergence_score ALONE is "
    "weak evidence, because ordinary item-level noise routinely produces "
    "scores this large even with no real anomaly.\n"
    "- `burst_score`: looks specifically for an unusually large SHORT (1-2 "
    "day) spike in an item's daily total, relative to its own history. "
    "Typical range can be tens to low hundreds; NOT comparable in magnitude "
    "to the other two scores. A high burst_score is more specific evidence "
    "of a short, deliberate shock than a high item_divergence_score, "
    "because it requires a concentrated spike shape rather than general "
    "noisiness.\n"
    "- A candidate may have one, two, or all three scores computed; a "
    "missing score is reported as null, not zero.\n"
)

DECISION_RULE_MIXED = (
    "\nDECISION RULE (apply this; item-level scores are seductive but usually "
    "wrong): start from the single best coarse_score candidate as your "
    "default #1 pick. Only replace it with an item-level candidate (by "
    "item_divergence_score or burst_score) if that item's evidence is NOT "
    "just the single biggest number in the list, but is dramatically "
    "isolated from every other item-level candidate too (e.g. burst_score "
    "> 60 and clearly larger than the next item's burst_score, or "
    "item_divergence_score more than ~3x the next-highest item's score) -- "
    "a merely-highest-of-many noisy item scores is NOT enough, because "
    "ordinary non-drift item volatility routinely produces the single "
    "largest number in any list of many items by chance alone.\n"
)

DECISION_RULE_ITEM_ONLY = (
    "\nDECISION RULE: every candidate below is an individual item. These "
    "are shown INSTEAD OF coarse candidates because the best coarse "
    "(dept/store/state/category) score for this specific alert was too "
    "weak to trust (below this pipeline's own significance bar) -- NOT "
    "because the drift type is known in advance to be item-level; it may "
    "simply be a case with weak signal everywhere. Prefer burst_score over "
    "item_divergence_score when both are present (a short, "
    "concentrated spike is more specific evidence than general "
    "distributional noise). Pick the candidate that is a clear OUTLIER "
    "relative to the REST OF THIS LIST (e.g. its burst_score is much "
    "larger than the second-highest burst_score here), not merely "
    "whichever happens to be numerically largest by a small margin --  "
    "ordinary items routinely produce the single largest score in a list "
    "purely by chance, so a narrow win is weak evidence.\n"
)


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


@dataclass
class RerankResultV2:
    used_groq: bool
    fallback_reason: str | None
    ranked_ext: list = field(default_factory=list)  # list[ExtCandidate], len<=3
    raw_content: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    http_status: int | None = None
    remaining_tokens: int | None = None
    reset_tokens_s: float | None = None


def _parse_duration(s: str | None) -> float | None:
    """Parse Groq's `x-ratelimit-reset-*` header format, e.g. "2.085s",
    "577ms", or "1m26.4s", into seconds. Returns None if unparseable/absent.

    BUG FIXED HERE (caused a real ~9.6-hour stuck sleep mid-session, see
    conversation): the original version checked `"m" in s` to detect a
    minutes component, but "577ms" also contains the letter "m" (it's
    MILLISECONDS, not minutes) -- `"577ms".split("m", 1)` gave
    `("577", "s")`, and the old code computed `577 * 60 = 34620s`. Must
    check for the "ms" suffix FIRST, before any minutes-splitting logic.
    """
    if not s:
        return None
    try:
        s = s.strip()
        if s.endswith("ms"):
            return float(s[:-2]) / 1000.0
        total = 0.0
        if "m" in s:
            m_part, s_part = s.split("m", 1)
            total += float(m_part) * 60
        else:
            s_part = s
        s_part = s_part.rstrip("s")
        if s_part:
            total += float(s_part)
        return total
    except (ValueError, IndexError):
        return None


def _rate_limit_headers(resp) -> tuple[int | None, float | None]:
    remaining = resp.headers.get("x-ratelimit-remaining-tokens")
    reset = resp.headers.get("x-ratelimit-reset-tokens")
    return (int(remaining) if remaining is not None else None, _parse_duration(reset))


def _parse_ranking(content: str, n: int) -> list[int]:
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
    if any(i < 1 or i > n for i in idxs):
        raise ValueError(f"index out of range 1..{n}: {idxs}")
    if len(set(idxs)) != len(idxs):
        raise ValueError(f"duplicate index: {idxs}")
    return idxs[:3]


def groq_rerank_v2(
    alert_context: dict,
    pool: list[ExtCandidate],
    model: str = GROQ_MODEL_DEFAULT,
    reasoning_effort: str = "low",
    max_tokens: int = 900,
    timeout: float = 25.0,
) -> RerankResultV2:
    if not pool:
        return RerankResultV2(False, "malformed_response", [])

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return RerankResultV2(False, "no_api_key", pool[:3])

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": _build_prompt(alert_context, pool)}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "reasoning_effort": reasoning_effort,
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
        return RerankResultV2(False, "timeout", pool[:3], latency_s=time.time() - t0)
    except requests.exceptions.RequestException as e:
        return RerankResultV2(False, "network_error", pool[:3], raw_content=str(e), latency_s=time.time() - t0)

    latency = time.time() - t0
    remaining_tokens, reset_tokens_s = _rate_limit_headers(resp)

    if resp.status_code in (401, 403):
        return RerankResultV2(False, "auth_error", pool[:3], raw_content=resp.text[:500], latency_s=latency,
                               http_status=resp.status_code, remaining_tokens=remaining_tokens, reset_tokens_s=reset_tokens_s)
    if resp.status_code == 429:
        # Groq has TWO separate 429 causes that look identical at the HTTP
        # level but need very different handling: a per-MINUTE burst limit
        # (worth a short backoff-and-retry -- see run_trials_v2's adaptive
        # pacing) vs. a per-DAY quota (TPD) -- discovered the hard way when
        # ~45 calls in a row all 429'd despite x-ratelimit-remaining-tokens
        # showing a healthy per-minute bucket the whole time. The per-minute
        # headers say NOTHING about the daily cap; only the error body does.
        # A daily-quota 429 should make the CALLER stop immediately, not
        # retry for minutes -- the per-minute bucket looks fine forever and
        # every retry just burns more of a budget that isn't coming back
        # today.
        reason = "rate_limit"
        try:
            err = resp.json().get("error", {})
            if err.get("code") == "rate_limit_exceeded" and "per day" in err.get("message", "").lower():
                reason = "daily_quota_exceeded"
        except (ValueError, json.JSONDecodeError):
            pass
        return RerankResultV2(False, reason, pool[:3], raw_content=resp.text[:500], latency_s=latency,
                               http_status=resp.status_code, remaining_tokens=remaining_tokens, reset_tokens_s=reset_tokens_s)
    if resp.status_code != 200:
        return RerankResultV2(False, "http_error", pool[:3], raw_content=resp.text[:500], latency_s=latency,
                               http_status=resp.status_code, remaining_tokens=remaining_tokens, reset_tokens_s=reset_tokens_s)

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        return RerankResultV2(False, "malformed_response", pool[:3], raw_content=resp.text[:500], latency_s=latency,
                               http_status=resp.status_code, remaining_tokens=remaining_tokens, reset_tokens_s=reset_tokens_s)

    if not content:
        return RerankResultV2(False, "malformed_response", pool[:3], raw_content=content,
                               prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                               latency_s=latency, http_status=resp.status_code,
                               remaining_tokens=remaining_tokens, reset_tokens_s=reset_tokens_s)

    try:
        idxs = _parse_ranking(content, len(pool))
    except Exception:
        return RerankResultV2(False, "malformed_response", pool[:3], raw_content=content,
                               prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                               latency_s=latency, http_status=resp.status_code,
                               remaining_tokens=remaining_tokens, reset_tokens_s=reset_tokens_s)

    ranked = [pool[i - 1] for i in idxs]
    return RerankResultV2(True, None, ranked, raw_content=content,
                           prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                           latency_s=latency, http_status=resp.status_code,
                           remaining_tokens=remaining_tokens, reset_tokens_s=reset_tokens_s)
