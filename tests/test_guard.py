from kestrel.guard import check, collect_facts

TRACKING = "https://track.kestrelcycles.example/1Z84KC0002"
RETURNS = "https://kestrelcycles.example/returns"
CODE = "NIGHTOWL-ABC234"


def rules(reply, allowed=frozenset()):
    return {(v.rule, v.hard) for v in check(reply, set(allowed))}


# --- facts come only from tool results ---


def test_facts_are_the_links_and_codes_found_in_a_tool_result():
    result = {"tracking_url": TRACKING, "items": [{"note": f"Portal: {RETURNS}"}], "code": CODE}
    assert collect_facts(result) == {TRACKING, RETURNS, CODE}


def test_a_tool_result_without_links_or_codes_has_no_facts():
    assert collect_facts({"status": "outside_window", "discount_percent": 15}) == set()


# --- hard rules: never show an invented code or link ---


def test_a_code_the_tools_never_issued_is_a_hard_violation():
    assert ("unissued_code", True) in rules("Here you go:\nNIGHTOWL-FREE50")


def test_an_issued_code_on_its_own_line_passes():
    assert rules(f"Your code is ready.\n{CODE}\nSmooth roads ahead!", {CODE}) == set()


def test_a_link_the_tools_never_returned_is_a_hard_violation():
    assert ("unknown_link", True) in rules("Track it here:\nhttps://track.kestrelcycles.example/FAKE1")


def test_the_returns_link_is_blocked_until_a_tool_returns_it():
    assert ("unknown_link", True) in rules(f"Start here:\n{RETURNS}")
    assert rules(f"Start here:\n{RETURNS}", {RETURNS}) == set()


def test_an_invented_support_address_is_a_hard_violation():
    assert ("unknown_link", True) in rules("Email support@kestrelcycles.example for help.")


def test_trailing_punctuation_does_not_hide_an_allowed_link_from_the_check():
    # The link is allowed, so no hard violation; it is not alone on its line, so a soft one.
    assert rules(f"Track it at {TRACKING}.", {TRACKING}) == {("own_line", False)}


# --- soft rules: brand voice formatting ---


def test_more_than_two_emoji_is_a_soft_violation():
    assert rules("Let's get you rolling! 🚲 ⛰️ 🚵") == {("too_many_emoji", False)}


def test_two_emoji_pass():
    assert rules("Let's get you rolling! 🚲 ⛰️") == set()


def test_a_code_inside_a_sentence_is_a_soft_violation():
    assert rules(f"Your code is {CODE}, enjoy!", {CODE}) == {("own_line", False)}


def test_an_order_number_inside_a_sentence_is_a_soft_violation():
    assert rules("Your order KC-10433 has shipped.") == {("own_line", False)}


def test_an_order_number_on_its_own_line_passes():
    assert rules("Here is the latest on your order:\nKC-10433\nIt has shipped.") == set()


def test_plain_text_passes():
    assert rules("Happy to help! What's your order number and email?") == set()


def test_markdown_emphasis_is_a_soft_violation_because_the_terminal_shows_it_raw():
    assert rules("The **Switchback 29 Hardtail** is a great pick.") == {("markdown", False)}


def test_a_markdown_heading_is_a_soft_violation():
    assert rules("## Your options\nThe Switchback 29 Hardtail.") == {("markdown", False)}


def test_one_formatting_correction_names_every_crowded_token():
    [violation] = check(f"Order KC-10433 shipped, track it at {TRACKING} today.", {TRACKING})
    assert "KC-10433" in violation.detail and TRACKING in violation.detail
