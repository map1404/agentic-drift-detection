"""Offline checks for the LLM investigator agent (no API key needed).

1. Leakage: run_agent's inputs contain no ground truth; the agent module
   imports nothing from the injector; the literal prompt and tool
   definitions contain none of the injector's vocabulary; and every tool
   output produced on real drifted trials (all tools, all columns, screen
   and per-slice) is free of it too.
2. Loop mechanics, with a scripted fake model: budget enforcement, forced
   submit, invalid values, rejected submissions, fallbacks, quota abort,
   and that only API-standard keys are ever sent back to the model.
3. Chat client, with faked HTTP responses: Cerebras request settings, and
   that a daily quota, exhausted credits or a rate limit that never clears
   stop the run (QuotaExhausted) instead of becoming trial fallbacks.

Run: python src/evaluation/test_llm_agent_leakage.py
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from agents import llm_investigator_agent as A
from agents.investigator import SLICE_COLUMNS
from evaluation.evaluate_llm_agent import iter_trials

FORBIDDEN = re.compile(r"sudden|gradual|intermittent|drift_type|inject|magnitude|ground.truth|synthetic|label",
                       re.IGNORECASE)
ALLOWED_MSG_KEYS = {"role", "content", "tool_calls", "tool_call_id"}


def check_static():
    params = set(inspect.signature(A.run_agent).parameters)
    assert params == {"df", "reference_window", "current_window", "js", "linf", "is_drift", "chat",
                      "max_tool_calls", "max_turns", "max_nudges", "max_tool_use_failures",
                      "max_submit_attempts"}, params
    tree = ast.parse(open(A.__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", None) or ""
            names = [a.name for a in node.names]
            assert "drift" not in mod and "evaluation" not in mod and not any("inject" in n for n in names), mod
        if isinstance(node, ast.Name):
            assert node.id not in ("log", "drift_type"), node.id
    text = A.SYSTEM_PROMPT_TEMPLATE + json.dumps(A.TOOL_DEFS) + A.USER_KICKOFF + A.NUDGE + A.BUDGET_DONE
    hit = FORBIDDEN.search(text)
    assert hit is None, f"forbidden word in prompt/tool defs: {hit.group(0)!r}"
    print("static leakage checks: OK")


def check_tool_outputs(df):
    n = 0
    for dt, trial, ref, cur, make in iter_trials(df, 7, 2):
        drifted, log = make()
        tools = A.SliceTools(drifted, ref, cur)
        prompt = A.build_system_prompt(tools, 0.01, 0.02, True, 10)
        assert FORBIDDEN.search(prompt) is None
        if log["slice_col"] == "item_id":
            assert log["slice_value"] not in prompt, "true item id appears in prompt"
        for name in ("get_slice_history", "compare_windows", "check_spikes"):
            for col in SLICE_COLUMNS:
                vals = [None] + ([log["slice_value"]] if col == log["slice_col"] else [tools.values[col][0]])
                for v in vals:
                    out = json.dumps(tools.call(name, {"col": col, "val": v}))
                    assert FORBIDDEN.search(out) is None, (name, col, v, out[:200])
                    n += 1
    print(f"tool-output leakage checks: OK ({n} outputs on 6 drifted seed-7 trials)")


class FakeChat:
    """Replays a script of assistant messages; records what it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def __call__(self, messages, tools, tool_choice="auto"):
        self.sent.append((json.loads(json.dumps(messages)), [t["function"]["name"] for t in tools], tool_choice))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, A.ChatResult):
            return step
        return A.ChatResult(True, message=step, prompt_tokens=100, completion_tokens=10)


def tc(i, name, **args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def msg(*calls, content="", reasoning="thinking..."):
    return {"role": "assistant", "content": content, "reasoning": reasoning, "tool_calls": list(calls) or None}


SUBMIT = dict(ranking=[{"col": "dept_id", "val": "FOODS_3"}, {"col": "cat_id", "val": "FOODS"},
                       {"col": "store_id", "val": "CA_1"}], reasoning="FOODS_3 shifted most vs own history")


def check_loop(df):
    _, _, ref, cur, make = next(iter_trials(df, 7, 1))
    drifted, _ = make()
    run = lambda chat, **kw: A.run_agent(drifted, ref, cur, 0.01, 0.02, True, chat, **kw)

    # normal path: parallel calls, an invalid value, a rejected submission, then success
    chat = FakeChat([
        msg(tc(1, "compare_windows", col="dept_id"), tc(2, "check_spikes", col="item_id")),
        msg(tc(3, "get_slice_history", col="item_id", val="NOT_AN_ITEM")),
        msg(tc(4, "submit_answer", ranking=SUBMIT["ranking"][:2], reasoning="x")),
        msg(tc(5, "submit_answer", **SUBMIT)),
    ])
    r = run(chat)
    assert r.ok and r.ranking[0] == ("dept_id", "FOODS_3") and r.n_tool_calls == 3
    assert r.n_invalid_calls == 1 and r.n_submit_attempts == 2 and not r.forced_submit
    for messages, _, _ in chat.sent:
        for m in messages:
            assert set(m) <= ALLOWED_MSG_KEYS, set(m)
    assert any(m.get("reasoning") == "thinking..." for m in r.transcript)
    assert "calls_remaining" in json.loads(chat.sent[1][0][-1]["content"])

    # budget: 3 calls max -> 4th call refused, only submit offered and forced
    chat = FakeChat([msg(*[tc(i, "compare_windows", col="store_id") for i in range(4)]),
                     msg(tc(9, "submit_answer", **SUBMIT))])
    r = run(chat, max_tool_calls=3)
    assert r.ok and r.forced_submit and r.n_tool_calls == 3 and r.n_invalid_calls == 1
    assert chat.sent[1][1] == ["submit_answer"] and chat.sent[1][2]["function"]["name"] == "submit_answer"

    # fallbacks: no tool calls, API error, repeated bad submissions -- no answer substituted
    r = run(FakeChat([msg(content="I think FOODS_3")] * 3))
    assert not r.ok and r.fallback_reason == "no_tool_call" and r.ranking == []
    r = run(FakeChat([A.ChatResult(False, error="retries_exhausted:http_503")]))
    assert not r.ok and r.fallback_reason == "retries_exhausted:http_503" and r.ranking == []
    bad = dict(SUBMIT, ranking=[{"col": "dept_id", "val": "FOODS_9"}] * 3)
    r = run(FakeChat([msg(tc(i, "submit_answer", **bad)) for i in range(3)]))
    assert not r.ok and r.fallback_reason == "invalid_submission"

    # daily quota propagates (harness saves and stops)
    try:
        run(FakeChat([A.QuotaExhausted("tokens per day")]))
        raise AssertionError("QuotaExhausted not raised")
    except A.QuotaExhausted:
        pass
    print("agent loop checks: OK")


class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


OK_BODY = {"choices": [{"message": {"role": "assistant", "content": "", "reasoning": "r",
                                    "tool_calls": None}}],
           "usage": {"prompt_tokens": 50, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 40}}}


def check_client():
    import requests
    orig_post, orig_sleep = requests.post, A.time.sleep
    sent, slept = [], []
    A.time.sleep = slept.append

    def run(responses, **kw):
        seq = list(responses)

        def post(url, json=None, **_):
            sent.append((url, json))
            return seq.pop(0)
        requests.post = post
        return A.ChatClient(provider="cerebras", api_key="test", min_interval_s=0, **kw)([], A.TOOL_DEFS)

    def raises(responses, exc, **kw):
        try:
            run(responses, **kw)
        except exc:
            return True
        return False

    try:
        r = run([FakeResp(200, OK_BODY)])
        url, payload = sent[-1]
        assert url == "https://api.cerebras.ai/v1/chat/completions" and payload["model"] == "gpt-oss-120b"
        assert payload["temperature"] == 0 and payload["reasoning_format"] == "parsed" and "seed" in payload
        assert r.ok and r.cached_tokens == 40 and r.message["reasoning"] == "r"

        # per-minute 429 then success: waits, succeeds, no retry consumed
        slept.clear()
        r = run([FakeResp(429, {"message": "Requests per minute limit exceeded"}, {"retry-after": "7"}),
                 FakeResp(200, OK_BODY)])
        assert r.ok and r.n_retries == 0 and slept and slept[0] == 9.0

        # daily limits / credits / day-scoped header -> stop the run
        assert raises([FakeResp(429, {"message": "Tokens per day limit exceeded"})], A.QuotaExhausted)
        assert raises([FakeResp(429, {"message": "Too many requests"},
                                {"x-ratelimit-remaining-tokens-day": "0"})], A.QuotaExhausted)
        assert raises([FakeResp(402, {"message": "Insufficient credits"})], A.QuotaExhausted)
        # a rate limit that never clears -> stop, not fallback
        assert raises([FakeResp(429, {"message": "Too many requests"}, {"retry-after": "60"})] * 20,
                      A.QuotaExhausted, max_rate_limit_wait_s=200)
        assert raises([FakeResp(401, {"message": "bad key"})], A.AuthFailed)

        # server errors exhaust retries -> logged fallback (legitimate)
        r = run([FakeResp(503, {"message": "down"})] * 3, max_retries=2)
        assert not r.ok and r.error == "retries_exhausted:http_503"
    finally:
        requests.post, A.time.sleep = orig_post, orig_sleep
    print("chat client checks: OK")


if __name__ == "__main__":
    check_static()
    check_client()
    df = pd.read_parquet("data/raw/real_m5_panel_cache.parquet")
    check_tool_outputs(df)
    check_loop(df)
    print("ALL PASSED")
