"""Night Owl: the 15% evening promotion, one code per email."""

import re
from datetime import datetime

from pydantic import Field

from ..promo import DISCOUNT_PERCENT, WINDOW_TEXT, in_window, request_code
from ..tools import Args, Email, Tool
from .base import Context, Feature

CODE = re.compile(r"NIGHTOWL-[A-Z0-9]+", re.IGNORECASE)

# Whether the window is open is a fact about the clock, so code states it. Without
# it the model asked for an email first and learned the window was closed only
# from the tool: one wasted customer turn and two extra model calls.
OPEN_NOW = """\
- Night Owl is OPEN right now. Call get_night_owl_code only when the customer explicitly asks for the \
Night Owl promotion or its code. You need their email; ask for it if you don't have it."""
CLOSED_NOW = """\
- Night Owl is CLOSED right now. If the customer asks for it, say at once when it runs ({window}) and \
that they can ask again then. Do not ask for an email and do not call get_night_owl_code."""
GUIDANCE = """\
- Never offer a code unprompted. You may list Night Owl among the things you help with, but give its \
details only when the customer asks about Night Owl, discounts, deals or promotions: then say that it \
gives {discount}% off ({window}), whether it is open right now (above), and that they can ask for it. \
You know of no other promotion. The only percentage you may mention is {discount}%.
- "outside_window" means no code right now: explain when the promotion runs.
- "existing" means this email already had a code: give the same code and say it is the same one.
- Say nothing about expiry or redemption; you were not told."""


def build(ctx: Context) -> Feature:
    class NightOwlArgs(Args):
        email: Email = Field(description="The customer's email address; the code is tied to it.")

    def get_night_owl_code(args: NightOwlArgs) -> dict:
        return request_code(ctx.promo_store, args.email, ctx.clock.now())

    def guidance(now: datetime) -> str:
        status = (OPEN_NOW if in_window(now) else CLOSED_NOW).format(window=WINDOW_TEXT)
        return status + "\n" + GUIDANCE.format(discount=DISCOUNT_PERCENT, window=WINDOW_TEXT)

    return Feature(
        name="night_owl",
        summary=f"The Night Owl promotion: {DISCOUNT_PERCENT}% off, {WINDOW_TEXT}, one code per email.",
        tools=(
            Tool(
                "get_night_owl_code",
                "Get the customer's Night Owl discount code. Call only when the customer explicitly asks for "
                "the Night Owl promotion or its code. The tool checks the time window itself and returns the "
                "same code if this email already has one.",
                NightOwlArgs,
                get_night_owl_code,
                customer_supplied=("email",),
            ),
        ),
        guidance=guidance,
        offer="look up the Night Owl promotion",
        issued=(CODE,),
        copyable=(CODE,),
        allowed_percents=frozenset({float(DISCOUNT_PERCENT)}),
    )
