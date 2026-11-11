"""Customer journeys with a simulated customer.

    python -m evals.simulate                        # every journey, 3 runs each
    python -m evals.simulate --only typo-order-number --repeat 1
    python -m evals.simulate --save-run evals/journeys-run.txt

Scripted scenarios (run_eval.py) check what the agent says to fixed messages.
They cannot tell whether the agent asks the right question, because the next
scripted message is the same whatever it asked. Here a second model plays a
customer: it has a goal and some facts it gives only when asked. The
conversation runs until code decides the problem is solved (from the tools
called and the replies, never from the simulated customer's opinion) or the
turn limit is reached. Then the customer rates satisfaction (CSAT, 1-5) and
effort (CES, 1 easy - 5 hard).
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
from kestrel.__main__ import DEFAULT_MODEL, build_agent, load_dotenv
from kestrel.llm import LLMError, OpenRouterClient

from .run_eval import DEFAULT_NOW, failed_checks

JOURNEYS = Path(__file__).resolve().parent / "journeys.json"

CUSTOMER_PROMPT = """\
You are role-playing a customer of Kestrel Cycles, a bike shop, chatting with its support assistant.
Your situation: {situation}
Your goal: {goal}
You have already sent your first message.
Facts you know, to share ONLY when the assistant asks for them or clearly needs them:
{reveal}
Rules:
- Write like a real customer: one or two short, casual sentences.
- Never invent facts beyond the ones above. If asked something you do not know, say you don't know.
- If the assistant restates your problem correctly, confirm it.
- When your goal is met, or the assistant has clearly done everything it can, reply with exactly: DONE"""
RATING_PROMPT = (
    "The conversation is over. As this customer, rate your satisfaction with the help from 1 (very unhappy) "
    "to 5 (very happy), and the effort it took you from 1 (very easy) to 5 (very hard). "
    'Reply only with JSON: {"csat": n, "effort": n}'
)


def _rating(text: str) -> dict:
    found = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        data = json.loads(found.group(0)) if found else {}
        return {"csat": int(data["csat"]), "effort": int(data["effort"])}
    except (ValueError, KeyError, TypeError):
        return {"csat": None, "effort": None}


def run_journey(journey: dict, make_agent: Callable, customer) -> dict:
    """Play one journey. make_agent(now, state_file) returns an agent; customer has .complete(messages, tools)."""
    reveal = "\n".join(f"- {name}: {value}" for name, value in journey.get("reveal", {}).items()) or "- nothing else"
    persona = CUSTOMER_PROMPT.format(situation=journey["situation"], goal=journey["goal"], reveal=reveal)
    convo = [{"role": "system", "content": persona}]
    said, replies, said_log, resolved_turn = journey["opening"], [], [], None
    customer_calls = 0

    with tempfile.TemporaryDirectory() as state_dir:
        agent = make_agent(journey.get("now", DEFAULT_NOW), Path(state_dir) / "codes.json")
        for turn in range(1, journey.get("max_turns", 4) + 1):
            said_log.append(said)
            convo.append({"role": "assistant", "content": said})
            reply = agent.reply(said)
            replies.append(reply)
            convo.append({"role": "user", "content": reply})
            if not failed_checks(replies, [c["tool"] for c in agent.trace], journey["resolved_when"], agent.trace):
                resolved_turn = turn
                break
            if turn == journey.get("max_turns", 4):
                break
            said = (customer.complete(convo, []).get("content") or "").strip()
            customer_calls += 1
            if not said or said.upper().startswith("DONE"):
                break
        try:
            rating = _rating(customer.complete(convo + [{"role": "user", "content": RATING_PROMPT}], []).get("content"))
            customer_calls += 1
        except LLMError:
            rating = {"csat": None, "effort": None}

    failures = [] if resolved_turn else [f"not resolved within {len(replies)} turn(s)"]
    failures += failed_checks(replies, [c["tool"] for c in agent.trace], journey.get("must_not", {}), agent.trace)
    customer_text, facts = "", set()
    for number, (text, reply) in enumerate(zip(said_log, replies), 1):
        customer_text += text + "\n"
        facts |= set().union(*(guard.collect_facts(c["result"]) for c in agent.trace))
        failures += [f"turn {number}: ungrounded {v.rule}" for v in guard.check(reply, facts, customer_text)]
    calls = getattr(agent.llm, "calls", [])
    return {
        "id": journey["id"],
        "turns": said_log,
        "replies": replies,
        "trace": [{"tool": c["tool"], "arguments": c["arguments"]} for c in agent.trace],
        "resolved_turn": resolved_turn,
        "rating": rating,
        "agent_calls": len(calls),
        "agent_cost": sum(c["cost"] or 0 for c in calls),
        "customer_calls": customer_calls,
        "failures": failures,
    }


def summarize(results: list[dict]) -> dict:
    resolved = [r for r in results if r["resolved_turn"]]
    csat = [r["rating"]["csat"] for r in results if r["rating"]["csat"]]
    effort = [r["rating"]["effort"] for r in results if r["rating"]["effort"]]
    count = len(results) or 1
    return {
        "runs": len(results),
        "passed": sum(not r["failures"] for r in results),
        "resolution_rate": round(len(resolved) / count, 3),
        "turns_to_resolution": round(statistics.mean(r["resolved_turn"] for r in resolved), 2) if resolved else None,
        "csat": round(statistics.mean(csat), 2) if csat else None,
        "effort": round(statistics.mean(effort), 2) if effort else None,
        "agent_calls_per_journey": round(sum(r["agent_calls"] for r in results) / count, 2),
        "agent_cost_usd": round(sum(r["agent_cost"] for r in results), 4),
    }


def report(journey: dict, result: dict) -> str:
    rating = result["rating"]
    lines = [
        f"{'PASS' if not result['failures'] else 'FAIL'}  {journey['id']}  ({journey['why']})",
        *[f"      ! {failure}" for failure in result["failures"]],
        f"      resolved at turn: {result['resolved_turn']}   CSAT {rating['csat']}   effort {rating['effort']}",
        "      tools: " + (", ".join(f"{c['tool']}({c['arguments']})" for c in result["trace"]) or "none"),
    ]
    for said, reply in zip(result["turns"], result["replies"]):
        lines.append(f"      customer> {said}")
        lines += [f"      kestrel > {line}" for line in reply.splitlines()]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="run the journey with this id")
    parser.add_argument("--model", help="model for the agent (default: the agent's default)")
    parser.add_argument("--customer-model", help="model for the simulated customer (default: the agent's model)")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--save-run", help="write the summary and one run per journey (a failing one if any)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()

    journeys = json.loads(JOURNEYS.read_text(encoding="utf-8"))
    if args.only:
        journeys = [j for j in journeys if j["id"] == args.only] or sys.exit(f"No journey with id {args.only!r}")
    model = args.model or os.environ.get("KESTREL_MODEL") or DEFAULT_MODEL

    def play(journey):
        customer = OpenRouterClient(os.environ["OPENROUTER_API_KEY"], args.customer_model or model)
        return run_journey(journey, lambda now, state: build_agent(now, state, args.model), customer)

    jobs = [journey for journey in journeys for _ in range(args.repeat)]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(play, jobs))

    reports = []
    for journey in journeys:
        runs = [r for r in results if r["id"] == journey["id"]]
        passed = sum(not r["failures"] for r in runs)
        turns = [r["resolved_turn"] for r in runs if r["resolved_turn"]]
        csat = [r["rating"]["csat"] for r in runs if r["rating"]["csat"]]
        label = "PASS " if passed == len(runs) else "FLAKY" if passed else "FAIL "
        print(f"{label} {passed}/{len(runs)}  {journey['id']}   turns {turns}   CSAT {csat}")
        for run in runs:
            for failure in run["failures"]:
                print(f"         ! {failure}")
        reports.append(report(journey, next((r for r in runs if r["failures"]), runs[0])))

    s = summarize(results)
    text = (
        f"journeys passed: {s['passed']}/{s['runs']}   resolved: {s['resolution_rate']:.0%}   "
        f"turns to resolution: {s['turns_to_resolution']}   CSAT: {s['csat']}/5   effort (CES): {s['effort']}/5\n"
        f"agent: {s['agent_calls_per_journey']} model calls per journey, ${s['agent_cost_usd']:.4f} in total"
    )
    print(f"\nmodel: {model}\n{text}")
    if args.save_run:
        header = f"Kestrel customer journeys, {datetime.now():%Y-%m-%d %H:%M}, model: {model}, {args.repeat} run(s) each\n"
        Path(args.save_run).write_text(header + text + "\n\n" + "\n\n".join(reports) + "\n", encoding="utf-8")
    sys.exit(0 if s["passed"] == s["runs"] else 1)


if __name__ == "__main__":
    main()
