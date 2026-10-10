
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

## Phase 7 — Cross-dataset transfer to Corporación Favorita (2026-10-07)

**Question:** does the M5-built pipeline (and the LLM tool-calling
investigator) transfer to a second retail dataset without re-tuning?

### Setup
- M5 state frozen first: annotated git tag `m5-frozen` on commit `98a9635`
  (thresholds, scoring, LLM prompt/tools as validated on M5).
- `src/data/load_favorita.py` builds the M5 panel schema from Kaggle
  "favorita-grocery-sales-forecasting". Documented choices: `dept_id` = family
  (33); `cat_id` = perishable group from `items.perishable` (verified constant
  within every family, so a true hierarchy; using family for both levels would
  make cat and dept slices identical rows); `store_id` = store_nbr (54);
  `state_id` = Ecuadorian province (16); negative unit_sales (returns, 423
  rows) clipped to 0; zero-sales days, which Favorita omits, zero-filled on the
  (item, store) x date grid of pairs with any sale in the window (62% of rows);
  `event_flag` from holidays_events (National/Regional/Local, not transferred,
  excl. Work Day; 9.1% of store-days); extra `onpromotion` column (unused).
  Neutral fills: `sell_price`=1.0, `revenue`=sales, `snap`=0. Same window/sample
  logic as M5: last 913 days (2015-02-15..2017-08-15), 200//33 = 6 items per
  family -> 181 items (4 families have fewer), 6.64M rows.
- `src/agents/hierarchy.py` now takes a per-dataset spec (`HIERARCHY_SPECS`);
  M5 behaviour verified identical on all 49,284 M5 slice-value pairs.
- Prompt audit (agent prompt + tool descriptions): 3 M5-specific hits, all in
  the system prompt -- "(Walmart M5 sample)", "items named
  <dept_id>_<3-digit number> (e.g. FOODS_3_NNN)", and "daily unit sales"
  (Favorita sales are fractional for weighed items). With the user's approval
  (option B) the first two became per-dataset fields; the M5 rendering was
  verified byte-identical against all 120 M5 validation prompts. "unit sales"
  left as is. Tools unchanged.
- Protocol identical to M5: n=20 per drift type, seeds 42 and 123, 200-day
  pool, 60-day rolling reference, 30-day window, same injectors.

### (a) Frozen deterministic pipeline: transfers poorly
Strict top-1 2.5% [1, 7] (M5: 20%), hier top-1 13% [8, 21] (M5: 50%), strict
top-3 15% [10, 22] (M5: 48%); intermittent 0/40 strict. It never ranks an
item first although 61/120 true slices are items; its top answers are
dominated by sparse single-item families (BOOKS, HOME APPLIANCES) and store
52, which opened during the window (organic ramp from zero).

### (c) Deterministic pipeline re-tuned on Favorita seed 7 only
Grid: slice set {coarse, coarse+item} x COARSE_SCORE_BAR {0..0.05, inf} x
BURST_SCORE_BAR {10..250, inf}; pre-declared rule: max pooled strict top-1 on
seed 7, ties -> hier top-1 -> strict top-3 -> fewest changes
(outputs/favorita_tuning_grid_seed7.csv). Changed: COARSE_SCORE_BAR
0.005 -> inf and BURST_SCORE_BAR 60 -> 10 (outputs/favorita_tuning_changes.csv),
i.e. effectively "answer with the item burst ranking". Both values sit at the
edge of the grid. Not tuned: Sentinel thresholds, min_slice_size, fallback.
On seeds 42/123 vs (a), paired McNemar: strict top-1 9% vs 2.5% (p=0.04),
strict top-3 35% vs 15% (p=0.0002), but hier top-1 unchanged (14% vs 13%).
All of the gain is intermittent (strict top-3 0% -> 78%); sudden hier top-1
fell 18% -> 5% (p=0.125). The same coarse-vs-item trade-off seen on M5.

### False-alarm test, (a), 50 drift-free windows
Global Sentinel: 0/50 on every feature. Full pipeline (global scan +
within-slice fallback): **50/50 = 100% [93, 100]** -- every alarm via the
fallback. M5's saved result is identical (50/50), so the "100% detection" in
drift trials on BOTH datasets does not separate drift from no drift. This
pre-existing limitation was not headlined in earlier write-ups.

### (b) Frozen LLM agent (gpt-oss-120b via Cerebras, temp 0, max 10 calls)
120 trials, 118 answered by the agent; 2 fallbacks (`no_tool_call`, gradual)
excluded from agent rates. Config fingerprinted (outputs/llm_agent/
FROZEN_fav_val.sha256) and unchanged for the whole run. Infrastructure notes:
the Cerebras free trial ended mid-run (HTTP 402 from 2026-10-09 01:31) after
101 trials; the last 19 (all seed 123 intermittent) were run on 2026-10-09
with a new key for the same model. An earlier resume loop wasted quota by
starting trials it could not finish; cut-off trials are never recorded, so
results are unaffected.
Results: strict top-1 8% [4, 14], hier top-1 18% [12, 26], strict top-3 19%
[13, 28]. Paired vs (a) on the same 118 trials (exact McNemar): strict top-1
8% vs 2.5% (p=0.11), hier top-1 18% vs 14% (p=0.30), strict top-3 19% vs 15%
(p=0.40) -- **no significant difference overall**. By drift type: sudden and
gradual about equal; intermittent better for the agent (strict top-3 15% vs
0%, p=0.03; strict top-1 10% vs 0%, p=0.125). Unlike on M5 (where the agent
was significantly worse than the pipeline on hier top-1), on Favorita it is
not worse -- but only because the frozen pipeline itself collapsed.
The M5 failure mode persists: the agent put an item first in 79/118 trials
(true slice is an item in 59), and one item (885543) was its top answer in 34
unrelated trials. ~9 tool calls per trial; 36/118 hit the 10-call budget;
5.3M total tokens.
Neither frozen arm transfers: compared with M5, (a) fell from 20% to 2.5%
strict top-1 and from 50% to 13% hier top-1; (b) fell from 19% to 8% and from
34% to 18%. The re-tuned pipeline (c) is the best Favorita arm on strict
top-3 (35%), entirely through intermittent drift.

### Outputs
outputs/favorita_trials_frozen.csv, favorita_trials_retuned.csv,
favorita_trials_llm_agent.csv, favorita_llm_tool_calls.csv, favorita_summary.csv,
favorita_mcnemar.csv, favorita_tuning_grid_seed7.csv,
favorita_tuning_changes.csv, favorita_retuned_config.json,
favorita_false_alarm_summary.csv, favorita_false_alarm_windows.csv,
favorita_panel_report.json.

## Phase 8 — Can an LLM agent draft a dataset adapter config? (Favorita)

### Pre-registration (written and committed 2026-10-10, BEFORE any run)

Measures configuration effort, not drift-localisation accuracy. The drift
pipeline, thresholds, agent prompts and the M5/Favorita adapters
(load_m5.py, load_favorita.py, hierarchy.py) are not modified.

SUPPORTED if: hierarchy edge F1 >= 0.9 in BOTH conditions (named and
obscured); correct bin path chosen in >= 4 of 5 repeats; downstream drift
accuracy within CI overlap of the hand-written config; manual edits needed to
convert the config <= 3.
REFUTED if: hierarchy F1 < 0.9 in the named condition, or downstream accuracy
clearly worse than the hand-written config.
INTERPRETATION: a pass in the named condition alone is not evidence of
inference (the model may have seen Favorita). Only a pass in the obscured
condition counts, and even then it is partial evidence, since value
distributions can still be recognised.
LIMITATION: Favorita is a public Kaggle dataset that may appear in the model's
training data, so results cannot fully separate inference from recall. Time
to build the hand-written adapter was not measured; effort is reported as
manual edit count only.

#### Addendum: operational definitions (fixed before any run)
- Model/provider: gpt-oss-120b on Cerebras, as for the frozen investigator
  agent; temperature 0, seed = repeat index (0-4), reasoning_effort medium.
  Run-to-run agreement at temperature 0 partly reflects decoding determinism,
  not only robustness.
- Tools: the eleven listed tools, each taking a `file` argument (the raw data
  is several files), plus `list_files` and a `submit_config` tool for the
  strict-JSON answer. Tools read only the condition's sandbox directory.
- Obscured condition: every distinct original column name maps to one opaque
  code across all files (so join keys still share a name, as in real data);
  family, city, state values and holiday descriptions/locale names map to
  opaque codes; files renamed file_1..file_5.
- Reference (scorer only), from load_favorita.py: ground-truth hierarchy
  edges family->item and state->store. family->perishable (cat_id) is a DESIGN
  JUDGEMENT: excluded from scoring, listed for human rating, as are true
  dependencies the adapter does not use (class, city, store type/cluster).
- Edge F1: predicted and reference edges are compared on transitive closure,
  restricted to {item, family, store, state}; a predicted path item->class->
  family therefore counts as item->family. "Hierarchy F1 >= 0.9 in a
  condition" = mean F1 over its 5 repeats.
- Flags: zero_inflated reference = True (zero-sales days are omitted from the
  raw file; the panel is 62% zeros once filled); fractional_sales = True.
- Bin path: the path the frozen Sentinel's adaptive_bin_edges takes for
  `sales` on the hand-written panel (computed in the scorer). "Correct in >= 4
  of 5 repeats" is judged per condition.
- Known data-quality issues for recall: negative sales, omitted zero-sales
  days, absent prices. Matched by pre-declared keyword rules; every match is
  written to a CSV for human audit.
- Conversion: a fixed, pre-written converter maps a config to the panel:
  dept_id = immediate parent of the item key, cat_id = parent of dept_id,
  state_id = immediate parent of the store key (after transitive reduction);
  negative-sales issue -> clip at 0; omitted-days issue -> zero-fill;
  sell_price=1.0, snap=0 and event_flag=0 always (not used by the drift
  pipeline's localisation). Obscured configs are translated back to original
  names by the scorer's private mapping (mechanical, not counted as an edit).
  Anything the converter cannot resolve (missing or ambiguous level, a
  claimed edge that is not a true hierarchy) needs a manual edit; each is
  logged and counted. Same item sample and window as the hand-written panel.
- Downstream: frozen pipeline, seed 42, n=20 per drift type, Wilson 95% CIs;
  hand-written panel on the same seed. "Within CI overlap" and "clearly worse"
  are judged on pooled strict top-1 and pooled hier top-1; "clearly worse" =
  the config's CI lies entirely below the hand-written CI on either.
- KNOWN WEAKNESS of the downstream criterion: the hand-written config scores
  only 2.5% strict / 13% hier top-1 on Favorita (Phase 7), so CI overlap is
  easy to achieve and is weak evidence of equivalence.
- Best-scoring repeat = highest hierarchy F1, ties -> flag accuracy -> issue
  recall -> lowest repeat index. Random repeat = numpy default_rng(2026)
  choice among the 5, drawn once per condition.
