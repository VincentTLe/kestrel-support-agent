import json

import pytest

from kestrel.clock import Clock, parse_now
from kestrel.promo import PromoStore
from kestrel.features import build_tools
from kestrel.tools import Tool, run_tool


def make_tools(orders, catalog, tmp_path, now="2026-12-01 21:30"):
    return build_tools(orders, catalog, Clock(parse_now(now)), PromoStore(tmp_path / "codes.json"))


@pytest.fixture
def tools(orders, catalog, tmp_path):
    return make_tools(orders, catalog, tmp_path)


def run(tools, name, **arguments):
    return run_tool(tools, name, json.dumps(arguments))


# --- schemas sent to the model ---


def test_every_tool_has_a_function_schema(tools):
    assert set(tools) == {"lookup_order", "search_products", "get_night_owl_code"}
    for tool in tools.values():
        schema = tool.schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == tool.name
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"


def test_search_schema_lists_only_values_that_exist_in_the_catalog(tools):
    properties = tools["search_products"].schema()["function"]["parameters"]["properties"]
    assert "Bikes" in json.dumps(properties["category"])
    assert "gravel" in json.dumps(properties["riding_style"])
    assert "bmx" not in json.dumps(properties)


# --- lookup_order ---


def test_lookup_order_returns_status_and_return_eligibility(tools):
    result = run(tools, "lookup_order", email="jordan.kim@example.com", order_number="KC-10452")
    assert result["status"] == "Delivered"
    assert [item["return"]["eligible"] for item in result["items"]] == [False, True]


def test_wrong_pair_and_unknown_order_are_indistinguishable(tools):
    wrong_pair = run(tools, "lookup_order", email="ava.chen@example.com", order_number="KC-10433")
    unknown = run(tools, "lookup_order", email="ava.chen@example.com", order_number="KC-99999")
    stranger = run(tools, "lookup_order", email="nobody@example.com", order_number="KC-10433")
    assert wrong_pair == unknown == stranger
    assert wrong_pair["error"] == "no_match"
    assert "Marcus" not in json.dumps(wrong_pair)


MARCUS = {"email": "marcus.reed@example.com", "order_number": "KC-10433"}


def test_lookup_values_must_appear_in_what_the_customer_said(tools):
    said = "hi, it's order kc 10433, email Marcus.Reed@Example.com"
    assert run_tool(tools, "lookup_order", json.dumps(MARCUS), customer_text=said)["status"] == "Shipped"

    guessed = run_tool(tools, "lookup_order", json.dumps(MARCUS), customer_text="I'm Marcus Reed, order KC-10433")
    assert guessed["error"] == "not_provided_by_customer"
    assert "email" in guessed["message"]
    assert "Marcus" not in json.dumps(guessed)


def test_a_night_owl_email_must_come_from_the_customer(tools):
    result = run_tool(tools, "get_night_owl_code", json.dumps({"email": "ava.chen@example.com"}), customer_text="code pls")
    assert result["error"] == "not_provided_by_customer"


def test_after_three_failed_lookups_no_more_are_attempted(tools):
    for number in ("KC-10420", "KC-10422", "KC-10423"):
        assert run(tools, "lookup_order", email="ava.chen@example.com", order_number=number)["error"] == "no_match"
    blocked = run(tools, "lookup_order", email="ava.chen@example.com", order_number="KC-10421")
    assert blocked["error"] == "too_many_attempts"
    assert "Ridgeline" not in json.dumps(blocked)


def test_successful_lookups_do_not_count_toward_the_limit(tools):
    for _ in range(5):
        assert run(tools, "lookup_order", email="ava.chen@example.com", order_number="KC-10421")["status"] == "Delivered"


# --- search_products ---


def test_height_in_inches_is_converted_by_code(tools):
    # 70 in = 177.8 cm: size M (167-178), not L (178-188)
    result = run(tools, "search_products", name_or_sku="KCB-101", rider_height=70, height_unit="in")
    assert [s["size"] for s in result["matches"][0]["fitting_sizes"]] == ["M"]


def test_implausible_height_is_rejected_instead_of_searched(tools):
    result = run(tools, "search_products", category="Bikes", rider_height=5.83)
    assert result["error"] == "invalid_arguments"


def test_riding_style_outside_the_catalog_is_rejected(tools):
    result = run(tools, "search_products", riding_style="bmx")
    assert result["error"] == "invalid_arguments"


# --- get_night_owl_code ---


def test_night_owl_code_is_issued_inside_the_window(tools):
    result = run(tools, "get_night_owl_code", email="ava.chen@example.com")
    assert result["status"] == "issued"
    assert result["code"].startswith("NIGHTOWL-")


def test_night_owl_code_is_refused_outside_the_window(orders, catalog, tmp_path):
    tools = make_tools(orders, catalog, tmp_path, now="2026-12-01 15:00")
    result = run(tools, "get_night_owl_code", email="ava.chen@example.com")
    assert result["status"] == "outside_window"
    assert "code" not in result


def test_night_owl_rejects_text_that_is_not_an_email(tools):
    result = run(tools, "get_night_owl_code", email="ava chen")
    assert result["error"] == "invalid_arguments"


# --- failures become data, never exceptions ---


def test_unknown_tool_is_an_error_result(tools):
    assert run(tools, "issue_refund", order_number="KC-10421")["error"] == "unknown_tool"


def test_arguments_that_are_not_json_are_an_error_result(tools):
    assert run_tool(tools, "lookup_order", "{email: ava")["error"] == "invalid_arguments"


def test_missing_argument_is_an_error_result_naming_the_field(tools):
    result = run(tools, "lookup_order", order_number="KC-10421")
    assert result["error"] == "invalid_arguments"
    assert "email" in result["message"]


def test_unexpected_argument_is_rejected(tools):
    result = run(tools, "get_night_owl_code", email="a@example.com", discount_percent=50)
    assert result["error"] == "invalid_arguments"


def test_a_crashing_handler_is_an_error_result(tools):
    def explode(_args):
        raise RuntimeError("database on fire")

    broken = dict(tools)
    broken["lookup_order"] = Tool(
        name="lookup_order",
        description="x",
        args_model=tools["lookup_order"].args_model,
        handler=explode,
    )
    result = run(broken, "lookup_order", email="ava.chen@example.com", order_number="KC-10421")
    assert result["error"] == "tool_failed"
    assert "database on fire" not in json.dumps(result)  # internals stay out of the prompt
