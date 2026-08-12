"""Durable dispatch from Frappe to the Agent Runtime.

Dispatch is asynchronous and idempotent. Frappe creates the run first, so a
timeout means unknown delivery rather than failure: the same run ID and
idempotency key are simply sent again.

There is no automatic fallback to embedded orchestration. If the runtime is
unavailable the run stays Queued and is retried by the scheduler, or it fails
visibly. An explicit, transition-only rollback flag exists for operators and is
never flipped by a failure.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import frappe
from frappe.utils import cint

from nextgen_erp.agent_gateway import contracts
from nextgen_erp.agent_gateway.redaction import redact_error
from nextgen_erp.agent_records import runs

SETTINGS_DOCTYPE = "NextGen AI Settings"
MAX_DISPATCH_ATTEMPTS = 5
DEFAULT_TIMEOUT = 10.0

Transport = Callable[[str, str, dict[str, str], bytes, float], tuple[int, bytes]]


class DispatchError(RuntimeError):
	"""The runtime could not be reached or refused the delivery."""


def settings() -> dict[str, Any]:
	doc = frappe.get_single(SETTINGS_DOCTYPE)
	return {
		"enabled": bool(cint(doc.get("enable_agent_runtime"))),
		"legacy_rollback": bool(cint(doc.get("agent_runtime_legacy_rollback"))),
		"url": (doc.get("agent_runtime_url") or "").strip().rstrip("/"),
		"token": doc.get_password("agent_runtime_token", raise_exception=False) or "",
		"service_user": (doc.get("agent_runtime_service_user") or "").strip(),
		"timeout": DEFAULT_TIMEOUT,
	}


def is_enabled(company: str | None = None) -> bool:
	"""Deployment switch, company policy and rollback flag must all agree."""
	from nextgen_erp.agent_gateway import policy

	config = settings()
	if not config["enabled"] or config["legacy_rollback"] or not config["url"]:
		return False
	return policy.runtime_enabled(company) if company else True


def _default_transport(method: str, url: str, headers: dict[str, str], body: bytes, timeout: float):
	request = urllib.request.Request(url, data=body or None, method=method, headers=headers)
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			return response.status, response.read()
	except urllib.error.HTTPError as exc:
		return exc.code, exc.read()
	except urllib.error.URLError as exc:
		raise DispatchError(f"Agent Runtime is unreachable: {exc.reason}") from exc


_transport: Transport = _default_transport


def set_transport(transport: Transport | None) -> None:
	"""Test seam. Production always uses the urllib transport."""
	global _transport
	_transport = transport or _default_transport


def _call(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
	config = settings()
	if not config["url"] or not config["token"]:
		raise DispatchError("Agent Runtime URL and service token are not configured")
	headers = {
		"Content-Type": "application/json",
		"Accept": "application/json",
		"X-NextGen-Service-Token": config["token"],
	}
	if payload and payload.get("correlation_id"):
		headers["X-NextGen-Correlation-Id"] = payload["correlation_id"]
	body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b""
	status, raw = _transport(method, f"{config['url']}{path}", headers, body, config["timeout"])
	text = raw.decode(errors="replace")
	if status >= 400:
		raise DispatchError(f"Agent Runtime returned HTTP {status}: {text[:300]}")
	try:
		return json.loads(text or "{}")
	except json.JSONDecodeError as exc:
		raise DispatchError("Agent Runtime returned invalid JSON") from exc


def dispatch(run) -> dict[str, Any]:
	"""Send one run to the runtime. Safe to call repeatedly for the same run."""
	payload = contracts.validate(
		"dispatch_run_request",
		{
			"contract_version": contracts.CONTRACT_VERSION,
			"run_id": run.name,
			"correlation_id": run.correlation_id,
			"idempotency_key": run.idempotency_key,
		},
	)
	response = _call("POST", "/v1/runs/dispatch", payload)
	runs.mark_dispatched(run)
	return response


def enqueue_dispatch(run) -> None:
	"""Queue delivery after commit so the run is durable before it is sent."""
	frappe.enqueue(
		"nextgen_erp.agent_gateway.dispatch.deliver",
		queue="short",
		enqueue_after_commit=True,
		job_name=f"agent-dispatch:{run.name}",
		run_id=run.name,
	)


def deliver(run_id: str) -> None:
	"""Background delivery. A failure leaves the run Queued for the retrier."""
	run = runs.get_run(run_id)
	if run.status not in ("Queued", "Dispatched"):
		return
	try:
		dispatch(run)
	except DispatchError as exc:
		# Fail closed: no embedded orchestration takes over, and the run stays
		# observable and recoverable.
		run.db_set("dispatch_attempts", cint(run.dispatch_attempts) + 1, update_modified=False)
		frappe.log_error(
			title=f"NextGen agent dispatch {run_id}",
			message=redact_error(exc),
		)


def retry_queued_runs(limit: int = 50) -> list[str]:
	"""Scheduler task: redeliver runs the runtime never acknowledged."""
	if not is_enabled():
		return []
	pending = frappe.get_all(
		"NextGen Agent Run",
		filters={
			"status": ["in", ["Queued", "Dispatched"]],
			"dispatch_attempts": ["<", MAX_DISPATCH_ATTEMPTS],
		},
		pluck="name",
		order_by="creation asc",
		limit=limit,
	)
	for name in pending:
		deliver(name)
	exhausted = frappe.get_all(
		"NextGen Agent Run",
		filters={
			"status": ["in", ["Queued", "Dispatched"]],
			"dispatch_attempts": [">=", MAX_DISPATCH_ATTEMPTS],
		},
		pluck="name",
		limit=limit,
	)
	for name in exhausted:
		runs.fail(name, f"runtime did not accept the run after {MAX_DISPATCH_ATTEMPTS} attempts")
	return pending


def cancel(run, reason: str | None = None) -> dict[str, Any]:
	payload = contracts.validate(
		"cancel_run_request",
		{
			"contract_version": contracts.CONTRACT_VERSION,
			"run_id": run.name,
			"correlation_id": run.correlation_id,
			"reason": reason,
		},
	)
	return _call("POST", "/v1/runs/cancel", payload)


def runtime_health() -> dict[str, Any]:
	try:
		health = _call("GET", "/healthz")
	except DispatchError as exc:
		return {"status": "unreachable", "error": redact_error(exc)}
	health["contract_matches"] = health.get("contract_version") == contracts.CONTRACT_VERSION
	return health


def maintenance() -> None:
	"""Daily upkeep: redeliver, expire proposals and reap abandoned runs."""
	from nextgen_erp.action_proposals import service as proposals

	retry_queued_runs()
	proposals.expire_due()
	runs.reap_stale_runs()
