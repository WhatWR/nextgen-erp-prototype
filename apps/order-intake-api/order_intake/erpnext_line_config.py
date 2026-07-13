"""LINE channel config sourced from ERPNext (Desk-managed).

When ``LINE_CONFIG_SOURCE=erpnext`` the server reads the LINE channel secret,
access token and enabled flag from the ERPNext ``LINE Channel Settings`` DocType
via the ``nextgen_erp.api.get_line_config`` whitelisted method, instead of the
local JSON store. The webhook receiver still runs here (extraction stays
external); ERPNext just owns the configuration.

Exposes the same method surface (`webhook_enabled`, `secret`, `access_token`,
`merchant_id`, `status`) that :class:`LineIntegrationStore` does, so it is a
drop-in for the server handler.
"""

from __future__ import annotations

import time

from .erpnext_client import ERPNextClient

_CACHE_TTL = 30.0


class ErpnextLineConfig:
    def __init__(self, client: ERPNextClient | None = None, cache_ttl: float = _CACHE_TTL):
        self.client = client or ERPNextClient()
        self._ttl = cache_ttl
        self._cache: dict | None = None
        self._fetched_at = 0.0

    def _config(self) -> dict:
        now = time.monotonic()
        if self._cache is None or (now - self._fetched_at) > self._ttl:
            message = self.client.call_method("nextgen_erp.api.get_line_config")
            self._cache = message if isinstance(message, dict) else {}
            self._fetched_at = now
        return self._cache

    def refresh(self) -> None:
        self._cache = None

    # --- surface used by the server handler ---------------------------------
    def webhook_enabled(self) -> bool:
        c = self._config()
        return bool(c.get("enabled") and c.get("channel_secret"))

    def secret(self) -> str:
        return str(self._config().get("channel_secret") or "")

    def access_token(self) -> str:
        return str(self._config().get("channel_access_token") or "")

    def merchant_id(self) -> str:
        return str(self._config().get("merchant") or "demo")

    def status(self) -> dict:
        c = self._config()
        secret = str(c.get("channel_secret") or "")
        token = str(c.get("channel_access_token") or "")
        return {
            "source": "erpnext",
            "configured": bool(c.get("channel_id") and secret),
            "enabled": self.webhook_enabled(),
            "channel_id": c.get("channel_id") or "",
            "merchant_id": self.merchant_id(),
            "webhook_url": c.get("webhook_url") or "",
            "access_token_configured": bool(token),
            "receive_only": not bool(token),
        }

    def save(self, payload: dict) -> dict:
        raise ValueError("LINE settings are managed in ERPNext (LINE Channel Settings)")

    def test(self) -> dict:
        return {"ok": self.webhook_enabled(), "source": "erpnext"}
