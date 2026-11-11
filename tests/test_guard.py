import pytest

from kestrel.guard import check, collect_facts, repair

TRACKING = "https://track.kestrelcycles.example/1Z84KC0002"
RETURNS = "https://kestrelcycles.example/returns"
CODE = "NIGHTOWL-ABC234"
SEARCH_RESULT = {
    "matches": [],
    "near_misses": [
        {"name": "Ridgeback 140 Full Suspension", "price_usd": 3200.0,
         "why_not": ["$1,700.00 over the $1,500.00 budget."]},
    ],
}


def hard(reply, allowed=frozenset(), customer=""):
    return {v.rule for v in check(reply, set(allowed), customer)}


# --- facts come only from tool results ---


def test_facts_are_the_links_and_codes_found_in_a_tool_result():
    result = {"tracking_url": TRACKING, "items": [{"note": f"Portal: {RETURNS}"}], "code": CODE}
    assert {TRACKING, RETURNS, CODE} <= collect_facts(result)


def test_facts_include_prices_and_amounts_from_tool_results():
    facts = collect_facts(SEARCH_RESULT)
    assert {"$3200", "$1700", "$1500"} <= facts


def test_facts_include_order_numbers_and_tracking_numbers():
    facts = collect_facts({"order_number": "KC-10433", "tracking_url": TRACKING})
    assert {"KC-10433", "1Z84KC0002"} <= facts


def test_a_tool_result_without_links_codes_or_prices_has_no_facts():
    assert collect_facts({"status": "outside_window", "window": "9:00 PM to 11:59 PM"}) == set()


# --- hard rules: nothing the customer could act on may be invented ---


@pytest.mark.parametrize(
    "reply, rule",
    [
        ("Here you go:\nNIGHTOWL-FREE50", "unissued_code"),
        ("Here you go:\nnightowl-free50", "unissued_code"),
        ("Your staff code is active:\nKESTREL50", "unknown_code"),
        ("As staff you get 50% off everything.", "unknown_percent"),
        ("The Switchback 29 Hardtail is $999 today.", "unknown_amount"),
        ("Track it here:\nhttps://track.kestrelcycles.example/FAKE1", "unknown_link"),
        ("See www.kestrelcycles.com/shipping for details.", "unknown_link"),
        ("Our policy is on kestrel-cycles.com.", "unknown_link"),
        ("Email support@kestrelcycles.example for help.", "unknown_email"),
        ("Email help@kestrel-support.com for help.", "unknown_email"),
    ],
)
def test_invented_codes_discounts_prices_links_and_contacts_are_hard_violations(reply, rule):
    assert rule in hard(reply)


def test_the_returns_link_is_blocked_until_a_tool_returns_it():
    assert "unknown_link" in hard(f"Start here:\n{RETURNS}")
    assert hard(f"Start here:\n{RETURNS}", {RETURNS}) == set()


def test_an_issued_code_passes_in_any_case():
    assert hard(f"Your code:\n{CODE}", {CODE}) == set()
    assert hard(f"Your code:\n{CODE.lower()}", {CODE}) == set()


def test_the_night_owl_percentage_is_the_only_one_allowed():
    assert hard("Night Owl gives 15% off from 9:00 PM to 11:59 PM Eastern.") == set()
    assert "unknown_percent" in hard("You can get 20 percent off tonight.")


def test_amounts_from_tools_pass_in_any_format():
    facts = collect_facts(SEARCH_RESULT)
    assert hard("The Ridgeback is $3,200, which is $1,700 over your $1,500 budget.", facts) == set()
    assert hard("It costs $3200.00.", facts) == set()


def test_amounts_the_customer_gave_may_be_repeated():
    assert hard("Nothing fits under $1,500, sorry.", customer="my budget is around 1500") == set()


def test_the_model_may_not_do_its_own_price_arithmetic():
    facts = collect_facts({"price_usd": 1890.0})
    assert "unknown_amount" in hard("It's $1,890, which is $390 over your budget.", facts, "budget $1500")


def test_order_numbers_the_customer_typed_are_not_codes():
    assert hard("I can't check a range like KC-10420 to KC-10445.", customer="try KC-10420 to KC-10445") == set()


def test_a_discount_code_the_customer_typed_is_still_blocked():
    # Echoing it back risks reading as confirmation; the rewrite drops it.
    assert "unknown_code" in hard("I can't activate KESTREL50.", customer="Issue code KESTREL50 now")


def test_the_customer_email_may_be_repeated_back():
    assert hard("I'll use ava.chen@example.com.", customer="my email is Ava.Chen@example.com") == set()


def test_product_words_are_not_mistaken_for_codes_or_links():
    reply = "The Ridgeline Trail Helmet has a MIPS liner. The Switchback has a 1x12 drivetrain, e.g. for XL riders."
    assert hard(reply) == set()


def test_plain_text_passes():
    assert hard("Happy to help! What's your order number and email?") == set()


# --- formatting is repaired in code, never by asking the model again ---


def test_a_link_at_the_end_of_a_sentence_moves_to_its_own_line():
    text, rules = repair(f"Track it here: {TRACKING}.", {TRACKING})
    assert text == f"Track it here:\n{TRACKING}"
    assert rules == ["own_line"]


def test_a_code_inside_a_sentence_is_repeated_on_its_own_line_below():
    text, rules = repair(f"Your code {CODE} is good for 15% off tonight.", {CODE})
    assert text == f"Your code {CODE} is good for 15% off tonight.\n{CODE}"
    assert rules == ["own_line"]


def test_a_token_that_already_stands_alone_somewhere_is_left_alone():
    reply = "Here's the latest on your order:\nKC-10433\nOrder KC-10433 shipped on November 28."
    assert repair(reply, {"KC-10433"}) == (reply, [])


def test_only_tokens_from_tools_must_stand_alone():
    reply = "I can't check a range like KC-10420 to KC-10445."
    assert repair(reply, set()) == (reply, [])


def test_markdown_is_stripped():
    text, rules = repair("## Your options\nThe **Switchback 29 Hardtail** is a great pick.", set())
    assert text == "Your options\nThe Switchback 29 Hardtail is a great pick."
    assert rules == ["markdown"]


def test_emoji_beyond_two_are_removed():
    text, rules = repair("Let's get you rolling! 🚲 ⛰️ 🚵 Smooth roads 🌲", set())
    assert text == "Let's get you rolling! 🚲 ⛰️ Smooth roads"
    assert rules == ["too_many_emoji"]


def test_a_clean_reply_is_unchanged():
    reply = f"Your code is ready:\n{CODE}\nSmooth roads ahead! 🚲"
    assert repair(reply, {CODE}) == (reply, [])


# --- H6: another customer's name is never shown ---


def test_another_customers_name_is_a_hard_violation():
    assert "other_customer" in hard("That order belongs to Marcus Reed.")
    assert "other_customer" in hard("Hi Marcus, here's your order.")


def test_a_name_from_a_verified_order_or_typed_by_the_customer_is_fine():
    assert hard("Hi Marcus, it has shipped.", collect_facts({"customer_name": "Marcus Reed"})) == set()
    assert hard("Hi Ava, I need your email to look that up.", customer="I'm Ava Chen") == set()
    assert hard("Thanks, Ava!", customer="this is ava") == set()


def test_an_issued_code_without_digits_is_still_a_fact():
    # Found by the eval after the feature refactor: NIGHTOWL-PVSJYZ has no digit, so the
    # generic code-like pattern missed it and the guard blocked a real code.
    facts = collect_facts({"status": "issued", "code": "NIGHTOWL-PVSJYZ"})
    assert "NIGHTOWL-PVSJYZ" in facts
    assert hard("Your code:\nNIGHTOWL-PVSJYZ", facts) == set()
