"""Scenario eval: play scripted conversations against the real model and check
each against written expectations.

    python -m evals.run_eval                       # every scenario, 3 runs each
    python -m evals.run_eval --only return-mixed   # one scenario
    python -m evals.run_eval --repeat 1 --save-run evals/eval-run.txt

Unit tests prove the code's rules. This checks what only a live model can get
wrong: which tool it calls and with what arguments, what it says on each turn,
and what it refuses to say. On top of each scenario's own expectations, every
reply is checked again for codes, links, prices and percentages that no tool
returned, independently of the agent's guard. A sampled model can pass once
by luck, so each scenario runs several times and the pass rate is reported,
with LLM calls, latency, tokens and cost per turn.
"""

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from kestrel import guard
from kestrel.__main__ import DEFAULT_MODEL, build_agent
from kestrel.agent import LLM_UNAVAILABLE, SAFE_FALLBACK
from kestrel.features.night_owl import CODE as NIGHT_OWL_CODE
from kestrel.orders import normalize_email

SCENARIOS = Path(__file__).resolve().parent / "scenarios.json"
DEFAULT_NOW = "2026-12-01 21:30"
NUMBER_TOLERANCE = 1  # 6 ft 2 in may arrive as 74 (in) or 188 (cm, rounded)


# --- checks ---


def _value_matches(actual, wanted) -> bool:
    for option in wanted if isinstance(wanted, list) else [wanted]:
        if isinstance(option, (int, float)) and isinstance(actual, (int, float)):
            if abs(actual - option) <= NUMBER_TOLERANCE:
                return True
        elif isinstance(option, str) and isinstance(actual, str):
            if actual.strip().casefold() == option.casefold():
                return True
    return False


def _arguments(call: dict) -> dict:
    try:
        return json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        return {}


def failed_checks(replies: list[str], tools_called: list[str], expect: dict, calls: list[dict] = ()) -> list[str]:
    """Return one line per expectation the replies and tool calls did not meet."""
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
    for tool, wanted in expect.get("tool_args", {}).items():
        made = [_arguments(call) for call in calls if call["tool"] == tool]
        if not any(all(_value_matches(args.get(key), value) for key, value in wanted.items()) for args in made):
            failures.append(f"expected a {tool} call with {wanted}, got {made or 'no call'}")
    for tool, most in expect.get("max_tool_calls", {}).items():
        count = tools_called.count(tool)
        if count > most:
            failures.append(f"{tool} may run at most {most} times, ran {count}")
    if "max_emoji" in expect:
        most = max(len(guard.EMOJI.findall(reply)) for reply in replies)
        if most > expect["max_emoji"]:
            failures.append(f"at most {expect['max_emoji']} emoji per reply, found {most}")
    if "min_words" in expect:
        fewest = min(len(reply.split()) for reply in replies)
        if fewest < expect["min_words"]:
            failures.append(f"every reply needs at least {expect['min_words']} words, found one with {fewest}")
    if "distinct_codes" in expect:
        codes = {code.upper() for code in NIGHT_OWL_CODE.findall(text)}
        if len(codes) != expect["distinct_codes"]:
            failures.append(f"expected {expect['distinct_codes']} distinct code(s), found {sorted(codes)}")
    return failures


# --- running ---


def _seed_codes(state_file: Path, codes: dict[str, str]) -> None:
    """Pretend these emails were issued a Night Owl code earlier."""
    records = {normalize_email(email): {"code": code, "issued_at": "2026-11-30T21:15:00-05:00"} for email, code in codes.items()}
    state_file.write_text(json.dumps(records), encoding="utf-8")


def run_scenario(scenario: dict, make_agent: Callable) -> dict:
    """Play one scenario turn by turn. make_agent(now, state_file) returns an agent."""
    turns = [turn if isinstance(turn, dict) else {"say": turn} for turn in scenario["turns"]]
    failures, replies, turn_stats = [], [], []
    customer_text = ""
    with tempfile.TemporaryDirectory() as state_dir:  # every scenario starts with no codes issued
        state_file = Path(state_dir) / "codes.json"
        if scenario.get("seed_codes"):
            _seed_codes(state_file, scenario["seed_codes"])
        agent = make_agent(scenario.get("now", DEFAULT_NOW), state_file)
        llm_calls = getattr(agent.llm, "calls", [])

        for number, turn in enumerate(turns, 1):
            marks = len(agent.trace), len(llm_calls), len(agent.guard_events)
            reply = agent.reply(turn["say"])
            customer_text += turn["say"] + "\n"
            calls, requests, events = agent.trace[marks[0]:], llm_calls[marks[1]:], agent.guard_events[marks[2]:]
            replies.append(reply)

            facts = set().union(*(guard.collect_facts(call["result"]) for call in agent.trace))
            failures += [f"turn {number}: ungrounded {v.rule}: {v.detail}" for v in guard.check(reply, facts, customer_text)]
            if reply.startswith(SAFE_FALLBACK) or reply == LLM_UNAVAILABLE:
                failures.append(f"turn {number}: fell back to a fixed reply")
            expect = turn.get("expect", {})
            per_turn = failed_checks([reply], [call["tool"] for call in calls], expect, calls)
            if "max_llm_calls" in expect and len(requests) > expect["max_llm_calls"]:
                per_turn.append(f"used {len(requests)} model call(s), at most {expect['max_llm_calls']} expected")
            failures += [f"turn {number}: {failure}" for failure in per_turn]

            actions = {event["action"] for event in events}
            turn_stats.append({
                "llm_calls": len(requests),
                "seconds": sum(request["seconds"] for request in requests),
                "prompt_tokens": sum(request["prompt_tokens"] for request in requests),
                "completion_tokens": sum(request["completion_tokens"] for request in requests),
                "cost": sum(request["cost"] or 0 for request in requests),
                "rewrite": "rewrite" in actions,
                "fallback": "fallback" in actions,
                "repaired": "repaired" in actions,
            })

    failures += failed_checks(replies, [call["tool"] for call in agent.trace], scenario.get("expect", {}), agent.trace)
    return {
        "id": scenario["id"],
        "turns": [turn["say"] for turn in turns],
        "replies": replies,
        "trace": [{"tool": call["tool"], "arguments": call["arguments"]} for call in agent.trace],
        "guard_events": agent.guard_events,
        "turn_stats": turn_stats,
        "failures": failures,
    }


def summarize(results: list[dict]) -> dict:
    turns = [stats for result in results for stats in result["turn_stats"]]
    seconds = sorted(stats["seconds"] for stats in turns)
    count = len(turns) or 1
    return {
        "runs": len(results),
        "runs_passed": sum(not result["failures"] for result in results),
        "turns": len(turns),
        "llm_calls_per_turn": round(sum(stats["llm_calls"] for stats in turns) / count, 2),
        "latency_p50_s": round(statistics.median(seconds), 2) if seconds else 0,
        "latency_p95_s": round(seconds[min(len(seconds) - 1, int(0.95 * len(seconds)))], 2) if seconds else 0,
        "prompt_tokens": sum(stats["prompt_tokens"] for stats in turns),
        "completion_tokens": sum(stats["completion_tokens"] for stats in turns),
        "cost_usd": round(sum(stats["cost"] for stats in turns), 6),
        "rewrite_rate": round(sum(stats["rewrite"] for stats in turns) / count, 3),
        "fallback_rate": round(sum(stats["fallback"] for stats in turns) / count, 3),
        "repair_rate": round(sum(stats["repaired"] for stats in turns) / count, 3),
    }


# --- reporting ---


def report(scenario: dict, result: dict) -> str:
    lines = [f"{'PASS' if not result['failures'] else 'FAIL'}  {scenario['id']}  ({scenario['why']})"]
    lines += [f"      ! {failure}" for failure in result["failures"]]
    tools = ", ".join(f"{call['tool']}({call['arguments']})" for call in result["trace"]) or "none"
    lines.append(f"      clock: {scenario.get('now', DEFAULT_NOW)}   tools: {tools}")
    if result["guard_events"]:
        lines.append(f"      guard: {result['guard_events']}")
    for turn, reply in zip(result["turns"], result["replies"]):
        lines.append(f"      customer> {turn}")
        lines += [f"      kestrel > {line}" for line in reply.splitlines()]
    return "\n".join(lines)


def format_summary(summary: dict, scenarios: list[dict], by_id: dict[str, list[dict]]) -> str:
    safety = [s["id"] for s in scenarios if s.get("safety")]
    safety_runs = [r for sid in safety for r in by_id[sid]]
    return "\n".join([
        f"runs passed: {summary['runs_passed']}/{summary['runs']}   "
        f"scenarios passing every run: {sum(all(not r['failures'] for r in by_id[s['id']]) for s in scenarios)}/{len(scenarios)}   "
        f"safety runs passed: {sum(not r['failures'] for r in safety_runs)}/{len(safety_runs)}",
        f"per turn: {summary['llm_calls_per_turn']} LLM calls, latency p50 {summary['latency_p50_s']} s, "
        f"p95 {summary['latency_p95_s']} s",
        f"guard: rewrite {summary['rewrite_rate']:.1%} of turns, fallback {summary['fallback_rate']:.1%}, "
        f"repaired in code {summary['repair_rate']:.1%}",
        f"tokens: {summary['prompt_tokens']:,} in, {summary['completion_tokens']:,} out   "
        f"cost: ${summary['cost_usd']:.4f} for {summary['runs']} conversations",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="run the scenario with this id")
    parser.add_argument("--model", help="OpenRouter model slug (default: the agent's default)")
    parser.add_argument("--repeat", type=int, default=3, help="runs per scenario (default 3)")
    parser.add_argument("--workers", type=int, default=4, help="conversations run in parallel (default 4)")
    parser.add_argument("--save-run", help="write the summary and every failing (or first) run to this file")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if args.only:
        scenarios = [scenario for scenario in scenarios if scenario["id"] == args.only]
        if not scenarios:
            sys.exit(f"No scenario with id {args.only!r}")

    def make_agent(now, state_file):
        return build_agent(now, state_file, args.model)

    jobs = [scenario for scenario in scenarios for _ in range(args.repeat)]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda scenario: run_scenario(scenario, make_agent), jobs))

    by_id: dict[str, list[dict]] = {scenario["id"]: [] for scenario in scenarios}
    for result in results:
        by_id[result["id"]].append(result)

    reports = []
    for scenario in scenarios:
        runs = by_id[scenario["id"]]
        passed = sum(not run["failures"] for run in runs)
        label = "PASS " if passed == len(runs) else "FLAKY" if passed else "FAIL "
        print(f"{label} {passed}/{len(runs)}  {scenario['id']}{'  [safety]' if scenario.get('safety') else ''}")
        for run in runs:
            for failure in run["failures"]:
                print(f"         ! {failure}")
        shown = next((run for run in runs if run["failures"]), runs[0])
        reports.append(report(scenario, shown))

    summary = summarize(results)
    model = args.model or os.environ.get("KESTREL_MODEL") or DEFAULT_MODEL
    text = format_summary(summary, scenarios, by_id)
    print(f"\nmodel: {model}\n{text}")
    if args.save_run:
        header = f"Kestrel scenario eval, {datetime.now():%Y-%m-%d %H:%M}, model: {model}, {args.repeat} run(s) per scenario\n"
        body = "\n\n".join(reports)
        Path(args.save_run).write_text(f"{header}{text}\n\nOne run per scenario (a failing one if any):\n\n{body}\n", encoding="utf-8")
    sys.exit(0 if summary["runs_passed"] == summary["runs"] else 1)


if __name__ == "__main__":
    main()
