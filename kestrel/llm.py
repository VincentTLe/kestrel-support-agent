"""Chat-completions client for OpenRouter over raw HTTP (standard library only).

The agent depends on the small LLM protocol below, so tests swap in a scripted
fake and never touch the network.
"""

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Protocol

API_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504, 524, 529}
MAX_ATTEMPTS = 3


class LLMError(Exception):
    """The model could not be reached or returned something unusable."""


class LLM(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], *, tool_choice: str | None = None) -> dict:
        """Return the assistant message: {"role", "content", optional "tool_calls"}.

        tool_choice="none" asks for a text answer even though tools are offered.
        """


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = 60,
        send: Callable[[dict], tuple[int, str]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._send = send or self._post
        self._sleep = sleep
        self.calls: list[dict] = []  # one record per answered request: tokens, cost, latency

    def complete(self, messages: list[dict], tools: list[dict], *, tool_choice: str | None = None) -> dict:
        payload = {"model": self._model, "messages": messages, "temperature": 0.2, "max_tokens": 800}
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

        started = time.perf_counter()
        problem = "no attempt made"
        for attempt in range(MAX_ATTEMPTS):
            if attempt:
                self._sleep(2 ** (attempt - 1))  # 1s, then 2s
            try:
                status, body = self._send(payload)
            except OSError as error:  # timeouts, DNS failures, dropped connections
                problem = f"network error: {error}"
                continue
            if status in RETRYABLE_STATUSES:
                problem = f"HTTP {status}"
                continue
            choice, usage = self._parse(status, body)
            self.calls.append({
                "seconds": time.perf_counter() - started,
                "attempts": attempt + 1,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "cost": usage.get("cost"),  # USD, when the provider reports it
                "finish_reason": choice.get("finish_reason"),
            })
            return choice["message"]
        raise LLMError(f"OpenRouter request failed after {MAX_ATTEMPTS} attempts ({problem})")

    @staticmethod
    def _parse(status: int, body: str) -> tuple[dict, dict]:
        """Return (first choice, usage) or raise LLMError."""
        try:
            data = json.loads(body)
        except json.JSONDecodeError as error:
            raise LLMError(f"HTTP {status}: response was not JSON") from error
        if isinstance(data, dict) and data.get("error"):  # may arrive with a 200
            raise LLMError(f"HTTP {status}: {data['error'].get('message', data['error'])}")
        try:
            choice = data["choices"][0]
            choice["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise LLMError(f"HTTP {status}: response had no assistant message") from error
        return choice, data.get("usage") or {}

    def _post(self, payload: dict) -> tuple[int, str]:
        request = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", errors="replace")
