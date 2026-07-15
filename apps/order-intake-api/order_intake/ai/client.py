"""Minimal OpenAI-compatible chat/embeddings client.

Any endpoint speaking ``/v1/chat/completions`` and ``/v1/embeddings`` works
(OpenAI, Together, a local gateway, ...). Mirrors the design of
:mod:`order_intake.erpnext_client`: stdlib-only, with an injectable transport
so tests never need a live endpoint.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 700


class AIError(RuntimeError):
    """Raised when the AI gateway fails or returns an unexpected response."""


# A transport takes (url, method, headers, body) and returns (status, body_bytes).
Transport = Callable[[str, str, dict[str, str], "bytes | None"], "tuple[int, bytes]"]


def _urllib_transport(
    url: str, method: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class AIClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("AI_GATEWAY_URL", "")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("AI_GATEWAY_API_KEY", "")
        self._transport = transport or _urllib_transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.base_url:
            raise AIError("AI gateway URL is not configured")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, raw = self._transport(f"{self.base_url}{path}", "POST", headers, body)
        text = raw.decode("utf-8", errors="replace") if raw else ""
        if status >= 400:
            raise AIError(f"AI gateway {path} failed (HTTP {status}): {text[:300].strip()}")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIError(f"AI gateway {path} returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise AIError(f"AI gateway {path} returned an unexpected response")
        return parsed

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> dict[str, Any]:
        """One non-streaming chat turn; returns the assistant ``message`` object."""
        if not model:
            raise AIError("A chat model must be configured")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        elif tool_choice:
            payload["tool_choice"] = tool_choice
        parsed = self._post("/v1/chat/completions", payload)
        choices = parsed.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIError("AI gateway returned no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise AIError("AI gateway returned no message")
        return message

    def embed(self, *, model: str, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts; order of vectors matches the input order."""
        if not model:
            raise AIError("An embeddings model must be configured")
        if not texts:
            return []
        parsed = self._post("/v1/embeddings", {"model": model, "input": texts})
        data = parsed.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise AIError("AI gateway returned a malformed embeddings response")
        vectors: list[list[float]] = []
        for entry in sorted(data, key=lambda d: d.get("index", 0)):
            vector = entry.get("embedding")
            if not isinstance(vector, list):
                raise AIError("AI gateway returned a malformed embedding vector")
            vectors.append([float(v) for v in vector])
        return vectors
