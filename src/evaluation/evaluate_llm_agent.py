"""Evaluation harness for the LLM investigator agent
(`agents/llm_investigator_agent.py`) vs the existing deterministic pipeline
(`evaluate._detect_and_localize`), on identical trials.

Protocol (same as Task 5 / Task 5 v2): real cached M5 panel, current
windows drawn from the last 200 days, 60-day rolling reference, 30-day
current window, trial RNG seeded exactly as `evaluate.run_evaluation`.
Seed 7 is the tuning seed; 42 and 123 are validation only.

Both arms see the same drifted panel. The injector's log is used ONLY to
score answers after the agent has returned -- it is never passed to
`run_agent`.

Output (under outputs/llm_agent/, keyed by --tag so tuning and validation
runs never mix):
  trials_<tag>.csv        one row per completed trial, both arms
  toolcalls_<tag>.jsonl   one line per tool call (args, result, timing)
  transcripts_<tag>.jsonl full conversation per trial incl. model reasoning
Rows are appended after every trial; rerunning the same tag skips trials
already written. A daily-quota error stops the run without writing the
in-flight trial, so a rerun tomorrow resumes exactly there.

Provider: Cerebras gpt-oss-120b by default (export CEREBRAS_API_KEY);
--provider groq uses GROQ_API_KEY.

Usage:
  python src/evaluation/evaluate_llm_agent.py --tag tune_v1 --seeds 7
  python src/evaluation/evaluate_llm_agent.py --tag val_v1 --seeds 42 123
  python src/evaluation/evaluate_llm_agent.py --tag val_v1 --seeds 42 123 --pipeline-only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.sentinel import Sentinel
from agents.investigator import Investigator
from agents.hierarchy import Hierarchy
from agents.llm_investigator_agent import (
    DEFAULT_PROVIDER, MAX_TOOL_CALLS, PROVIDERS, AuthFailed, ChatClient, QuotaExhausted, run_agent,
)
from drift.inject_drift import inject_sudden_drift, inject_gradual_drift, inject_intermittent_drift
from evaluation.evaluate import _detect_and_localize, _stable_seed_component

INJECTORS = {
    "sudden": (inject_sudden_drift, dict(magnitude=0.6, start_offset_days=10)),
    "gradual": (inject_gradual_drift, dict(magnitude=0.7)),
    "intermittent": (inject_intermittent_drift, dict(magnitude=3.0, n_spikes=4, spike_len_days=2, slice_col="item_id")),
}
OUT_DIR = "outputs/llm_agent"
PANELS = {"m5": "data/raw/real_m5_panel_cache.parquet",
          "favorita": "data/raw/favorita_panel_cache.parquet"}  # built by data/load_favorita.py
POOL_DAYS, REF_DAYS, WINDOW_DAYS = 200, 60, 30


def iter_trials(df, seed, n_trials):
    """Yield (drift_type, trial, ref_window, cur_window, drifted_df, log),
    reproducing run_evaluation's trial draws exactly."""
    pool_end = df["date"].max()
    pool_start = pool_end - pd.Timedelta(days=POOL_DAYS)
    max_start_offset = (pool_end - pool_start).days - WINDOW_DAYS
    for drift_type, (inject_fn, kwargs) in INJECTORS.items():
        for trial in range(n_trials):
            rng = np.random.default_rng(seed * 1000 + _stable_seed_component(drift_type) + trial)
            offset = int(rng.integers(0, max(max_start_offset, 1)))
            cur_start = pool_start + pd.Timedelta(days=offset)
            cur = (cur_start, cur_start + pd.Timedelta(days=WINDOW_DAYS))
            ref = (cur_start - pd.Timedelta(days=REF_DAYS), cur_start - pd.Timedelta(days=1))
            # inject lazily: the caller may skip already-completed trials
            yield drift_type, trial, ref, cur, (lambda f=inject_fn, k=kwargs, c=cur, r=rng: f(df, c, rng=r, **k))


def score(ranking, log, hierarchy):
    """ranking: list of (col, val). Returns strict/hier top-1/top-3 and the
    1-based rank of the exact true slice (None if absent)."""
    tc, tv = log["slice_col"], log["slice_value"]
    exact = [str(c) == tc and str(v) == tv for c, v in ranking]
    related = [e or hierarchy.is_related_slice(str(c), str(v), tc, tv) for e, (c, v) in zip(exact, ranking)]
    return dict(
        strict_top1=bool(exact[:1] and exact[0]), hier_top1=bool(related[:1] and related[0]),
        strict_top3=any(exact[:3]), hier_top3=any(related[:3]),
        true_rank=(exact.index(True) + 1) if any(exact) else None,
    )


def _fmt(ranking):
    return "|".join(f"{c}={v}" for c, v in ranking)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--dataset", default="m5", choices=list(PANELS))
    ap.add_argument("--seeds", type=int, nargs="+", required=True)
    ap.add_argument("--n", type=int, default=20, help="trials per drift type per seed")
    ap.add_argument("--provider", default=DEFAULT_PROVIDER, choices=list(PROVIDERS))
    ap.add_argument("--model", default=None, help="default: the provider's gpt-oss-120b id")
    ap.add_argument("--min-request-interval", type=float, default=None,
                    help="seconds between API requests (default: provider setting; 24.5s for Cerebras = 147/hour)")
    ap.add_argument("--reasoning-effort", default="medium")
    ap.add_argument("--max-tool-calls", type=int, default=MAX_TOOL_CALLS)
    ap.add_argument("--drift-types", nargs="+", default=list(INJECTORS))
    ap.add_argument("--min-daily-tokens", type=int, default=25000,
                    help="do not start a new trial when the provider reports fewer daily tokens left")
    ap.add_argument("--max-trials", type=int, default=None, help="stop after this many new trials")
    ap.add_argument("--pipeline-only", action="store_true", help="run only the deterministic arm (no API)")
    args = ap.parse_args(argv)

    # API keys from the project-root .env; a key already exported in the
    # shell takes precedence. Empty placeholders are treated as unset.
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    for k in ("CEREBRAS_API_KEY", "GROQ_API_KEY"):
        if os.environ.get(k) == "":
            del os.environ[k]

    os.makedirs(OUT_DIR, exist_ok=True)
    arm = "pipeline" if args.pipeline_only else "agent"
    trials_path = f"{OUT_DIR}/trials_{args.tag}_{arm}.csv" if args.pipeline_only else f"{OUT_DIR}/trials_{args.tag}.csv"
    calls_path = f"{OUT_DIR}/toolcalls_{args.tag}.jsonl"
    trans_path = f"{OUT_DIR}/transcripts_{args.tag}.jsonl"

    done = set()
    if os.path.exists(trials_path):
        prev = pd.read_csv(trials_path)
        done = set(zip(prev["seed"], prev["drift_type"], prev["trial"]))
        print(f"resuming {trials_path}: {len(done)} trials already done", flush=True)

    chat = None
    if not args.pipeline_only:
        try:
            chat = ChatClient(provider=args.provider, model=args.model, reasoning_effort=args.reasoning_effort,
                              min_interval_s=args.min_request_interval)
        except AuthFailed as e:
            print(f"STOP (auth_failed: {e}). Put the key in .env at the project root.", flush=True)
            return 3

    df = pd.read_parquet(PANELS[args.dataset])
    df.attrs["dataset"] = args.dataset  # read by Hierarchy and the agent's dataset-specific prompt fields
    hierarchy = Hierarchy(df)
    sentinel = Sentinel()
    investigator = Investigator()
    config = dict(provider=args.provider, model=chat.model if chat else None, reasoning_effort=args.reasoning_effort, max_tool_calls=args.max_tool_calls)

    new = 0
    stop_reason = "completed"
    try:
        for seed in args.seeds:
            for drift_type, trial, ref, cur, make in iter_trials(df, seed, args.n):
                if drift_type not in args.drift_types or (seed, drift_type, trial) in done:
                    continue
                if args.max_trials is not None and new >= args.max_trials:
                    stop_reason = "max_trials"
                    raise StopIteration
                left = chat.last_ratelimit.get("x-ratelimit-remaining-tokens-day") if chat else None
                if left is not None and int(left) < args.min_daily_tokens:
                    raise QuotaExhausted(f"only {left} daily tokens left (< {args.min_daily_tokens}); "
                                         "not starting a trial that would likely be cut off")
                drifted, log = make()

                detected, cands, det_s = _detect_and_localize(drifted, ref, cur, sentinel, investigator)
                pipe_rank = [(str(c.slice_col), str(c.slice_val)) for c in cands[:5]]
                ps = score(pipe_rank, log, hierarchy)
                row = dict(seed=seed, drift_type=drift_type, trial=trial,
                           cur_start=str(cur[0].date()), cur_end=str(cur[1].date()),
                           true_slice=f"{log['slice_col']}={log['slice_value']}", detected=detected,
                           pipe_ranking=_fmt(pipe_rank[:3]), pipe_time_s=round(det_s, 2),
                           **{f"pipe_{k}": v for k, v in ps.items()})

                if not args.pipeline_only:
                    alert = next(a for a in sentinel.scan(drifted, ref, cur) if a.feature == "sales")
                    t0 = time.time()
                    # NOTE: only the panel, windows and alert numbers go in. `log` does not.
                    res = run_agent(drifted, ref, cur, alert.js_divergence, alert.l_inf_distance,
                                    alert.is_drift, chat, max_tool_calls=args.max_tool_calls)
                    wall = time.time() - t0
                    a = score(res.ranking, log, hierarchy) if res.ok else \
                        dict(strict_top1=None, hier_top1=None, strict_top3=None, hier_top3=None, true_rank=None)
                    row.update(agent_ok=res.ok, fallback_reason=res.fallback_reason,
                               agent_ranking=_fmt(res.ranking), n_tool_calls=res.n_tool_calls,
                               n_invalid_calls=res.n_invalid_calls, n_submit_attempts=res.n_submit_attempts,
                               forced_submit=res.forced_submit, n_turns=res.n_turns,
                               prompt_tokens=res.prompt_tokens, completion_tokens=res.completion_tokens,
                               cached_tokens=res.cached_tokens,
                               agent_wall_s=round(wall, 1), tools_used="|".join(e["tool"] for e in res.tool_log),
                               **{f"agent_{k}": v for k, v in a.items()}, **config)
                    key = dict(seed=seed, drift_type=drift_type, trial=trial)
                    with open(calls_path, "a") as f:
                        for i, e in enumerate(res.tool_log):
                            f.write(json.dumps(dict(key, call_index=i, **e), default=str) + "\n")
                    with open(trans_path, "a") as f:
                        f.write(json.dumps(dict(key, true_slice=row["true_slice"], ok=res.ok,
                                                fallback_reason=res.fallback_reason,
                                                ranking=res.ranking, reasoning=res.reasoning,
                                                transcript=res.transcript), default=str) + "\n")

                pd.DataFrame([row]).to_csv(trials_path, mode="a", index=False,
                                           header=not os.path.exists(trials_path))
                new += 1
                if args.pipeline_only:
                    msg = f"pipe={'Y' if row['pipe_strict_top1'] else '.'}"
                else:
                    msg = (f"agent={'FALLBACK:' + str(res.fallback_reason) if not res.ok else ('Y' if a['strict_top1'] else '.')} "
                           f"pipe={'Y' if row['pipe_strict_top1'] else '.'} calls={res.n_tool_calls} "
                           f"tok={res.prompt_tokens + res.completion_tokens} {wall:.0f}s "
                           f"[{row.get('agent_ranking', '')}]")
                print(f"seed={seed} {drift_type:12s} t={trial:2d} true={row['true_slice']:22s} {msg}", flush=True)
    except StopIteration:
        pass
    except QuotaExhausted as e:
        stop_reason = f"daily_quota: {e}"
    except AuthFailed as e:
        stop_reason = f"auth_failed: {e}"
    print(f"STOP ({stop_reason}). {new} new trials written to {trials_path}", flush=True)
    return 0 if stop_reason in ("completed", "max_trials") else 3


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    sys.exit(main())
