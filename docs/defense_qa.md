# Defense Prep: Anticipated Q&A

Honest, specific answers — grounded in what was actually built and
measured in this codebase, not aspirational claims. Cross-references to
`README.md` and `docs/project_changelog.md` (Phase 6) throughout.

---

**Q: Why build detection/diagnosis machinery instead of just retraining the
model regularly?**

Frequent blind retraining doesn't tell you *why* performance changed, and
it can quietly bake in a data-quality bug (e.g. a broken discount feature)
as if it were a genuine demand shift. It also costs compute on a fixed
schedule regardless of whether anything actually changed. The value of
this system isn't replacing retraining — it's deciding *when* retraining
is warranted, *what* changed, and whether the cause is a pipeline defect
(don't retrain on it, fix the pipeline) or a real market shift (retrain).
Blind periodic retraining can't make that distinction; it can only ever
guess a schedule.

**Q: Why isn't the Sentinel itself agentic?**

Deliberate scope boundary, not an oversight. The Sentinel's divergence
computation runs on every monitored feature, every scan — it needs to be
cheap, deterministic, and auditable. An LLM call for that would be slower,
costlier, and harder to audit than a fixed statistical threshold, and
production ML monitoring under frameworks like the EU AI Act (2024) is
moving toward requiring exactly that kind of auditability. "Agentic"
behavior is scoped specifically to the *decision* of what to investigate
further once a statistical anomaly is already flagged (see
`AgenticInvestigator`), not to the underlying arithmetic. See README's
"What's actually agentic here" section.

**Q: Is this really "agentic AI," or is it just a pipeline with a
buzzword?**

Mostly a fair challenge, and the README says so directly: the base
Sentinel → Investigator → Synthesizer sequence is a fixed pipeline, not
agentic in the Wooldridge (2020) sense. The one component that IS
genuinely agentic — a controller making judgment calls, not executing a
script — is `AgenticInvestigator`. Its deterministic best-first search
decides which slice *combinations* to test (something the base
Investigator cannot express, since it only scores one dimension at a
time) and stops once nothing improves. Its LLM tool-calling variant lets a
model decide what to test and when to stop, using real tool-calling
against a live endpoint (see the Anthropic 401-authentication-boundary
result). It's a small, honestly-scoped claim rather than a system-wide one.

**Q: Why is your sample size n=20 (n=10 for the SHAP/agentic comparisons)?**

A Master's thesis timeline and this compute budget (target: ~1 CPU, ~4GB
RAM) don't support the hundreds-to-thousands of trials a journal
submission might expect. n=20 with Wilson score intervals (chosen
specifically because several observed rates sit at or near 0%/100%, where
the normal approximation misbehaves) is enough to make a defensible
detection-rate claim with a CI half-width of roughly ±16 percentage
points at the extremes. It is *not* enough to defensibly claim one drift
type is easier to localize than another when their CIs overlap as heavily
as sudden's and gradual's do here — and the README says exactly that
rather than overstating what n=20 supports.

**Q: What doesn't work, and why?**

Three genuine, measured limitations, not hypothetical ones:
1. **Intermittent-drift localization**: 0% strict RCA accuracy (n=20, CI
   upper bound 16.1%). Detection succeeds (100%) but precise localization
   of a short, sparse, single-item spike structurally defeats a
   distributional-comparison approach — single-item daily counts are too
   sparse for a histogram-based JS-divergence comparison to distinguish a
   real spike from noise. A per-item anomaly detector (z-score/CUSUM on
   each item's own series) is the natural fix, not more tuning of the
   current approach.
2. **Agentic combination search underperforms on a randomized benchmark**:
   on 2D-intersection drift, the deterministic combination controller
   scored 12.5% exact-both-dims vs. the simpler base Investigator's 50%,
   despite solving a hand-constructed case correctly during development.
   The seed-frontier construction (which slice columns get considered for
   combination in the first place) is the suspected bottleneck — widening
   it from top-3 to top-5 columns measurably helped (0%→12.5% exact) but
   didn't close the gap. This is flagged as a real open problem, not
   glossed over.
3. **Groq/xAI backends unverified past schema construction**: both are
   network-blocked in this build sandbox (HTTP 403 before the request
   reaches them). Only Anthropic's request format was verified against a
   live endpoint (a 401 auth error — proof the request reached the server
   and passed schema validation, not proof the full round-trip with a
   valid key works).

**Q: What would you do differently, given more time/compute?**

- Run everything against real M5 data (the code path exists; this build's
  sandbox couldn't reach Kaggle).
- Build the Slice Finder head-to-head comparison (real, runnable code
  exists at `github.com/yeounoh/slicefinder`) rather than relying only on
  the SHAP-baseline comparison built here.
- Reimplement DriftGuard from its January 2026 preprint for a
  same-dataset comparison, since no public code exists for it.
- Widen the agentic controller's seed frontier further, or replace the
  deterministic best-first heuristic with the LLM-guided search, and
  re-run the intersection benchmark at a larger n to see whether either
  closes the gap against the base Investigator.
- Increase n_trials substantially (100+) now that a single trial is fast
  (~0.6-0.9s for detection+localization; the SHAP comparison's
  25,000-row-sample `pred_contrib` call is the actual bottleneck at
  ~5-20s/trial) to get tighter CIs and a defensible sudden-vs-gradual
  comparison.

**Q: Why a 24-month baseline / 30-month total window?**

Grounded in retail-forecasting literature, not picked arbitrarily: a full
year is the bare minimum to capture annual seasonality (Mobidev, 2024);
24 months is the practitioner-recommended baseline specifically because a
single annual cycle can't distinguish genuine year-over-year drift from
ordinary seasonal recurrence (LatentView, 2024); the M5 competition itself
(Makridakis et al., 2022) requires 5+ years for training with 2+ special
events in validation/test. 24 months of reference + 6 months of holdout
(913 days total) is the defensible minimum given this project's much
smaller compute and time budget than M5's own benchmark design assumes.

**Q: How do you justify the 350→200-item stratified sample instead of the
full dataset?**

Compute tractability, documented as a scoping decision rather than a
hidden shortcut. The full M5 panel at a 913-day window is tens of millions
of rows — too large to train a model and compute SHAP values on quickly
under a ~1 CPU / ~4GB RAM target. Sampling by `item_id`, stratified by
`cat_id`, preserves the category mix; since every M5 item sells at all 10
stores, this also preserves full store/state diversity for free.
`max_items=None` is a documented escape hatch for anyone with more
compute. This build used `max_items=200` for the synthetic panel, smaller
than the spec's suggested 350, specifically to keep the SHAP-comparison
experiment's per-trial runtime inside this sandbox's command time limits —
a real, stated tradeoff, not an unstated one.

**Q: Business case — why should a company use this over Evidently/Arize/etc.?**

Not a replacement claim. Those tools are strong at detection and
dashboards and consistently weak at automated root-cause diagnosis — this
system is positioned as a complementary layer that plugs the diagnosis gap
those tools leave open, not a competitor to their infrastructure, scale,
or compliance certifications (none of which this project can or should
claim to match). The competitive-landscape table and quantified ROI model
are flagged in the changelog as the natural next increment, not yet built
in this pass.
