from kestrel.clock import Clock, parse_now
from kestrel.features import BUILDERS, build_features, static_features
from kestrel.guard import GuardPolicy

FEATURES = static_features(Clock(parse_now("2026-12-01 21:30")))
MAX_GUIDANCE_WORDS = 200  # a feature's guidance is loaded on every call while it is active


def test_each_builder_makes_one_feature_with_unique_tool_names():
    assert len(FEATURES) == len(BUILDERS)
    names = [tool.name for feature in FEATURES for tool in feature.tools]
    assert len(names) == len(set(names))
    assert {"lookup_order", "search_products", "get_night_owl_code"} <= set(names)


def test_every_feature_has_a_one_line_summary_and_short_guidance():
    now = parse_now("2026-12-01 21:30")
    for feature in FEATURES:
        assert feature.summary and "\n" not in feature.summary
        words = len(feature.guidance(now).split())
        assert words <= MAX_GUIDANCE_WORDS, f"{feature.name} guidance has {words} words"


def test_the_guard_policy_is_assembled_from_the_features():
    policy = GuardPolicy.from_features(FEATURES)
    assert any(p.search("NIGHTOWL-ABC234") for p in policy.issued)
    assert any(p.search("KC-10433") for p in policy.copyable)
    assert policy.allowed_percents == {15.0}
    assert "Marcus Reed" in policy.protected
    assert any("KC-10433" in fn("it's kc 10433") for fn in policy.customer_identifiers)


def test_two_features_with_the_same_tool_name_are_rejected(orders, catalog, tmp_path):
    import pytest

    from kestrel.features import Context
    from kestrel.promo import PromoStore

    ctx = Context(orders, catalog, Clock(parse_now("2026-12-01 21:30")), PromoStore(tmp_path / "c.json"))
    import kestrel.features as registry

    original = registry.BUILDERS
    registry.BUILDERS = original + (original[0],)
    try:
        with pytest.raises(ValueError, match="same tool name"):
            build_features(ctx)
    finally:
        registry.BUILDERS = original
