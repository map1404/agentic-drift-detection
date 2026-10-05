# Agentic AI for Automated Model Drift Detection

A multi-agent system for detecting and diagnosing model drift in an
e-commerce demand-forecasting scenario, built from `ai_build_prompt.md`
and grounded in `Literature_Review_Final.docx`.

## ⚠️ Read this first: data-source substitution

The build spec calls for the real Kaggle **M5 Forecasting – Accuracy**
dataset. This codebase was built inside a sandboxed environment whose
network egress allow-list does **not** include `kaggle.com` (nor any
legitimate mirror — M5 is Kaggle competition data, and no GitHub repo
found during this build actually redistributes the CSVs; they all expect
the user to download it themselves). Rather than fabricate results against
data that was never touched, `src/data/load_m5.py` ships **two** paths:

- `build_demand_price_panel(data_dir="data/raw", ...)` — uses the **real**
  M5 CSVs if you place `calendar.csv`, `sell_prices.csv`, and
  `sales_train_validation.csv` (downloaded from Kaggle yourself) under
  `data/raw/`. This is a real, tested code path (`_build_from_real_m5`),
  just not one that has run against real data in this environment.
- Otherwise it **falls back to `build_synthetic_m5_like_panel`**, a
  schema-faithful generator (same columns, hierarchy, weekly/annual
  seasonality, SNAP flags, promo-price dips, Poisson sparsity) — and warns
  loudly every time it does so.

**Every number in this README, and every CSV under `outputs/`, was
produced against the synthetic fallback**, not real M5 data. Treat all
reported rates as a demonstration that the architecture and evaluation
methodology work end-to-end and are internally consistent — not as a
claim about performance on real retail data. If you run this with real
M5 CSVs in `data/raw/`, you will get different (and more meaningful)
numbers; the code path is ready for that.

## What's implemented

| Component | File | Status |
|---|---|---|
| Data panel (real M5 + synthetic fallback) | `src/data/load_m5.py` | ✅ both paths implemented; only synthetic path exercised here |
| Sentinel (JS divergence, L∞, adaptive window, within-slice scan) | `src/agents/sentinel.py` | ✅ |
| Investigator (vectorized divergence-removal slicing) | `src/agents/investigator.py` | ✅ |
| Synthesizer (rule-based + 3 LLM backends) | `src/agents/synthesizer.py` | ✅ rule-based fully working; LLM backends implemented, partially verified (see below) |
| Demand model (LightGBM) + SHAP-native baseline | `src/model/demand_model.py` | ✅ |
| Hierarchy-aware scoring | `src/agents/hierarchy.py` | ✅ |
| Drift injection (sudden/gradual/intermittent) | `src/drift/inject_drift.py` | ✅ |
| Base pipeline orchestration | `src/pipeline.py` | ✅ |
| Agentic investigator (deterministic best-first) | `src/agents/agentic_investigator.py` | ✅ working; see honest results below |
| Agentic investigator (LLM tool-calling loop) | `src/agents/agentic_investigator.py` | ✅ implemented for Anthropic/Groq/xAI; only Anthropic's request format could be verified past the auth boundary here (see below) |
| Evaluation harness (Wilson CIs, t-intervals) | `src/evaluation/evaluate.py`, `evaluate_agentic.py` | ✅ |

## What's actually "agentic" here

Most of this pipeline (`src/pipeline.py`, `DriftDetectionPipeline`) is a
**fixed sequence of function calls**: Sentinel → Investigator →
Synthesizer. It is a clean, modular monitoring pipeline. It is **not**
"agentic AI" in the sense the literature (Wooldridge, 2020; LangGraph /
AutoGen-style frameworks) uses the term, and this README says so plainly
rather than overselling it, as most projects claiming "agentic AI" do.

The one genuinely agentic component is `AgenticInvestigator`
(`src/agents/agentic_investigator.py`): a controller that *decides* which
slice **combinations** are worth testing (something the base Investigator
structurally cannot do, since it only ever scores one column at a time),
and stops once nothing improves. Two controllers are implemented:

- a **deterministic best-first search** (default, no API key needed), and
- an **LLM tool-calling agent loop** (opt-in; Anthropic/Groq/xAI backends),
  which gives the model `score_combination` / `submit_answer` tools and
  lets it decide what to test and when to stop.

The Sentinel's core divergence math is **deliberately not agentic** — it
runs on every feature, every scan, and needs to stay cheap, deterministic
and auditable (see the EU AI Act framing in the literature review, §2.1).
"Agentic" is scoped to the decision of what to investigate next, not the
underlying arithmetic.

## Honest evaluation results (synthetic data, n as noted, 95% CI)

All CIs are Wilson score intervals for proportions, t-distribution
intervals for the mean. 200 items × 10 stores × 913 days synthetic panel;
24-month reference window, 6-month holdout pool that trial current-windows
are drawn from (per lit review §2.10). Full per-trial data in
`outputs/*.csv`.

### Main evaluation — n=20 trials/drift type (`outputs/main_evaluation_summary.csv`)

| Drift type | Detection rate | RCA strict | RCA hierarchical | Mean time-to-insight |
|---|---|---|---|---|
| Sudden | 100% [83.9, 100]% | 55% [34.2, 74.2]% | 70% [48.1, 85.5]% | 0.60s [0.50, 0.71] |
| Gradual | 100% [83.9, 100]% | 45% [25.8, 65.8]% | 55% [34.2, 74.2]% | 0.72s [0.58, 0.87] |
| Intermittent | 100% [83.9, 100]% | **0%** [0, 16.1]% | 40% [21.9, 61.3]% | 0.94s [0.78, 1.09] |

**What this does and doesn't support:** sudden vs. gradual RCA-strict CIs
overlap heavily (34–74% vs. 26–66%) — this run does **not** support a claim
that one drift type is easier to localize than the other. Intermittent
drift's RCA-strict rate is a genuine, n=20-backed **0%** (CI upper bound
16.1%) — precise localization of a short, sparse, single-item spike
structurally defeats a distributional-comparison approach, exactly as the
original project's own development history predicted, even though here
(unlike that project's specific run) **detection** of intermittent drift
was 100%, not 0% — because this codebase's `slice_fallback` path plus a
larger injected magnitude (3×) was enough to flag it globally; it is
*localizing* it precisely that fails, not noticing it happened.

### SHAP-baseline comparison — n=10 trials/drift type (`outputs/shap_baseline_comparison.csv`)

| Drift type | Investigator strict | Investigator hier. | SHAP-baseline strict | SHAP-baseline hier. |
|---|---|---|---|---|
| Sudden | 40% | 80% | **0%** | 10% |
| Gradual | 40% | 60% | **0%** | 30% |

This is the result the build spec explicitly expects and asks to be
reported honestly, not hidden: the SHAP-native (`pred_contrib`) baseline
is meaningfully weaker than the Investigator's own divergence-removal
slicing at localizing *distributional* drift — because SHAP explains
feature importance to *predictions*, not distribution-shift localization.
That's a structural mismatch, not a tuning problem.

### Agentic (combination) search — n=8 detected of 10 trials, 2D intersection drift (`outputs/agentic_intersection_summary.csv`)

Drift was injected on the intersection of two dimensions at once (e.g.
`cat_id='HOBBIES' AND state_id='CA'`) — a scenario the base Investigator
cannot name exactly by construction.

| Method | Exact match on both dims | Hierarchy-aware match on both dims |
|---|---|---|
| Base Investigator (best single dim) | 50% [21.5, 78.5]% | 87.5% [52.9, 97.8]% |
| Agentic Investigator (combination search) | 12.5% [2.2, 47.1]% | 50% [21.5, 78.5]% |

**This is reported honestly even though it complicates the narrative**:
on this randomized n=8 benchmark, the deterministic combination search
does *not* reliably beat the simpler base Investigator's single-best-answer
when scored strictly, and its own hierarchical-match rate is *lower* than
the base Investigator's (which only needs to be hierarchically related to
*either* true dimension, a lower bar than the combination search's
both-dimensions requirement). Inspecting `outputs/agentic_intersection_trials.csv`
shows the search does frequently converge on a *hierarchically plausible*
combination (e.g. finding `dept_id='HOBBIES_1'` — a child of the true
`cat_id='HOBBIES'` — paired with the correct `state_id`), which is a real
and useful signal, just not one that beats the base Investigator's answer
at this sample size and with the current seed-frontier / efficiency-ranking
design. Earlier in development (see `docs/project_changelog.md` Phase 3)
a *hand-constructed* single intersection case was solved correctly; this
n=8 randomized benchmark shows that result does not generalize as
reliably as hoped. Flagged as genuine future work: a wider seed frontier,
LLM-guided search (see below), or a different combination-ranking rule
are the most promising next steps, not further hand-tuning of the
efficiency formula.

## LLM backend connectivity — what was and wasn't verified

No API keys are present in this build environment. What *was* checked:

- **Anthropic** (`api.anthropic.com`): reachable. A request with an
  invalid key returns **HTTP 401** `authentication_error` — meaning the
  request reached the server and passed schema validation. This is real
  evidence the Claude backend's request format (`ClaudeSynthesizer`,
  `LLMAgenticInvestigator(backend="anthropic")`) is correct, and the
  Synthesizer's fallback-to-rule-based path was verified end-to-end (see
  the caught `HTTPError` and `backend="anthropic_claude_fallback_rule_based"`
  in the smoke test).
- **Groq** (`api.groq.com`) and **xAI** (`api.x.ai`): **not reachable** —
  blocked at the sandbox's network-policy level (HTTP 403, "Host not in
  allow-list"), before the request ever reaches their servers. Those
  backends are implemented to the OpenAI-compatible tool-calling spec but
  could not be schema-checked here, only Anthropic's could.

"The request format was validated against the live API up to the auth
boundary" is the precise, honest claim here — not "this works." Nobody
should read a passing 401 as proof the JSON round-trips correctly on a
real key; it's evidence the format and endpoint are right, nothing more.

## Running it

```bash
pip install -r requirements.txt
python -c "
from src.data.load_m5 import build_demand_price_panel
df = build_demand_price_panel(n_last_days=913, max_items=200)
"
python src/evaluation/evaluate_agentic.py   # writes outputs/agentic_intersection_*.csv
```

To use real M5 data instead of the synthetic fallback: download
`calendar.csv`, `sell_prices.csv`, `sales_train_validation.csv` from the
Kaggle M5 Forecasting – Accuracy competition into `data/raw/`, then call
`build_demand_price_panel()` as normal — the real-data path activates
automatically.

## Business framing (secondary, proportionate — see `docs/defense_qa.md`)

Positioned as a **complementary root-cause layer** on top of existing
commercial monitoring tools (Evidently AI, Arize AI, Fiddler AI, WhyLabs,
Arthur AI, AWS SageMaker Model Monitor), which are strong on
detection/dashboards and consistently weak on automated root-cause
diagnosis — not a replacement for their infrastructure, scale, or
compliance certifications. See the literature review §2.5 for the
industry evidence this argument rests on (67% of organizations reporting
a critical drift-related incident; ~35% error-rate growth on unmonitored
models).

## Known limitations (see `docs/project_changelog.md` and `docs/defense_qa.md` for the full list)

- All reported numbers are against **synthetic**, not real M5, data (see
  top of this README).
- Intermittent-drift **localization** (not detection) genuinely fails
  (0% strict RCA accuracy, n=20-backed).
- The agentic combination search does not yet reliably outperform the
  base Investigator on a randomized 2D-intersection benchmark, despite
  working on a hand-constructed case — see the evaluation section above.
- Groq/xAI LLM backends are implemented but unverified past schema
  construction in this environment (network-blocked).
- Sample sizes (n=20 main, n=10 SHAP/agentic) are appropriate for a
  Master's thesis, not a journal submission; several CIs are wide enough
  that comparative claims between conditions are not supportable — this
  is stated explicitly wherever it applies, rather than glossed over.
# agentic-drift-detection
