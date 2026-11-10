"""Scenario eval: play scripted conversations against the real model and check
each against written expectations.

    python -m evals.run_eval                     # all scenarios
    python -m evals.run_eval --only return-mixed # one scenario
    python -m evals.run_eval --save-run evals/eval-run.txt

Unit tests prove the code's rules. This checks what only a live model can get
wrong: when it calls a tool, what it says, and what it refuses to say. Checks
are regular expressions, so they are cheap and repeatable but shallow; the
saved run keeps every reply so a person can read the ones that matter.
"""

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from kestrel import guard
from kestrel.__main__ import build_agent

SCENARIOS = Path(__file__).resolve().parent / "scenarios.json"
DEFAULT_NOW = "2026-12-01 21:30"


def failed_checks(replies: list[str], tools_called: list[str], expect: dict) -> list[str]:
    """Return one line per expectation the conversation did not meet."""
    text = "\n".join(replies)
    flags = re.IGNORECASE | re.MULTILINE
    failures = []
    for pattern in expect.get("reply_matches", []):
        if not re.search(pattern, text, flags):
            failures.append(f"expected a reply to match /{pattern}/")
    for pattern in expect.get("reply_lacks", []):
        if found := re.search(pattern, text, flags):
            failures.append(f"no reply may match /{pattern}/, found {found.group(0)!r}")
    for tool in expect.get("tools_called", []):
        if tool not in tools_called:
            failures.append(f"expected a call to {tool}")
    for tool in expect.get("tools_not_called", []):
        if tool in tools_called:
            failures.append(f"{tool} must not be called")
    if "max_emoji" in expect:
        most = max(len(guard.EMOJI.findall(reply)) for reply in replies)
        if most > expect["max_emoji"]:
            failures.append(f"at most {expect['max_emoji']} emoji per reply, found {most}")
    if "distinct_codes" in expect:
        codes = set(guard.CODE.findall(text))
        if len(codes) != expect["distinct_codes"]:
            failures.append(f"expected {expect['distinct_codes']} distinct code(s), found {sorted(codes)}")
    return failures


def run_scenario(scenario: dict, model: str | None) -> dict:
    with tempfile.TemporaryDirectory() as state_dir:  # every scenario starts with no codes issued
        agent = build_agent(scenario.get("now", DEFAULT_NOW), Path(state_dir) / "codes.json", model)
        replies = [agent.reply(turn) for turn in scenario["turns"]]
    tools_called = [call["tool"] for call in agent.trace]
    return {
        "replies": replies,
        "tools_called": tools_called,
        "guard_events": agent.guard_events,
        "failures": failed_checks(replies, tools_called, scenario["expect"]),
    }


def report(scenario: dict, result: dict) -> str:
    lines = [f"{'PASS' if not result['failures'] else 'FAIL'}  {scenario['id']}  ({scenario['why']})"]
    lines += [f"      ! {failure}" for failure in result["failures"]]
    lines.append(f"      clock: {scenario.get('now', DEFAULT_NOW)}   tools: {result['tools_called'] or 'none'}")
    if result["guard_events"]:
        lines.append(f"      guard: {result['guard_events']}")
    for turn, reply in zip(scenario["turns"], result["replies"]):
        lines.append(f"      customer> {turn}")
        lines += [f"      kestrel > {line}" for line in reply.splitlines()]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="run the scenario with this id")
    parser.add_argument("--model", help="OpenRouter model slug (default: the agent's default)")
    parser.add_argument("--save-run", help="write the full run, with every reply, to this file")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if args.only:
        scenarios = [scenario for scenario in scenarios if scenario["id"] == args.only]
        if not scenarios:
            sys.exit(f"No scenario with id {args.only!r}")

    reports, passed = [], 0
    for scenario in scenarios:
        result = run_scenario(scenario, args.model)
        passed += not result["failures"]
        reports.append(report(scenario, result))
        print(reports[-1].split("\n      customer>")[0] if not result["failures"] else reports[-1])

    summary = f"{passed}/{len(scenarios)} scenarios passed"
    print(f"\n{summary}")
    if args.save_run:
        header = f"Kestrel scenario eval, {datetime.now():%Y-%m-%d %H:%M}, model: {args.model or 'default'}\n{summary}\n"
        Path(args.save_run).write_text(header + "\n" + "\n\n".join(reports) + "\n", encoding="utf-8")
    sys.exit(0 if passed == len(scenarios) else 1)


if __name__ == "__main__":
    main()
