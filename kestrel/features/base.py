"""What a feature is. Each feature module builds one of these; the agent, the
prompt and the guard read everything they need about a feature from it."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from ..clock import Clock
from ..data import Order, Product
from ..promo import PromoStore
from ..tools import Tool


@dataclass(frozen=True)
class Context:
    """What feature builders may use. Built once per conversation."""

    orders: list[Order]
    catalog: list[Product]
    clock: Clock
    promo_store: PromoStore


@dataclass(frozen=True)
class Feature:
    name: str
    summary: str  # one line in the capability catalog the model always sees
    tools: tuple[Tool, ...]
    guidance: Callable[[datetime], str]  # short rules, in the prompt only while the feature is active
    offer: str = ""  # a short phrase for fallbacks: "check an order's status, tracking or returns"
    status: Callable[[datetime], str] | None = None  # live state shown in the catalog, e.g. "OPEN now"
    # For the guard. Facts a tool returns are allowed automatically; these say what else to watch for.
    issued: tuple[re.Pattern, ...] = ()  # identifiers only a tool may produce, e.g. discount codes
    copyable: tuple[re.Pattern, ...] = ()  # identifiers that must stand on a line of their own
    allowed_percents: frozenset[float] = frozenset()  # percentages the agent may state
    protected: tuple[str, ...] = ()  # data never shown unless a tool returned it, e.g. other customers' names
    customer_identifiers: tuple[Callable[[str], set[str]], ...] = field(default=())  # ids the customer typed
