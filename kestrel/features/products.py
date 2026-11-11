"""Products: search the catalog and recommend bikes and gear."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .. import catalog as catalog_search
from ..tools import Args, Tool
from .base import Context, Feature

CM_PER_INCH = 2.54
PLAUSIBLE_HEIGHT_CM = (90, 230)

GUIDANCE = """\
- Recommend only what search_products returns. Pass on what the customer told you: budget, riding \
style, and rider height for bikes.
- Search first with what you already know, and show what you found. Then, if budget, riding style or \
rider height is still missing and would change the answer, ask for it in one short question.
- "matches" are in stock. For a bike, name a size from fitting_sizes that is in stock.
- When the customer gave a height, fitting_sizes is the whole answer on fit. Never suggest any other \
size, not even as a close or snug fit. The catalog has no size chart for helmets or clothing, so do \
not offer sizing advice for those.
- "near_misses" did not fully fit; each has a why_not. If one is what the customer asked for but is \
out of stock, say so by name. Never present an out-of-stock item or size as available. Leave out a \
near miss that has no size for the rider.
- If "matches" is empty, say that nothing fits exactly, then offer the closest near miss and state \
its why_not, including the real price if it is over budget."""


def build(ctx: Context) -> Feature:
    # Enums come from the data, so the model cannot ask for a category or
    # riding style that Kestrel does not sell.
    Category = Literal[tuple(sorted({product.category for product in ctx.catalog}))]
    Style = Literal[tuple(sorted({style for product in ctx.catalog for style in product.riding_style} - {"all"}))]

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

    def search_products(args: SearchProductsArgs) -> dict:
        return catalog_search.search(
            ctx.catalog,
            category=args.category,
            riding_style=args.riding_style,
            max_price=args.max_price,
            rider_height_cm=args.rider_height_cm,
            name_or_sku=args.name_or_sku,
        )

    def guidance(now: datetime) -> str:
        return GUIDANCE

    return Feature(
        name="products",
        summary="Bike and gear questions and recommendations from the Kestrel catalog (price, style, rider height, stock).",
        tools=(
            Tool(
                "search_products",
                "Search the Kestrel catalog, the only source of product facts. Returns 'matches' (meet every "
                "filter and are in stock) and 'near_misses' (each with why_not: the reasons it missed, such as "
                "out of stock). Use the filters instead of several broad searches. "
                "For bikes, pass rider_height to get the sizes that fit.",
                SearchProductsArgs,
                search_products,
            ),
        ),
        guidance=guidance,
        offer="find bikes and gear that fit you",
    )
