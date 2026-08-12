"""The runtime's HTTP surface.

Only Frappe talks to this service. Browser, LINE, webhook and Desk clients
authenticate through Frappe and never reach the runtime directly, which is
enforced here by a deployment-issued service token on every non-probe route and
by the private service network in front of it.

Dispatch is asynchronous: the endpoint accepts a run and returns. A timeout on
the caller's side means unknown delivery, not failure, so redelivery of the
same run ID is a no-op while that run is in flight.
"""

from __future__ import annotations

import asyncio
import hmac
import json
from typing import Any

from .. import telemetry
from ..models import contracts
from ..redaction import redact_error
from .openapi import build_openapi

MAX_BODY_BYTES = 256 * 1024
SERVICE_TOKEN_HEADER = "x-nextgen-service-token"
CORRELATION_HEADER = "x-nextgen-correlation-id"


class RunScheduler:
    """Bounded, deduplicating background execution of claimed runs."""

    def __init__(self, executor, max_concurrent: int = 8):
        self.executor = executor
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._in_flight: set[str] = set()
        self._cancelled: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

    @property
    def in_flight(self) -> int:
        return len(self._in_flight)

    def is_cancelled(self, run_id: str) -> bool:
        return run_id in self._cancelled

    def submit(self, run_id: str, correlation_id: str | None = None) -> bool:
        """Return False when the run is already in flight (duplicate delivery)."""
        if run_id in self._in_flight:
            telemetry.info("dispatch.duplicate", run_id=run_id, correlation_id=correlation_id)
            return False
        self._cancelled.discard(run_id)
        self._in_flight.add(run_id)
        task = asyncio.get_running_loop().create_task(self._execute(run_id, correlation_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    def cancel(self, run_id: str) -> bool:
        """Cooperative cancellation checked between turns of the tool loop."""
        self._cancelled.add(run_id)
        return run_id in self._in_flight

    async def _execute(self, run_id: str, correlation_id: str | None) -> None:
        try:
            async with self._semaphore:
                await asyncio.to_thread(self.executor.execute, run_id)
        except Exception as exc:  # pragma: no cover - executor is already fail-closed
            telemetry.error("run.scheduler_error", run_id=run_id, error=redact_error(exc))
        finally:
            self._in_flight.discard(run_id)
            self._cancelled.discard(run_id)

    async def drain(self) -> None:
        """Await outstanding runs; used by tests and graceful shutdown."""
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)


class Response:
    __slots__ = ("status", "payload", "headers")

    def __init__(self, status: int, payload: Any, headers: dict[str, str] | None = None):
        self.status = status
        self.payload = payload
        self.headers = headers or {}

    def encode(self) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
        body = json.dumps(self.payload, ensure_ascii=False, default=str).encode()
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ]
        headers += [(key.lower().encode(), value.encode()) for key, value in self.headers.items()]
        return self.status, headers, body


class RuntimeApp:
    """Minimal ASGI application. No framework, no middleware surface to widen."""

    def __init__(self, config, scheduler: RunScheduler):
        self.config = config
        self.scheduler = scheduler
        self.routes = {
            ("GET", "/healthz"): self._health,
            ("GET", "/readyz"): self._ready,
            ("GET", "/openapi.json"): self._openapi,
            ("POST", "/v1/runs/dispatch"): self._dispatch,
            ("POST", "/v1/runs/resume"): self._resume,
            ("POST", "/v1/runs/cancel"): self._cancel,
        }
        self.public_paths = {"/healthz", "/readyz", "/openapi.json"}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":  # pragma: no cover - websockets are not served
            return
        response = await self._handle(scope, receive)
        status, headers, body = response.encode()
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def _lifespan(self, receive, send):
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                telemetry.configure(self.config.log_level)
                ready, missing = self.config.readiness()
                telemetry.info(
                    "runtime.startup",
                    runtime_version=self.config.runtime_version,
                    contract_version=contracts.CONTRACT_VERSION,
                    ready=ready,
                    missing=missing,
                    enabled_agents=sorted(self.config.enabled_agents),
                )
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self.scheduler.drain()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _handle(self, scope, receive) -> Response:
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/").rstrip("/") or "/"
        headers = {
            key.decode().lower(): value.decode() for key, value in scope.get("headers") or []
        }
        handler = self.routes.get((method, path))
        if handler is None:
            allowed = {route_method for route_method, route_path in self.routes if route_path == path}
            if allowed:
                return Response(405, {"error": "method_not_allowed", "allow": sorted(allowed)})
            return Response(404, {"error": "not_found"})
        if path not in self.public_paths and not self._authorized(headers):
            telemetry.warning("request.unauthorized", path=path)
            return Response(401, {"error": "unauthorized"})
        correlation_id = headers.get(CORRELATION_HEADER)
        with telemetry.bind(correlation_id=correlation_id):
            try:
                if method == "GET":
                    return handler()
                body = await _read_body(receive)
                return handler(body, correlation_id)
            except contracts.ContractError as exc:
                return Response(400, {"error": "contract_violation", "detail": str(exc)})
            except ValueError as exc:
                return Response(400, {"error": "bad_request", "detail": redact_error(exc)})
            except Exception as exc:  # pragma: no cover - defensive
                telemetry.error("request.failed", path=path, error=redact_error(exc))
                return Response(500, {"error": "internal_error"})

    def _authorized(self, headers: dict[str, str]) -> bool:
        expected = self.config.service_token
        if not expected:
            # Fail closed: an unconfigured token never authorises a caller.
            return False
        return hmac.compare_digest(headers.get(SERVICE_TOKEN_HEADER, ""), expected)

    # -- handlers ----------------------------------------------------------

    def _health_payload(self) -> dict[str, Any]:
        from ..agents import enabled_agents

        ready, _missing = self.config.readiness()
        return contracts.validate(
            "runtime_health",
            {
                "status": "ok" if ready else "degraded",
                "runtime_version": self.config.runtime_version,
                "contract_version": contracts.CONTRACT_VERSION,
                "enabled_agents": enabled_agents(self.config.enabled_agents),
                "in_flight": self.scheduler.in_flight,
            },
        )

    def _health(self) -> Response:
        return Response(200, self._health_payload())

    def _ready(self) -> Response:
        ready, missing = self.config.readiness()
        payload = {**self._health_payload(), "missing": missing}
        return Response(200 if ready else 503, payload)

    def _openapi(self) -> Response:
        return Response(200, build_openapi(self.config.runtime_version))

    def _accept(self, run_id: str, status: str) -> Response:
        return Response(
            202, contracts.validate("accepted", {"accepted": True, "run_id": run_id, "status": status})
        )

    def _dispatch(self, body: dict[str, Any], correlation_id: str | None) -> Response:
        payload = contracts.validate("dispatch_run_request", body)
        ready, missing = self.config.readiness()
        if not ready:
            telemetry.error("dispatch.not_ready", missing=missing)
            return Response(503, {"error": "not_ready", "missing": missing})
        run_id = payload["run_id"]
        started = self.scheduler.submit(run_id, payload.get("correlation_id") or correlation_id)
        telemetry.info("dispatch.accepted", run_id=run_id, started=started)
        return self._accept(run_id, "Dispatched" if started else "Running")

    def _resume(self, body: dict[str, Any], correlation_id: str | None) -> Response:
        # Resume is dispatch with the same idempotency guarantees: the run is
        # re-claimed and continues from its persisted next sequence.
        return self._dispatch(body, correlation_id)

    def _cancel(self, body: dict[str, Any], correlation_id: str | None) -> Response:
        payload = contracts.validate("cancel_run_request", body)
        run_id = payload["run_id"]
        in_flight = self.scheduler.cancel(run_id)
        telemetry.info("cancel.requested", run_id=run_id, in_flight=in_flight)
        return self._accept(run_id, "Cancelled")


async def _read_body(receive) -> dict[str, Any]:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body") or b""
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise ValueError("request body is too large")
        chunks.append(chunk)
        if not message.get("more_body"):
            break
    raw = b"".join(chunks)
    if not raw:
        return {}
    try:
        body = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request body is not valid JSON") from exc
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    return body


def create_app(config=None, *, gateway=None, provider=None, executor=None) -> RuntimeApp:
    """Build the application. Collaborators are injectable for tests."""
    from ..config import RuntimeConfig
    from ..models.provider import HTTPModelProvider
    from ..orchestration import FrappeGatewayClient, RunExecutor

    config = config or RuntimeConfig.from_env()
    telemetry.configure(config.log_level)
    if executor is None:
        gateway = gateway or FrappeGatewayClient(config)
        provider = provider or HTTPModelProvider(
            config.model_base_url, config.model_api_key, timeout=config.request_timeout_seconds
        )
        executor = RunExecutor(config, gateway, provider)
    scheduler = RunScheduler(executor, config.max_concurrent_runs)
    if getattr(executor, "cancel_check", None) is None:
        executor.cancel_check = scheduler.is_cancelled
    return RuntimeApp(config, scheduler)
