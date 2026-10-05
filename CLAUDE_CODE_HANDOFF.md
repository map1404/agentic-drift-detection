# Context for Claude Code: drift-detection Investigator regression

## RESOLVED (2026-09-25) -- see bottom of file for the full writeup

The unresolved problem below has been investigated and partially fixed.
Short version: the sparsity hypothesis was refuted; the real cause was
genuine organic (non-drift) item-level demand churn in real M5 data
routinely exceeding the injected drift magnitude, combined with item_id
having 20-60x more candidates than any other slice column. Fix: exclude
item_id from the default candidate pool (`Investigator` now defaults to
`COARSE_SLICE_COLUMNS`). Verified end-to-end against the real cached panel:
RCA-strict went from the regression's 0/5/0% to 25/5/0% (sudden/gradual/
intermittent), hier-aware to 55/10/15%, restoring and modestly exceeding the
pre-regression baseline for non-item-level drift. Item-level localization
(~45% of trials, 100% of intermittent trials) is NOT solved and is now
documented as a genuine limitation, not silently dropped. Full account,
including four other fixes that were tried and measurably did NOT work, is
in `src/agents/investigator.py`'s module docstring ("REAL-SCALE REGRESSION"
section) -- read that before touching this file again.

## FOLLOW-UP (2026-09-26): improved gradual and intermittent specifically

Two further, verified changes on top of the above (full account in
investigator.py's docstring, "FOLLOW-UP" section):

1. `run_evaluation()`'s `reference_window` argument now sets the reference
   period LENGTH, not fixed dates -- each trial gets a reference window that
   ROLLS to end right before that trial's current window, instead of one
   fixed historical window shared by all trials. A fixed distant reference
   was picking up real secular growth trends (specific depts/stores
   genuinely growing ~50%+ over the ~2-year gap in real M5) as false drift,
   which especially hurt gradual (weaker average injected effect than
   sudden). Verified with a 60-day rolling reference: sudden 25%->30%
   strict, gradual 5%->30% strict.
2. Added `Investigator.investigate_bursts()`, a statistic for short (1-2
   day) spikes -- intermittent's actual shape, which whole-window JS
   divergence structurally dilutes away. Wired into
   `evaluate.py::_detect_and_localize` as a GATED fallback (only tried when
   the coarse answer is weak, only trusted if the burst score itself clears
   its own bar -- see `COARSE_SCORE_BAR`/`BURST_SCORE_BAR` in
   investigator.py) -- an unconditional "prefer burst whenever it clears a
   threshold" was tried first and regressed sudden from 30%->0% strict, so
   don't remove the coarse-weakness gate.

Combined, verified end-to-end via `run_evaluation()` on the real cached
panel: sudden 30%/75% (strict/hier), gradual 25%/50%, intermittent 10%/15%
(up from this file's earlier 25/5/0% strict, 55/10/15% hier). Gradual's hier
rate alone is a small, honest give-back from rolling-reference-alone's
55% -- a few weak-coarse gradual trials now get overridden by a wrong burst
guess.

### A real reproducibility bug was found and fixed along the way

While chasing down why a saved CSV showed different numbers than what was
reported in conversation, found that `evaluate.py` derived trial RNG seeds
from `hash(drift_type) % 1000` -- and Python randomizes string `hash()`
per PROCESS by default, so `seed=42` was silently NOT actually reproducible
across separate runs (confirmed directly: 3 back-to-back identical calls
gave 3 different RCA-strict rates for sudden, ranging 20-35%). Every
percentage in this file and in investigator.py's docstring was re-measured
after fixing this (`_stable_seed_component`, a real fixed hash via
`zlib.crc32`) and confirmed identical across repeated runs. If you ever see
a number here not match a fresh run of the same code on the same data,
suspect the seeding first, not the code -- and verify by running twice
before concluding anything changed.

`outputs/main_evaluation_summary_REAL.csv` and
`main_evaluation_trials_REAL.csv` reflect these latest numbers.


## Project
Multi-agent model-drift detection system (Sentinel -> Investigator ->
Synthesizer) for e-commerce demand forecasting, in this directory. Built
against real M5 Forecasting - Accuracy data in `data/raw/` (calendar.csv,
sell_prices.csv, sales_train_validation.csv).

## What already happened (so you don't have to rediscover it)

1. Built and ran the full pipeline against real M5 data. `Investigator`
   (in `src/agents/investigator.py`) was scoring candidate root-cause
   slices (which department/store/item explains a drift alert) using a
   "divergence removal" method: score a slice by how much removing it
   from the aggregate current-window distribution lowers divergence back
   toward the reference distribution.

2. Found this was badly broken on real data: it kept picking `FOODS_2`
   (or later `HOUSEHOLD_1`) as the answer almost regardless of where
   drift was actually injected. Confirmed with a controlled test: even
   with ZERO drift injected, the same department kept "winning" -- proof
   the score was measuring pre-existing structural differences between
   departments (real departments have very different baseline sales
   volumes), not the injected drift itself.

3. Fixed it by rewriting the default scoring to "within-slice" comparison:
   for each candidate slice value, compare ITS OWN reference-window
   distribution against ITS OWN current-window distribution, instead of
   comparing the whole aggregate with vs. without that slice. This is
   immune to the cross-department confound by construction, since it
   never compares one slice against another.

4. Verified the within-slice fix on a controlled synthetic benchmark
   matching real M5's department statistics: accuracy went from ~30%
   (old method) to ~85% (new method) at picking the correct department.

5. Also fixed a real, separate, confirmed bug in `src/data/load_m5.py`'s
   real-M5 loader: the price merge was joining on `(store_id, item_id)`
   only, not including `wm_yr_wk` (the week id), causing a ~130x row
   explosion (a full cross-product of every historical price against
   every day) that OOM-killed the process. Fixed by merging on
   `(store_id, item_id, wm_yr_wk)` instead. This fix is already applied
   and confirmed working at real scale.

6. Sent the user the two updated files (`sentinel.py` -- which also got
   a related, VERIFIED fix for a different bug: equal-width histogram
   binning was putting 95-99%+ of every department's mass in a single
   bin because M5 `sales` is small zero-inflated integers with rare huge
   spikes, so the JS-divergence math had almost no real signal to work
   with; replaced with an adaptive quantile/integer-native binning
   function `adaptive_bin_edges` -- and `investigator.py`, with the new
   within-slice default). Both fixes are real, tested in isolation, and
   already in this codebase.

## THE UNRESOLVED PROBLEM -- this is what you need to solve

The user re-ran the FULL evaluation harness (`src/evaluation/evaluate.py`,
`run_evaluation()`, n=20 trials per drift type: sudden/gradual/intermittent)
against the real cached M5 panel (`data/raw/real_m5_panel_cache.parquet`,
or rebuild via `build_demand_price_panel(data_dir='data/raw', ...)`) AFTER
both fixes landed, and got WORSE results across the board than before the
within-slice fix, not just for gradual drift as initially guessed:

| Drift type   | RCA-strict BEFORE within-slice fix | RCA-strict AFTER |
|--------------|-------------------------------------|-------------------|
| sudden       | 15%                                  | 0%                |
| gradual      | 5%                                   | 5% (same, still bad) |
| intermittent | 5%                                   | 0%                |

This is a regression on 2 of 3 drift types (sudden got WORSE, not just
gradual as hypothesized), at full real-data scale (~200 items x 10 stores
x 913 days, stratified by category). Detection rate stayed 100% throughout
-- this is purely a ranking/localization problem in the Investigator, not
a Sentinel detection problem.

## Working hypothesis to check first (not yet verified at this scale)

At full scale, individual items are very sparse (most M5 items sell 0-3
units/day). The within-slice method's `min_slice_size` default is 20 rows
-- with a 30-day current window, an `item_id`-level slice only has ~30
rows total, so the floor barely filters anything, and item-level
within-slice JS-divergence comparisons may be extremely noisy at that
sample size, potentially swamping the real signal. This was not caught
by the earlier controlled benchmark because that benchmark used a smaller,
cleaner synthetic panel that didn't reproduce real M5's sparsity at the
item level.

## What to actually do

1. Reproduce the regression yourself first -- run
   `src/evaluation/evaluate.py`'s `run_evaluation()` against the real
   cached panel and confirm you see the same numbers, so you're debugging
   against a live repro, not a hypothesis.
2. Investigate whether item-level sparsity is really the cause: for a few
   real trials, look at the actual `SliceCandidate` scores returned by
   `Investigator.investigate()` for the true slice vs. the top-ranked
   (wrong) slice, and check sample sizes / score magnitudes at each
   `slice_col` level (cat_id, dept_id, state_id, store_id, item_id).
3. Consider that the right fix might be per-slice-column-appropriate
   `min_slice_size` (item_id needs a much higher floor than cat_id, given
   the sparsity difference), or scoring only at coarser levels first and
   drilling down, or a different statistic entirely for very sparse
   slices (e.g. total-count change rather than distributional divergence,
   since JS divergence on near-empty histograms is inherently unstable).
4. Whatever fix you land on, VERIFY it end-to-end against the real cached
   panel using the actual evaluation harness before calling it fixed --
   don't just trust a small controlled mock, since that's exactly what
   missed this regression the first time.
5. Also worth double-checking: whether `method="removal"` (the legacy
   scoring, still available as a parameter on `Investigator.investigate()`)
   actually did better than these new real-data numbers suggest, or
   whether something else changed between runs (e.g. different random
   seeds picking different drift locations/magnitudes across the two
   evaluation runs being compared isn't a perfectly controlled A/B -- rerun
   both methods back-to-back with the same trial seeds if you want a
   clean comparison rather than relying on the two runs already reported).

## Files already changed and delivered
- `src/agents/sentinel.py` -- adaptive_bin_edges fix (verified good)
- `src/agents/investigator.py` -- within-slice default scoring (verified
  good on controlled benchmark, NOT yet verified good at full real scale --
  this is the open problem)

## Task 5 (2026-09-29): Groq re-ranking of the Investigator's top-5 -- does NOT clearly help

Added an optional re-ranking step: give Groq (`openai/gpt-oss-20b` -- this
account's key has no `llama-3.x` model, checked directly via `/v1/models`)
the Investigator's deterministic top-5 candidates + alert context (feature,
drift-type hint, JS/L-inf scores, per-candidate score/coverage), temperature
0, and ask for a ranked top-3. New files, don't touch the trusted
`evaluate.py`/`investigator.py`: `src/agents/groq_reranker.py` (single-call
wrapper, only reorders/subsets the given 5, any failure -- no key, network,
timeout, non-200, malformed JSON, out-of-range index -- returns
`used_groq=False` + an explicit `fallback_reason`, never silently) and
`src/evaluation/evaluate_groq_rerank.py` (calls the existing
`_detect_and_localize` unmodified to get the deterministic top-5, then feeds
it to Groq).

**GROQ_API_KEY verified working** with a direct, non-pipeline call (real
HTTP 200 + real content) -- this contradicts README.md's "LLM backend
connectivity" section, which says Groq is network-blocked; that's stale for
this environment. Also confirmed directly: `gpt-oss-20b` is a reasoning
model that spends `max_tokens` on a hidden `reasoning` field before
`content` -- a naive small `max_tokens` silently truncates to empty content
(`finish_reason="length"`). Fixed with `reasoning_effort="low"` +
`max_tokens=500`.

Ran n=20/drift-type x seed in {42, 123} x 2 independent repeats/trial
(paced 5s/call -- this key's real limit is 8000 tokens/min, checked via
response headers), real cached M5 panel, same 60-day-rolling-reference/
30-day-window protocol as Tasks 0/1/3/4. Reproducibility caveat: the exact
historical `current_window_pool` bounds weren't persisted anywhere
findable, so a 12-point sweep got close (200-day pool reproduces sudden's
exact strict rate, 30%) but not an exact match on every metric -- today's
det-only and det+Groq arms are directly comparable to EACH OTHER (same
trials), just not bit-identical to the old CSV.

**Honesty gate result: 240/240 calls (100%) really used Groq** -- zero
fallbacks of any kind (0 rate-limit, 0 timeout, 0 network, 0 malformed, 0
auth, 0 http errors). Not a case of silent-fallback-passing-as-a-result.

**Verdict: does not clearly help, and trades away top-3 accuracy to get a
marginal top-1 gain.**
- Top-1 strict: sudden 32.5%->42.5% (CIs [23,43] vs [32,53] -- heavily
  overlapping), gradual 17.5%->17.5% (unchanged), intermittent 10%->10%
  (unchanged). Top-1 hier: unchanged for all three (0.80/0.45/0.25 both
  ways).
- Top-3 strict: sudden 77.5%->**67.5%** (a real 10pp loss -- Groq's forced
  3-of-5 selection drops the true answer out of its own top-3 more often
  than it was dropped from the deterministic top-3), gradual 45%->42.5%,
  intermittent unchanged.
- Mechanism: Groq changed its top-1 away from the deterministic top-1 in
  only 10/240 trials (4.2%) -- 8 in sudden (always `store_id=X_N` ->
  `state_id=X`, the parent already sitting in the top-5; true slice was
  that state both times, so this is a store-vs-state tie-break, not new
  evidence -- hier accuracy is unchanged because both levels were already
  hier-correct), 2 in intermittent (still wrong after), 0 in gradual.
  Contradictions: 8 fixed, 0 broken, out of 240 -- Groq never makes a
  correct deterministic answer worse, but 95.8% of the time it changes
  nothing at all.
- Stability (temp=0, same input, 2 independent live calls): top-1 pick is
  100% stable everywhere, but the full top-3 ranking is NOT fully
  deterministic -- identical between repeats only 80-100% of the time
  depending on seed/drift-type (worst: intermittent, 80-85%).
- Cost: ~139k tokens over 240 calls (478 prompt / 101 completion tokens/call
  avg), 0.49s mean latency, zero errors of any kind.

Raw outputs: `outputs/groq_rerank_trials.csv` (240 rows, per-trial-per-repeat),
plus `_accuracy_summary.csv`, `_fallback_breakdown.csv`, `_token_usage.csv`,
`_stability.csv`, `_contradictions.csv` -- all in `outputs/`.

## Task 5 follow-up (2026-10-03): tried to optimize Groq re-ranking past the deterministic baseline -- it's a tie, not a win

Asked to optimize the Task 5 Groq re-ranker until it beats the
deterministic "simplistic" approach. Tried, in order, on held-out seeds
42/123 (n=20/drift-type, 2 repeats, same protocol as Task 5):

1. **"expanded" pool** (coarse + item-divergence + burst candidates shown
   together, every trial): measurably WORSE on the tuning seed (sudden
   32.5%->12.5% hier) -- noisy item-level scores seduced the model into
   breaking correct coarse answers. Abandoned.
2. **"hybrid" pool, v1** (coarse-only for sudden/gradual, item-only for
   intermittent): looked promising on tuning seed, but the routing
   branched on the TRUE injector `drift_type` -- real ground-truth leakage
   the deterministic baseline never gets. Caught and fixed: routing now
   gates on the coarse pool's own score vs `COARSE_SCORE_BAR` (same
   data-derived signal `_detect_and_localize` already legitimately uses),
   and the `drift_type` hint was removed from the prompt entirely (it was
   ALSO present in Task 5 v1's prompt, mislabeled as "not ground truth" --
   a latent flaw there too, though it barely mattered since v1 rarely
   deviated from the deterministic answer).

**Final validated result (leakage-free, n=202/240 -- seed=123's
intermittent trials cut short by a daily quota, see below -- zero
fallbacks among all 202, so this is a fully clean measurement just short
of full planned coverage):** pooled across all three drift types, top-1
strict is EXACTLY tied (46/202 both ways, 22.77%), and every other pooled
metric (hier top-1, strict/hier top-3) is marginally worse (within ~1pp,
deep CI overlap). This is not noise: by drift type, sudden is unchanged
(32.5%/32.5%), gradual is consistently WORSE across all 4 metrics (strict
top-1 17.5%->12.5%), and intermittent is consistently BETTER across all 4
metrics (strict top-1 14.3%->23.8%) -- contradiction count is literally 4
fixed / 4 broken, net 0. A real mechanism (giving up on coarse when its
score is weak and letting the model weigh item-level burst/divergence
evidence genuinely helps the one drift type coarse can never solve by
construction) exactly cancelled by a real cost elsewhere. Top-1 stability
across repeats is 100% in every drift type now (up from Task 5 v1's
80-100% range) -- the bigger model + gate-based routing produces more
decisive, reproducible answers, just not NET better ones.

**Operational lessons, worth knowing before touching this again:**
- Groq enforces a **200,000 tokens/day (TPD) quota PER MODEL**, separate
  from and invisible in the per-minute `x-ratelimit-*` headers -- a run
  can look perfectly healthy on the per-minute budget right up until every
  call starts 429ing. Diagnosed via the 429 response BODY (`error.message`
  contains "tokens per day"), not the headers. `groq_reranker_v2.py` now
  tags this distinctly as `fallback_reason="daily_quota_exceeded"` (vs
  generic `"rate_limit"`), and the harness aborts immediately on it instead
  of grinding through doomed retries.
- `_parse_duration` had a real bug: Groq's sub-second header format
  `"577ms"` contains the letter "m", which a naive minutes-parser
  misreads as minutes -- produced a ~9.6-hour stuck sleep once. Fixed
  (check for the "ms" suffix first) plus a hard 75s wait cap added as
  defense-in-depth.
- The evaluation script did not save any output until the very end --
  a daily-quota abort on seed=123 once silently discarded a fully-valid,
  already-computed 120-row seed=42 result. Fixed: now saves incrementally
  after every seed.
- Different models on the same Groq key have INDEPENDENT daily quotas.
  `gpt-oss-20b` and `gpt-oss-120b` both got exhausted the same day from
  cumulative experimentation; `qwen/qwen3.8-27b` had a fresh quota but
  turned out operationally unreliable for this task (62% fallback rate at
  the same pacing that was 100% clean on gpt-oss-120b -- stricter/different
  rate limits and less consistent JSON-following). Don't assume a fresh
  model is a clean drop-in without a quick compatibility check first.

Code: `src/agents/groq_reranker_v2.py`, `src/evaluation/evaluate_groq_rerank_v2.py`.
Raw outputs: `outputs/groq_rerank_v2_*.csv`.

## Honesty norm for this project (please keep following it)
This project's whole ethos is reporting real, measured results -- including
negative/partial ones -- rather than a clean narrative. If your fix doesn't
fully work, or trades one problem for another, say so explicitly with the
actual numbers, the same way this handoff document does.

## Task 6 (2026-10-04): LLM-driven investigator agent -- BUILT, agent arm NOT YET RUN

The LLM (Groq `openai/gpt-oss-120b`, temperature 0, tool calling) runs on
every trial with no gate, chooses its own tool calls (max 10) and when to
stop, and submits a ranked top 3. Code:
`src/agents/llm_investigator_agent.py` (tools, prompt, loop),
`src/evaluation/evaluate_llm_agent.py` (harness: both arms on identical
drifted panels, saves after every trial, resumes, stops cleanly on a
daily-quota 429), `src/evaluation/summarize_llm_agent.py` (Wilson CIs,
paired McNemar, tool-call counts, sample traces incl. wrong answers),
`src/evaluation/test_llm_agent_leakage.py` (offline; passes). Literal
prompt + tool JSON: `docs/agent_prompt_and_tools.md`.

Leakage design: `run_agent()` only receives the panel, windows and Sentinel
numbers (never the injector log); the prompt/tools avoid the injector's
vocabulary (tested); tools report only per-row means/ratios/JS because raw
M5 sales are int64 and the sudden/gradual injectors make drifted rows
fractional -- raw quantiles or integer totals would have been a tell.
Tool statistics are verified identical to `Investigator.investigate()` JS
and `investigate_bursts()` scores.

Pipeline arm already run (no API needed) and verified to reproduce Task 5's
deterministic numbers trial-for-trial (101/101 overlapping trials match):
outputs/llm_agent/trials_seed7_pipeline.csv, trials_val_pipeline.csv.
Validation (seeds 42+123, n=40/type) strict top-1: sudden 32.5%, gradual
17.5%, intermittent 10%; hier top-1 80/45/25%.

NOT DONE: the agent arm (tuning on seed 7, validation on 42/123) -- no
GROQ_API_KEY in the session that built this. Token budget is the practical
constraint: ~1.3k-token starting context, est. ~15-25k tokens per trial,
so the free tier's 200k tokens/day/model allows only ~10 trials/day. The
harness resumes, so a multi-day run works, but a paid tier is the
realistic path for 180+ trials.

### Task 6 update (2026-10-04): switched the agent backend to Cerebras

Default provider is now Cerebras `gpt-oss-120b` (`CEREBRAS_API_KEY`); Groq
remains available via `--provider groq`. Free trial per the Cerebras docs: 1M
tokens/day and 1M/hour; per-minute limits are listed inconsistently (5 RPM /
30K uncached TPM on the rate-limits page vs 30 RPM / 60K on the model page),
so the client paces at one request per 12.5s by default
(`--min-request-interval` to change). $5 trial credit expiring after 30 days;
at $0.35/M input and $0.75/M output, the ~4M-token plan is about $1.50-2.
Requests send `reasoning_format: parsed` (reasoning is logged in
transcripts but not resent) and `seed: 0`. Cerebras does not document its
rate-limit header names or 429 wording, so the client treats any daily/credit
signal, a 402, or a 429 that has not cleared after 10 minutes of waiting as
`QuotaExhausted` (save and stop), never as a trial fallback. Every turn's
rate-limit headers are saved in the transcripts so the real names can be
checked after the first run. Client behavior is covered by
`check_client()` in test_llm_agent_leakage.py.

### Task 6 tuning (2026-10-04, seed 7 only) -- FROZEN at v2 for validation

- smoke (tune_v1, 3 trials): agent ranked a low-volume item first in 3/3,
  claiming it "drives" the department without checking. Fix in v2:
  get_slice_history reports `where_the_change_lives` (share of parent's
  change; change split across children and across the other hierarchy).
- tune_v2 (15 trials, 0 fallbacks): strict top-1 3/15 (pipe 2), hier top-1
  7/15 (pipe 7), strict top-3 7/15 (pipe 9), hier top-3 10/15 (pipe 9).
- tune_v3 (same 15 trials): added past-window baselines to screens,
  parent-%-without-slice, prompt notes on multiple comparisons/launches.
  Not better: 3 / 6 / 3 / 8 on the same metrics. Reverted.
- Frozen config = v2, verified byte-identical against the v2 transcripts
  (12/12 prompts, 106/106 tool outputs). SHA-256 fingerprints in
  outputs/llm_agent/FROZEN_val_v2.sha256 -- check them before seed 123.
- Persistent failure mode: organically volatile items (FOODS_3_764,
  FOODS_3_150, HOBBIES_2_121, launches/relaunches) get ranked first across
  unrelated trials. Intermittent 0/5 on seed 7 for agent and pipeline; in at
  least one trial the true item's burst is below a typical window's top item.
- Cerebras gotcha: a mid-trial gap of ~45 min was the Mac hibernating at 1%
  battery, not the API. Run validation plugged in, under `caffeinate -i`.
- Validation plan: --tag val_v2, seed 42 on 2026-10-05, seed 123 on
  2026-10-06, no changes in between.
