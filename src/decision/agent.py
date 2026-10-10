"""Phase 9 decision agent: "retrain" vs "ignore" for a detected drift.

Same model/provider/settings as the frozen investigator agent (reuses its
ChatClient unchanged: Cerebras gpt-oss-120b, temperature 0, seed 0,
reasoning medium). One call per trial per regime, a single forced
`submit_decision` tool call for strict JSON; no retries -- an invalid answer
is a logged fallback, excluded from scoring and counted.

The payload comes from build_trials.py and never contains the drift type,
the injection log, the counterfactual MAE or the label. Prompts were fixed
before any run (no prompt tuning).

  python src/decision/agent.py --seeds 42 123
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

from agents.llm_investigator_agent import AuthFailed, ChatClient, QuotaExhausted

OUT_CSV = "outputs/decision_agent.csv"
OUT_RAW = "outputs/decision_agent_raw.jsonl"

MODEL_DESC = (
    "The monitored model is a gradient-boosted demand forecaster that predicts daily unit sales for every "
    "item x store x day of a retail panel (about 200 items in 10 stores). Its inputs are the item's sell price, "
    "calendar features (weekday, weekend, month, SNAP day, event day) and the item/department/category/store/"
    "state identifiers. It does NOT use recent sales as an input. A drift monitor compared the current 30-day "
    "window with the preceding 60-day reference window and raised the alert below; a localiser ranked the "
    "slices that changed most.")

DECISION_DEF = (
    "Decide whether this drift has measurably degraded the model. 'retrain' means: the drift is likely to make "
    "the model's mean absolute error over the whole current window at least 5% higher than it would have been "
    "without the drift. 'ignore' means: it is not (for example, the change is small, confined to a small slice, "
    "or does not affect what the model gets wrong). Retraining has a cost, and so does missing a harmful drift.")

REGIME_NOTE = {
    "A": "You also see the model's mean absolute error on the reference window and on the current window.",
    "B": ("You are NOT shown the model's error on the current window; decide from the drift evidence alone. 'mean_sales_pct_change' is the slice's observed mean-sales change "
          "from reference to current; 'score' is its within-slice divergence; 'share_of_window' is the share of "
          "rows the slice covers."),
}

TOOL = {"type": "function", "function": {
    "name": "submit_decision", "description": "Submit the decision.",
    "parameters": {"type": "object", "properties": {
        "decision": {"type": "string", "enum": ["retrain", "ignore"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"}},
        "required": ["decision", "confidence", "rationale"]}}}


def system_prompt(regime):
    return f"{MODEL_DESC}\n\n{DECISION_DEF}\n\n{REGIME_NOTE[regime]}\n\nCall submit_decision exactly once."


def decide(chat, regime, payload_json):
    messages = [{"role": "system", "content": system_prompt(regime)},
                {"role": "user", "content": "Drift evidence (JSON):\n" + payload_json}]
    out = chat(messages, [TOOL], {"type": "function", "function": {"name": "submit_decision"}})
    rec = dict(prompt_tokens=out.prompt_tokens, completion_tokens=out.completion_tokens, cached_tokens=out.cached_tokens)
    if not out.ok:
        return dict(rec, ok=False, fallback=out.error, raw=out.detail)
    msg = out.message
    rec["reasoning"] = msg.get("reasoning")
    calls = msg.get("tool_calls") or []
    raw = calls[0]["function"].get("arguments") if calls else msg.get("content")
    try:
        d = json.loads(raw)
        conf = float(d["confidence"])
        if d["decision"] not in ("retrain", "ignore") or not 0 <= conf <= 1 or not isinstance(d.get("rationale"), str):
            raise ValueError("schema")
        return dict(rec, ok=True, fallback=None, decision=d["decision"], confidence=conf, rationale=d["rationale"], raw=raw)
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return dict(rec, ok=False, fallback="invalid_json", raw=raw)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 123])
    ap.add_argument("--max-calls", type=int, default=None)
    a = ap.parse_args(argv)
    load_dotenv(".env", override=True)
    trials = pd.read_csv("outputs/decision_trials.csv")
    trials = trials[trials.seed.isin(a.seeds)]
    done = set()
    if os.path.exists(OUT_CSV):
        p = pd.read_csv(OUT_CSV)
        done = set(zip(p.regime, p.seed, p.drift_type, p.trial))
    chat = ChatClient(provider="cerebras", reasoning_effort="medium", seed=0)
    n = 0
    for _, t in trials.iterrows():
        for regime in ("A", "B"):
            if (regime, t.seed, t.drift_type, t.trial) in done:
                continue
            if a.max_calls is not None and n >= a.max_calls:
                print("STOP (max_calls)", flush=True)
                return 0
            try:
                r = decide(chat, regime, t[f"payload_{regime}"])
            except (QuotaExhausted, AuthFailed) as e:
                print(f"STOP ({type(e).__name__}: {str(e)[:160]})", flush=True)
                return 3
            n += 1
            key = dict(regime=regime, seed=int(t.seed), drift_type=t.drift_type, trial=int(t.trial))
            meta = dict(provider=chat.provider, model=chat.model, temperature=0, seed=0, reasoning_effort="medium",
                        ts=datetime.now(timezone.utc).isoformat())
            with open(OUT_RAW, "a") as f:
                f.write(json.dumps(dict(key, **meta, **r), default=str) + "\n")
            pd.DataFrame([dict(key, ok=r["ok"], fallback=r["fallback"], decision=r.get("decision"),
                               confidence=r.get("confidence"), model=chat.model, temperature=0,
                               prompt_tokens=r["prompt_tokens"], completion_tokens=r["completion_tokens"])]
                         ).to_csv(OUT_CSV, mode="a", index=False, header=not os.path.exists(OUT_CSV))
            print(f"{regime} seed={t.seed} {t.drift_type:16s} t={t.trial:2d} -> {r.get('decision', 'FALLBACK:' + str(r['fallback']))}",
                  flush=True)
    print("COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
