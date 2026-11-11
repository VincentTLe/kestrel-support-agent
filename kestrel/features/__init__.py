"""The feature registry.

A feature is one module in this package that builds a Feature: its tools, its
short guidance for the model, and what the guard should watch for. Adding a
fourth feature means writing a module and adding its build function to
BUILDERS; the agent loop, the core prompt and the guard do not change.
"""

from ..clock import Clock
from ..data import Order, Product, load_catalog, load_orders
from ..promo import PromoStore
from ..tools import Tool
from . import night_owl, orders, products
from .base import Context, Feature

BUILDERS = (orders.build, products.build, night_owl.build)


def build_features(ctx: Context) -> list[Feature]:
    features = [build(ctx) for build in BUILDERS]
    names = [tool.name for feature in features for tool in feature.tools]
    if len(names) != len(set(names)):
        raise ValueError(f"Two features declare the same tool name: {names}")
    return features


def build_tools(orders: list[Order], catalog: list[Product], clock: Clock, promo_store: PromoStore) -> dict[str, Tool]:
    """Every tool of every feature, by name."""
    features = build_features(Context(orders, catalog, clock, promo_store))
    return {tool.name: tool for feature in features for tool in feature.tools}


def static_features(clock: Clock | None = None, promo_store: PromoStore | None = None) -> list[Feature]:
    """The features over the shipped data, for code that has no conversation of its own."""
    from pathlib import Path
    import tempfile

    store = promo_store or PromoStore(Path(tempfile.gettempdir()) / "kestrel-unused-codes.json")
    return build_features(Context(load_orders(), load_catalog(), clock or Clock(), store))


__all__ = ["BUILDERS", "Context", "Feature", "build_features", "build_tools", "static_features"]
