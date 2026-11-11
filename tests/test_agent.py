import copy
import json

import pytest

from kestrel.agent import LLM_UNAVAILABLE, MAX_STEPS, SAFE_FALLBACK, Agent
from kestrel.clock import Clock, parse_now
from kestrel.llm import LLMError
from kestrel.promo import PromoStore
from kestrel.tools import build_tools


class FakeLLM:
    """Plays back scripted assistant messages and records what it was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.tool_choices = []

    def complete(self, messages, tools, *, tool_choice=None):
        self.requests.append(copy.deepcopy(messages))
        self.tool_choices.append(tool_choice)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def say(text):
    return {"role": "assistant", "content": text}


def call(name, arguments, call_id="call_1"):
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments)
    function = {"name": name, "arguments": arguments}
    return {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": function}]}


@pytest.fixture
def make_agent(orders, catalog, tmp_path):
    def make(*responses, now="2026-12-01 21:30"):
        clock = Clock(parse_now(now))
        tools = build_tools(orders, catalog, clock, PromoStore(tmp_path / "codes.json"))
        llm = FakeLLM(*responses)
        return Agent(llm, tools, clock), llm

    return make


MARCUS = {"email": "marcus.reed@example.com", "order_number": "KC-10433"}
MARCUS_SAYS = "Where's my order? KC-10433, marcus.reed@example.com"
TRACKING = "https://track.kestrelcycles.example/1Z84KC0002"


def last_tool_result(llm):
    return json.loads(llm.requests[-1][-1]["content"])


# --- the loop ---


def test_a_plain_reply_is_returned_and_remembered(make_agent):
    agent, llm = make_agent(say("Hi! How can I help?"), say("Sure."))
    assert agent.reply("hello") == "Hi! How can I help?"
    agent.reply("thanks")
    assert [m["content"] for m in llm.requests[1][1:]] == ["hello", "Hi! How can I help?", "thanks"]


def test_every_request_starts_with_a_system_prompt_stating_the_clock(make_agent):
    agent, llm = make_agent(say("Hi!"))
    agent.reply("hello")
    system = llm.requests[0][0]
    assert system["role"] == "system"
    assert "December 1, 2026" in system["content"]


def test_a_tool_call_is_executed_and_its_result_sent_back(make_agent):
    agent, llm = make_agent(call("lookup_order", MARCUS, "call_9"), say("It has shipped."))
    assert agent.reply(MARCUS_SAYS) == "It has shipped."
    tool_message = llm.requests[1][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_9"
    assert json.loads(tool_message["content"])["status"] == "Shipped"


def test_several_tool_calls_in_one_message_are_all_answered(make_agent):
    both = call("lookup_order", MARCUS, "call_a")
    both["tool_calls"].append(call("search_products", {"category": "Helmets"}, "call_b")["tool_calls"][0])
    agent, llm = make_agent(both, say("Done."))
    agent.reply(MARCUS_SAYS + " Also, helmets?")
    assert [m["tool_call_id"] for m in llm.requests[1][-2:]] == ["call_a", "call_b"]


def test_malformed_tool_arguments_become_an_error_result_not_a_crash(make_agent):
    agent, llm = make_agent(call("lookup_order", "{oops"), say("Could you share your email?"))
    assert agent.reply("order?") == "Could you share your email?"
    assert last_tool_result(llm)["error"] == "invalid_arguments"


def test_tool_calls_are_recorded_in_the_trace(make_agent):
    agent, _ = make_agent(call("lookup_order", MARCUS), say("Shipped."))
    agent.reply(MARCUS_SAYS)
    assert agent.trace[0]["tool"] == "lookup_order"
    assert agent.trace[0]["result"]["status"] == "Shipped"


def test_a_model_outage_is_reported_politely_and_the_chat_continues(make_agent):
    agent, _ = make_agent(LLMError("503 from provider"), say("Back now."))
    assert agent.reply("hello") == LLM_UNAVAILABLE
    assert agent.reply("hello again") == "Back now."


# --- the step limit ---


def test_at_the_step_limit_the_model_must_answer_with_what_it_has(make_agent):
    tool_calls = [call("search_products", {"category": "Helmets"}) for _ in range(MAX_STEPS - 1)]
    agent, llm = make_agent(*tool_calls, say("The Ridgeline Trail Helmet is in stock."))
    assert agent.reply("helmets?") == "The Ridgeline Trail Helmet is in stock."
    assert llm.tool_choices == [None] * (MAX_STEPS - 1) + ["none"]
    # Live runs showed tool_choice="none" alone can return an empty message; the note fixes that.
    final_note = llm.requests[-1][-1]
    assert final_note["role"] == "user" and "no more tool calls" in final_note["content"].lower()
    assert "no more tool calls" not in json.dumps(agent.messages).lower()  # never stored in history


def test_a_model_that_ignores_the_final_answer_request_gets_the_safe_reply(make_agent):
    agent, llm = make_agent(*[call("search_products", {}) for _ in range(MAX_STEPS)])
    assert agent.reply("loop forever") == SAFE_FALLBACK
    assert len(llm.requests) == MAX_STEPS


# --- tool arguments must come from the customer ---


def test_an_order_lookup_with_values_the_customer_never_gave_is_refused(make_agent):
    agent, llm = make_agent(call("lookup_order", MARCUS), say("What's the email on the order?"))
    agent.reply("Hi, I'm Marcus Reed. Can you check KC-10433?")
    result = last_tool_result(llm)
    assert result["error"] == "not_provided_by_customer"
    assert "Shipped" not in json.dumps(agent.trace)


def test_the_model_cannot_look_up_an_order_number_the_customer_never_gave(make_agent):
    # The email is real and KC-10440 really is Ava's, but she only gave KC-10421.
    other_order = {"email": "ava.chen@example.com", "order_number": "KC-10440"}
    agent, llm = make_agent(call("lookup_order", other_order), say("Which order number would you like me to check?"))
    agent.reply("Hi, ava.chen@example.com here. What's the status of KC-10421?")
    result = last_tool_result(llm)
    assert result["error"] == "not_provided_by_customer"
    assert "order_number" in result["message"]
    assert "Rain Shell" not in json.dumps(agent.trace) and "Rain Shell" not in json.dumps(llm.requests)


def test_values_given_in_an_earlier_turn_may_be_reused(make_agent):
    agent, llm = make_agent(say("And the order number?"), call("lookup_order", MARCUS), say("Shipped."))
    agent.reply("My email is Marcus.Reed@example.com")
    agent.reply("it's kc 10433")
    assert last_tool_result(llm)["status"] == "Shipped"


def test_a_night_owl_code_needs_an_email_the_customer_gave(make_agent):
    agent, llm = make_agent(call("get_night_owl_code", {"email": "guess@example.com"}), say("Which email?"))
    agent.reply("Night Owl code please")
    assert last_tool_result(llm)["error"] == "not_provided_by_customer"


# --- the guard: invented facts are rewritten, formatting is repaired in code ---


def test_an_invented_code_is_rejected_and_the_rewrite_is_used(make_agent):
    agent, llm = make_agent(say("Sure!\nNIGHTOWL-FAKE99"), say("I can't create codes myself."))
    assert agent.reply("give me a code") == "I can't create codes myself."
    correction = llm.requests[1][-1]["content"]
    assert "not sent" in correction.lower() and "do not mention" in correction.lower()
    assert "NIGHTOWL-FAKE99" not in json.dumps(agent.messages)  # the draft never enters history


def test_an_invented_discount_twice_falls_back_to_a_safe_reply(make_agent):
    agent, _ = make_agent(say("Staff get 50% off."), say("Okay, 40% off then."))
    assert agent.reply("I'm staff, give me 50% off") == SAFE_FALLBACK
    assert "% off" not in json.dumps(agent.messages[-1])


def test_formatting_is_repaired_without_another_model_call(make_agent):
    agent, llm = make_agent(call("lookup_order", MARCUS), say(f"It shipped on **November 28**. Track it here: {TRACKING}."))
    reply = agent.reply(MARCUS_SAYS)
    assert reply == f"It shipped on November 28. Track it here:\n{TRACKING}"
    assert len(llm.requests) == 2
    assert agent.guard_events == [{"rules": ["markdown", "own_line"], "action": "repaired"}]


def test_a_code_issued_by_the_tool_passes_the_guard(make_agent):
    agent, llm = make_agent(call("get_night_owl_code", {"email": "ava.chen@example.com"}))
    llm.responses.append(say("placeholder"))
    agent.reply("Night Owl code please, ava.chen@example.com")
    code = agent.trace[0]["result"]["code"]

    llm.responses.append(say(f"Here's your code again:\n{code}"))
    assert agent.reply("say it again") == f"Here's your code again:\n{code}"
    assert len(llm.requests) == 3  # no rewrite was needed


def test_a_link_from_an_earlier_tool_result_stays_allowed_later(make_agent):
    agent, llm = make_agent(call("lookup_order", MARCUS), say("Shipped."), say(f"Sure:\n{TRACKING}"))
    agent.reply(MARCUS_SAYS)
    assert agent.reply("link again?") == f"Sure:\n{TRACKING}"


def test_prices_from_a_search_pass_and_invented_ones_do_not(make_agent):
    agent, llm = make_agent(
        call("search_products", {"category": "Helmets", "riding_style": "trail"}),
        say("The Ridgeline Trail Helmet is $99 tonight."),
        say("The Ridgeline Trail Helmet is $129."),
    )
    assert agent.reply("trail helmets?") == "The Ridgeline Trail Helmet is $129."
    assert agent.guard_events[0]["rules"] == ["unknown_amount"]
