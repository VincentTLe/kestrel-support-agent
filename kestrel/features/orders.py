"""Orders: status, tracking and return eligibility for one verified order."""

import re
from datetime import datetime

from pydantic import Field

from .. import orders as rules
from ..tools import Args, Email, Tool
from .base import Context, Feature

ORDER_NUMBER = re.compile(r"\bKC-\d+\b")
CUSTOMER_ORDER_NUMBER = re.compile(r"\bKC[\s-]?\d+\b", re.IGNORECASE)
MAX_FAILED_LOOKUPS = 3  # per conversation; order numbers are sequential and easy to guess
# The same answer for every miss, so a miss never reveals whether an email has orders.
NO_MATCH = {
    "error": "no_match",
    "message": "No order matches these details together. The system does not reveal which value is wrong.",
    "next_step": "Ask whether they may have used another email, and for the full name on the order: with the "
    "email, the name and the number they remember, recover_order can find an order whose number is off by one digit.",
}

GUIDANCE = """\
- Discuss an order only with both the email and the order number from the customer; ask for whichever \
is missing and never guess either. Each order needs its own number. Earlier values may be reused.
- No match: never say which value is wrong. Help them find it: ask whether they used another email, \
and for the full name on the order; recover_order then finds an order whose number is off by one digit.
- For status or whereabouts, include tracking_url on its own line whenever the result has one, \
delivered orders included. Pass on notes that affect the customer, such as a delay.
- Returns: go item by item with each item's "return" field and give the reason for any refusal. Share \
returns_portal_url only when the result has it; the customer starts the return there, not you.
- Use the result's "options" for what the customer can do next, especially when you cannot do what \
they asked (cancel, change, a package that never arrived)."""


def customer_order_numbers(text: str) -> set[str]:
    return {rules.normalize_order_number(number) for number in CUSTOMER_ORDER_NUMBER.findall(text)}


def build(ctx: Context) -> Feature:
    class LookupOrderArgs(Args):
        email: Email = Field(description="The email address the customer gave in this conversation.")
        order_number: str = Field(min_length=1, description="The order number the customer gave, e.g. KC-10421.")

    class RecoverOrderArgs(Args):
        email: Email = Field(description="The email address the customer gave.")
        customer_name: str = Field(min_length=2, description="The full name on the order, as the customer typed it.")
        order_number: str = Field(min_length=1, description="The order number the customer remembers, even if wrong.")

    failed_lookups = 0  # shared by both tools: recovering is not a way around the limit

    def attempt(find) -> dict:
        nonlocal failed_lookups
        if failed_lookups >= MAX_FAILED_LOOKUPS:
            return {
                "error": "too_many_attempts",
                "message": "Too many lookups did not match in this conversation, so no more are allowed. "
                "Tell the customer you can't look up more orders in this chat. Do not suggest which value is wrong.",
            }
        order = find()
        if order is None:
            failed_lookups += 1
            return dict(NO_MATCH)
        return rules.order_summary(order, ctx.catalog, ctx.clock.now().date())

    def lookup_order(args: LookupOrderArgs) -> dict:
        return attempt(lambda: rules.find_order(ctx.orders, args.email, args.order_number))

    def recover_order(args: RecoverOrderArgs) -> dict:
        result = attempt(lambda: rules.find_order_by_name(ctx.orders, args.email, args.customer_name, args.order_number))
        if "error" not in result:
            result["recovered_from"] = rules.normalize_order_number(args.order_number)
        return result

    def guidance(now: datetime) -> str:
        return GUIDANCE

    return Feature(
        name="orders",
        summary="Order status, tracking and return eligibility, once the customer gives their email and order number.",
        tools=(
            Tool(
                "lookup_order",
                "Look up one order. Requires the customer's email AND order number; both must belong to the "
                "same order. Returns status, tracking link if shipped, notes, and per-item return eligibility "
                "with reasons. Call only after the customer has given both values.",
                LookupOrderArgs,
                lookup_order,
                customer_supplied=("email", "order_number"),
            ),
            Tool(
                "recover_order",
                "Find an order whose number the customer mistyped. Needs the email, the full name on the order "
                "and the number they remember, all typed by the customer. Returns the order only when exactly one "
                "order matches the email and name and its number is off by one digit; otherwise the same no_match.",
                RecoverOrderArgs,
                recover_order,
                customer_supplied=("email", "customer_name", "order_number"),
            ),
        ),
        guidance=guidance,
        offer="check an order's status, tracking or returns",
        copyable=(ORDER_NUMBER,),
        protected=tuple(sorted({o.customer_name for o in ctx.orders} | {o.email for o in ctx.orders})),
        customer_identifiers=(customer_order_numbers,),
    )
