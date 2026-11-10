"""Terminal chat loop: python -m kestrel [--now "2026-12-01 21:30"]"""

import argparse
import logging
import os
import sys
from pathlib import Path

from .agent import Agent
from .clock import Clock, parse_now
from .data import load_catalog, load_orders
from .llm import OpenRouterClient
from .promo import PromoStore
from .tools import build_tools

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "anthropic/claude-haiku-5.5"
DEFAULT_STATE_FILE = ROOT / "state" / "promo_codes.json"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines into the environment; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def build_agent(now: str | None, state_file: Path | str | None, model: str | None = None) -> Agent:
    """Wire the agent from flags, falling back to environment variables."""
    load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key.")
    now = now or os.environ.get("KESTREL_NOW")
    clock = Clock(parse_now(now) if now else None)
    store = PromoStore(Path(state_file or os.environ.get("KESTREL_STATE_FILE") or DEFAULT_STATE_FILE))
    llm = OpenRouterClient(api_key, model or os.environ.get("KESTREL_MODEL") or DEFAULT_MODEL)
    return Agent(llm, build_tools(load_orders(), load_catalog(), clock, store), clock)


def main() -> None:
    parser = argparse.ArgumentParser(prog="kestrel", description="Kestrel Cycles support agent")
    parser.add_argument("--now", help='pretend it is this Eastern time, e.g. "2026-12-01 21:30" (or set KESTREL_NOW)')
    parser.add_argument("--state-file", help="where Night Owl codes are stored (default: state/promo_codes.json)")
    parser.add_argument("--model", help="OpenRouter model slug (or set KESTREL_MODEL)")
    parser.add_argument("--transcript", help="also write the conversation, with tool calls, to this markdown file")
    parser.add_argument("--debug", action="store_true", help="print tool calls and guard decisions to stderr")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr, sys.stdin):
        stream.reconfigure(encoding="utf-8")  # emoji on Windows consoles
    logging.basicConfig(level=logging.WARNING if args.debug else logging.CRITICAL, format="  [%(name)s] %(message)s")

    agent = build_agent(args.now, args.state_file, args.model)
    transcript = open(args.transcript, "w", encoding="utf-8") if args.transcript else None
    if transcript:
        transcript.write(f"Clock: {agent.clock.now():%Y-%m-%d %I:%M %p} Eastern\n")

    print("Kestrel Cycles support. Type 'quit' to leave.\n")
    while True:
        try:
            user_text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user_text.lower() in {"quit", "exit"}:
            break
        if not user_text:
            continue
        if not sys.stdin.isatty():
            print(user_text)  # echo piped input so the output reads as a conversation

        calls_before = len(agent.trace)
        reply = agent.reply(user_text)
        new_calls = agent.trace[calls_before:]
        if args.debug:
            for call in new_calls:
                print(f"  [tool] {call['tool']}({call['arguments']}) -> {call['result']}", file=sys.stderr)
        print(f"\nkestrel> {reply}\n")

        if transcript:
            transcript.write(f"\n**Customer:** {user_text}\n\n")
            for call in new_calls:
                transcript.write(f"    [tool] {call['tool']}({call['arguments']})\n")
            transcript.write(f"**Kestrel:**\n\n```text\n{reply}\n```\n")
            transcript.flush()
    if transcript:
        transcript.close()


if __name__ == "__main__":
    main()
