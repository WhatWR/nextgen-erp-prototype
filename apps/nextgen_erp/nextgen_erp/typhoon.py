"""Small OpenAI-compatible client for the hosted Typhoon API."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any


class TyphoonError(RuntimeError):
	pass


Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


def normalize_base_url(value: str) -> str:
	"""Accept either the provider host or its documented ``/v1`` base URL."""
	base = (value or "").strip().rstrip("/")
	if not base:
		return ""
	return base if base.endswith("/v1") else f"{base}/v1"


def _default_transport(url: str, headers: dict[str, str], body: bytes, timeout: float):
	request = urllib.request.Request(url, data=body, method="POST", headers=headers)
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			return response.status, response.read()
	except urllib.error.HTTPError as exc:
		return exc.code, exc.read()
	except urllib.error.URLError as exc:
		raise TyphoonError(f"Typhoon API is unreachable: {exc.reason}") from exc


def iter_sse_content(lines) -> Iterator[str]:
	"""Parse OpenAI-compatible SSE lines; kept pure for deterministic tests."""
	for raw_line in lines:
		line = raw_line.decode(errors="replace").strip() if isinstance(raw_line, bytes) else str(raw_line).strip()
		if not line.startswith("data:"):
			continue
		value = line[5:].strip()
		if value == "[DONE]":
			break
		try:
			event = json.loads(value)
		except json.JSONDecodeError:
			continue
		choices = event.get("choices") or []
		if choices:
			content = (choices[0].get("delta") or {}).get("content")
			if content:
				yield str(content)


class TyphoonClient:
	def __init__(
		self,
		base_url: str,
		api_key: str,
		*,
		timeout: float = 45,
		transport: Transport | None = None,
	):
		self.base_url = normalize_base_url(base_url)
		self.api_key = api_key or ""
		self.timeout = timeout
		self.transport = transport or _default_transport
		self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

	@property
	def endpoint(self) -> str:
		return f"{self.base_url}/chat/completions"

	def _headers(self) -> dict[str, str]:
		if not self.base_url or not self.api_key:
			raise TyphoonError("Typhoon gateway URL and API key are required")
		return {
			"Authorization": f"Bearer {self.api_key}",
			"Content-Type": "application/json",
			"Accept": "application/json",
		}

	def chat(
		self,
		*,
		model: str,
		messages: list[dict[str, Any]],
		tools: list[dict[str, Any]] | None = None,
		tool_choice: str | None = None,
		temperature: float = 0.1,
		max_tokens: int = 900,
	) -> dict[str, Any]:
		payload: dict[str, Any] = {
			"model": model,
			"messages": messages,
			"stream": False,
			"temperature": temperature,
			"max_tokens": max_tokens,
		}
		if tools:
			payload["tools"] = tools
			payload["tool_choice"] = tool_choice or "auto"
		elif tool_choice:
			payload["tool_choice"] = tool_choice
		status, raw = self.transport(
			self.endpoint,
			self._headers(),
			json.dumps(payload, ensure_ascii=False).encode(),
			self.timeout,
		)
		text = raw.decode(errors="replace")
		if status >= 400:
			raise TyphoonError(f"Typhoon API returned HTTP {status}: {text[:300]}")
		try:
			data = json.loads(text)
		except json.JSONDecodeError as exc:
			raise TyphoonError("Typhoon API returned invalid JSON") from exc
		choices = data.get("choices") if isinstance(data, dict) else None
		usage = data.get("usage") if isinstance(data, dict) else None
		if isinstance(usage, dict):
			for key in self.usage:
				self.usage[key] += int(usage.get(key) or 0)
		message = choices[0].get("message") if choices else None
		if not isinstance(message, dict):
			raise TyphoonError("Typhoon API returned no assistant message")
		return message

	def stream_chat(
		self,
		*,
		model: str,
		messages: list[dict[str, Any]],
		temperature: float = 0.1,
		max_tokens: int = 900,
	) -> Iterator[str]:
		"""Yield OpenAI-style SSE deltas using a direct streaming HTTP request."""
		payload = {
			"model": model,
			"messages": messages,
			"stream": True,
			"temperature": temperature,
			"max_tokens": max_tokens,
		}
		request = urllib.request.Request(
			self.endpoint,
			data=json.dumps(payload, ensure_ascii=False).encode(),
			method="POST",
			headers=self._headers(),
		)
		try:
			with urllib.request.urlopen(request, timeout=self.timeout) as response:
				yield from iter_sse_content(response)
		except urllib.error.HTTPError as exc:
			body = exc.read().decode(errors="replace")
			raise TyphoonError(f"Typhoon stream returned HTTP {exc.code}: {body[:300]}") from exc
		except urllib.error.URLError as exc:
			raise TyphoonError(f"Typhoon stream is unreachable: {exc.reason}") from exc
