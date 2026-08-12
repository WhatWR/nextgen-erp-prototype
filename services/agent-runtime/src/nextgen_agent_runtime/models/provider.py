"""Model provider adapters.

The runtime owns provider retries and response normalisation. Nothing the
model returns is trusted: tool names are re-checked against the release
allowlist by the orchestrator and re-authorised again by Frappe, and every
quantity, price and permission is re-derived server-side.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class ModelError(RuntimeError):
    """Any failure to obtain a usable assistant message."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    finish_reason: str = ""

    def as_assistant_message(self) -> dict[str, Any]:
        if not self.tool_calls:
            return {"role": "assistant", "content": self.content}
        return {
            "role": "assistant",
            "content": self.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in self.tool_calls
            ],
        }


class ModelProvider(Protocol):
    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse: ...


def normalize_base_url(value: str) -> str:
    """Accept either the provider host or its documented ``/v1`` base URL."""
    base = (value or "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/v1") else f"{base}/v1"


def parse_tool_calls(message: dict[str, Any]) -> tuple[ToolCall, ...]:
    """Normalise OpenAI-style ``tool_calls`` into typed calls.

    Malformed arguments become an empty mapping rather than an exception: an
    unusable call is rejected by the allowlist or by Frappe's tool validation,
    which produces a recorded step instead of an aborted run.
    """
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        if not isinstance(raw, dict):
            continue
        function = raw.get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(ToolCall(id=str(raw.get("id") or uuid.uuid4()), name=name, arguments=arguments))
    return tuple(calls)


def _default_transport(url: str, headers: dict[str, str], body: bytes, timeout: float):
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise ModelError(f"model provider is unreachable: {exc.reason}") from exc


class HTTPModelProvider:
    """OpenAI-compatible chat-completions client with bounded retries."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        max_attempts: int = 3,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key or ""
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self.transport = transport or _default_transport
        self.sleep = sleep

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        if not self.base_url or not self.api_key:
            raise ModelError("model base URL and API key are required")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": 0.1,
            "max_tokens": 900,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        body = json.dumps(payload, ensure_ascii=False).encode()
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            status, raw = self.transport(self.endpoint, self._headers(), body, self.timeout)
            text = raw.decode(errors="replace")
            if status < 400:
                return self._parse(text, model)
            last_error = f"HTTP {status}: {text[:300]}"
            if status not in RETRYABLE_STATUS or attempt == self.max_attempts:
                break
            self.sleep(min(2.0 * attempt, 5.0))
        raise ModelError(f"model provider failed: {last_error}")

    def _parse(self, text: str, requested_model: str) -> ModelResponse:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ModelError("model provider returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ModelError("model provider returned an unexpected payload")
        choices = data.get("choices") or []
        message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise ModelError("model provider returned no assistant message")
        raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        usage = {
            key: int(raw_usage.get(key) or 0)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        return ModelResponse(
            content=str(message.get("content") or "").strip(),
            tool_calls=parse_tool_calls(message),
            usage=usage,
            model=str(data.get("model") or requested_model),
            finish_reason=str((choices[0] or {}).get("finish_reason") or ""),
        )


class ScriptedModelProvider:
    """Deterministic provider for tests and shadow rehearsals."""

    def __init__(self, responses: list[ModelResponse]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        self.calls.append({"model": model, "messages": list(messages), "tools": list(tools or [])})
        if not self._responses:
            raise ModelError("scripted provider exhausted")
        return self._responses.pop(0)
