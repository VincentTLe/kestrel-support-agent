"""The tools the model may call, and the one function that runs them.

A tool is a name, a description, a pydantic model for its arguments and a
handler. Adding a feature means adding one entry in build_tools(); the agent
loop does not change. A failed call is returned to the model as data
({"error": ...}) and never raised, so one bad call cannot end the chat.
"""

import json
import logging
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from . import catalog as catalog_search
from .clock import Clock
from .data import Order, Product
from .orders import find_order, order_summary
from .promo import PromoStore, request_code

log = logging.getLogger(__name__)

Email = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
CM_PER_INCH = 2.54
PLAUSIBLE_HEIGHT_CM = (90, 230)


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[Any], dict]

    def schema(self) -> dict:
        """The tool in the OpenAI-compatible function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }


def run_tool(tools: dict[str, Tool], name: str, raw_arguments: str) -> dict:
    """Validate the model's arguments, run the tool, and always return a dict."""
    tool = tools.get(name)
    if tool is None:
        return {"error": "unknown_tool", "message": f"There is no tool named {name!r}."}
    try:
        args = tool.args_model.model_validate(json.loads(raw_arguments or "{}"))
    except json.JSONDecodeError:
        return {"error": "invalid_arguments", "message": "Arguments were not valid JSON."}
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in e['loc']) or 'arguments'}: {e['msg']}" for e in error.errors()
        )
        return {"error": "invalid_arguments", "message": problems}
    try:
        return tool.handler(args)
    except Exception:  # the customer should get an apology, not a stack trace
        log.exception("tool %s failed", name)
        return {"error": "tool_failed", "message": "The tool hit an internal error. Apologize; do not guess."}


def build_tools(orders: list[Order], catalog: list[Product], clock: Clock, promo_store: PromoStore) -> dict[str, Tool]:
    # Enums come from the data, so the model cannot ask for a category or
    # riding style that Kestrel does not sell.
    Category = Literal[tuple(sorted({product.category for product in catalog}))]
    Style = Literal[tuple(sorted({style for product in catalog for style in product.riding_style} - {"all"}))]

    class LookupOrderArgs(Args):
        email: Email = Field(description="The email address the customer gave in this conversation.")
        order_number: str = Field(min_length=1, description="The order number the customer gave, e.g. KC-10421.")

    class SearchProductsArgs(Args):
        category: Category | None = Field(None, description="Limit results to one category.")
        riding_style: Style | None = Field(None, description="How or where the customer rides.")
        max_price: float | None = Field(None, gt=0, description="The customer's budget in USD.")
        rider_height: float | None = Field(None, gt=0, description="Rider height, for bike sizing.")
        height_unit: Literal["cm", "in"] = Field("cm", description="Unit of rider_height. 5 ft 10 in is 70 in.")
        name_or_sku: str | None = Field(None, description="Part of a product name, or an exact SKU.")

        @property
        def rider_height_cm(self) -> float | None:
            if self.rider_height is None:
                return None
            return self.rider_height * CM_PER_INCH if self.height_unit == "in" else self.rider_height

        @model_validator(mode="after")
        def height_is_plausible(self):
            low, high = PLAUSIBLE_HEIGHT_CM
            if self.rider_height_cm is not None and not low <= self.rider_height_cm <= high:
                raise ValueError(
                    f"rider_height {self.rider_height:g} {self.height_unit} is not a plausible height; "
                    "check the unit or ask the customer"
                )
            return self

    class NightOwlArgs(Args):
        email: Email = Field(description="The customer's email address; the code is tied to it.")

    def lookup_order(args: LookupOrderArgs) -> dict:
        order = find_order(orders, args.email, args.order_number)
        if order is None:
            return {
                "error": "no_match",
                "message": "No order matches this email and order number together. "
                "The system does not reveal which of the two is wrong.",
            }
        return order_summary(order, catalog, clock.now().date())

    def search_products(args: SearchProductsArgs) -> dict:
        return catalog_search.search(
            catalog,
            category=args.category,
            riding_style=args.riding_style,
            max_price=args.max_price,
            rider_height_cm=args.rider_height_cm,
            name_or_sku=args.name_or_sku,
        )

    def get_night_owl_code(args: NightOwlArgs) -> dict:
        return request_code(promo_store, args.email, clock.now())

    tools = [
        Tool(
            "lookup_order",
            "Look up one order. Requires the customer's email AND order number; both must belong to the "
            "same order. Returns status, tracking link if shipped, notes, and per-item return eligibility "
            "with reasons. Call only after the customer has given both values.",
            LookupOrderArgs,
            lookup_order,
        ),
        Tool(
            "search_products",
            "Search the Kestrel catalog, the only source of product facts. Returns 'matches' (meet every "
            "filter and are in stock) or, when nothing matches, 'near_misses' with the reasons each missed. "
            "For bikes, pass rider_height to get the sizes that fit.",
            SearchProductsArgs,
            search_products,
        ),
        Tool(
            "get_night_owl_code",
            "Get the customer's Night Owl discount code. Call only when the customer explicitly asks for "
            "the Night Owl promotion or its code. The tool checks the time window itself and returns the "
            "same code if this email already has one.",
            NightOwlArgs,
            get_night_owl_code,
        ),
    ]
    return {tool.name: tool for tool in tools}
