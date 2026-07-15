from __future__ import annotations

import importlib.util
import json
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from order_intake.line import verify_line_signature

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("sandbox", ROOT / "scripts" / "sandbox.py")
sandbox = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(sandbox)


def _request(url, method="GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=body, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode() or "{}")


class _FakeIntake(BaseHTTPRequestHandler):
    """Captures the signed webhook the sandbox forwards to order-intake."""

    captured: list[tuple[bytes, str]] = []

    def log_message(self, *args):  # noqa: A002
        pass

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).captured.append((body, self.headers.get("X-Line-Signature", "")))
        reply = json.dumps({"accepted": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)


class SandboxTest(unittest.TestCase):
    SECRET = "test-channel-secret"

    @classmethod
    def setUpClass(cls):
        cls.intake_server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeIntake)
        threading.Thread(target=cls.intake_server.serve_forever, daemon=True).start()
        intake_url = f"http://127.0.0.1:{cls.intake_server.server_port}"
        cls.server = sandbox.serve(
            "127.0.0.1", 0, intake_url=intake_url, channel_secret=cls.SECRET
        )
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.intake_server.shutdown()

    def setUp(self):
        _request(f"{self.base}/inbox", method="DELETE")
        _FakeIntake.captured.clear()

    def test_line_push_lands_in_the_inbox(self):
        _request(
            f"{self.base}/v2/bot/message/push",
            method="POST",
            payload={"to": "Udemo", "messages": [{"type": "text", "text": "ใบแจ้งหนี้ค่ะ"}]},
        )
        _, inbox = _request(f"{self.base}/inbox")
        self.assertEqual(len(inbox["messages"]), 1)
        self.assertEqual(inbox["messages"][0]["to"], "Udemo")

    def test_chat_uses_script_then_falls_back_to_default(self):
        _request(
            f"{self.base}/script/chat",
            method="POST",
            payload={"messages": [{"role": "assistant", "content": "scripted"}]},
        )
        _, first = _request(
            f"{self.base}/v1/chat/completions",
            method="POST",
            payload={"messages": [{"role": "user", "content": "สวัสดี"}]},
        )
        _, second = _request(
            f"{self.base}/v1/chat/completions",
            method="POST",
            payload={"messages": [{"role": "user", "content": "สวัสดี"}]},
        )
        self.assertEqual(first["choices"][0]["message"]["content"], "scripted")
        self.assertIn("สวัสดี", second["choices"][0]["message"]["content"])

    def test_embeddings_are_deterministic(self):
        _, a = _request(
            f"{self.base}/v1/embeddings", method="POST", payload={"input": ["ก", "ข"]}
        )
        _, b = _request(
            f"{self.base}/v1/embeddings", method="POST", payload={"input": ["ก", "ข"]}
        )
        self.assertEqual(a["data"], b["data"])
        self.assertNotEqual(a["data"][0]["embedding"], a["data"][1]["embedding"])

    def test_slip_verifier_echoes_expected_amount_by_default(self):
        _, result = _request(
            f"{self.base}/verify-slip",
            method="POST",
            payload={"image_base64": "", "expected_amount": 780.0},
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["amount"], 780.0)
        self.assertTrue(result["reference_no"].startswith("SANDBOX-"))

    def test_slip_verifier_can_be_configured_to_fail(self):
        _request(
            f"{self.base}/config/slip",
            method="POST",
            payload={"verified": False, "confidence": 0.2, "amount": 1.0},
        )
        try:
            _, result = _request(
                f"{self.base}/verify-slip",
                method="POST",
                payload={"image_base64": "", "expected_amount": 780.0},
            )
            self.assertFalse(result["verified"])
        finally:
            _request(f"{self.base}/config/slip", method="POST", payload={})

    def test_simulated_webhook_is_correctly_signed(self):
        status, result = _request(
            f"{self.base}/simulate/line",
            method="POST",
            payload={"line_id": "Udemo", "text": "M-150 2 ลัง"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["sent"])
        body, signature = _FakeIntake.captured[0]
        # The signature must satisfy the same verifier the real webhook uses.
        self.assertTrue(verify_line_signature(body, signature, self.SECRET))
        events = json.loads(body.decode())["events"]
        self.assertEqual(events[0]["source"]["userId"], "Udemo")
        self.assertEqual(events[0]["message"]["text"], "M-150 2 ลัง")

    def test_image_content_endpoint_serves_a_png(self):
        with urllib.request.urlopen(
            f"{self.base}/v2/bot/message/slip-1/content", timeout=10
        ) as response:
            content = response.read()
        self.assertEqual(content[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
