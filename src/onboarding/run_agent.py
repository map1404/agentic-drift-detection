"""Run the adapter-drafting agent: 5 repeats x {named, obscured} (Phase 8).

One raw JSON per run in outputs/onboarding/raw/ (config, full transcript incl.
model reasoning, tokens, model/provider/temperature/seed, timestamps); tool
calls with timestamps in outputs/onboarding/toolcalls.jsonl. Finished runs are
skipped, so a rerun resumes; a daily-quota / payment error stops cleanly.

  python src/onboarding/run_agent.py [--repeats 5]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from onboarding import agent

OUT = "outputs/onboarding"
CONDITIONS = ["named", "obscured"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=5)
    args = ap.parse_args(argv)
    load_dotenv(".env")
    os.makedirs(f"{OUT}/raw", exist_ok=True)
    for rep in range(args.repeats):
        for cond in CONDITIONS:
            path = f"{OUT}/raw/agent_{cond}_rep{rep}.json"
            if os.path.exists(path):
                continue
            chat = agent.ChatClient(provider="cerebras", reasoning_effort="medium", seed=rep)
            meta = dict(condition=cond, repeat=rep, provider=chat.provider, model=chat.model, temperature=0,
                        seed=rep, reasoning_effort="medium", max_tool_calls=agent.MAX_TOOL_CALLS,
                        started=datetime.now(timezone.utc).isoformat())
            t0 = time.time()
            try:
                res = agent.run(f"{OUT}/sandbox/{cond}", chat, f"{OUT}/toolcalls.jsonl",
                                dict(condition=cond, repeat=rep))
            except (agent.QuotaExhausted, agent.AuthFailed) as e:
                print(f"STOP ({type(e).__name__}: {str(e)[:200]}) before {cond} rep{rep}", flush=True)
                return 3
            meta.update(finished=datetime.now(timezone.utc).isoformat(), wall_s=round(time.time() - t0))
            json.dump(dict(meta=meta, **res), open(path, "w"), indent=1, default=str)
            print(f"{cond} rep{rep}: ok={res['ok']} fallback={res['fallback_reason']} calls={res['n_tool_calls']} "
                  f"tokens={res['tokens']} {meta['wall_s']}s", flush=True)
    print("COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
