from __future__ import annotations

import json
import unittest

from order_intake.erpnext_client import ERPNextClient
from order_intake.erpnext_line_config import ErpnextLineConfig


class CountingTransport:
    """Fake transport returning a fixed get_line_config message; counts calls."""

    def __init__(self, message: dict):
        self.message = message
        self.calls = 0

    def __call__(self, url, method, headers, body):
        self.calls += 1
        return 200, json.dumps({"message": self.message}).encode("utf-8")


CONFIG = {
    "enabled": True,
    "merchant": "demo",
    "channel_id": "2000000000",
    "channel_secret": "sekret",
    "webhook_url": "https://example.com/webhooks/line",
}


class ErpnextLineConfigTest(unittest.TestCase):
    def _cfg(self, message=None):
        transport = CountingTransport(message if message is not None else CONFIG)
        client = ERPNextClient("https://erp.example", "k", "s", transport=transport)
        return ErpnextLineConfig(client), transport

    def test_reads_signature_config_without_exposing_push_token(self) -> None:
        cfg, _ = self._cfg()
        self.assertTrue(cfg.webhook_enabled())
        self.assertEqual(cfg.secret(), "sekret")
        self.assertEqual(cfg.access_token(), "")
        self.assertEqual(cfg.merchant_id(), "demo")
        self.assertEqual(cfg.status()["source"], "erpnext")

    def test_caches_config_between_calls(self) -> None:
        cfg, transport = self._cfg()
        cfg.secret()
        cfg.access_token()
        cfg.webhook_enabled()
        self.assertEqual(transport.calls, 1)  # one fetch, then cached

    def test_disabled_when_no_secret(self) -> None:
        cfg, _ = self._cfg({"enabled": True, "channel_secret": "", "merchant": "demo"})
        self.assertFalse(cfg.webhook_enabled())


if __name__ == "__main__":
    unittest.main()
