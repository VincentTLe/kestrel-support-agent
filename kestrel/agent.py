"""The agent loop, written by hand.

One customer turn: call the model; if it asks for tools, run them and call it
again; when it answers in text, check the text before it is returned. The
model chooses what to do next. Code runs the tools, refuses arguments the
customer never gave, caps the loop, and has the last word on what the
customer sees.
"""

import json
import logging

from . import guard
from .clock import Clock
from .llm import LLM, LLMError
from .features import Feature
from .prompt import build_system_prompt
from .tools import run_tool

log = logging.getLogger(__name__)

MAX_STEPS = 6  # model calls per customer turn; the last one must answer in text
SAFE_FALLBACK = (
    "Sorry, I couldn't put together a reliable answer to that. "
    "Could you rephrase it, or tell me a bit more about what you need?"
)
LLM_UNAVAILABLE = "Sorry, I'm having trouble reaching our systems right now. Please try that again in a moment."
# Sent with tool_choice="none" on the last step. In live runs tool_choice alone
# made the model stop with an empty message; this note gets a real answer.
FINAL_STEP_NOTE = {
    "role": "user",
    "content": "[Automated note, not the customer] No more tool calls are available this turn. "
    "Answer the customer's last message now, using only the tool results above.",
}


class Agent:
    def __init__(self, llm: LLM, features: list[Feature], clock: Clock):
        self.llm = llm
        self.features = list(features)
        self._tools = {tool.name: tool for feature in self.features for tool in feature.tools}
        self._schemas = [tool.schema() for tool in self._tools.values()]
        self._policy = guard.GuardPolicy.from_features(self.features)
        self.clock = clock
        self._facts: set[str] = set()  # codes, links and prices returned by tools so far
        self.messages: list[dict] = []  # the conversation, without the system prompt
        self.trace: list[dict] = []  # every tool call, for --debug, transcripts and evals
        self.guard_events: list[dict] = []

    def reply(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        system = {"role": "system", "content": build_system_prompt(self.clock.now(), self.features)}
        scratch: list[dict] = []  # a rejected draft and its correction: sent to the model, never stored
        rewritten = False

        for step in range(MAX_STEPS):
            last_step = step == MAX_STEPS - 1
            final_note = [FINAL_STEP_NOTE] if last_step else []
            try:
                message = self.llm.complete(
                    [system, *self.messages, *scratch, *final_note],
                    self._schemas,
                    tool_choice="none" if last_step else None,
                )
            except LLMError as error:
                log.error("model call failed: %s", error)
                return self._finish(LLM_UNAVAILABLE)

            if message.get("tool_calls") and not last_step:
                self._run_tool_calls(message)
                scratch = []
                continue

            draft = (message.get("content") or "").strip()
            violations = (
                guard.check(draft, self._facts, self._customer_text(), self._policy)
                if draft
                else [guard.Violation("empty", "The reply was empty.")]
            )
            if violations and not rewritten and not last_step:
                rewritten = True
                self._record_guard(violations, "rewrite")
                scratch = [{"role": "assistant", "content": draft}, _correction(violations)]
                continue
            if violations:
                self._record_guard(violations, "fallback")
                return self._finish(SAFE_FALLBACK)

            text, repairs = guard.repair(draft, self._facts, self._policy)
            if repairs:
                self.guard_events.append({"rules": repairs, "action": "repaired"})
            return self._finish(text)

        return self._finish(SAFE_FALLBACK)  # not reached: the last step always returns

    def _customer_text(self) -> str:
        return "\n".join(m["content"] for m in self.messages if m["role"] == "user" and isinstance(m["content"], str))

    def _run_tool_calls(self, message: dict) -> None:
        # Store only the fields the API accepts back; providers add extras.
        calls = [
            {"id": call["id"], "type": "function", "function": {
                "name": call["function"]["name"], "arguments": call["function"].get("arguments") or "{}"}}
            for call in message["tool_calls"]
        ]
        self.messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
        customer_text = self._customer_text()
        for call in calls:
            name, arguments = call["function"]["name"], call["function"]["arguments"]
            result = run_tool(self._tools, name, arguments, customer_text)
            self._facts |= guard.collect_facts(result, policy=self._policy)
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
    problems = " ".join(violation.detail for violation in violations)
    return {
        "role": "user",
        "content": "[Automated check, not the customer] Your draft was not sent. "
        f"{problems} Write a new reply to the customer's last message. Use only codes, links, prices and "
        "percentages that a tool returned; if you have none, say what you can and cannot do instead. "
        "Do not mention this check or your earlier draft.",
    }
