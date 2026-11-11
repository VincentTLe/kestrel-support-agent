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


def within_one_edit(typed: str, real: str) -> bool:
    """True when `typed` is `real` with at most one digit wrong, swapped with its neighbour, missing or extra."""
    if typed == real:
        return True
    if abs(len(typed) - len(real)) > 1:
        return False
    if len(typed) == len(real):
        diff = [i for i, (a, b) in enumerate(zip(typed, real)) if a != b]
        if len(diff) == 1:
            return True
        i, j = diff[0], diff[-1]
        return len(diff) == 2 and j == i + 1 and typed[i] == real[j] and typed[j] == real[i]
    shorter, longer = sorted((typed, real), key=len)
    return any(longer[:i] + longer[i + 1:] == shorter for i in range(len(longer)))


def _name(text: str) -> str:
    return " ".join(re.findall(r"[\w'-]+", text.casefold()))


def find_order_by_name(orders: list[Order], email: str, customer_name: str, typed_number: str) -> Order | None:
    """Recover a mistyped order number: the order whose email and name match and whose number is one edit away.

    Only an unambiguous match counts. Someone who knows just an email learns nothing: they also need the
    name on the order and a number close to the real one.
    """
    wanted_email, wanted_name = normalize_email(email), _name(customer_name)
    typed_digits = normalize_order_number(typed_number).removeprefix("KC-")
    candidates = [
        order for order in orders
        if normalize_email(order.email) == wanted_email
        and _name(order.customer_name) == wanted_name
        and within_one_edit(typed_digits, order.order_number.removeprefix("KC-"))
    ]
    return candidates[0] if len(candidates) == 1 else None


def options(order: Order, products: dict[str, Product]) -> list[str]:
    """What the customer can do next with this order, so the agent can guide them instead of stopping at no."""
    final_sale = [item.name for item in order.items if item.sku in products and products[item.sku].final_sale]
    returnable_later = [item.name for item in order.items if item.name not in final_sale]
    later = (
        f"Once it is delivered, these items can be returned within {RETURN_WINDOW_DAYS} days of delivery: "
        + ", ".join(returnable_later) + "."
        + (f" Final sale items cannot be returned: {', '.join(final_sale)}." if final_sale else "")
    )
    if order.status == "Processing":
        return ["It has not shipped. It cannot be cancelled or changed in this chat.", later]
    if order.status == "Shipped":
        return ["It is on its way; tracking_url shows where it is.", later]
    if order.status == "Delivered":
        return [
            "Return eligibility for each item is in items[].return.",
            "If the customer says it never arrived: give delivered_date and tracking_url, and ask whether they "
            "have checked around the delivery address, with neighbours or a mailroom. The returns portal does "
            "not help with a missing package.",
        ]
    return [f"The order is {order.status}; nothing more will ship."]


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
        "options": options(order, products),
    }
    if order.status in ("Shipped", "Delivered") and order.tracking_number:
        summary["tracking_url"] = TRACKING_URL.format(tracking_number=order.tracking_number)
    if any(item["return"]["eligible"] for item in items):
        summary["returns_portal_url"] = RETURNS_PORTAL_URL
    return summary
