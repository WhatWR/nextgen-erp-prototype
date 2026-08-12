"""Sanitisation applied before persistence, logging or model-visible results.

The runtime redacts before transport and Frappe redacts again before writing an
Agent Step: neither side trusts the other to have done it. The hash of the
complete pre-redaction payload is stored alongside the redacted copy so audit
remains verifiable without keeping the secret.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[redacted]"
MAX_STRING_LENGTH = 2000
MAX_DEPTH = 8
MAX_ITEMS = 100

SECRET_KEY_PARTS = (
	"api_key",
	"apikey",
	"access_token",
	"authorization",
	"channel_secret",
	"client_secret",
	"credential",
	"id_token",
	"password",
	"passwd",
	"private_key",
	"pwd",
	"refresh_token",
	"secret",
	"session_key",
	"signature",
	"token",
)

REASONING_KEY_PARTS = ("reasoning", "reasoning_content", "thinking", "chain_of_thought")

_LINE_ID_RE = re.compile(r"\b[Uu][0-9a-fA-F]{32}\b")
_BEARER_RE = re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-]{8,}")
_FRAPPE_TOKEN_RE = re.compile(r"\btoken\s+[A-Za-z0-9]{8,}:[A-Za-z0-9]{8,}")
_LONG_SECRET_RE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9]{16,}\b")
_THAI_PHONE_RE = re.compile(r"\b0\d{8,9}\b")
_PROMPTPAY_RE = re.compile(r"\b\d{13}\b")


def _matches(key: str, parts: tuple[str, ...]) -> bool:
	lowered = key.casefold()
	return any(part in lowered for part in parts)


def is_secret_key(key: str) -> bool:
	return _matches(key, SECRET_KEY_PARTS)


def is_reasoning_key(key: str) -> bool:
	return _matches(key, REASONING_KEY_PARTS)


def redact_text(value: str) -> str:
	value = _BEARER_RE.sub(REDACTED, value)
	value = _FRAPPE_TOKEN_RE.sub(REDACTED, value)
	value = _LONG_SECRET_RE.sub(REDACTED, value)
	value = _LINE_ID_RE.sub(lambda match: f"{match.group(0)[:3]}***", value)
	value = _THAI_PHONE_RE.sub(lambda match: f"{match.group(0)[:4]}***", value)
	value = _PROMPTPAY_RE.sub(lambda match: f"{match.group(0)[:4]}***", value)
	if len(value) > MAX_STRING_LENGTH:
		return value[:MAX_STRING_LENGTH] + "…[truncated]"
	return value


def redact(value: Any, *, depth: int = 0) -> Any:
	"""Return a JSON-safe, bounded, secret-free copy of ``value``."""
	if depth > MAX_DEPTH:
		return "[truncated: max depth]"
	if value is None or isinstance(value, (bool, int, float)):
		return value
	if isinstance(value, str):
		return redact_text(value)
	if isinstance(value, dict):
		result: dict[str, Any] = {}
		for index, (key, item) in enumerate(value.items()):
			name = str(key)
			if index >= MAX_ITEMS:
				result["[truncated]"] = f"{len(value) - MAX_ITEMS} more field(s)"
				break
			if is_secret_key(name) or is_reasoning_key(name):
				result[name] = REDACTED
				continue
			result[name] = redact(item, depth=depth + 1)
		return result
	if isinstance(value, (list, tuple, set)):
		items = list(value)
		redacted = [redact(item, depth=depth + 1) for item in items[:MAX_ITEMS]]
		if len(items) > MAX_ITEMS:
			redacted.append(f"[truncated: {len(items) - MAX_ITEMS} more item(s)]")
		return redacted
	return redact_text(str(value))


def redact_error(error: BaseException | str, *, limit: int = 500) -> str:
	"""One sanitised line for an Agent Step or Agent Run error summary."""
	if isinstance(error, BaseException):
		text = f"{type(error).__name__}: {error}"
	else:
		text = str(error)
	return redact_text(text.replace("\n", " ").strip())[:limit]
