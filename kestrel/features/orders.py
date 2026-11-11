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

GUIDANCE = """\
- You need both the email and the order number from the customer before discussing any order. Ask for \
whichever is missing. Never guess either value. Values given earlier in the conversation may be reused.
- Each order needs its own order number, even when the email is the same.
- If lookup_order finds no match, say you couldn't find an order with that email and order number \
together and ask the customer to double-check both. Never suggest which one is wrong.
- Report the status. When the customer asks about an order's status or whereabouts, include \
tracking_url on its own line whenever the result has one, delivered orders included. Pass on notes that \
affect the customer, such as a delay or a refund.
- Returns: go item by item using each item's "return" field and give the reason for any item that is \
not eligible. Share returns_portal_url only when the result contains it; the customer starts the \
return there, not you."""


def customer_order_numbers(text: str) -> set[str]:
    return {rules.normalize_order_number(number) for number in CUSTOMER_ORDER_NUMBER.findall(text)}


def build(ctx: Context) -> Feature:
    class LookupOrderArgs(Args):
        email: Email = Field(description="The email address the customer gave in this conversation.")
        order_number: str = Field(min_length=1, description="The order number the customer gave, e.g. KC-10421.")

    failed_lookups = 0

    def lookup_order(args: LookupOrderArgs) -> dict:
        nonlocal failed_lookups
        if failed_lookups >= MAX_FAILED_LOOKUPS:
            return {
                "error": "too_many_attempts",
                "message": "Too many lookups did not match in this conversation, so no more are allowed. "
                "Tell the customer you can't look up more orders in this chat. Do not suggest which value is wrong.",
            }
        order = rules.find_order(ctx.orders, args.email, args.order_number)
        if order is None:
            failed_lookups += 1
            return {
                "error": "no_match",
                "message": "No order matches this email and order number together. "
                "The system does not reveal which of the two is wrong.",
            }
        return rules.order_summary(order, ctx.catalog, ctx.clock.now().date())

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
        ),
        guidance=guidance,
        offer="check an order's status, tracking or returns",
        copyable=(ORDER_NUMBER,),
        protected=tuple(sorted({o.customer_name for o in ctx.orders} | {o.email for o in ctx.orders})),
        customer_identifiers=(customer_order_numbers,),
    )
