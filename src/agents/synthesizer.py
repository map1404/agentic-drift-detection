"""Synthesizer — explanation agent.

Combines a DriftAlert + top slice candidates + calendar context into a
natural-language narrative and recommended actions. Default is rule-based
(no LLM, no external dependency, always works). Optional LLM-backed
subclasses are exact drop-in replacements: same `generate_report(...)`
signature, same return shape, so swapping backends never changes calling
code, and every LLM path falls back to the rule-based path on any error.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class DriftReport:
    feature: str
    is_drift: bool
    top_slices: list
    narrative: str
    recommended_actions: list
    backend: str = "rule_based"


class Synthesizer:
    """Default rule-based synthesizer. No API key, no network, always works."""

    def generate_report(self, alert, candidates, calendar_context: dict | None = None) -> DriftReport:
        calendar_context = calendar_context or {}
        if not candidates:
            narrative = (
                f"Drift detected in '{alert.feature}' (JS={alert.js_divergence:.4f}, "
                f"L-inf={alert.l_inf_distance:.4f}) but no localized slice explained a "
                f"meaningful share of it — likely a broad, cross-cutting shift rather "
                f"than a localized one."
            )
            actions = ["Investigate global pipeline changes (schema, ingestion timing).",
                       "Check for a platform-wide event (site-wide sale, outage) around this window."]
            return DriftReport(alert.feature, alert.is_drift, [], narrative, actions)

        top = candidates[0]
        event_note = ""
        if calendar_context.get("promo_overlap"):
            event_note = (
                f" This window overlaps a known promotional/event period "
                f"({calendar_context['promo_overlap']}), which is consistent with a "
                f"demand-side (not data-quality) explanation."
            )
        elif calendar_context.get("looks_like_pipeline_issue"):
            event_note = (
                " No calendar event overlaps this window, which is more consistent with "
                "a data-quality or pipeline issue than a genuine demand shift."
            )

        narrative = (
            f"Drift detected in '{alert.feature}' (JS={alert.js_divergence:.4f}, "
            f"L-inf={alert.l_inf_distance:.4f}). The strongest localized explanation is "
            f"{top.slice_col}='{top.slice_val}' (divergence-removal score={top.score:.4f}, "
            f"covering {top.coverage_frac:.1%} of the current window)."
            f"{event_note}"
        )
        actions = []
        if calendar_context.get("promo_overlap"):
            actions.append(f"Verify against the marketing/promotions calendar for {top.slice_col}='{top.slice_val}'.")
        else:
            actions.append(f"Check pipeline/ingestion logs for {top.slice_col}='{top.slice_val}' around this window.")
        actions.append(f"Compare against the SHAP-based localization baseline for a second opinion on {alert.feature}.")
        if len(candidates) > 1:
            second = candidates[1]
            actions.append(
                f"Secondary candidate to rule out: {second.slice_col}='{second.slice_val}' "
                f"(score={second.score:.4f})."
            )
        return DriftReport(alert.feature, alert.is_drift, candidates, narrative, actions)


class _LLMSynthesizerBase(Synthesizer):
    """Shared scaffolding for LLM-backed synthesizers. Never hard-fails:
    catches every exception around the API call and falls back to the
    rule-based path, printing one clear log line explaining the fallback."""

    system_prompt = (
        "You are the Synthesizer agent in a model-drift monitoring system. "
        "You are given a statistical drift alert and the top candidate slices "
        "that a root-cause agent identified as explaining it. Write a concise, "
        "operationally useful explanation and a short list of recommended next "
        "actions for an ML engineer. "
        "Respond with STRICT JSON ONLY, no markdown fences, no preamble, in "
        "exactly this shape: "
        '{"narrative": "...", "recommended_actions": ["...", "..."]}'
    )

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

    def _parse_json_response(self, text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        return json.loads(text.strip())

    def generate_report(self, alert, candidates, calendar_context: dict | None = None) -> DriftReport:
        try:
            narrative, actions = self._call_llm(alert, candidates, calendar_context)
            return DriftReport(alert.feature, alert.is_drift, candidates, narrative, actions, backend=self.backend_name)
        except Exception as e:  # noqa: BLE001 - deliberate: never hard-fail the pipeline
            print(f"[Synthesizer:{self.backend_name}] LLM call failed ({type(e).__name__}: {e}); "
                  f"falling back to rule-based synthesizer.")
            report = super().generate_report(alert, candidates, calendar_context)
            report.backend = f"{self.backend_name}_fallback_rule_based"
            return report

    def _call_llm(self, alert, candidates, calendar_context):
        raise NotImplementedError


class ClaudeSynthesizer(_LLMSynthesizerBase):
    backend_name = "anthropic_claude"

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

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


class GroqSynthesizer(_LLMSynthesizerBase):
    backend_name = "groq"

    def __init__(self, model: str = "llama-3.3-70b-versatile", api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")

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


class XAISynthesizer(_LLMSynthesizerBase):
    backend_name = "xai_grok"

    def __init__(self, model: str = "grok-2-latest", api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.environ.get("XAI_API_KEY")

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
