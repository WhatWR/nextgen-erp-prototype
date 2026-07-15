#!/usr/bin/env python3
"""NextGen local sandbox — simulates every third party the stack talks to.

LOCAL DEVELOPMENT ONLY. One stdlib process that stands in for:

* the LINE Messaging API (``POST /v2/bot/message/push`` and
  ``GET /v2/bot/message/<id>/content``) — everything NextGen ERP "writes back"
  to the customer lands in an inspectable inbox;
* an OpenAI-compatible AI gateway (``/v1/chat/completions``, ``/v1/embeddings``)
  — scripted replies or a deterministic default; and
* the payment-slip verifier (``POST /verify-slip``) — configurable outcome.

It can also *drive* the stack: ``POST /simulate/line`` builds a correctly
HMAC-signed LINE webhook and posts it to the order-intake service.

Point the stack at the sandbox:

    # LINE push + slip download (site config; NEVER on a production site)
    bench --site nextgen.localhost set-config nextgen_line_api_base http://127.0.0.1:8300
    # AI gateway: Desk → NextGen AI Settings → Gateway URL = http://127.0.0.1:8300
    # Slip verifier: Desk → NextGen Payment Settings → URL = http://127.0.0.1:8300/verify-slip

Run::

    LINE_CHANNEL_SECRET=... python3 scripts/sandbox.py --port 8300

Inspect what the ERP sent to the "customer"::

    curl -s http://127.0.0.1:8300/inbox | python3 -m json.tool

Drive a customer message end-to-end::

    curl -s -X POST http://127.0.0.1:8300/simulate/line \
        -H 'Content-Type: application/json' \
        -d '{"line_id": "Udemo", "text": "M-150 ราคาเท่าไหร่"}'
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.request
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

MAX_BODY_BYTES = 2 * 1024 * 1024
EMBEDDING_DIM = 8
DEFAULT_PORT = 8300
DEFAULT_INTAKE_URL = "http://127.0.0.1:8200"

# 1x1 transparent PNG — served as the "payment slip" image content.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg=="
)


def sign_line_body(secret: str, body: bytes) -> str:
    """Signature exactly as LINE computes it (and order-intake verifies it)."""
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def build_line_webhook(
    line_id: str,
    *,
    text: str | None = None,
    image_message_id: str | None = None,
    event_id: str | None = None,
) -> dict:
    """A minimal but correctly-shaped LINE webhook payload for one event."""
    if text is None and image_message_id is None:
        raise ValueError("either text or image_message_id is required")
    message: dict = (
        {"type": "image", "id": image_message_id}
        if image_message_id
        else {"type": "text", "id": f"msg-{int(time.time() * 1000)}", "text": text}
    )
    return {
        "destination": "sandbox",
        "events": [
            {
                "type": "message",
                "webhookEventId": event_id or f"sandbox-{int(time.time() * 1000)}",
                "source": {"type": "user", "userId": line_id},
                "message": message,
            }
        ],
    }


def deterministic_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Stable pseudo-embedding so RAG ranking is reproducible offline."""
    digest = hashlib.sha256((text or "").encode("utf-8")).digest()
    return [digest[i] / 255.0 for i in range(dim)]


class SandboxState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.inbox: list[dict] = []
        self.chat_script: deque[dict] = deque()
        self.slip_result: dict | None = None
        self.slip_counter = 0


def _default_chat_reply(payload: dict) -> dict:
    last_user = ""
    for message in reversed(payload.get("messages") or []):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            last_user = message["content"]
            break
    return {
        "role": "assistant",
        "content": f"(sandbox) ได้รับข้อความ: {last_user[:200]}".strip(),
    }


def serve(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    *,
    intake_url: str = DEFAULT_INTAKE_URL,
    channel_secret: str = "",
    state: SandboxState | None = None,
) -> ThreadingHTTPServer:
    state = state or SandboxState()
    intake_url = intake_url.rstrip("/")

    class Handler(BaseHTTPRequestHandler):
        server_version = "NextGenSandbox/0.1"

        # ------------------------------------------------------------- #
        def log_message(self, fmt, *args):  # noqa: A002
            if os.environ.get("SANDBOX_QUIET", "1") not in ("1", "true"):
                super().log_message(fmt, *args)

        def _json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY_BYTES)
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            parsed = json.loads(raw.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {}

        # ------------------------------------------------------------- #
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                self._json({"status": "ok", "service": "nextgen-sandbox"})
                return
            if path == "/inbox":
                with state.lock:
                    self._json({"messages": list(state.inbox)})
                return
            if path.startswith("/v2/bot/message/") and path.endswith("/content"):
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(TINY_PNG)))
                self.end_headers()
                self.wfile.write(TINY_PNG)
                return
            self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

        def do_DELETE(self) -> None:  # noqa: N802
            if urlparse(self.path).path == "/inbox":
                with state.lock:
                    state.inbox.clear()
                self._json({"cleared": True})
                return
            self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            try:
                path = urlparse(self.path).path
                if path == "/v2/bot/message/push":
                    self._handle_line_push()
                elif path == "/v1/chat/completions":
                    self._handle_chat()
                elif path == "/v1/embeddings":
                    self._handle_embeddings()
                elif path == "/verify-slip":
                    self._handle_verify_slip()
                elif path == "/script/chat":
                    self._handle_script_chat()
                elif path == "/config/slip":
                    self._handle_config_slip()
                elif path == "/simulate/line":
                    self._handle_simulate_line()
                else:
                    self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            except (ValueError, json.JSONDecodeError) as exc:
                self._json({"error": "bad_request", "detail": str(exc)}, HTTPStatus.BAD_REQUEST)

        # --------------------------- LINE stub ------------------------ #
        def _handle_line_push(self) -> None:
            payload = self._body()
            entry = {
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "to": payload.get("to"),
                "messages": payload.get("messages") or [],
            }
            with state.lock:
                state.inbox.append(entry)
            self._json({})

        # ------------------------ AI gateway stub --------------------- #
        def _handle_chat(self) -> None:
            payload = self._body()
            with state.lock:
                message = state.chat_script.popleft() if state.chat_script else None
            if message is None:
                message = _default_chat_reply(payload)
            self._json({"choices": [{"message": message}]})

        def _handle_embeddings(self) -> None:
            payload = self._body()
            texts = payload.get("input") or []
            if isinstance(texts, str):
                texts = [texts]
            data = [
                {"index": i, "embedding": deterministic_embedding(str(t))}
                for i, t in enumerate(texts)
            ]
            self._json({"data": data})

        def _handle_script_chat(self) -> None:
            payload = self._body()
            messages = payload.get("messages") or []
            with state.lock:
                state.chat_script.extend(m for m in messages if isinstance(m, dict))
                queued = len(state.chat_script)
            self._json({"queued": queued})

        # ----------------------- slip verifier stub ------------------- #
        def _handle_verify_slip(self) -> None:
            payload = self._body()
            with state.lock:
                state.slip_counter += 1
                counter = state.slip_counter
                configured = dict(state.slip_result) if state.slip_result else None
            if configured is None:
                configured = {"verified": True, "confidence": 0.99}
            if configured.get("amount") is None:
                configured["amount"] = payload.get("expected_amount")
            configured.setdefault("reference_no", f"SANDBOX-{counter:04d}")
            self._json(configured)

        def _handle_config_slip(self) -> None:
            payload = self._body()
            with state.lock:
                state.slip_result = payload or None
            self._json({"slip_result": payload or "default"})

        # ------------------------ webhook driver ---------------------- #
        def _handle_simulate_line(self) -> None:
            payload = self._body()
            line_id = str(payload.get("line_id") or "")
            if not line_id:
                self._json({"error": "line_id_required"}, HTTPStatus.BAD_REQUEST)
                return
            if not channel_secret:
                self._json(
                    {"error": "channel_secret_not_configured",
                     "detail": "start the sandbox with --channel-secret or LINE_CHANNEL_SECRET"},
                    HTTPStatus.CONFLICT,
                )
                return
            webhook = build_line_webhook(
                line_id,
                text=payload.get("text"),
                image_message_id=payload.get("image_message_id"),
                event_id=payload.get("event_id"),
            )
            body = json.dumps(webhook, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                f"{intake_url}/webhooks/line",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Line-Signature": sign_line_body(channel_secret, body),
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    intake_status = response.status
                    intake_body = json.loads(response.read().decode("utf-8") or "{}")
            except Exception as exc:  # surface connection problems to the caller
                self._json(
                    {"error": "intake_unreachable", "detail": str(exc)[:300]},
                    HTTPStatus.BAD_GATEWAY,
                )
                return
            self._json({"sent": True, "intake_status": intake_status, "intake": intake_body})

    server = ThreadingHTTPServer((host, port), Handler)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="NextGen local sandbox (LINE/AI/slip simulator)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--intake-url", default=os.environ.get("ORDER_INTAKE_URL", DEFAULT_INTAKE_URL))
    parser.add_argument(
        "--channel-secret",
        default=os.environ.get("LINE_CHANNEL_SECRET", ""),
        help="LINE channel secret used to sign simulated webhooks",
    )
    args = parser.parse_args()
    server = serve(
        args.host,
        args.port,
        intake_url=args.intake_url,
        channel_secret=args.channel_secret,
    )
    print(f"NextGen sandbox listening on http://{args.host}:{args.port}")
    print(f"  inbox:        GET  http://{args.host}:{args.port}/inbox")
    print(f"  simulate:     POST http://{args.host}:{args.port}/simulate/line")
    print(f"  intake target: {args.intake_url}")
    if not args.channel_secret:
        print("  WARNING: no channel secret configured; /simulate/line is disabled")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
