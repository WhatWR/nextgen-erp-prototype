"""AI assistant config sourced from ERPNext (Desk-managed), with env fallbacks.

Mirrors :class:`order_intake.erpnext_line_config.ErpnextLineConfig`: the
``NextGen AI Settings`` single DocType owns the gateway URL, API key and model
names; this service caches them briefly. Environment variables
(``AI_GATEWAY_URL``, ``AI_GATEWAY_API_KEY``, ``AI_CHAT_MODEL``,
``AI_EMBEDDINGS_MODEL``, ``AI_ASSISTANT_ENABLED``) fill any blank field so
local development works without touching Desk. A missing or failing ERPNext
method degrades to "disabled" unless the env explicitly enables the assistant —
the order flow must never depend on this feature.
"""

from __future__ import annotations

import os
import time

from .erpnext_client import ERPNextClient, ERPNextError

_CACHE_TTL = 30.0
_TRUTHY = {"1", "true", "yes", "on"}
DEFAULT_MAX_TOOL_CALLS = 4
CONFIG_METHOD = "nextgen_erp.ai.get_ai_config"


class ErpnextAIConfig:
    def __init__(self, client: ERPNextClient | None = None, cache_ttl: float = _CACHE_TTL):
        self.client = client or ERPNextClient()
        self._ttl = cache_ttl
        self._cache: dict | None = None
        self._fetched_at = 0.0

    def _remote(self) -> dict:
        now = time.monotonic()
        if self._cache is None or (now - self._fetched_at) > self._ttl:
            try:
                message = self.client.call_method(CONFIG_METHOD)
                self._cache = message if isinstance(message, dict) else {}
            except ERPNextError:
                self._cache = {}
            self._fetched_at = now
        return self._cache

    def refresh(self) -> None:
        self._cache = None

    def snapshot(self) -> dict:
        remote = self._remote()
        env_enabled = os.environ.get("AI_ASSISTANT_ENABLED", "").strip().lower() in _TRUTHY
        merged = {
            "enabled": bool(remote.get("enabled")) if remote else env_enabled,
            "gateway_url": str(remote.get("gateway_url") or os.environ.get("AI_GATEWAY_URL", "")),
            "api_key": str(remote.get("api_key") or os.environ.get("AI_GATEWAY_API_KEY", "")),
            "chat_model": str(remote.get("chat_model") or os.environ.get("AI_CHAT_MODEL", "")),
            "embeddings_model": str(
                remote.get("embeddings_model") or os.environ.get("AI_EMBEDDINGS_MODEL", "")
            ),
            "max_tool_calls": int(remote.get("max_tool_calls") or DEFAULT_MAX_TOOL_CALLS),
        }
        return merged

    def enabled(self) -> bool:
        config = self.snapshot()
        return bool(config["enabled"] and config["gateway_url"] and config["chat_model"])
