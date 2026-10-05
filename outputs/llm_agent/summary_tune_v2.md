# LLM investigator agent: tune_v2

Trials: 15 (seeds [7]); provider cerebras, model gpt-oss-120b, reasoning_effort medium, max tool calls 10, temperature 0.

## Coverage / fallbacks

| drift_type | fallback_reason | n |
|---|---|---|
| gradual | none (agent answered) | 5 |
| intermittent | none (agent answered) | 5 |
| sudden | none (agent answered) | 5 |

## Accuracy (k/n = rate [95% Wilson CI])

`agent` = trials the agent answered itself; `agent_itt` = all trials, fallbacks as misses; `pipeline_paired` = existing pipeline on the same trials as `agent`; `pipeline_all` = all trials.


### strict_top1

| group | agent | pipeline_paired | agent_itt | pipeline_all |
|---|---|---|---|---|
| sudden | 3/5 = 60% [23%, 88%] | 2/5 = 40% [12%, 77%] | 3/5 = 60% [23%, 88%] | 2/5 = 40% [12%, 77%] |
| gradual | 0/5 = 0% [0%, 43%] | 0/5 = 0% [0%, 43%] | 0/5 = 0% [0%, 43%] | 0/5 = 0% [0%, 43%] |
| intermittent | 0/5 = 0% [0%, 43%] | 0/5 = 0% [0%, 43%] | 0/5 = 0% [0%, 43%] | 0/5 = 0% [0%, 43%] |
| ALL | 3/15 = 20% [7%, 45%] | 2/15 = 13% [4%, 38%] | 3/15 = 20% [7%, 45%] | 2/15 = 13% [4%, 38%] |

### hier_top1

| group | agent | pipeline_paired | agent_itt | pipeline_all |
|---|---|---|---|---|
| sudden | 4/5 = 80% [38%, 96%] | 5/5 = 100% [57%, 100%] | 4/5 = 80% [38%, 96%] | 5/5 = 100% [57%, 100%] |
| gradual | 3/5 = 60% [23%, 88%] | 2/5 = 40% [12%, 77%] | 3/5 = 60% [23%, 88%] | 2/5 = 40% [12%, 77%] |
| intermittent | 0/5 = 0% [0%, 43%] | 0/5 = 0% [0%, 43%] | 0/5 = 0% [0%, 43%] | 0/5 = 0% [0%, 43%] |
| ALL | 7/15 = 47% [25%, 70%] | 7/15 = 47% [25%, 70%] | 7/15 = 47% [25%, 70%] | 7/15 = 47% [25%, 70%] |

### strict_top3

| group | agent | pipeline_paired | agent_itt | pipeline_all |
|---|---|---|---|---|
| sudden | 4/5 = 80% [38%, 96%] | 5/5 = 100% [57%, 100%] | 4/5 = 80% [38%, 96%] | 5/5 = 100% [57%, 100%] |
| gradual | 3/5 = 60% [23%, 88%] | 3/5 = 60% [23%, 88%] | 3/5 = 60% [23%, 88%] | 3/5 = 60% [23%, 88%] |
| intermittent | 0/5 = 0% [0%, 43%] | 1/5 = 20% [4%, 62%] | 0/5 = 0% [0%, 43%] | 1/5 = 20% [4%, 62%] |
| ALL | 7/15 = 47% [25%, 70%] | 9/15 = 60% [36%, 80%] | 7/15 = 47% [25%, 70%] | 9/15 = 60% [36%, 80%] |

### hier_top3

| group | agent | pipeline_paired | agent_itt | pipeline_all |
|---|---|---|---|---|
| sudden | 5/5 = 100% [57%, 100%] | 5/5 = 100% [57%, 100%] | 5/5 = 100% [57%, 100%] | 5/5 = 100% [57%, 100%] |
| gradual | 4/5 = 80% [38%, 96%] | 3/5 = 60% [23%, 88%] | 4/5 = 80% [38%, 96%] | 3/5 = 60% [23%, 88%] |
| intermittent | 1/5 = 20% [4%, 62%] | 1/5 = 20% [4%, 62%] | 1/5 = 20% [4%, 62%] | 1/5 = 20% [4%, 62%] |
| ALL | 10/15 = 67% [42%, 85%] | 9/15 = 60% [36%, 80%] | 10/15 = 67% [42%, 85%] | 9/15 = 60% [36%, 80%] |

## Paired agent vs pipeline (agent-answered trials; exact McNemar)

| group | metric | n | both_right | agent_only | pipeline_only | both_wrong | mcnemar_exact_p |
|---|---|---|---|---|---|---|---|
| sudden | strict_top1 | 5 | 1 | 2 | 1 | 1 | 1 |
| sudden | hier_top1 | 5 | 4 | 0 | 1 | 0 | 1 |
| sudden | strict_top3 | 5 | 4 | 0 | 1 | 0 | 1 |
| sudden | hier_top3 | 5 | 5 | 0 | 0 | 0 | 1 |
| gradual | strict_top1 | 5 | 0 | 0 | 0 | 5 | 1 |
| gradual | hier_top1 | 5 | 2 | 1 | 0 | 2 | 1 |
| gradual | strict_top3 | 5 | 2 | 1 | 1 | 1 | 1 |
| gradual | hier_top3 | 5 | 3 | 1 | 0 | 1 | 1 |
| intermittent | strict_top1 | 5 | 0 | 0 | 0 | 5 | 1 |
| intermittent | hier_top1 | 5 | 0 | 0 | 0 | 5 | 1 |
| intermittent | strict_top3 | 5 | 0 | 0 | 1 | 4 | 1 |
| intermittent | hier_top3 | 5 | 1 | 0 | 0 | 4 | 1 |
| ALL | strict_top1 | 15 | 1 | 2 | 1 | 11 | 1 |
| ALL | hier_top1 | 15 | 6 | 1 | 1 | 7 | 1 |
| ALL | strict_top3 | 15 | 6 | 1 | 3 | 5 | 0.625 |
| ALL | hier_top3 | 15 | 9 | 1 | 0 | 5 | 1 |

## Tool calls per trial

| group | n_trials | mean_calls | median_calls | min_calls | max_calls | hit_budget | forced_submit | invalid_calls | mean_prompt_tokens | mean_completion_tokens | mean_cached_tokens | mean_wall_s | share_get_slice_history | share_compare_windows | share_check_spikes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sudden | 5 | 9 | 9 | 8 | 10 | 2 | 2 | 0 | 38880 | 2504 | 22246 | 255 | 0.67 | 0.29 | 0.04 |
| gradual | 5 | 8.6 | 9 | 7 | 9 | 0 | 0 | 0 | 30830 | 2147 | 16921 | 240 | 0.67 | 0.3 | 0.02 |
| intermittent | 5 | 9.6 | 10 | 9 | 10 | 3 | 3 | 3 | 39395 | 2591 | 20966 | 274 | 0.65 | 0.29 | 0.06 |
| ALL | 15 | 9.07 | 9 | 7 | 10 | 5 | 5 | 3 | 36368 | 2414 | 20044 | 256 | 0.66 | 0.29 | 0.04 |

### Distribution of investigation calls per trial

| drift_type | 7 | 8 | 9 | 10 |
|---|---|---|---|---|
| gradual | 1 | 0 | 4 | 0 |
| intermittent | 0 | 0 | 2 | 3 |
| sudden | 0 | 2 | 1 | 2 |
