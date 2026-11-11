"""How the prompt grows with the number of features, loading all tools vs on demand.

    python -m evals.scale

Adds synthetic features (one tool and about 150 words of guidance each, the
size of a real one) next to the real three, then asks the same order question
in both loading modes and reports the prompt tokens of the first model call
and whether the question still got answered.
"""

import sys
import tempfile
from pathlib import Path

from pydantic import Field, create_model

from kestrel.__main__ import build_agent
from kestrel.agent import Agent
from kestrel.features import Feature
from kestrel.tools import Args, Tool

QUESTION = "Hi, where's my order KC-10433? My email is marcus.reed@example.com"
GUIDANCE = " ".join(["Follow the documented procedure for this request and quote only what the tool returns."] * 12)


def synthetic_feature(i: int) -> Feature:
    args = create_model(
        f"Extra{i}Args", __base__=Args,
        account_id=(str, Field(description="The customer's account identifier.")),
        reference=(str | None, Field(None, description="An optional reference number from a receipt or email.")),
        reason=(str | None, Field(None, description="Why the customer is asking, in their words.")),
        priority=(int | None, Field(None, description="1 (low) to 3 (high).")),
    )
    tool = Tool(f"extra_tool_{i}", f"Handle request type {i} for a customer account: look up, update or explain the "
                "account record. Returns the record and the next steps the customer can take.", args,
                lambda a: {"ok": True})
    return Feature(name=f"extra_{i}", summary=f"Account service number {i}: lookups and changes for that service.",
                   tools=(tool,), guidance=lambda now: GUIDANCE, offer=f"help with service {i}")


def run(extra: int, mode: str) -> tuple[int, bool, int]:
    agent: Agent = build_agent("2026-12-01 21:30", Path(tempfile.mkdtemp()) / "c.json", tool_loading=mode)
    fakes = [synthetic_feature(i) for i in range(extra)]
    agent.features += fakes
    agent._tools.update({t.name: t for f in fakes for t in f.tools})
    agent._feature_of.update({t.name: f.name for f in fakes for t in f.tools})
    if not agent.on_demand:
        agent.active |= {f.name for f in fakes}
    reply = agent.reply(QUESTION)
    return agent.llm.calls[0]["prompt_tokens"], "1Z84KC0002" in reply, len(agent.llm.calls)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"{'features':>9} {'mode':>10} {'first-call prompt tokens':>25} {'model calls':>12} {'answered':>9}")
    for extra in (0, 20, 50):
        for mode in ("all", "on_demand"):
            tokens, answered, calls = run(extra, mode)
            print(f"{3 + extra:>9} {mode:>10} {tokens:>25,} {calls:>12} {'yes' if answered else 'NO':>9}")


if __name__ == "__main__":
    main()
