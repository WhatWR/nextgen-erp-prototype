from __future__ import annotations

import json
import base64
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .line import verify_line_signature
from .service import (
    ConflictError,
    NotFoundError,
    OrderIntakeService,
    ValidationError,
)


REVIEW_RE = re.compile(r"^/api/reviews/(?P<draft_id>[^/]+)$")
DECISION_RE = re.compile(r"^/api/reviews/(?P<draft_id>[^/]+)/(?P<action>approve|reject)$")


def build_handler(service: OrderIntakeService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NextGenOrderIntake/0.1"

        def end_headers(self) -> None:
            origin = self.headers.get("Origin") or "*"
            allowed = os.environ.get("ORDER_INTAKE_ALLOWED_ORIGIN", "*")
            self.send_header("Access-Control-Allow-Origin", origin if allowed == "*" else allowed)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Prototype-Key, X-Line-Signature")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
            super().end_headers()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            try:
                self._require_prototype_key()
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if parsed.path == "/health":
                    self._json({"status": "ok", "service": "order-intake-api"})
                    return
                if parsed.path == "/api/dashboard":
                    merchant_id = self._query_value(query, "merchant_id", "demo")
                    self._json(service.dashboard(merchant_id))
                    return
                if parsed.path == "/api/reviews":
                    merchant_id = self._query_value(query, "merchant_id", "demo")
                    status = self._query_value(query, "status")
                    self._json({"reviews": service.list_reviews(merchant_id, status=status)})
                    return
                if parsed.path == "/api/audit":
                    merchant_id = self._query_value(query, "merchant_id", "demo")
                    self._json({"events": service.list_audit(merchant_id)})
                    return
                match = REVIEW_RE.match(parsed.path)
                if match:
                    self._json(service.get_review(match.group("draft_id")))
                    return
                self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self._handle_error(exc)

        def do_POST(self) -> None:  # noqa: N802
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/webhooks/line":
                    self._handle_line_webhook()
                    return
                self._require_prototype_key()
                body = self._json_body()
                if parsed.path == "/api/demo/seed":
                    self._json(service.seed_demo(reset=bool(body.get("reset", False))))
                    return
                if parsed.path == "/api/intake/messages":
                    result = service.create_from_message(
                        merchant_id=str(body.get("merchant_id") or "demo"),
                        customer_ref=body.get("customer_ref"),
                        text=str(body.get("text") or ""),
                        idempotency_key=str(body.get("idempotency_key") or ""),
                        source_channel=str(body.get("source_channel") or "simulator"),
                    )
                    self._json(result, HTTPStatus.CREATED if result.get("created") else HTTPStatus.OK)
                    return
                if parsed.path == "/api/catalog/import":
                    filename = str(body.get("filename") or "")
                    try:
                        content = base64.b64decode(str(body.get("content_base64") or ""), validate=True)
                    except ValueError as exc:
                        raise ValidationError("content_base64 must be valid base64") from exc
                    self._json(
                        service.import_catalog(
                            str(body.get("merchant_id") or "demo"), filename, content
                        )
                    )
                    return
                match = DECISION_RE.match(parsed.path)
                if match:
                    reviewer = str(body.get("reviewer") or "")
                    if match.group("action") == "approve":
                        result = service.approve_review(
                            match.group("draft_id"),
                            reviewer=reviewer,
                            note=body.get("note"),
                            confirm_exceptions=bool(body.get("confirm_exceptions", False)),
                        )
                    else:
                        result = service.reject_review(
                            match.group("draft_id"), reviewer=reviewer, note=body.get("note")
                        )
                    self._json(result)
                    return
                self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            except Exception as exc:
                self._handle_error(exc)

        def do_PATCH(self) -> None:  # noqa: N802
            try:
                self._require_prototype_key()
                parsed = urlparse(self.path)
                match = REVIEW_RE.match(parsed.path)
                if not match:
                    self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                    return
                body = self._json_body()
                self._json(
                    service.update_review(
                        match.group("draft_id"),
                        reviewer=str(body.get("reviewer") or ""),
                        note=body.get("note"),
                        items=list(body.get("items") or []),
                    )
                )
            except Exception as exc:
                self._handle_error(exc)

        def _handle_line_webhook(self) -> None:
            body = self._raw_body()
            secret = os.environ.get("LINE_CHANNEL_SECRET", "")
            signature = self.headers.get("X-Line-Signature", "")
            if not verify_line_signature(body, signature, secret):
                self._json({"error": "invalid_line_signature"}, HTTPStatus.UNAUTHORIZED)
                return
            payload = json.loads(body.decode("utf-8") or "{}")
            created = []
            for event in payload.get("events", []):
                message = event.get("message") or {}
                if event.get("type") != "message" or message.get("type") != "text":
                    continue
                source = event.get("source") or {}
                result = service.create_from_message(
                    merchant_id=os.environ.get("LINE_MERCHANT_ID", "demo"),
                    customer_ref=None,
                    text=str(message.get("text") or ""),
                    idempotency_key=str(event.get("webhookEventId") or message.get("id") or ""),
                    source_channel="line",
                )
                created.append(result["id"])
            self._json({"accepted": True, "draft_ids": created})

        def _raw_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > 1_000_000:
                raise ValidationError("request body is too large")
            return self.rfile.read(length)

        def _json_body(self) -> dict:
            raw = self._raw_body()
            if not raw:
                return {}
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError("request body must be valid UTF-8 JSON") from exc
            if not isinstance(value, dict):
                raise ValidationError("request body must be a JSON object")
            return value

        def _require_prototype_key(self) -> None:
            expected = os.environ.get("ORDER_INTAKE_API_KEY")
            if expected and self.headers.get("X-Prototype-Key") != expected:
                raise PermissionError("invalid prototype API key")

        @staticmethod
        def _query_value(query: dict, key: str, default=None):
            values = query.get(key)
            return values[0] if values else default

        def _json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _handle_error(self, exc: Exception) -> None:
            if isinstance(exc, NotFoundError):
                status = HTTPStatus.NOT_FOUND
            elif isinstance(exc, ConflictError):
                status = HTTPStatus.CONFLICT
            elif isinstance(exc, (ValidationError, ValueError)):
                status = HTTPStatus.UNPROCESSABLE_ENTITY
            elif isinstance(exc, PermissionError):
                status = HTTPStatus.UNAUTHORIZED
            else:
                status = HTTPStatus.INTERNAL_SERVER_ERROR
            self._json({"error": type(exc).__name__, "message": str(exc)}, status)

        def log_message(self, fmt: str, *args) -> None:
            if os.environ.get("ORDER_INTAKE_QUIET") != "1":
                super().log_message(fmt, *args)

    return Handler


def serve(
    host: str = "127.0.0.1",
    port: int = 8200,
    db_path: str | Path | None = None,
    export_dir: str | Path | None = None,
) -> None:
    base = Path(__file__).resolve().parent.parent
    db_path = Path(db_path or os.environ.get("ORDER_INTAKE_DB", base / "data" / "app.sqlite3"))
    export_dir = Path(
        export_dir or os.environ.get("ORDER_INTAKE_EXPORT_DIR", base / "data" / "exports")
    )
    service = OrderIntakeService(db_path, export_dir)
    if os.environ.get("ORDER_INTAKE_AUTO_SEED", "1") == "1":
        service.seed_demo(reset=False)
    server = ThreadingHTTPServer((host, port), build_handler(service))
    print(f"Order Intake API listening on http://{host}:{port}")
    print("Local prototype only; legal accounting remains in the customer's existing system.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
