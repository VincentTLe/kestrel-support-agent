"""Deterministic check on every reply before the customer sees it.

Hard rules protect the COO's condition: a discount code or a link is shown only
if a tool returned it in this conversation. Soft rules are the brand voice's
formatting. The guard cannot judge free-text claims ("free shipping"); the
prompt and the scenario eval cover those.
"""

import re
from dataclasses import dataclass

CODE = re.compile(r"NIGHTOWL-[A-Z0-9]+")
# Any URL, plus anything mentioning Kestrel's domain (catches invented support emails).
LINK = re.compile(r"https?://\S+|\S*kestrelcycles\.example\S*")
ORDER_NUMBER = re.compile(r"KC-\d+")
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
WRAPPING = "()[]<>\"'`*.,;:!?"
MAX_EMOJI = 2


@dataclass(frozen=True)
class Violation:
    rule: str
    hard: bool
    detail: str


def _links(text: str) -> list[str]:
    return [match.strip(WRAPPING) for match in LINK.findall(text)]


def collect_facts(tool_result) -> set[str]:
    """Every link and discount code that appears anywhere in a tool result."""
    if isinstance(tool_result, dict):
        return set().union(*(collect_facts(value) for value in tool_result.values()))
    if isinstance(tool_result, list):
        return set().union(*(collect_facts(value) for value in tool_result))
    if isinstance(tool_result, str):
        return set(CODE.findall(tool_result)) | set(_links(tool_result))
    return set()


def check(reply: str, allowed: set[str]) -> list[Violation]:
    """Return what is wrong with `reply`, given the facts tools have returned."""
    violations = []
    for code in CODE.findall(reply):
        if code not in allowed:
            violations.append(Violation("unissued_code", True, f"{code} was not issued by a tool."))
    for link in _links(reply):
        if link not in allowed:
            violations.append(Violation("unknown_link", True, f"{link} did not come from a tool result."))

    if len(EMOJI.findall(reply)) > MAX_EMOJI:
        violations.append(Violation("too_many_emoji", False, f"Use at most {MAX_EMOJI} emoji per reply."))

    for line in reply.splitlines():
        tokens = CODE.findall(line) + _links(line) + ORDER_NUMBER.findall(line)
        if tokens and line.strip() != tokens[0]:
            violations.append(
                Violation("own_line", False, f"Put {tokens[0]} alone on its own line, with no other text.")
            )
            break
    return violations
