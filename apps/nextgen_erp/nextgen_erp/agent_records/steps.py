"""Ordered, idempotent Agent Step persistence.

Every model request, tool call, policy evaluation, proposal, revalidation,
approval and result appends a step. Steps are append-only and keyed by a
globally unique idempotency key, so a retried callback, a duplicate delivery or
a runtime restart replays instead of duplicating the trace.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import cint, now_datetime

from nextgen_erp.agent_gateway.contracts import payload_hash
from nextgen_erp.agent_gateway.redaction import redact

STEP_DOCTYPE = "NextGen Agent Step"


def next_sequence(run_id: str) -> int:
	highest = frappe.db.get_value(
		STEP_DOCTYPE, {"run": run_id}, "sequence", order_by="sequence desc"
	)
	return cint(highest) + 1


def find_by_key(idempotency_key: str):
	name = frappe.db.get_value(STEP_DOCTYPE, {"idempotency_key": idempotency_key}, "name")
	return frappe.get_doc(STEP_DOCTYPE, name) if name else None


def _free_sequence(run_id: str, requested: int) -> int:
	"""Keep ordering monotonic when a sequence is already taken by another step."""
	requested = max(1, cint(requested))
	if not frappe.db.exists(STEP_DOCTYPE, {"run": run_id, "sequence": requested}):
		return requested
	return max(requested, next_sequence(run_id))


def append(
	run_id: str,
	*,
	sequence: int,
	step_type: str,
	operation: str,
	status: str,
	idempotency_key: str,
	sanitized_input: Any = None,
	result: Any = None,
	latency_ms: int | None = None,
	error: str | None = None,
	result_doctype: str | None = None,
	result_name: str | None = None,
	correlation_id: str | None = None,
) -> dict[str, Any]:
	"""Append one step, or return the existing one for a replayed key."""
	existing = find_by_key(idempotency_key)
	if existing:
		return {
			"step_id": existing.name,
			"sequence": cint(existing.sequence),
			"replayed": True,
		}

	now = now_datetime()
	doc = frappe.get_doc(
		{
			"doctype": STEP_DOCTYPE,
			"run": run_id,
			"sequence": _free_sequence(run_id, sequence),
			"step_type": step_type,
			"operation": operation,
			"status": status,
			"idempotency_key": idempotency_key,
			"correlation_id": correlation_id,
			"started_at": now,
			"ended_at": now,
			"latency_ms": cint(latency_ms),
			"sanitized_input": _dump(redact(sanitized_input)) if sanitized_input is not None else None,
			"sanitized_output": _dump(redact(result)) if result is not None else None,
			# Hash the complete payload so redaction and truncation stay verifiable.
			"input_hash": payload_hash(sanitized_input) if sanitized_input is not None else None,
			"output_hash": payload_hash(result) if result is not None else None,
			"result_doctype": result_doctype,
			"result_name": result_name,
			"error": error,
		}
	)
	doc.insert(ignore_permissions=True)
	return {"step_id": doc.name, "sequence": cint(doc.sequence), "replayed": False}


def stored_result(step) -> Any:
	"""The sanitized output of a replayed step, used to answer a retried call."""
	try:
		return json.loads(step.sanitized_output or "null")
	except (TypeError, ValueError):
		return None


def _dump(value: Any) -> str:
	return json.dumps(value, ensure_ascii=False, default=str)
