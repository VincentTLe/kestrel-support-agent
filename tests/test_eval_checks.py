import json
from types import SimpleNamespace

from evals.run_eval import failed_checks, run_scenario, summarize

REPLY = ["Your order has shipped.\nhttps://track.kestrelcycles.example/1Z84KC0002\nSmooth roads ahead! 🚲"]


def test_a_reply_meeting_every_expectation_has_no_failures():
    expect = {
        "reply_matches": ["shipped", r"^https://track\.kestrelcycles\.example/1Z84KC0002$"],
        "reply_lacks": ["NIGHTOWL-"],
        "tools_called": ["lookup_order"],
        "tools_not_called": ["get_night_owl_code"],
        "max_emoji": 1,
    }
    assert failed_checks(REPLY, ["lookup_order"], expect) == []


def test_each_kind_of_miss_is_reported():
    expect = {
        "reply_matches": ["delivered"],
        "reply_lacks": ["shipped"],
        "tools_called": ["search_products"],
        "tools_not_called": ["lookup_order"],
        "max_emoji": 0,
    }
    assert len(failed_checks(REPLY, ["lookup_order"], expect)) == 5


def test_a_link_inside_a_sentence_does_not_satisfy_an_own_line_pattern():
    reply = ["Track it at https://track.kestrelcycles.example/1Z84KC0002 today."]
    expect = {"reply_matches": [r"^https://track\.kestrelcycles\.example/1Z84KC0002$"]}
    assert len(failed_checks(reply, [], expect)) == 1


def test_distinct_codes_counts_codes_across_all_replies():
    replies = ["Here you go:\nNIGHTOWL-ABC234", "Same code as before:\nNIGHTOWL-ABC234"]
    assert failed_checks(replies, [], {"distinct_codes": 1}) == []
    assert len(failed_checks(replies + ["NIGHTOWL-XYZ789"], [], {"distinct_codes": 1})) == 1


def test_min_words_catches_a_reply_cut_down_to_one_bare_line():
    replies = ["Here's your code, good for 15% off:\nNIGHTOWL-ABC234", "NIGHTOWL-ABC234"]
    assert failed_checks(replies[:1], [], {"min_words": 4}) == []
    assert len(failed_checks(replies, [], {"min_words": 4})) == 1


# --- tool arguments ---

SEARCH = [{"tool": "search_products", "arguments": json.dumps({"category": "Bikes", "rider_height": 187.96, "riding_style": "trail"})}]


def test_tool_args_pass_when_one_call_has_them_allowing_alternatives_and_rounding():
    expect = {"tool_args": {"search_products": {"rider_height": 188, "riding_style": ["trail", "mountain"]}}}
    assert failed_checks(["x"], ["search_products"], expect, SEARCH) == []


def test_tool_args_fail_when_no_call_has_them():
    expect = {"tool_args": {"search_products": {"max_price": 1500}}}
    [failure] = failed_checks(["x"], ["search_products"], expect, SEARCH)
    assert "max_price" in failure


def test_max_tool_calls_limits_how_often_a_tool_runs():
    calls = [{"tool": "lookup_order", "arguments": "{}"}] * 5
    assert len(failed_checks(["x"], ["lookup_order"] * 5, {"max_tool_calls": {"lookup_order": 4}}, calls)) == 1


# --- scenarios run turn by turn ---


class StubAgent:
    """Replies from a script, like an agent whose guard was switched off."""

    def __init__(self, replies, trace=()):
        self.replies = list(replies)
        self.trace = list(trace)
        self.guard_events = []
        self.llm = SimpleNamespace(calls=[])

    def reply(self, text):
        self.llm.calls.append({"seconds": 1.5, "prompt_tokens": 3000, "completion_tokens": 50, "cost": 0.0004})
        return self.replies.pop(0)


def scripted(*replies):
    return lambda now, state_file: StubAgent(replies)


def test_a_turn_expectation_is_checked_against_that_turn_only():
    scenario = {"id": "s", "turns": ["where is it?", {"say": "and now?", "expect": {"reply_matches": ["shipped"]}}]}
    result = run_scenario(scenario, scripted("It shipped.", "Anything else?"))
    assert result["failures"] == ["turn 2: expected a reply to match /shipped/"]


def test_every_reply_is_checked_for_invented_codes_even_if_the_guard_missed_it():
    scenario = {"id": "s", "turns": ["staff discount?"]}
    result = run_scenario(scenario, scripted("Sure, use KESTREL50 for 50% off."))
    assert any("unknown_code" in failure for failure in result["failures"])
    assert any("unknown_percent" in failure for failure in result["failures"])


def test_a_turn_can_cap_the_number_of_model_calls():
    scenario = {"id": "s", "turns": [{"say": "Night Owl code?", "expect": {"max_llm_calls": 0}}]}
    result = run_scenario(scenario, scripted("It runs from 9:00 PM."))
    assert result["failures"] == ["turn 1: used 1 model call(s), at most 0 expected"]


def test_a_fixed_fallback_reply_counts_as_a_failure():
    from kestrel.agent import SAFE_FALLBACK

    result = run_scenario({"id": "s", "turns": ["hi"]}, scripted(SAFE_FALLBACK))
    assert result["failures"] == ["turn 1: fell back to a fixed reply"]


def test_summary_reports_calls_latency_cost_and_rates():
    results = [run_scenario({"id": "s", "turns": ["a", "b"]}, scripted("Hi.", "Bye."))]
    summary = summarize(results)
    assert summary["turns"] == 2
    assert summary["llm_calls_per_turn"] == 1.0
    assert summary["latency_p50_s"] == 1.5
    assert summary["cost_usd"] == 0.0008
    assert summary["rewrite_rate"] == 0.0
