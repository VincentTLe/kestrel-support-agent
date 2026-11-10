from datetime import date

import pytest

from kestrel.data import Order, OrderItem
from kestrel.orders import RETURNS_PORTAL_URL, find_order, order_summary

from .conftest import TODAY


def summary(orders, catalog, email, number, today=TODAY):
    order = find_order(orders, email, number)
    assert order is not None
    return order_summary(order, catalog, today)


def returns_by_sku(result):
    return {item["sku"]: item["return"] for item in result["items"]}


# --- identity: email and order number must belong to the same order ---


def test_matching_email_and_order_number_finds_the_order(orders):
    order = find_order(orders, "marcus.reed@example.com", "KC-10433")
    assert order.customer_name == "Marcus Reed"


def test_email_match_ignores_case_and_surrounding_spaces(orders):
    # KC-10466 is stored as "Ella.Foster@Example.com"
    assert find_order(orders, "  ella.foster@example.com ", "KC-10466") is not None
    assert find_order(orders, "AVA.CHEN@EXAMPLE.COM", "KC-10421") is not None


@pytest.mark.parametrize("typed", ["kc-10421", "KC10421", " kc 10421 ", "10421"])
def test_order_number_is_normalized(orders, typed):
    assert find_order(orders, "ava.chen@example.com", typed) is not None


def test_real_email_with_someone_elses_order_number_is_not_found(orders):
    assert find_order(orders, "ava.chen@example.com", "KC-10433") is None


def test_unknown_order_number_is_not_found(orders):
    assert find_order(orders, "ava.chen@example.com", "KC-99999") is None


def test_each_order_of_a_repeat_customer_needs_its_own_number(orders):
    first = find_order(orders, "ava.chen@example.com", "KC-10421")
    second = find_order(orders, "ava.chen@example.com", "KC-10440")
    assert first.order_number != second.order_number


# --- status and tracking ---


def test_shipped_order_has_tracking_link_in_the_brief_format(orders, catalog):
    result = summary(orders, catalog, "marcus.reed@example.com", "KC-10433")
    assert result["status"] == "Shipped"
    assert result["tracking_url"] == "https://track.kestrelcycles.example/1Z84KC0002"


def test_delivered_order_has_tracking_link(orders, catalog):
    result = summary(orders, catalog, "ava.chen@example.com", "KC-10421")
    assert result["tracking_url"] == "https://track.kestrelcycles.example/1Z84KC0001"


@pytest.mark.parametrize(
    "email, number",
    [("priya.n@example.com", "KC-10437"), ("sam.ortiz@example.com", "KC-10458")],
)
def test_unshipped_orders_have_no_tracking_link(orders, catalog, email, number):
    assert "tracking_url" not in summary(orders, catalog, email, number)


def test_order_notes_are_passed_through(orders, catalog):
    result = summary(orders, catalog, "li.wei@example.com", "KC-10460")
    assert "weather delay" in result["notes"]


# --- return eligibility, clock at 2026-12-01 ---


def test_recently_delivered_item_is_eligible(orders, catalog):
    result = summary(orders, catalog, "ava.chen@example.com", "KC-10421")
    ret = returns_by_sku(result)["KCH-210"]
    assert ret["eligible"] is True
    assert result["returns_portal_url"] == RETURNS_PORTAL_URL


def test_item_delivered_47_days_ago_is_not_eligible_and_says_why(orders, catalog):
    result = summary(orders, catalog, "ava.chen@example.com", "KC-10440")
    ret = returns_by_sku(result)["KCC-410"]
    assert ret["eligible"] is False
    assert "47 days ago" in ret["reason"]
    assert "returns_portal_url" not in result


def test_mixed_order_final_sale_item_is_refused_and_the_other_is_eligible(orders, catalog):
    result = summary(orders, catalog, "jordan.kim@example.com", "KC-10452")
    ret = returns_by_sku(result)
    assert ret["KCC-499"]["eligible"] is False
    assert "final sale" in ret["KCC-499"]["reason"].lower()
    assert ret["KCA-310"]["eligible"] is True
    assert result["returns_portal_url"] == RETURNS_PORTAL_URL


@pytest.mark.parametrize(
    "email, number, status",
    [
        ("marcus.reed@example.com", "KC-10433", "Shipped"),
        ("priya.n@example.com", "KC-10437", "Processing"),
        ("sam.ortiz@example.com", "KC-10458", "Cancelled"),
    ],
)
def test_orders_that_are_not_delivered_are_not_eligible(orders, catalog, email, number, status):
    result = summary(orders, catalog, email, number)
    for item in result["items"]:
        assert item["return"]["eligible"] is False
        assert status in item["return"]["reason"]
    assert "returns_portal_url" not in result


@pytest.mark.parametrize(
    "today, eligible",
    [
        (date(2026, 11, 20), True),  # delivery day
        (date(2026, 12, 20), True),  # day 30: "no more than 30 days ago"
        (date(2026, 12, 21), False),  # day 31
    ],
)
def test_return_window_boundary_is_30_days_inclusive(orders, catalog, today, eligible):
    result = summary(orders, catalog, "ava.chen@example.com", "KC-10421", today=today)
    assert returns_by_sku(result)["KCH-210"]["eligible"] is eligible


def test_item_missing_from_catalog_is_unconfirmed_not_guessed(catalog):
    order = Order(
        order_number="KC-20000",
        email="x@example.com",
        customer_name="X",
        status="Delivered",
        order_date=date(2026, 11, 1),
        shipped_date=date(2026, 11, 2),
        delivered_date=date(2026, 11, 25),
        tracking_number="T1",
        items=[OrderItem(sku="ZZZ-000", name="Mystery Part", size=None, qty=1, unit_price=1.0)],
        total=1.0,
        notes=None,
    )
    result = order_summary(order, catalog, TODAY)
    assert result["items"][0]["return"]["eligible"] is None
    assert "returns_portal_url" not in result
