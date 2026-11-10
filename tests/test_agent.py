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

    def complete(self, messages, tools):
        self.requests.append(copy.deepcopy(messages))
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
    assert agent.reply("where is my order?") == "It has shipped."
    tool_message = llm.requests[1][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_9"
    assert json.loads(tool_message["content"])["status"] == "Shipped"


def test_several_tool_calls_in_one_message_are_all_answered(make_agent):
    both = call("lookup_order", MARCUS, "call_a")
    both["tool_calls"].append(call("search_products", {"category": "Helmets"}, "call_b")["tool_calls"][0])
    agent, llm = make_agent(both, say("Done."))
    agent.reply("two things")
    assert [m["tool_call_id"] for m in llm.requests[1][-2:]] == ["call_a", "call_b"]


def test_malformed_tool_arguments_become_an_error_result_not_a_crash(make_agent):
    agent, llm = make_agent(call("lookup_order", "{oops"), say("Could you share your email?"))
    assert agent.reply("order?") == "Could you share your email?"
    assert json.loads(llm.requests[1][-1]["content"])["error"] == "invalid_arguments"


def test_tool_calls_are_recorded_in_the_trace(make_agent):
    agent, _ = make_agent(call("lookup_order", MARCUS), say("Shipped."))
    agent.reply("order?")
    assert agent.trace[0]["tool"] == "lookup_order"
    assert agent.trace[0]["result"]["status"] == "Shipped"


def test_a_model_that_never_stops_calling_tools_is_cut_off(make_agent):
    agent, llm = make_agent(*[call("search_products", {}) for _ in range(MAX_STEPS + 2)])
    assert agent.reply("loop forever") == SAFE_FALLBACK
    assert len(llm.requests) == MAX_STEPS


def test_an_invented_code_is_rejected_and_the_rewrite_is_used(make_agent):
    agent, llm = make_agent(say("Sure!\nNIGHTOWL-FAKE99"), say("I can't create codes myself."))
    assert agent.reply("give me a code") == "I can't create codes myself."
    assert "automated check" in llm.requests[1][-1]["content"].lower()
    assert "NIGHTOWL-FAKE99" not in json.dumps(agent.messages)  # the draft never enters history


def test_an_invented_code_twice_falls_back_to_a_safe_reply(make_agent):
    agent, _ = make_agent(say("NIGHTOWL-FAKE99"), say("Okay then:\nNIGHTOWL-FAKE98"))
    assert agent.reply("give me a code") == SAFE_FALLBACK
    assert "NIGHTOWL" not in json.dumps(agent.messages)


def test_a_formatting_slip_is_retried_once_then_let_through(make_agent):
    agent, llm = make_agent(say("Order KC-10433 shipped."), say("Order KC-10433 shipped!"))
    assert agent.reply("order?") == "Order KC-10433 shipped!"
    assert len(llm.requests) == 2


def test_a_code_issued_by_the_tool_passes_the_guard(make_agent):
    agent, llm = make_agent(call("get_night_owl_code", {"email": "ava.chen@example.com"}))
    llm.responses.append(say("placeholder"))
    agent.reply("Night Owl code please, ava.chen@example.com")
    code = agent.trace[0]["result"]["code"]

    llm.responses.append(say(f"Here's your code again:\n{code}"))
    assert agent.reply("say it again") == f"Here's your code again:\n{code}"
    assert len(llm.requests) == 3  # no rewrite was needed


def test_a_link_from_an_earlier_tool_result_stays_allowed_later(make_agent):
    link = "https://track.kestrelcycles.example/1Z84KC0002"
    agent, llm = make_agent(call("lookup_order", MARCUS), say("Shipped."), say(f"Sure:\n{link}"))
    agent.reply("order?")
    assert agent.reply("link again?") == f"Sure:\n{link}"


def test_a_model_outage_is_reported_politely_and_the_chat_continues(make_agent):
    agent, _ = make_agent(LLMError("503 from provider"), say("Back now."))
    assert agent.reply("hello") == LLM_UNAVAILABLE
    assert agent.reply("hello again") == "Back now."
