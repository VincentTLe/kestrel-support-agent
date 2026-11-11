import pytest

from kestrel.clock import Clock, parse_now
from kestrel.features import static_features
from kestrel.prompt import build_system_prompt

FEATURES = static_features(Clock(parse_now("2026-12-01 21:30")))


def prompt_at(now, **kwargs):
    return build_system_prompt(parse_now(now), FEATURES, **kwargs)


@pytest.mark.parametrize("now", ["2026-12-01 21:00", "2026-12-01 21:30", "2026-12-01 23:59"])
def test_inside_the_window_the_prompt_says_night_owl_is_open(now):
    prompt = prompt_at(now)
    assert "Night Owl is OPEN right now" in prompt
    assert "CLOSED" not in prompt


@pytest.mark.parametrize("now", ["2026-12-01 15:00", "2026-12-01 20:59", "2026-12-02 00:00"])
def test_outside_the_window_the_prompt_says_closed_so_no_email_is_asked_for(now):
    prompt = prompt_at(now)
    assert "Night Owl is CLOSED right now" in prompt
    assert "OPEN right now" not in prompt


def test_every_feature_is_in_the_catalog_even_when_its_guidance_is_not_loaded():
    prompt = prompt_at("2026-12-01 21:30", active={"orders"})
    for feature in FEATURES:
        assert feature.summary in prompt
    assert "Each order needs its own number" in prompt  # orders guidance is loaded
    assert "fitting_sizes is the whole answer" not in prompt  # products guidance is not


def test_the_case_file_is_appended_when_there_is_one():
    assert "CASE FILE" not in prompt_at("2026-12-01 21:30")
    assert "Verified order: KC-10433" in prompt_at("2026-12-01 21:30", case_file="Verified order: KC-10433")
