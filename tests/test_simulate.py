from types import SimpleNamespace

from evals.simulate import run_journey, summarize

TRACKING = "https://track.kestrelcycles.example/1Z84KC0002"
JOURNEY = {
    "id": "typo",
    "opening": "where's KC-10432? marcus.reed@example.com",
    "situation": "s",
    "goal": "g",
    "reveal": {"name": "Marcus Reed"},
    "max_turns": 4,
    "resolved_when": {"reply_matches": ["1Z84KC0002"]},
    "must_not": {"reply_lacks": ["kestrelcycles\\.example/returns"]},
}


class StubAgent:
    def __init__(self, replies, trace=()):
        self.replies, self.trace = list(replies), list(trace)
        self.llm = SimpleNamespace(calls=[])

    def reply(self, text):
        self.llm.calls.append({"cost": 0.001})
        return self.replies.pop(0)


class ScriptedCustomer:
    def __init__(self, *messages):
        self.messages, self.seen = list(messages), []

    def complete(self, messages, tools):
        self.seen.append(list(messages))
        return {"content": self.messages.pop(0)}


FOUND = [{"tool": "recover_order", "arguments": "{}", "result": {"tracking_url": TRACKING}}]


def test_a_journey_is_resolved_on_the_turn_the_check_passes_and_then_rated():
    agent = StubAgent(["What's the full name on the order?", f"Found it:\n{TRACKING}"], FOUND)
    customer = ScriptedCustomer("Marcus Reed", '{"csat": 5, "effort": 2}')
    result = run_journey(JOURNEY, lambda now, state: agent, customer)
    assert result["resolved_turn"] == 2 and result["failures"] == []
    assert result["turns"] == [JOURNEY["opening"], "Marcus Reed"]
    assert result["rating"] == {"csat": 5, "effort": 2}


def test_the_simulated_customer_sees_the_agent_as_the_other_side():
    agent = StubAgent(["Name?", f"Found it:\n{TRACKING}"], FOUND)
    customer = ScriptedCustomer("Marcus Reed", '{"csat": 4, "effort": 1}')
    run_journey(JOURNEY, lambda now, state: agent, customer)
    first = customer.seen[0]
    assert first[0]["role"] == "system" and "Marcus Reed" in first[0]["content"]
    assert [m["role"] for m in first[1:]] == ["assistant", "user"]  # customer's opening, then the agent's reply


def test_giving_up_ends_the_journey_unresolved():
    agent = StubAgent(["I can't help with that."])
    result = run_journey(JOURNEY, lambda now, state: agent, ScriptedCustomer("DONE", "no json"))
    assert result["resolved_turn"] is None
    assert result["failures"] == ["not resolved within 1 turn(s)"]
    assert result["rating"] == {"csat": None, "effort": None}


def test_a_forbidden_reply_fails_the_journey_even_when_resolved():
    trace = FOUND + [{"tool": "lookup_order", "arguments": "{}", "result": {"returns_portal_url": "https://kestrelcycles.example/returns"}}]
    agent = StubAgent([f"{TRACKING}\nReturn it here:\nhttps://kestrelcycles.example/returns"], trace)
    result = run_journey(JOURNEY, lambda now, state: agent, ScriptedCustomer('{"csat": 3, "effort": 3}'))
    assert result["resolved_turn"] == 1
    assert any("kestrelcycles" in failure for failure in result["failures"])


def test_summary_reports_resolution_turns_and_ratings():
    agent_a = StubAgent(["Name?", f"Found it:\n{TRACKING}"], FOUND)
    agent_b = StubAgent(["No."])
    a = run_journey(JOURNEY, lambda n, s: agent_a, ScriptedCustomer("Marcus Reed", '{"csat": 5, "effort": 1}'))
    b = run_journey(JOURNEY, lambda n, s: agent_b, ScriptedCustomer("DONE", '{"csat": 1, "effort": 5}'))
    s = summarize([a, b])
    assert s["resolution_rate"] == 0.5 and s["turns_to_resolution"] == 2
    assert s["csat"] == 3 and s["effort"] == 3
