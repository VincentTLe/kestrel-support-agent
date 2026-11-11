"""Deterministic checks on every reply before the customer sees it.

check() enforces the COO's condition: anything a customer could act on (a
discount code, a link, an email address, a price, a percentage) must have come
from a tool result in this conversation, or from the customer. A violation
sends the draft back to the model once; a second one ends in a fixed reply.

repair() applies the brand voice's formatting in code (copyable tokens on
their own line, no markdown, at most two emoji), so a formatting slip never
costs another model call or a reworded answer.

Neither can judge a free-text claim such as "we offer free shipping"; the
prompt and the scenario eval cover those.
"""

import re
from dataclasses import dataclass

from .orders import normalize_order_number
from .promo import DISCOUNT_PERCENT

CODE = re.compile(r"NIGHTOWL-[A-Z0-9]+", re.IGNORECASE)
# Upper-case letters and digits mixed, like KESTREL50 or 1Z84KC0002. Product
# words (MIPS, XL, 1x12, 29er) do not mix both, or are lower case.
CODE_LIKE = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
MIN_CODE_LENGTH = 5
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
LINK = re.compile(
    r"https?://\S+|www\.\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|io|co|us|shop|store|example)\b(?:/\S*)?",
    re.IGNORECASE,
)
ORDER_NUMBER = re.compile(r"\bKC-\d+\b")
CUSTOMER_ORDER_NUMBER = re.compile(r"\bKC[\s-]?\d+\b", re.IGNORECASE)
AMOUNT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent\b)", re.IGNORECASE)
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]️?")
MARKDOWN_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.MULTILINE)
WRAPPING = "()[]<>\"'`*.,;:!?"
MAX_EMOJI = 2


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str


# --- facts ---


def _amount(text: str | float) -> str:
    value = float(str(text).replace(",", ""))
    return f"${value:.0f}" if value == int(value) else f"${value:.2f}"


def _links(text: str) -> list[str]:
    return [match.strip(WRAPPING) for match in LINK.findall(EMAIL.sub(" ", text))]


def _code_like(text: str) -> list[str]:
    text = CODE.sub(" ", LINK.sub(" ", EMAIL.sub(" ", text)))
    return [token for token in CODE_LIKE.findall(text) if len(token) >= MIN_CODE_LENGTH]


def collect_facts(tool_result, key: str = "") -> set[str]:
    """Every code, link, email, identifier and price that appears in a tool result."""
    if isinstance(tool_result, dict):
        return set().union(*(collect_facts(value, name) for name, value in tool_result.items()))
    if isinstance(tool_result, list):
        return set().union(*(collect_facts(value, key) for value in tool_result))
    if isinstance(tool_result, bool):
        return set()
    if isinstance(tool_result, (int, float)):
        return {_amount(tool_result)} if "price" in key or "usd" in key else set()
    if isinstance(tool_result, str):
        return (
            {code.upper() for code in CODE.findall(tool_result)}
            | set(_links(tool_result))
            | {email.casefold() for email in EMAIL.findall(tool_result)}
            | set(CODE_LIKE.findall(tool_result))
            | {_amount(number) for number in AMOUNT.findall(tool_result)}
        )
    return set()


# --- hard rules ---


def check(reply: str, allowed: set[str], customer_text: str = "") -> list[Violation]:
    """Return everything in `reply` that neither a tool nor the customer supplied."""
    customer_emails = {email.casefold() for email in EMAIL.findall(customer_text)}
    customer_orders = {normalize_order_number(number) for number in CUSTOMER_ORDER_NUMBER.findall(customer_text)}
    customer_amounts = {_amount(number) for number in NUMBER.findall(customer_text)}

    violations = []
    for email in EMAIL.findall(reply):
        if email.casefold() not in allowed | customer_emails:
            violations.append(Violation("unknown_email", f"{email} did not come from a tool or the customer."))
    for link in _links(reply):
        if link not in allowed:
            violations.append(Violation("unknown_link", f"{link} did not come from a tool result."))
    for code in CODE.findall(reply):
        if code.upper() not in allowed:
            violations.append(Violation("unissued_code", f"Leave {code} out entirely, even in a refusal: no tool issued it."))
    # A refusal that repeats the customer's number ("I can't give 50% off") is
    # still asked to drop it: the check cannot tell a refusal from a promise.
    for token in _code_like(reply):
        if token not in allowed and token not in customer_orders:
            violations.append(Violation(
                "unknown_code", f"Leave {token} out entirely, even in a refusal: no tool returned it."
            ))
    for percent in PERCENT.findall(reply):
        if float(percent) != DISCOUNT_PERCENT:
            violations.append(Violation(
                "unknown_percent",
                f"Leave {percent}% out entirely, even in a refusal: the only real discount is "
                f"Night Owl's {DISCOUNT_PERCENT}%.",
            ))
    for number in AMOUNT.findall(reply):
        if _amount(number) not in allowed | customer_amounts:
            violations.append(Violation(
                "unknown_amount", f"Leave ${number} out: no tool returned it. Quote amounts exactly as a tool gave them."
            ))
    return violations


# --- formatting, repaired in code ---


def repair(reply: str, allowed: set[str]) -> tuple[str, list[str]]:
    """Apply the brand voice's formatting rules. Returns the text and the rules applied."""
    applied = []

    text = MARKDOWN_HEADING.sub("", reply).replace("**", "").replace("__", "")
    if text != reply:
        applied.append("markdown")

    if len(EMOJI.findall(text)) > MAX_EMOJI:
        seen = 0

        def keep_first(match: re.Match) -> str:
            nonlocal seen
            seen += 1
            return match.group(0) if seen <= MAX_EMOJI else ""

        text = EMOJI.sub(keep_first, text)
        text = "\n".join(re.sub(r"(?<=\S)[ \t]{2,}", " ", line).rstrip() for line in text.split("\n"))
        applied.append("too_many_emoji")

    text, moved = _own_lines(text, allowed)
    if moved:
        applied.append("own_line")
    return text, applied


def _copyable(line: str, allowed: set[str]) -> list[tuple[int, str]]:
    """Codes, links and order numbers in `line` that came from a tool, with their positions."""
    found = []
    for match in LINK.finditer(line):
        link = match.group(0).strip(WRAPPING)
        if link in allowed:
            found.append((match.start(), link))
    for pattern in (CODE, ORDER_NUMBER):
        for match in pattern.finditer(line):
            token = match.group(0).upper()
            if token in allowed:
                found.append((match.start(), token))
    return sorted(found)


def _own_lines(text: str, allowed: set[str]) -> tuple[str, bool]:
    """Make sure every copyable token stands alone on at least one line."""
    lines = text.split("\n")
    alone = {line.strip().upper() for line in lines}
    out, moved = [], False
    for line in lines:
        out.append(line)
        pending = []
        for _, token in _copyable(line, allowed):
            if token.upper() not in alone:
                alone.add(token.upper())
                pending.append(token)
        if not pending:
            continue
        moved = True
        # A token that ends the sentence moves down; one in mid-sentence is repeated below it.
        at_end = re.search(re.escape(pending[-1]) + r"[\s.,;:!?)\]]*$", line, re.IGNORECASE)
        if at_end:
            before = line[: at_end.start()].rstrip()
            if before:
                out[-1] = before
            else:
                out.pop()
        out.extend(pending)
    return "\n".join(out), moved
