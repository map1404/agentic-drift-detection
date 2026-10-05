"""Summarize an evaluate_llm_agent.py run.

Accuracy is reported three ways so a fallback can never pass as an agent
result:
  agent        -- trials where the agent itself submitted an answer (n_ok)
  agent_itt    -- all trials, fallbacks counted as misses
  pipeline     -- the deterministic pipeline on the SAME n_ok trials (paired),
                  and on all trials
Paired agent-vs-pipeline differences use an exact McNemar test on the
discordant trials.

Usage: python src/evaluation/summarize_llm_agent.py --tag val [--n-traces 3]
Writes outputs/llm_agent/summary_<tag>.md, metrics_<tag>.csv, traces_<tag>.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import binomtest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from evaluation.evaluate import wilson_interval

OUT_DIR = "outputs/llm_agent"
METRICS = ["strict_top1", "hier_top1", "strict_top3", "hier_top3"]
GROUP_ORDER = ["sudden", "gradual", "intermittent", "ALL"]


def _cell(k, n):
    if n == 0:
        return "n/a"
    lo, hi = wilson_interval(int(k), int(n))
    return f"{k}/{n} = {k / n:.0%} [{lo:.0%}, {hi:.0%}]"


def _groups(t):
    for g in GROUP_ORDER:
        sub = t if g == "ALL" else t[t["drift_type"] == g]
        if len(sub):
            yield g, sub


def accuracy_rows(t):
    rows = []
    ok = t[t["agent_ok"]]
    for g, sub in _groups(t):
        sub_ok = sub[sub["agent_ok"]]
        for m in METRICS:
            a = sub_ok[f"agent_{m}"].astype(bool)
            rows += [
                dict(group=g, metric=m, arm="agent", k=int(a.sum()), n=len(sub_ok)),
                dict(group=g, metric=m, arm="agent_itt", k=int(a.sum()), n=len(sub)),
                dict(group=g, metric=m, arm="pipeline_paired", k=int(sub_ok[f"pipe_{m}"].sum()), n=len(sub_ok)),
                dict(group=g, metric=m, arm="pipeline_all", k=int(sub[f"pipe_{m}"].sum()), n=len(sub)),
            ]
    df = pd.DataFrame(rows)
    ci = [wilson_interval(k, n) if n else (np.nan, np.nan) for k, n in zip(df.k, df.n)]
    df["rate"] = df.k / df.n.replace(0, np.nan)
    df["ci_lo"], df["ci_hi"] = zip(*ci)
    return df


def paired_rows(t):
    rows = []
    for g, sub in _groups(t[t["agent_ok"]]):
        for m in METRICS:
            a, p = sub[f"agent_{m}"].astype(bool), sub[f"pipe_{m}"].astype(bool)
            b, c = int((a & ~p).sum()), int((~a & p).sum())
            pval = binomtest(b, b + c, 0.5).pvalue if b + c else 1.0
            rows.append(dict(group=g, metric=m, n=len(sub), both_right=int((a & p).sum()),
                             agent_only=b, pipeline_only=c, both_wrong=int((~a & ~p).sum()),
                             mcnemar_exact_p=pval))
    return pd.DataFrame(rows)


def tool_rows(t):
    rows = []
    for g, sub in _groups(t):
        tools = sub["tools_used"].fillna("").str.split("|").explode()
        tools = tools[(tools != "") & (tools != "submit_answer")]
        mix = tools.value_counts(normalize=True)
        c = sub["n_tool_calls"]
        rows.append(dict(group=g, n_trials=len(sub), mean_calls=round(c.mean(), 2), median_calls=c.median(),
                         min_calls=int(c.min()), max_calls=int(c.max()),
                         hit_budget=int((c >= sub["max_tool_calls"]).sum()),
                         forced_submit=int(sub["forced_submit"].sum()),
                         invalid_calls=int(sub["n_invalid_calls"].sum()),
                         mean_prompt_tokens=int(sub["prompt_tokens"].mean()),
                         mean_completion_tokens=int(sub["completion_tokens"].mean()),
                         mean_cached_tokens=int(sub["cached_tokens"].mean()) if "cached_tokens" in sub else 0,
                         mean_wall_s=round(sub["agent_wall_s"].mean(), 1),
                         **{f"share_{k}": round(v, 2) for k, v in mix.items()}))
    return pd.DataFrame(rows)


def _md_table(df):
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(v) else (f"{v:.3g}" if isinstance(v, float) else str(v))
                                       for v in r) + " |")
    return "\n".join(lines)


def write_traces(tag, t, n_per_cell):
    """n_per_cell correct and n_per_cell wrong agent traces per drift type,
    plus every fallback, picked in trial order (no cherry-picking)."""
    path = f"{OUT_DIR}/transcripts_{tag}.jsonl"
    trans = {}
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            trans[(r["seed"], r["drift_type"], r["trial"])] = r  # last write wins on reruns
    picks = []
    for dt in GROUP_ORDER[:-1]:
        sub = t[t["drift_type"] == dt].sort_values(["seed", "trial"])
        ok = sub[sub["agent_ok"]]
        picks += [("CORRECT (strict top-1)", r) for _, r in ok[ok["agent_strict_top1"].astype(bool)].head(n_per_cell).iterrows()]
        picks += [("WRONG (strict top-1)", r) for _, r in ok[~ok["agent_strict_top1"].astype(bool)].head(n_per_cell).iterrows()]
    picks += [("FALLBACK", r) for _, r in t[~t["agent_ok"]].iterrows()]

    out = [f"# Agent reasoning traces: {tag}\n",
           "Selected mechanically: the first correct and first wrong trials per drift type in "
           "(seed, trial) order, plus every fallback. The drift type and true slice in each heading "
           "are for the reader only; the agent never saw them.\n"]
    for label, r in picks:
        tr = trans.get((r["seed"], r["drift_type"], r["trial"]))
        out.append(f"\n---\n\n## {label}: seed={r['seed']} {r['drift_type']} trial={r['trial']}\n")
        out.append(f"- true slice: `{r['true_slice']}`\n- agent ranking: `{r['agent_ranking']}`"
                   f"\n- pipeline ranking: `{r['pipe_ranking']}`\n- tool calls: {r['n_tool_calls']}"
                   f"{' (forced submit)' if r['forced_submit'] else ''}; fallback: {'none' if pd.isna(r['fallback_reason']) else r['fallback_reason']}\n")
        if tr is None:
            out.append("\n(transcript missing)\n")
            continue
        for m in tr["transcript"]:
            role = m.get("role")
            if role == "system":
                out.append("\n**system prompt**: (literal text in agent_prompt_and_tools.md; trial-specific "
                           "dates/scores filled in)\n")
            elif role == "assistant":
                if m.get("reasoning"):
                    out.append(f"\n**model reasoning**:\n```\n{m['reasoning'].strip()}\n```\n")
                if m.get("content"):
                    out.append(f"\n**model text**: {m['content'].strip()}\n")
                for c in m.get("tool_calls") or []:
                    out.append(f"\n**call** `{c['function']['name']}({c['function']['arguments']})`\n")
            elif role == "tool":
                out.append(f"\n**result**:\n```json\n{m['content']}\n```\n")
            else:
                out.append(f"\n**{role}**: {m.get('content')}\n")
        out.append(f"\n**submitted reasoning**: {tr.get('reasoning')}\n")
    with open(f"{OUT_DIR}/traces_{tag}.md", "w") as f:
        f.write("\n".join(out))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--n-traces", type=int, default=2, help="correct and wrong traces per drift type")
    args = ap.parse_args(argv)

    t = pd.read_csv(f"{OUT_DIR}/trials_{args.tag}.csv")
    t = t.drop_duplicates(["seed", "drift_type", "trial"], keep="last")
    t["agent_ok"] = t["agent_ok"].astype(bool)

    acc = accuracy_rows(t)
    acc.to_csv(f"{OUT_DIR}/metrics_{args.tag}.csv", index=False)
    paired = paired_rows(t)
    tools = tool_rows(t)

    md = [f"# LLM investigator agent: {args.tag}\n",
          f"Trials: {len(t)} (seeds {sorted(t.seed.unique().tolist())}); "
          f"provider {t.get('provider', pd.Series(['groq'])).iloc[0]}, model {t.model.iloc[0]}, reasoning_effort {t.reasoning_effort.iloc[0]}, "
          f"max tool calls {t.max_tool_calls.iloc[0]}, temperature 0.\n",
          "## Coverage / fallbacks\n",
          _md_table(t.assign(fallback_reason=t.fallback_reason.fillna("none (agent answered)"))
                    .groupby(["drift_type", "fallback_reason"]).size().rename("n").reset_index()),
          "\n## Accuracy (k/n = rate [95% Wilson CI])\n",
          "`agent` = trials the agent answered itself; `agent_itt` = all trials, fallbacks as misses; "
          "`pipeline_paired` = existing pipeline on the same trials as `agent`; `pipeline_all` = all trials.\n"]
    for m in METRICS:
        piv = acc[acc.metric == m].assign(cell=lambda d: [_cell(k, n) for k, n in zip(d.k, d.n)]) \
            .pivot(index="group", columns="arm", values="cell")
        piv = piv.reindex([g for g in GROUP_ORDER if g in piv.index])
        md += [f"\n### {m}\n", _md_table(piv[["agent", "pipeline_paired", "agent_itt", "pipeline_all"]].reset_index())]
    md += ["\n## Paired agent vs pipeline (agent-answered trials; exact McNemar)\n", _md_table(paired),
           "\n## Tool calls per trial\n", _md_table(tools),
           "\n### Distribution of investigation calls per trial\n",
           _md_table(t.groupby(["drift_type", "n_tool_calls"]).size().unstack(fill_value=0).reset_index())]
    if t.seed.nunique() > 1:
        md.append("\n## Per seed (strict top-1 / hier top-1, agent-answered trials)\n")
        rows = []
        for (s, dt), g in t.groupby(["seed", "drift_type"]):
            ok = g[g.agent_ok]
            rows.append(dict(seed=s, drift_type=dt, n_ok=len(ok), n=len(g),
                             agent_strict=_cell(int(ok.agent_strict_top1.astype(bool).sum()), len(ok)),
                             pipe_strict=_cell(int(ok.pipe_strict_top1.sum()), len(ok)),
                             agent_hier=_cell(int(ok.agent_hier_top1.astype(bool).sum()), len(ok)),
                             pipe_hier=_cell(int(ok.pipe_hier_top1.sum()), len(ok))))
        md.append(_md_table(pd.DataFrame(rows)))
    with open(f"{OUT_DIR}/summary_{args.tag}.md", "w") as f:
        f.write("\n".join(md) + "\n")
    write_traces(args.tag, t, args.n_traces)
    print("\n".join(md))
    print(f"\nwrote {OUT_DIR}/summary_{args.tag}.md, metrics_{args.tag}.csv, traces_{args.tag}.md")


if __name__ == "__main__":
    main()
