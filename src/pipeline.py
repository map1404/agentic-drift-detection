"""Orchestration for the base pipeline.

HONEST NOTE: this base pipeline (Sentinel -> Investigator -> Synthesizer,
a fixed sequence of function calls, no LLM by default, no autonomy) is NOT
"agentic AI" in the sense the literature (Wooldridge 2020; LangGraph /
AutoGen-style frameworks) uses the term. It is a clean, modular,
multi-component monitoring pipeline. The genuinely agentic component —
a controller making judgment calls about what to investigate next, not
just executing a fixed script — is `agents/agentic_investigator.py`
(see its own module docstring, and README.md's "what's actually agentic
here" section).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from agents.sentinel import Sentinel, DriftAlert
from agents.investigator import Investigator
from agents.synthesizer import Synthesizer, DriftReport


class DriftDetectionPipeline:
    def __init__(self, sentinel: Sentinel | None = None, investigator: Investigator | None = None,
                 synthesizer: Synthesizer | None = None):
        self.sentinel = sentinel or Sentinel()
        self.investigator = investigator or Investigator()
        self.synthesizer = synthesizer or Synthesizer()

    def run(
        self,
        df: pd.DataFrame,
        reference_window: tuple,
        current_window: tuple,
        slice_fallback: bool = True,
        fallback_slice_cols=("cat_id", "item_id"),
        calendar_context: dict | None = None,
    ) -> list[DriftReport]:
        alerts = self.sentinel.scan(df, reference_window, current_window)
        reports = []

        flagged_features = {a.feature for a in alerts if a.is_drift}

        for alert in alerts:
            effective_alert = alert
            if not alert.is_drift and slice_fallback:
                # global scan missed it — check within-slice before giving up
                found = None
                for slice_col in fallback_slice_cols:
                    within = self.sentinel.scan_within_slices(
                        df, alert.feature, slice_col, reference_window, current_window
                    )
                    if within:
                        found = within[0]
                        break
                if found is not None:
                    effective_alert = DriftAlert(
                        alert.feature, found.js_divergence, found.l_inf_distance, True,
                        reference_window, current_window, method=found.method,
                    )

            if not effective_alert.is_drift:
                continue

            candidates = self.investigator.investigate(df, effective_alert, top_k=5)
            report = self.synthesizer.generate_report(effective_alert, candidates, calendar_context)
            reports.append(report)

        return reports
