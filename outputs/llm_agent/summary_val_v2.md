# LLM investigator agent: val_v2

Trials: 120 (seeds [42, 123]); provider cerebras, model gpt-oss-120b, reasoning_effort medium, max tool calls 10, temperature 0.

## Coverage / fallbacks

| drift_type | fallback_reason | n |
|---|---|---|
| gradual | none (agent answered) | 40 |
| intermittent | no_tool_call | 2 |
| intermittent | none (agent answered) | 38 |
| sudden | none (agent answered) | 40 |

## Accuracy (k/n = rate [95% Wilson CI])

`agent` = trials the agent answered itself; `agent_itt` = all trials, fallbacks as misses; `pipeline_paired` = existing pipeline on the same trials as `agent`; `pipeline_all` = all trials.


### strict_top1

| group | agent | pipeline_paired | agent_itt | pipeline_all |
|---|---|---|---|---|
| sudden | 8/40 = 20% [10%, 35%] | 13/40 = 32% [20%, 48%] | 8/40 = 20% [10%, 35%] | 13/40 = 32% [20%, 48%] |
| gradual | 8/40 = 20% [10%, 35%] | 7/40 = 18% [9%, 32%] | 8/40 = 20% [10%, 35%] | 7/40 = 18% [9%, 32%] |
| intermittent | 6/38 = 16% [7%, 30%] | 4/38 = 11% [4%, 24%] | 6/40 = 15% [7%, 29%] | 4/40 = 10% [4%, 23%] |
| ALL | 22/118 = 19% [13%, 27%] | 24/118 = 20% [14%, 28%] | 22/120 = 18% [12%, 26%] | 24/120 = 20% [14%, 28%] |

### hier_top1

| group | agent | pipeline_paired | agent_itt | pipeline_all |
|---|---|---|---|---|
| sudden | 18/40 = 45% [31%, 60%] | 32/40 = 80% [65%, 90%] | 18/40 = 45% [31%, 60%] | 32/40 = 80% [65%, 90%] |
| gradual | 16/40 = 40% [26%, 55%] | 18/40 = 45% [31%, 60%] | 16/40 = 40% [26%, 55%] | 18/40 = 45% [31%, 60%] |
| intermittent | 6/38 = 16% [7%, 30%] | 10/38 = 26% [15%, 42%] | 6/40 = 15% [7%, 29%] | 10/40 = 25% [14%, 40%] |
| ALL | 40/118 = 34% [26%, 43%] | 60/118 = 51% [42%, 60%] | 40/120 = 33% [26%, 42%] | 60/120 = 50% [41%, 59%] |

### strict_top3

| group | agent | pipeline_paired | agent_itt | pipeline_all |
|---|---|---|---|---|
| sudden | 15/40 = 38% [24%, 53%] | 31/40 = 78% [62%, 88%] | 15/40 = 38% [24%, 53%] | 31/40 = 78% [62%, 88%] |
| gradual | 17/40 = 42% [29%, 58%] | 18/40 = 45% [31%, 60%] | 17/40 = 42% [29%, 58%] | 18/40 = 45% [31%, 60%] |
| intermittent | 11/38 = 29% [17%, 45%] | 8/38 = 21% [11%, 36%] | 11/40 = 28% [16%, 43%] | 8/40 = 20% [10%, 35%] |
| ALL | 43/118 = 36% [28%, 45%] | 57/118 = 48% [39%, 57%] | 43/120 = 36% [28%, 45%] | 57/120 = 48% [39%, 56%] |

### hier_top3

| group | agent | pipeline_paired | agent_itt | pipeline_all |
|---|---|---|---|---|
| sudden | 28/40 = 70% [55%, 82%] | 32/40 = 80% [65%, 90%] | 28/40 = 70% [55%, 82%] | 32/40 = 80% [65%, 90%] |
| gradual | 28/40 = 70% [55%, 82%] | 25/40 = 62% [47%, 76%] | 28/40 = 70% [55%, 82%] | 25/40 = 62% [47%, 76%] |
| intermittent | 14/38 = 37% [23%, 53%] | 15/38 = 39% [26%, 55%] | 14/40 = 35% [22%, 50%] | 15/40 = 38% [24%, 53%] |
| ALL | 70/118 = 59% [50%, 68%] | 72/118 = 61% [52%, 69%] | 70/120 = 58% [49%, 67%] | 72/120 = 60% [51%, 68%] |

## Paired agent vs pipeline (agent-answered trials; exact McNemar)

| group | metric | n | both_right | agent_only | pipeline_only | both_wrong | mcnemar_exact_p |
|---|---|---|---|---|---|---|---|
| sudden | strict_top1 | 40 | 5 | 3 | 8 | 24 | 0.227 |
| sudden | hier_top1 | 40 | 18 | 0 | 14 | 8 | 0.000122 |
| sudden | strict_top3 | 40 | 15 | 0 | 16 | 9 | 3.05e-05 |
| sudden | hier_top3 | 40 | 27 | 1 | 5 | 7 | 0.219 |
| gradual | strict_top1 | 40 | 2 | 6 | 5 | 27 | 1 |
| gradual | hier_top1 | 40 | 11 | 5 | 7 | 17 | 0.774 |
| gradual | strict_top3 | 40 | 10 | 7 | 8 | 15 | 1 |
| gradual | hier_top3 | 40 | 23 | 5 | 2 | 10 | 0.453 |
| intermittent | strict_top1 | 38 | 2 | 4 | 2 | 30 | 0.688 |
| intermittent | hier_top1 | 38 | 4 | 2 | 6 | 26 | 0.289 |
| intermittent | strict_top3 | 38 | 5 | 6 | 3 | 24 | 0.508 |
| intermittent | hier_top3 | 38 | 11 | 3 | 4 | 20 | 1 |
| ALL | strict_top1 | 118 | 9 | 13 | 15 | 81 | 0.851 |
| ALL | hier_top1 | 118 | 33 | 7 | 27 | 51 | 0.000821 |
| ALL | strict_top3 | 118 | 30 | 13 | 27 | 48 | 0.0385 |
| ALL | hier_top3 | 118 | 61 | 9 | 11 | 37 | 0.824 |

## Tool calls per trial

| group | n_trials | mean_calls | median_calls | min_calls | max_calls | hit_budget | forced_submit | invalid_calls | mean_prompt_tokens | mean_completion_tokens | mean_cached_tokens | mean_wall_s | share_get_slice_history | share_compare_windows | share_check_spikes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sudden | 40 | 9.15 | 9 | 8 | 10 | 11 | 11 | 10 | 39670 | 2847 | 23648 | 260 | 0.75 | 0.2 | 0.05 |
| gradual | 40 | 9.15 | 9 | 7 | 10 | 14 | 14 | 5 | 38153 | 2583 | 23459 | 267 | 0.72 | 0.21 | 0.06 |
| intermittent | 40 | 9 | 9 | 7 | 10 | 10 | 10 | 9 | 39584 | 2784 | 21408 | 309 | 0.75 | 0.18 | 0.07 |
| ALL | 120 | 9.1 | 9 | 7 | 10 | 35 | 35 | 24 | 39136 | 2738 | 22838 | 279 | 0.74 | 0.2 | 0.06 |

### Distribution of investigation calls per trial

| drift_type | 7 | 8 | 9 | 10 |
|---|---|---|---|---|
| gradual | 1 | 6 | 19 | 14 |
| intermittent | 3 | 4 | 23 | 10 |
| sudden | 0 | 5 | 24 | 11 |

## Per seed (strict top-1 / hier top-1, agent-answered trials)

| seed | drift_type | n_ok | n | agent_strict | pipe_strict | agent_hier | pipe_hier |
|---|---|---|---|---|---|---|---|
| 42 | gradual | 20 | 20 | 4/20 = 20% [8%, 42%] | 4/20 = 20% [8%, 42%] | 7/20 = 35% [18%, 57%] | 8/20 = 40% [22%, 61%] |
| 42 | intermittent | 19 | 20 | 3/19 = 16% [6%, 38%] | 2/19 = 11% [3%, 31%] | 3/19 = 16% [6%, 38%] | 4/19 = 21% [9%, 43%] |
| 42 | sudden | 20 | 20 | 5/20 = 25% [11%, 47%] | 6/20 = 30% [15%, 52%] | 11/20 = 55% [34%, 74%] | 16/20 = 80% [58%, 92%] |
| 123 | gradual | 20 | 20 | 4/20 = 20% [8%, 42%] | 3/20 = 15% [5%, 36%] | 9/20 = 45% [26%, 66%] | 10/20 = 50% [30%, 70%] |
| 123 | intermittent | 19 | 20 | 3/19 = 16% [6%, 38%] | 2/19 = 11% [3%, 31%] | 3/19 = 16% [6%, 38%] | 6/19 = 32% [15%, 54%] |
| 123 | sudden | 20 | 20 | 3/20 = 15% [5%, 36%] | 7/20 = 35% [18%, 57%] | 7/20 = 35% [18%, 57%] | 16/20 = 80% [58%, 92%] |
