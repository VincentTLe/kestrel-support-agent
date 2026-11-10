import json

import pytest

from kestrel.llm import LLMError, OpenRouterClient

OK_BODY = json.dumps({"choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}]})


class FakeTransport:
    """Stands in for the HTTP POST: returns scripted (status, body) pairs."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(transport):
    return OpenRouterClient("key", "some/model", send=transport, sleep=lambda seconds: None)


def test_returns_the_assistant_message():
    transport = FakeTransport((200, OK_BODY))
    message = client(transport).complete([{"role": "user", "content": "hello"}], [])
    assert message == {"role": "assistant", "content": "hi"}


def test_request_carries_model_messages_and_tools():
    transport = FakeTransport((200, OK_BODY))
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}]
    client(transport).complete([{"role": "user", "content": "hello"}], tools)
    payload = transport.payloads[0]
    assert payload["model"] == "some/model"
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert payload["tools"] == tools


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
def test_transient_statuses_are_retried(status):
    transport = FakeTransport((status, "{}"), (200, OK_BODY))
    assert client(transport).complete([], [])["content"] == "hi"
    assert len(transport.payloads) == 2


def test_a_dropped_connection_is_retried():
    transport = FakeTransport(TimeoutError("timed out"), (200, OK_BODY))
    assert client(transport).complete([], [])["content"] == "hi"


def test_gives_up_after_three_attempts():
    transport = FakeTransport((503, "{}"), (503, "{}"), (503, "{}"))
    with pytest.raises(LLMError):
        client(transport).complete([], [])
    assert len(transport.payloads) == 3


@pytest.mark.parametrize("status", [400, 401, 402, 403])
def test_client_errors_are_not_retried(status):
    transport = FakeTransport((status, json.dumps({"error": {"code": status, "message": "nope"}})))
    with pytest.raises(LLMError, match="nope"):
        client(transport).complete([], [])
    assert len(transport.payloads) == 1


def test_an_error_inside_a_200_body_is_an_error():
    transport = FakeTransport((200, json.dumps({"error": {"code": 502, "message": "upstream died"}})))
    with pytest.raises(LLMError, match="upstream died"):
        client(transport).complete([], [])


@pytest.mark.parametrize("body", ["not json", "{}", json.dumps({"choices": []})])
def test_a_body_without_a_message_is_an_error(body):
    with pytest.raises(LLMError):
        client(FakeTransport((200, body))).complete([], [])


def test_the_api_key_never_appears_in_error_text():
    transport = FakeTransport((401, json.dumps({"error": {"code": 401, "message": "bad key"}})))
    with pytest.raises(LLMError) as raised:
        OpenRouterClient("sk-secret-123", "m", send=transport, sleep=lambda s: None).complete([], [])
    assert "sk-secret-123" not in str(raised.value)
