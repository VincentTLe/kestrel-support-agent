"""The case file: what the agent has learned in this conversation, kept by code.

It records what the customer has told the tools (email, height, budget...),
which orders have been verified, and where the last turn left the customer's
problem. The model reads a short summary of it on every call, which is what
lets old tool results be compacted without losing the thread.

Nothing here is feature-specific: tool arguments and results are recorded
generically, so a new feature's details land in the case file for free.
"""

import json
from dataclasses import dataclass, field

KEEP_RECENT_TURNS = 2  # tool results older than this many customer turns are compacted
DROP_WHEN_COMPACT = {"description", "sizes", "fitting_sizes", "riding_style", "final_sale", "note", "options", "category"}


@dataclass
class CaseState:
    known: dict[str, str] = field(default_factory=dict)  # argument name -> latest value the customer gave
    verified_orders: dict[str, str] = field(default_factory=dict)  # order number -> one-line status
    last_status: str | None = None
    last_next_step: str | None = None

    def record_tool(self, arguments: dict, result: dict) -> None:
        if result.get("error") in ("not_provided_by_customer", "invalid_arguments", "unknown_tool"):
            return  # these values did not come from the customer, or were not usable
        for name, value in arguments.items():
            if value not in (None, ""):
                self.known[name] = str(value)
        if "order_number" in result and "status" in result:
            items = ", ".join(item["name"] for item in result.get("items", []))
            self.verified_orders[result["order_number"]] = f"{result['status']}; {items}"

    def record_reply(self, status: str | None, next_step: str | None) -> None:
        self.last_status, self.last_next_step = status, next_step

    def summary(self) -> str:
        lines = []
        if self.verified_orders:
            lines.append("Verified orders: " + "; ".join(f"{n} ({s})" for n, s in self.verified_orders.items()))
        if self.known:
            lines.append("Customer gave: " + ", ".join(f"{k}={v}" for k, v in self.known.items()))
        if self.last_status:
            step = f" Your last next step: {self.last_next_step}" if self.last_next_step else ""
            lines.append(f"Last turn ended as: {self.last_status}.{step}")
        return "\n".join(lines)


def _compact(value):
    if isinstance(value, dict):
        return {k: _compact(v) for k, v in value.items() if k not in DROP_WHEN_COMPACT}
    if isinstance(value, list):
        return [_compact(v) for v in value]
    return value


def compact_history(messages: list[dict], keep_turns: int = KEEP_RECENT_TURNS) -> list[dict]:
    """A copy of the history where tool results older than `keep_turns` customer turns are shortened.

    Links, codes, prices and statuses stay; long descriptions and size tables go.
    """
    user_positions = [i for i, m in enumerate(messages) if m["role"] == "user"]
    if len(user_positions) <= keep_turns:
        return list(messages)
    cutoff = user_positions[-keep_turns]
    compacted = []
    for i, message in enumerate(messages):
        if i < cutoff and message["role"] == "tool":
            try:
                content = json.dumps(_compact(json.loads(message["content"])))
            except (json.JSONDecodeError, TypeError):
                content = message["content"]
            message = {**message, "content": content}
        compacted.append(message)
    return compacted
