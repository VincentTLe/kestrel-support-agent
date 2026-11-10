"""Typed views of the two static datasets. Loading fails loudly on bad data."""

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class OrderItem(BaseModel):
    sku: str
    name: str
    size: str | None
    qty: int
    unit_price: float


class Order(BaseModel):
    order_number: str
    email: str
    customer_name: str
    status: Literal["Processing", "Shipped", "Delivered", "Cancelled"]
    order_date: date
    shipped_date: date | None
    delivered_date: date | None
    tracking_number: str | None
    items: list[OrderItem]
    total: float
    notes: str | None


class Size(BaseModel):
    size: str
    rider_height_cm: tuple[float, float] | None = None  # bikes only
    in_stock: bool


class Product(BaseModel):
    sku: str
    name: str
    category: str
    riding_style: list[str]
    price: float
    description: str
    sizes: list[Size] | None
    in_stock: bool | None = None  # only set on products without sizes
    final_sale: bool


def load_orders(path: Path = DATA_DIR / "CustomerOrders.json") -> list[Order]:
    return [Order.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]


def load_catalog(path: Path = DATA_DIR / "ProductCatalog.json") -> list[Product]:
    return [Product.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]
