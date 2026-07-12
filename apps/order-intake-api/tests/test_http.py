from __future__ import annotations

import json
import base64
import hashlib
import hmac
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from order_intake.server import build_handler
from order_intake.line_integration import LineIntegrationStore
from order_intake.service import OrderIntakeService


class HttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        service = OrderIntakeService(root / "http.sqlite3", root / "exports")
        service.seed_demo(reset=True)
        self.integration = LineIntegrationStore(root / "line_integration.json")
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), build_handler(service, self.integration)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path: str, method: str = "GET", body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode())

    def raw_request(self, path: str, body: bytes, headers: dict):
        request = urllib.request.Request(self.base + path, data=body, method="POST", headers=headers)
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode())

    def test_health_dashboard_and_review_flow(self) -> None:
        status, health = self.request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")

        status, draft = self.request(
            "/api/intake/messages",
            "POST",
            {
                "merchant_id": "demo",
                "customer_ref": "C-001",
                "text": "น้ำแดง 2 ลัง",
                "idempotency_key": "http-1",
            },
        )
        self.assertEqual(status, 201)
        _, queue = self.request("/api/reviews?merchant_id=demo")
        self.assertEqual(queue["reviews"][0]["id"], draft["id"])

        _, approved = self.request(
            f"/api/reviews/{draft['id']}/approve",
            "POST",
            {"reviewer": "owner"},
        )
        self.assertEqual(approved["status"], "awaiting_customer_confirmation")
        _, reserved = self.request(
            f"/api/workflows/{draft['id']}/customer-confirm",
            "POST",
            {"confirmed": True},
        )
        self.assertEqual(reserved["status"], "reserved_for_pick")
        _, delivered = self.request(
            f"/api/workflows/{draft['id']}/delivery-complete", "POST", {}
        )
        self.assertEqual(delivered["status"], "awaiting_payment")
        _, paid = self.request(
            f"/api/workflows/{draft['id']}/payment-received",
            "POST",
            {"reference": "HTTP-PAY-1"},
        )
        self.assertEqual(paid["status"], "paid")
        _, dashboard = self.request("/api/dashboard?merchant_id=demo")
        self.assertEqual(dashboard["status_counts"]["paid"], 1)

    def test_line_integration_setup_test_and_webhook(self) -> None:
        _, initial = self.request("/api/integrations/line")
        self.assertFalse(initial["configured"])
        _, saved = self.request(
            "/api/integrations/line",
            "POST",
            {
                "channel_id": "2000000000",
                "channel_secret": "test-secret",
                "merchant_id": "demo",
                "webhook_url": f"{self.base}/webhooks/line",
                "enabled": True,
            },
        )
        self.assertTrue(saved["enabled"])
        self.assertNotIn("test-secret", json.dumps(saved))
        _, tested = self.request("/api/integrations/line/test", "POST", {})
        self.assertTrue(tested["ready_to_receive"])

        payload = json.dumps(
            {
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "line-http-1",
                        "message": {"id": "m-1", "type": "text", "text": "น้ำแดง 2 ลัง"},
                        "source": {"type": "user", "userId": "U123"},
                    }
                ]
            },
            ensure_ascii=False,
        ).encode()
        signature = base64.b64encode(
            hmac.new(b"test-secret", payload, hashlib.sha256).digest()
        ).decode()
        status, accepted = self.raw_request(
            "/webhooks/line",
            payload,
            {"Content-Type": "application/json", "X-Line-Signature": signature},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(accepted["draft_ids"]), 1)


if __name__ == "__main__":
    unittest.main()
