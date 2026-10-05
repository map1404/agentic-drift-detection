
---

## Phase 6 — Actually building it (this build)

**Trigger:** moved from planning/audit documents to an actual, runnable
build, following `ai_build_prompt.md` end to end in a fresh sandboxed
coding environment.

**Data-source constraint hit immediately:** the sandbox's network
egress allow-list does not include `kaggle.com`, and no GitHub repo found
via search actually redistributes the M5 CSVs (all of them expect the user
to download the data from Kaggle directly — appropriately, since
redistributing competition data outside Kaggle's own terms isn't something
to route around). Rather than block entirely or silently fabricate a
"real-data" result, `src/data/load_m5.py` was built with two paths: a real
M5 loader (`_build_from_real_m5`, written and ready, not exercised here)
and a schema-faithful synthetic generator that is used by default with a
loud warning. Every number in `README.md` is explicitly labeled synthetic
for this reason. This mirrors this project's own established norm (Phase 1
onward) of documenting workarounds rather than hiding them.

**Real, measured results this time** (not just an evaluation harness
description — it was actually run):

- Main evaluation (n=20/drift type): 100% detection across all three
  drift types; RCA-strict 55%/45%/0% (sudden/gradual/intermittent);
  RCA-hierarchical 70%/55%/40%. Sudden-vs-gradual CIs overlap heavily —
  not a defensible "one is easier" claim at this n. Intermittent's 0%
  strict RCA (CI upper bound 16.1%) reproduces the earlier project's
  diagnosed structural limitation (sparse single-item spikes defeat
  distributional comparison) — but *detection* of intermittent drift was
  100% here, not 0% as in the earlier project's run, because this build's
  slice-fallback path plus a larger injected magnitude was enough to flag
  it globally even though precise localization still fails. Different
  specific numbers from the earlier project's own run are expected and
  fine — different codebase, different synthetic data, different
  injection magnitudes — the *qualitative* finding (detection is easier
  than localization for this drift type) replicates.

- SHAP-baseline comparison (n=10/drift type): Investigator 40% strict /
  80% (sudden) and 60% (gradual) hierarchical vs. SHAP-native baseline's
  0% strict / 10-30% hierarchical. Confirms the build spec's expected
  finding: SHAP explains feature importance to predictions, not
  distribution-shift localization, and is a meaningfully weaker localizer
  here — reported as intended, not hidden.

- Agentic (combination) search on a randomized 2D-intersection benchmark
  (n=8 detected/10 trials): this is the most honestly complicated result
  of this build. The deterministic combination controller does **not**
  reliably beat the base Investigator's single-best answer here (12.5%
  vs. 50% exact-both-dims; 50% vs. 87.5% hierarchical-both-dims) — even
  though, separately, a hand-constructed single intersection case
  (`cat_id='HOBBIES' AND state_id='CA'`) WAS solved correctly (converging
  on a hierarchically-nested `dept_id='HOBBIES_2' AND state_id='CA'`)
  during initial development. The randomized benchmark shows that
  hand-picked success does not generalize as reliably as hoped. One real
  bug was found and fixed along the way: the initial seed frontier kept
  only the top-3 distinct slice columns by efficiency score, which
  sometimes excluded the actual true dimension from ever being tested in
  combination (raising `top_k_seed` from 3 to 5 improved but did not
  eliminate the shortfall — exact-both-dims went from 0% to 12.5%,
  hierarchical-both-dims from 0% to 50%). Flagged as genuine future work
  (wider seed diversity, LLM-guided search, or a different combination
  ranking rule), not swept under the rug.

- LLM connectivity: same asymmetric pattern as the earlier project's
  finding, reproduced independently in this build's sandbox — Anthropic's
  endpoint is reachable and returns a real 401 (schema validated, auth
  failed) with no key present; Groq and xAI are blocked at the network
  policy level (403, "Host not in allow-list") before the request ever
  reaches them. The Synthesizer's fallback-to-rule-based path was verified
  end-to-end with a deliberately invalid key, producing
  `backend="anthropic_claude_fallback_rule_based"` and one clear log line,
  exactly as designed.

**What this phase did NOT do:** implement the Slice Finder head-to-head
comparison or the DriftGuard reimplementation from the earlier project's
priority stack (out of scope for this build pass, and both still require
either the real Slice Finder repo or a from-paper reimplementation effort
this pass didn't reach), and did not build the competitive-landscape table
or ROI model (business section) — flagged as the natural next increment.

## Current priority stack (updated)

**Technical:**
1. Slice Finder head-to-head comparison — still not started
2. DriftGuard reimplementation from the published paper — still not started
3. Widen the agentic controller's seed frontier / try LLM-guided search to
   close the gap against the base Investigator on the intersection
   benchmark — newly identified this phase, not yet started
4. Re-run the full evaluation suite against real M5 data once available
   outside this sandbox — newly identified this phase

**Business (secondary):**
5. Competitive landscape table vs. real commercial tools — not yet started
6. Quantified ROI model — not yet started

## Deliverables produced this phase
- Full working codebase under `src/` (data, agents, model, drift,
  evaluation, pipeline)
- `outputs/main_evaluation_summary.csv`, `outputs/main_evaluation_trials.csv`
- `outputs/shap_baseline_comparison.csv`
- `outputs/agentic_intersection_summary.csv`, `outputs/agentic_intersection_trials.csv`
- `outputs/demand_model.joblib` (trained LightGBM model)
- Updated `README.md` with honest, run-verified results
- This changelog update
- `docs/defense_qa.md`
