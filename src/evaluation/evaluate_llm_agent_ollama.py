"""Run evaluate_llm_agent.py against a local Ollama model.

Registers an "ollama" provider at runtime instead of editing
agents/llm_investigator_agent.py, so the prompt, tools and agent loop stay
byte-identical to the frozen Cerebras validation config (whose SHA-256
fingerprints a running validation loop checks). Only the backend differs.

Ollama's default context window can be 4k tokens, below what an
investigation reaches (~6-7k), and it truncates silently. Use a model
variant with a larger window, e.g.:
  printf 'FROM gemma4:12b\\nPARAMETER num_ctx 16384\\n' > Modelfile
  ollama create gemma4-12b-ctx16k -f Modelfile

Usage (same flags as evaluate_llm_agent.py; --provider is set for you):
  python src/evaluation/evaluate_llm_agent_ollama.py --tag ollama_smoke --seeds 7 --max-trials 3
"""
from __future__ import annotations

import functools
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import llm_investigator_agent as agent
from evaluation import evaluate_llm_agent as harness

OLLAMA_MODEL = "gemma4-12b-ctx16k"

agent.PROVIDERS["ollama"] = dict(
    url=os.environ.get("OLLAMA_URL", "http://localhost:11434/v1/chat/completions"),
    key_env="OLLAMA_API_KEY", model=OLLAMA_MODEL, min_interval_s=0.0, extra={},
)
os.environ.setdefault("OLLAMA_API_KEY", "ollama")  # Ollama ignores the key; ChatClient requires one
# Local generation on a long context can exceed the 90s cloud default.
harness.ChatClient = functools.partial(agent.ChatClient, timeout=600.0)

if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    argv = sys.argv[1:]
    if "--provider" not in argv:
        argv = ["--provider", "ollama"] + argv
    sys.exit(harness.main(argv))
