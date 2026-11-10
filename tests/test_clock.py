from datetime import datetime, timedelta, timezone

import pytest

from kestrel.clock import EASTERN, Clock, parse_now


def test_naive_text_is_read_as_eastern_time():
    now = parse_now("2026-12-01 21:30")
    assert (now.year, now.month, now.day, now.hour, now.minute) == (2026, 12, 1, 21, 30)
    assert now.utcoffset() == timedelta(hours=-5)


def test_aware_text_is_converted_to_eastern_time():
    now = parse_now("2026-12-02T02:30:00+00:00")
    assert (now.day, now.hour, now.minute) == (1, 21, 30)
    assert now.tzinfo is EASTERN


def test_summer_dates_use_daylight_offset():
    assert parse_now("2026-07-01 21:30").utcoffset() == timedelta(hours=-4)


def test_unparseable_text_raises():
    with pytest.raises(ValueError):
        parse_now("half past nine")


def test_fixed_clock_returns_the_fixed_time():
    fixed = parse_now("2026-12-01 21:30")
    assert Clock(fixed).now() == fixed


def test_real_clock_is_timezone_aware_eastern():
    now = Clock().now()
    assert now.tzinfo is EASTERN
    assert abs(now - datetime.now(timezone.utc)) < timedelta(seconds=5)
