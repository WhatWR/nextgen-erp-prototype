from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from order_intake.server import build_handler
from order_intake.service import OrderIntakeService


class HttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        service = OrderIntakeService(root / "http.sqlite3", root / "exports")
        service.seed_demo(reset=True)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(service))
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
        self.assertEqual(approved["status"], "approved")
        _, dashboard = self.request("/api/dashboard?merchant_id=demo")
        self.assertEqual(dashboard["metrics"]["approved"], 1)


if __name__ == "__main__":
    unittest.main()
