"""Order lookup and return eligibility. All of it is decided here, not by the model."""

import re
from datetime import date, timedelta

from .data import Order, OrderItem, Product

TRACKING_URL = "https://track.kestrelcycles.example/{tracking_number}"
RETURNS_PORTAL_URL = "https://kestrelcycles.example/returns"
RETURN_WINDOW_DAYS = 30


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def normalize_order_number(text: str) -> str:
    """'kc10421', ' KC 10421 ' and '10421' all become 'KC-10421'."""
    compact = re.sub(r"[\s-]", "", text).upper()
    digits = compact.removeprefix("KC")
    return f"KC-{digits}" if digits.isdigit() else compact


def find_order(orders: list[Order], email: str, order_number: str) -> Order | None:
    """Return the order only when the email and the order number belong to it.

    A wrong email and a wrong order number are indistinguishable to the caller,
    so the agent cannot be used to confirm that either one exists.
    """
    wanted_email = normalize_email(email)
    wanted_number = normalize_order_number(order_number)
    for order in orders:
        if order.order_number == wanted_number and normalize_email(order.email) == wanted_email:
            return order
    return None


def return_eligibility(order: Order, item: OrderItem, product: Product | None, today: date) -> dict:
    """Eligible when Delivered, delivered no more than 30 days ago, and not final sale.

    `eligible` is None when the data cannot answer; the agent must not guess.
    """
    if order.status != "Delivered":
        reason = f"Order status is {order.status}; only delivered orders can be returned."
        return {"eligible": False, "reason": reason}
    if order.delivered_date is None or product is None:
        missing = "delivery date" if order.delivered_date is None else "catalog entry"
        return {"eligible": None, "reason": f"Cannot confirm: this item's {missing} is missing."}

    reasons = []
    if product.final_sale:
        reasons.append("This is a final sale item; final sale items cannot be returned.")
    days = (today - order.delivered_date).days
    window_end = order.delivered_date + timedelta(days=RETURN_WINDOW_DAYS)
    if days > RETURN_WINDOW_DAYS:
        reasons.append(
            f"Delivered {order.delivered_date}, {days} days ago; "
            f"the {RETURN_WINDOW_DAYS}-day return window ended {window_end}."
        )
    if reasons:
        return {"eligible": False, "reason": " ".join(reasons)}
    return {
        "eligible": True,
        "reason": f"Delivered {order.delivered_date}, {days} days ago; "
        f"the {RETURN_WINDOW_DAYS}-day return window is open through {window_end}.",
    }


def order_summary(order: Order, catalog: list[Product], today: date) -> dict:
    """Everything the agent may say about a verified order.

    Return eligibility is always included, so the model never does date math.
    """
    products = {product.sku: product for product in catalog}
    items = [
        {
            "sku": item.sku,
            "name": item.name,
            "size": item.size,
            "qty": item.qty,
            "unit_price": item.unit_price,
            "return": return_eligibility(order, item, products.get(item.sku), today),
        }
        for item in order.items
    ]
    summary = {
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "status": order.status,
        "order_date": str(order.order_date),
        "shipped_date": order.shipped_date and str(order.shipped_date),
        "delivered_date": order.delivered_date and str(order.delivered_date),
        "total_usd": order.total,
        "notes": order.notes,
        "items": items,
    }
    if order.status in ("Shipped", "Delivered") and order.tracking_number:
        summary["tracking_url"] = TRACKING_URL.format(tracking_number=order.tracking_number)
    if any(item["return"]["eligible"] for item in items):
        summary["returns_portal_url"] = RETURNS_PORTAL_URL
    return summary
