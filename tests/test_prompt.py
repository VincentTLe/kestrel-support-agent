import pytest

from kestrel.clock import parse_now
from kestrel.prompt import build_system_prompt


@pytest.mark.parametrize("now", ["2026-12-01 21:00", "2026-12-01 21:30", "2026-12-01 23:59"])
def test_inside_the_window_the_prompt_says_night_owl_is_open(now):
    prompt = build_system_prompt(parse_now(now))
    assert "Night Owl is OPEN right now" in prompt
    assert "CLOSED" not in prompt


@pytest.mark.parametrize("now", ["2026-12-01 15:00", "2026-12-01 20:59", "2026-12-02 00:00"])
def test_outside_the_window_the_prompt_says_closed_so_no_email_is_asked_for(now):
    prompt = build_system_prompt(parse_now(now))
    assert "Night Owl is CLOSED right now" in prompt
    assert "OPEN right now" not in prompt
