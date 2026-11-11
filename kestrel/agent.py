"""The agent loop, written by hand.

One customer turn: call the model; if it asks for tools, run them and call it
again; when it answers in text, pass the text through the guard before it is
returned. The model chooses what to do next. Code runs the tools, caps the
loop, and has the last word on what the customer sees.
"""

import json
import logging

from . import guard
from .clock import Clock
from .llm import LLM, LLMError
from .prompt import build_system_prompt
from .tools import Tool, run_tool

log = logging.getLogger(__name__)

MAX_STEPS = 6  # model calls per customer turn
SAFE_FALLBACK = (
    "Sorry, I couldn't put together a reliable answer to that. "
    "Could you rephrase it, or tell me a bit more about what you need?"
)
LLM_UNAVAILABLE = "Sorry, I'm having trouble reaching our systems right now. Please try that again in a moment."


class Agent:
    def __init__(self, llm: LLM, tools: dict[str, Tool], clock: Clock):
        self._llm = llm
        self._tools = tools
        self._schemas = [tool.schema() for tool in tools.values()]
        self.clock = clock
        self._facts: set[str] = set()  # links and codes returned by tools so far
        self.messages: list[dict] = []  # the conversation, without the system prompt
        self.trace: list[dict] = []  # every tool call, for --debug, transcripts and evals
        self.guard_events: list[dict] = []

    def reply(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        system = {"role": "system", "content": build_system_prompt(self.clock.now())}
        scratch: list[dict] = []  # a rejected draft and its correction: sent to the model, never stored
        rewritten = False

        for _ in range(MAX_STEPS):
            try:
                message = self._llm.complete([system, *self.messages, *scratch], self._schemas)
            except LLMError as error:
                log.error("model call failed: %s", error)
                return self._finish(LLM_UNAVAILABLE)

            if message.get("tool_calls"):
                self._run_tool_calls(message)
                scratch = []
                continue

            draft = (message.get("content") or "").strip()
            violations = guard.check(draft, self._facts) if draft else [guard.Violation("empty", True, "Empty reply.")]
            if not violations:
                return self._finish(draft)
            if not rewritten:
                rewritten = True
                self._record_guard(violations, "rewrite")
                scratch = [{"role": "assistant", "content": draft}, _correction(violations)]
                continue
            if any(violation.hard for violation in violations):
                self._record_guard(violations, "fallback")
                return self._finish(SAFE_FALLBACK)
            self._record_guard(violations, "let_through")
            return self._finish(draft)

        log.warning("gave up after %d model calls in one turn", MAX_STEPS)
        return self._finish(SAFE_FALLBACK)

    def _run_tool_calls(self, message: dict) -> None:
        # Store only the fields the API accepts back; providers add extras.
        calls = [
            {"id": call["id"], "type": "function", "function": {
                "name": call["function"]["name"], "arguments": call["function"].get("arguments") or "{}"}}
            for call in message["tool_calls"]
        ]
        self.messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
        for call in calls:
            name, arguments = call["function"]["name"], call["function"]["arguments"]
            result = run_tool(self._tools, name, arguments)
            self._facts |= guard.collect_facts(result)
            self.trace.append({"tool": name, "arguments": arguments, "result": result})
            self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})

    def _finish(self, text: str) -> str:
        self.messages.append({"role": "assistant", "content": text})
        return text

    def _record_guard(self, violations: list[guard.Violation], action: str) -> None:
        rules = [violation.rule for violation in violations]
        log.warning("guard: %s -> %s", rules, action)
        self.guard_events.append({"rules": rules, "action": action})


def _correction(violations: list[guard.Violation]) -> dict:
    """What the model is told about a rejected draft.

    A formatting slip must not cost the customer the content: in the first
    live runs the model answered a blunt "rewrite" with one bare line.
    """
    problems = " ".join(violation.detail for violation in violations)
    if any(violation.hard for violation in violations):
        instruction = (
            f"Your draft was not sent. {problems} Rewrite your reply to the customer's last message. "
            "Use only links and codes that a tool returned; if you have none, say what you can and cannot do instead."
        )
    else:
        instruction = (
            f"Your draft needs a formatting fix before it is sent. {problems} "
            "Send the full reply again with only that changed. Keep every other sentence as it was."
        )
    return {"role": "user", "content": f"[Automated check, not the customer] {instruction}"}
