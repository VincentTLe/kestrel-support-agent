"""Deterministic checks on every reply before the customer sees it.

Hard guardrails (check) block: anything a customer could act on (a code, a
link, an email address, a price, a percentage) or another customer's name must
have come from a tool result in this conversation, or from the customer. A
violation sends the draft back to the model once; a second one ends in a
fallback that still offers a way forward.

Soft guardrails (repair) never block: the brand voice's formatting (copyable
tokens on their own line, no markdown, at most two emoji, no emoji when the
customer is upset) is applied in code, so a formatting slip never costs
another model call or a reworded answer.

What counts as an issued code, a copyable identifier or an allowed percentage
comes from the features (GuardPolicy.from_features), so adding a feature does
not mean editing this file. Neither function can judge a free-text claim such
as "we offer free shipping"; the prompt and the eval cover those.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Iterable

# Upper-case letters and digits mixed, like KESTREL50 or 1Z84KC0002. Product
# words (MIPS, XL, 1x12, 29er) do not mix both, or are lower case.
CODE_LIKE = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
MIN_CODE_LENGTH = 5
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
LINK = re.compile(
    r"https?://\S+|www\.\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|io|co|us|shop|store|example)\b(?:/\S*)?",
    re.IGNORECASE,
)
AMOUNT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent\b)", re.IGNORECASE)
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]️?")
MARKDOWN_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.MULTILINE)
WRAPPING = "()[]<>\"'`*.,;:!?"
MAX_EMOJI = 2
FACT_KEYS = ("customer_name",)  # plain-text values collected as facts when a tool returns them
# The agent exists only in this chat: offering to act later is a capability it does not have.
UNKEEPABLE_PROMISE = re.compile(
    r"\b(I'll|I will|I can|let me|we'll|we will)\s+(follow up|keep an eye|notify|let you know|email you|"
    r"reach out|get back to you|check back|text you|call you)"
    r"|\bwould you like me to (check|look)\b[^.?!]{0,40}\b(later|again|back)\b"
    r"|\bwould you like me to (follow up|keep an eye|notify|email|remind|reach out|hold)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str


@dataclass(frozen=True)
class GuardPolicy:
    """What the features tell the guard to watch for."""

    issued: tuple[re.Pattern, ...] = ()
    copyable: tuple[re.Pattern, ...] = ()
    allowed_percents: frozenset[float] = frozenset()
    protected: tuple[str, ...] = ()
    customer_identifiers: tuple[Callable[[str], set[str]], ...] = field(default=())

    @classmethod
    def from_features(cls, features: Iterable) -> "GuardPolicy":
        features = list(features)
        return cls(
            issued=tuple(p for f in features for p in f.issued),
            copyable=tuple(p for f in features for p in f.copyable),
            allowed_percents=frozenset().union(*(f.allowed_percents for f in features)),
            protected=tuple(v for f in features for v in f.protected),
            customer_identifiers=tuple(fn for f in features for fn in f.customer_identifiers),
        )


@lru_cache(maxsize=1)
def default_policy() -> GuardPolicy:
    from .features import static_features  # late import: features import tools, not the other way round

    return GuardPolicy.from_features(static_features())


# --- facts ---


def _amount(text: str | float) -> str:
    value = float(str(text).replace(",", ""))
    return f"${value:.0f}" if value == int(value) else f"${value:.2f}"


def _links(text: str) -> list[str]:
    return [match.strip(WRAPPING) for match in LINK.findall(EMAIL.sub(" ", text))]


def collect_facts(tool_result, key: str = "", policy: GuardPolicy | None = None) -> set[str]:
    """Every code, link, email, identifier, price and customer name in a tool result."""
    policy = policy or default_policy()
    if isinstance(tool_result, dict):
        return set().union(*(collect_facts(value, name, policy) for name, value in tool_result.items()))
    if isinstance(tool_result, list):
        return set().union(*(collect_facts(value, key, policy) for value in tool_result))
    if isinstance(tool_result, bool):
        return set()
    if isinstance(tool_result, (int, float)):
        return {_amount(tool_result)} if "price" in key or "usd" in key else set()
    if isinstance(tool_result, str):
        return (
            ({tool_result} if key in FACT_KEYS else set())
            | set(_links(tool_result))
            | {email.casefold() for email in EMAIL.findall(tool_result)}
            | {token.upper() for token in CODE_LIKE.findall(tool_result.upper())}
            | {match.upper() for pattern in policy.issued + policy.copyable for match in pattern.findall(tool_result)}
            | {_amount(number) for number in AMOUNT.findall(tool_result)}
        )
    return set()


# --- hard rules ---


def _name_mentions(name: str) -> list[re.Pattern]:
    """The full name and a first name of three letters or more, as whole words."""
    first = name.split()[0]
    forms = [name] + ([first] if len(first) >= 3 and first != name else [])
    return [re.compile(rf"\b{re.escape(form)}\b") for form in forms]


def check(reply: str, allowed: set[str], customer_text: str = "", policy: GuardPolicy | None = None) -> list[Violation]:
    """Return everything in `reply` that neither a tool nor the customer supplied."""
    policy = policy or default_policy()
    customer_emails = {email.casefold() for email in EMAIL.findall(customer_text)}
    customer_ids = set().union(*(fn(customer_text) for fn in policy.customer_identifiers))
    customer_amounts = {_amount(number) for number in NUMBER.findall(customer_text)}
    customer_words = customer_text.casefold()

    violations = []
    for email in EMAIL.findall(reply):
        if email.casefold() not in allowed | customer_emails:
            violations.append(Violation("unknown_email", f"Leave {email} out: no tool returned it and the customer did not give it."))
    for link in _links(reply):
        if link not in allowed:
            violations.append(Violation("unknown_link", f"{link} did not come from a tool result."))

    without_links = LINK.sub(" ", EMAIL.sub(" ", reply))
    for pattern in policy.issued:
        for code in pattern.findall(without_links):
            if code.upper() not in allowed:
                violations.append(Violation("unissued_code", f"Leave {code} out entirely, even in a refusal: no tool issued it."))
        without_links = pattern.sub(" ", without_links)
    for token in CODE_LIKE.findall(without_links):
        if len(token) >= MIN_CODE_LENGTH and token not in allowed and token not in customer_ids:
            violations.append(Violation("unknown_code", f"Leave {token} out entirely, even in a refusal: no tool returned it."))

    # A refusal that repeats the customer's number ("I can't give 50% off") is
    # still asked to drop it: the check cannot tell a refusal from a promise.
    for percent in PERCENT.findall(reply):
        if float(percent) not in policy.allowed_percents:
            violations.append(Violation(
                "unknown_percent",
                f"Leave {percent}% out entirely, even in a refusal: it is not a discount any tool or promotion gives.",
            ))
    for number in AMOUNT.findall(reply):
        if _amount(number) not in allowed | customer_amounts:
            violations.append(Violation(
                "unknown_amount", f"Leave ${number} out: no tool returned it. Quote amounts exactly as a tool gave them."
            ))

    if promise := UNKEEPABLE_PROMISE.search(reply):
        violations.append(Violation(
            "unkeepable_promise",
            f"Leave out \"{promise.group(0)}\": you exist only in this chat and cannot do anything later. "
            "Tell the customer what they can do instead.",
        ))

    for value in policy.protected:
        forms = _name_mentions(value)
        if "@" in value or value in allowed or any(re.search(f.pattern, customer_words, re.IGNORECASE) for f in forms):
            continue  # emails are covered above; a name a tool returned or the customer typed may be used
        if any(form.search(reply) for form in forms):
            violations.append(Violation(
                "other_customer", "Leave out that person's name: it belongs to an order this customer has not verified."
            ))
    return violations


# --- formatting, repaired in code ---


def repair(reply: str, allowed: set[str], policy: GuardPolicy | None = None, *, plain: bool = False) -> tuple[str, list[str]]:
    """Apply the brand voice's formatting rules. Returns the text and the rules applied.

    plain=True drops every emoji, for a customer who is upset or a delivery that went wrong.
    """
    policy = policy or default_policy()
    applied = []

    text = MARKDOWN_HEADING.sub("", reply).replace("**", "").replace("__", "")
    if text != reply:
        applied.append("markdown")

    limit = 0 if plain else MAX_EMOJI
    if len(EMOJI.findall(text)) > limit:
        seen = 0

        def keep_first(match: re.Match) -> str:
            nonlocal seen
            seen += 1
            return match.group(0) if seen <= limit else ""

        text = EMOJI.sub(keep_first, text)
        text = "\n".join(re.sub(r"(?<=\S)[ \t]{2,}", " ", line).rstrip() for line in text.split("\n"))
        applied.append("plain_tone" if plain else "too_many_emoji")

    text, moved = _own_lines(text, allowed, policy)
    if moved:
        applied.append("own_line")
    return text, applied


def _copyable(line: str, allowed: set[str], policy: GuardPolicy) -> list[tuple[int, str]]:
    """Codes, links and identifiers in `line` that came from a tool, with their positions."""
    found = []
    for match in LINK.finditer(line):
        link = match.group(0).strip(WRAPPING)
        if link in allowed:
            found.append((match.start(), link))
    for pattern in dict.fromkeys(policy.issued + policy.copyable):
        for match in pattern.finditer(line):
            token = match.group(0).upper()
            if token in allowed:
                found.append((match.start(), token))
    return sorted(set(found))


def _own_lines(text: str, allowed: set[str], policy: GuardPolicy) -> tuple[str, bool]:
    """Make sure every copyable token stands alone on at least one line."""
    lines = text.split("\n")
    alone = {line.strip().upper() for line in lines}
    out, moved = [], False
    for line in lines:
        out.append(line)
        pending = []
        for _, token in _copyable(line, allowed, policy):
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
