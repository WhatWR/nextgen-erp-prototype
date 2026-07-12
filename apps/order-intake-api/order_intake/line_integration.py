from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .line import verify_line_signature


class LineIntegrationStore:
    """Small local configuration store for the receive-only LINE prototype.

    Production deployments should replace this file with a managed secrets vault.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def status(self) -> dict:
        config = self._read()
        secret = str(config.get("channel_secret") or os.environ.get("LINE_CHANNEL_SECRET", ""))
        channel_id = str(config.get("channel_id") or os.environ.get("LINE_CHANNEL_ID", ""))
        access_token = str(
            config.get("channel_access_token") or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        )
        enabled = bool(config.get("enabled", False))
        webhook_url = str(config.get("webhook_url") or "http://127.0.0.1:8200/webhooks/line")
        return {
            "provider": "LINE Official Account",
            "configured": bool(channel_id and secret),
            "enabled": enabled and bool(channel_id and secret),
            "channel_id": channel_id,
            "merchant_id": str(config.get("merchant_id") or os.environ.get("LINE_MERCHANT_ID", "demo")),
            "webhook_url": webhook_url,
            "secret_masked": self._mask(secret),
            "access_token_configured": bool(access_token),
            "access_token_masked": self._mask(access_token),
            "secret_source": "environment" if not config.get("channel_secret") and secret else "local_prototype",
            "updated_at": config.get("updated_at"),
            "receive_only": not bool(access_token),
        }

    def save(self, payload: dict) -> dict:
        current = self._read()
        channel_id = str(payload.get("channel_id") or "").strip()
        merchant_id = str(payload.get("merchant_id") or "demo").strip()
        webhook_url = str(payload.get("webhook_url") or "").strip()
        secret = str(payload.get("channel_secret") or "").strip() or str(
            current.get("channel_secret") or os.environ.get("LINE_CHANNEL_SECRET", "")
        )
        access_token = str(payload.get("channel_access_token") or "").strip() or str(
            current.get("channel_access_token") or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        )
        enabled = bool(payload.get("enabled", False))

        if not channel_id:
            raise ValueError("LINE Channel ID is required")
        if not secret:
            raise ValueError("LINE Channel Secret is required")
        if not merchant_id:
            raise ValueError("Merchant workspace is required")
        parsed = urlparse(webhook_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or not webhook_url.endswith("/webhooks/line"):
            raise ValueError("Webhook URL must be a complete URL ending in /webhooks/line")
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("LINE requires HTTPS for a public webhook URL")

        config = {
            "channel_id": channel_id,
            "channel_secret": secret,
            "channel_access_token": access_token,
            "merchant_id": merchant_id,
            "webhook_url": webhook_url,
            "enabled": enabled,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp_path, 0o600)
        temp_path.replace(self.path)
        return self.status()

    def test(self) -> dict:
        status = self.status()
        secret = self.secret()
        if not status["configured"] or not secret:
            raise ValueError("Save the LINE Channel ID and Channel Secret before testing")
        body = b'{"events":[]}'
        signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        passed = verify_line_signature(body, signature, secret)
        return {
            **status,
            "signature_verification": passed,
            "ready_to_receive": passed and status["enabled"],
            "message": "Signature verification passed" if passed else "Signature verification failed",
        }

    def secret(self) -> str:
        return str(self._read().get("channel_secret") or os.environ.get("LINE_CHANNEL_SECRET", ""))

    def access_token(self) -> str:
        return str(
            self._read().get("channel_access_token")
            or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        )

    def webhook_enabled(self) -> bool:
        return bool(self.status()["enabled"])

    def merchant_id(self) -> str:
        return str(self.status()["merchant_id"])

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _mask(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 6:
            return "•" * len(value)
        return f"{value[:3]}{'•' * 8}{value[-3:]}"
