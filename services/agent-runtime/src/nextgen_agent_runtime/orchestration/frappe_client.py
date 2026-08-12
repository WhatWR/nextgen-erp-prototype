"""The only channel the runtime has into ERP.

Every call is a whitelisted Frappe gateway method authenticated as the
dedicated restricted runtime service user. There is no MariaDB connection, no
shared Redis, no site-file access, no generic DocType CRUD and no raw SQL: if a
capability is not exposed as a gateway method, the runtime cannot reach it.

All mutating calls carry an idempotency key so a retry after an unknown-delivery
timeout can never duplicate a step, tool effect or proposal.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from ..models import contracts
from ..redaction import redact

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]

METHOD_PREFIX = "nextgen_erp.agent_gateway.api"
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class GatewayError(RuntimeError):
    """Transport or server-side failure. Safe to retry with the same key."""


class GatewayRejected(GatewayError):
    """Frappe refused the call: permission, policy, contract or state error.

    Never retried. Frappe is the authority; a rejection is a decision, not an
    outage, and the run fails closed.
    """

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _default_transport(url: str, headers: dict[str, str], body: bytes, timeout: float):
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise GatewayError(f"Frappe gateway is unreachable: {exc.reason}") from exc


class FrappeGatewayClient:
    def __init__(
        self,
        config,
        *,
        transport: Transport | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], None] | None = None,
    ):
        self.config = config
        self.transport = transport or _default_transport
        self.max_attempts = max(1, max_attempts)
        if sleep is None:
            import time

            sleep = time.sleep
        self.sleep = sleep

    # -- transport ---------------------------------------------------------

    def _headers(self, correlation_id: str | None) -> dict[str, str]:
        if not self.config.gateway_configured:
            raise GatewayError("Frappe gateway credentials are not configured")
        headers = {
            "Authorization": f"token {self.config.frappe_api_key}:{self.config.frappe_api_secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-NextGen-Runtime-Version": self.config.runtime_version,
        }
        if correlation_id:
            headers["X-NextGen-Correlation-Id"] = correlation_id
        return headers

    def _call(self, method: str, payload: dict[str, Any], *, correlation_id: str | None) -> Any:
        url = f"{self.config.frappe_base_url}/api/method/{METHOD_PREFIX}.{method}"
        body = json.dumps(payload, ensure_ascii=False).encode()
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            status, raw = self.transport(
                url, self._headers(correlation_id), body, self.config.request_timeout_seconds
            )
            text = raw.decode(errors="replace")
            if status < 400:
                return self._unwrap(text, method)
            last_error = f"HTTP {status}: {_error_message(text)}"
            if status not in RETRYABLE_STATUS:
                raise GatewayRejected(f"{method} rejected: {last_error}", status)
            if attempt == self.max_attempts:
                break
            self.sleep(min(1.0 * attempt, 4.0))
        raise GatewayError(f"{method} failed: {last_error}")

    @staticmethod
    def _unwrap(text: str, method: str) -> Any:
        try:
            data = json.loads(text or "{}")
        except json.JSONDecodeError as exc:
            raise GatewayError(f"{method} returned invalid JSON") from exc
        if isinstance(data, dict) and "message" in data:
            return data["message"]
        return data

    # -- gateway methods ---------------------------------------------------

    def claim_run(self, run_id: str) -> dict[str, Any]:
        """Claim a queued run. A duplicate claim returns the current state."""
        payload = contracts.validate(
            "claim_run_request",
            {
                "contract_version": contracts.CONTRACT_VERSION,
                "run_id": run_id,
                "runtime_version": self.config.runtime_version,
            },
        )
        context = self._call("claim_run", payload, correlation_id=None)
        return contracts.validate("run_context", context)

    def record_step(
        self,
        *,
        run_id: str,
        sequence: int,
        step_type: str,
        operation: str,
        status: str,
        idempotency_key: str,
        sanitized_input: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        payload = contracts.validate(
            "record_step_request",
            {
                "contract_version": contracts.CONTRACT_VERSION,
                "run_id": run_id,
                "sequence": sequence,
                "step_type": step_type,
                "operation": operation,
                "status": status,
                "sanitized_input": redact(sanitized_input) if sanitized_input is not None else None,
                "result": redact(result) if result is not None else None,
                "latency_ms": latency_ms,
                "idempotency_key": idempotency_key,
                "error": error,
            },
        )
        reference = self._call("record_step", payload, correlation_id=correlation_id)
        return contracts.validate("step_reference", reference)

    def execute_tool(
        self,
        *,
        run_id: str,
        sequence: int,
        tool_name: str,
        arguments: dict[str, Any],
        idempotency_key: str,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Run one allowlisted domain tool inside Frappe.

        Frappe re-derives the requester, execution identity, company and tool
        allowlist from the persisted run, rechecks live permissions and policy,
        and appends the authoritative Tool step itself. The runtime never
        records a duplicate step for a tool it successfully dispatched.
        """
        payload = contracts.validate(
            "execute_tool_request",
            {
                "contract_version": contracts.CONTRACT_VERSION,
                "run_id": run_id,
                "sequence": sequence,
                "tool_name": tool_name,
                "arguments": redact(arguments),
                "idempotency_key": idempotency_key,
            },
        )
        result = self._call("execute_tool", payload, correlation_id=correlation_id)
        return contracts.validate("tool_result", result)

    def create_proposal(
        self,
        *,
        run_id: str,
        action_type: str,
        company: str,
        preview: dict[str, Any],
        idempotency_key: str,
        policy_snapshot: dict[str, Any] | None = None,
        risk_level: str | None = None,
        expires_at: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Request a proposal. The runtime can never approve or execute one."""
        payload: dict[str, Any] = {
            "contract_version": contracts.CONTRACT_VERSION,
            "run_id": run_id,
            "action_type": action_type,
            "company": company,
            "preview": redact(preview),
            "idempotency_key": idempotency_key,
        }
        if policy_snapshot is not None:
            payload["policy_snapshot"] = redact(policy_snapshot)
        if risk_level:
            payload["risk_level"] = risk_level
        if expires_at:
            payload["expires_at"] = expires_at
        contracts.validate("create_proposal_request", payload)
        reference = self._call("create_proposal", payload, correlation_id=correlation_id)
        return contracts.validate("proposal_reference", reference)

    def create_node_run(
        self,
        *,
        parent_run_id: str,
        node_id: str,
        agent_type: str,
        input_payload: dict[str, Any],
        idempotency_key: str,
        node_prompt: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Ask Frappe to create one child run for a workflow node.

        Frappe derives company, requester, execution identity and the tool
        allowlist from the parent run, and refuses a node whose agent does not
        match the stored graph.
        """
        payload = contracts.validate(
            "create_node_run_request",
            {
                "contract_version": contracts.CONTRACT_VERSION,
                "parent_run_id": parent_run_id,
                "node_id": node_id,
                "agent_type": agent_type,
                "node_prompt": node_prompt or None,
                "input": redact(input_payload),
                "idempotency_key": idempotency_key,
            },
        )
        context = self._call("create_node_run", payload, correlation_id=correlation_id)
        return contracts.validate("run_context", context)

    def complete_run(
        self,
        *,
        run_id: str,
        status: str,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        payload = contracts.validate(
            "complete_run_request",
            {
                "contract_version": contracts.CONTRACT_VERSION,
                "run_id": run_id,
                "status": status,
                "usage": usage,
                "error": error,
            },
        )
        self._call("complete_run", payload, correlation_id=correlation_id)


def _error_message(text: str) -> str:
    """Frappe returns HTML for some failures; keep the log line short and safe."""
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return text[:300]
    if isinstance(data, dict):
        for key in ("exception", "message", "_server_messages", "exc_type"):
            value = data.get(key)
            if value:
                return str(value)[:300]
    return text[:300]
