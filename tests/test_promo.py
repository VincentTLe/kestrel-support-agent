import re

import pytest

from kestrel.clock import parse_now
from kestrel.promo import PromoStore, in_window, request_code

IN_WINDOW = parse_now("2026-12-01 21:30")
AFTERNOON = parse_now("2026-12-01 15:00")
CODE_FORMAT = re.compile(r"^NIGHTOWL-[A-Z0-9]{6}$")


@pytest.fixture
def store(tmp_path):
    return PromoStore(tmp_path / "promo_codes.json")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-12-01 20:59:59", False),
        ("2026-12-01 21:00:00", True),
        ("2026-12-01 23:59:59", True),
        ("2026-12-02 00:00:00", False),
        ("2026-12-01 09:30:00", False),  # 9:30 AM is not 9:30 PM
    ],
)
def test_window_is_9pm_to_midnight_eastern(text, expected):
    assert in_window(parse_now(text)) is expected


def test_window_is_judged_in_eastern_time_not_the_input_zone():
    assert in_window(parse_now("2026-12-02T02:30:00+00:00")) is True


def test_first_request_in_window_issues_a_code_in_the_brief_format(store):
    result = request_code(store, "ava.chen@example.com", IN_WINDOW)
    assert result["status"] == "issued"
    assert CODE_FORMAT.match(result["code"])
    assert result["discount_percent"] == 15


def test_same_email_gets_the_same_code_again(store):
    first = request_code(store, "ava.chen@example.com", IN_WINDOW)
    again = request_code(store, "ava.chen@example.com", IN_WINDOW)
    assert again["status"] == "existing"
    assert again["code"] == first["code"]


def test_email_is_matched_case_insensitively(store):
    first = request_code(store, "ava.chen@example.com", IN_WINDOW)
    again = request_code(store, " Ava.Chen@Example.com ", IN_WINDOW)
    assert again["code"] == first["code"]


def test_different_emails_get_different_codes(store):
    codes = {request_code(store, f"rider{i}@example.com", IN_WINDOW)["code"] for i in range(50)}
    assert len(codes) == 50


def test_codes_survive_a_restart(tmp_path):
    path = tmp_path / "promo_codes.json"
    first = request_code(PromoStore(path), "ava.chen@example.com", IN_WINDOW)
    again = request_code(PromoStore(path), "ava.chen@example.com", IN_WINDOW)
    assert again["code"] == first["code"]


def test_outside_the_window_no_code_is_issued_or_stored(store):
    result = request_code(store, "ava.chen@example.com", AFTERNOON)
    assert result["status"] == "outside_window"
    assert "code" not in result
    assert "9:00 PM" in result["window"]
    assert request_code(store, "ava.chen@example.com", IN_WINDOW)["status"] == "issued"


def test_outside_the_window_an_existing_code_is_not_repeated(store):
    # Assumption documented in the README: the window is the stricter rule.
    request_code(store, "ava.chen@example.com", IN_WINDOW)
    result = request_code(store, "ava.chen@example.com", AFTERNOON)
    assert result["status"] == "outside_window"
    assert "code" not in result


def test_corrupt_store_file_fails_loudly_instead_of_reissuing(tmp_path):
    path = tmp_path / "promo_codes.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        PromoStore(path)
