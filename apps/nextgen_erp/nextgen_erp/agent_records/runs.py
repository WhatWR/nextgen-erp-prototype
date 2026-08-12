"""Agent Run lifecycle.

Frappe creates the run *before* dispatch, so the queued run is the durable
record of intent. If the runtime never answers, the run stays Queued or fails;
it cannot silently disappear and it cannot be resurrected by a late callback.

The run also carries the derived authority for everything that follows: the
requester, the execution identity, the company and the tool allowlist. The
runtime receives a bounded copy and can neither extend nor replace it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from nextgen_erp.agent_gateway import correlation, policy
from nextgen_erp.agent_gateway.contracts import CONTRACT_VERSION, TERMINAL_RUN_STATUSES
from nextgen_erp.agent_gateway.redaction import redact, redact_error

RUN_DOCTYPE = "NextGen Agent Run"
# A claimed run that never reports back is failed by the reaper rather than
# being left indefinitely in Running.
STALE_RUN_MINUTES = 30


def create_run(
	*,
	agent_type: str,
	company: str,
	requested_by: str,
	trigger_type: str = "chat",
	input_payload: dict[str, Any] | None = None,
	execution_user: str | None = None,
	chat_session: str | None = None,
	trigger_doctype: str | None = None,
	trigger_name: str | None = None,
	correlation_id: str | None = None,
	idempotency_key: str | None = None,
	shadow: bool | None = None,
	model: str | None = None,
	legacy_chat_action: str | None = None,
	workflow: str | None = None,
	workflow_node: str | None = None,
	parent_run: str | None = None,
	graph_version: int | None = None,
):
	"""Create a Queued run with its company-scoped authority resolved."""
	from nextgen_erp import agents
	from nextgen_erp.domain_tools import allowed_tool_names

	agent = agents.get_agent(agent_type)
	policy.assert_company_access(company, requested_by)
	snapshot = policy.snapshot(company)
	correlation_id = correlation_id or correlation.current()
	# One correlation ID can legitimately produce several runs — a chat turn
	# that prepares two documents, or a workflow's node runs. Callers that want
	# redelivery of the *same* intent to reuse a run pass an explicit key;
	# everything else gets a fresh one.
	idempotency_key = idempotency_key or f"run:{correlation_id}:{uuid.uuid4()}"
	existing = frappe.db.get_value(RUN_DOCTYPE, {"idempotency_key": idempotency_key}, "name")
	if existing:
		# Redelivery of the same intent reuses the run instead of forking it.
		return frappe.get_doc(RUN_DOCTYPE, existing)

	allowed = allowed_tool_names(agent.key, company)
	doc = frappe.get_doc(
		{
			"doctype": RUN_DOCTYPE,
			"correlation_id": correlation_id,
			"idempotency_key": idempotency_key,
			"company": company,
			"agent_type": agent.key,
			"agent_version": f"{agent.key}-v1",
			"trigger_type": trigger_type,
			"trigger_doctype": trigger_doctype,
			"trigger_name": trigger_name,
			"requested_by": requested_by,
			"execution_user": execution_user or _execution_user(snapshot, requested_by),
			"status": "Queued",
			"shadow": cint(bool(snapshot.get("default_mode") == policy.SHADOW if shadow is None else shadow)),
			"model": model or snapshot.get("model") or None,
			"prompt_version": f"{agent.key}-prompt-v1",
			"max_tool_calls": policy.max_tool_calls(company),
			"allowed_tools": json.dumps(allowed, ensure_ascii=False),
			"input_payload": json.dumps(redact(input_payload or {}), ensure_ascii=False, default=str),
			"chat_session": chat_session,
			"legacy_chat_action": legacy_chat_action,
			"workflow": workflow,
			"workflow_node": workflow_node,
			"parent_run": parent_run,
			"graph_version": cint(graph_version),
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _execution_user(snapshot: dict[str, Any], requested_by: str) -> str:
	"""Prefer the company's restricted service identity for transport."""
	return snapshot.get("service_user") or _settings_service_user() or requested_by


def _settings_service_user() -> str | None:
	value = frappe.db.get_single_value("NextGen AI Settings", "agent_runtime_service_user")
	return (value or "").strip() or None


def get_run(run_id: str):
	if not frappe.db.exists(RUN_DOCTYPE, run_id):
		frappe.throw(_("Unknown agent run: {0}").format(run_id), frappe.DoesNotExistError)
	return frappe.get_doc(RUN_DOCTYPE, run_id)


def claim(run_id: str, runtime_version: str) -> dict[str, Any]:
	"""Claim a run for execution. A duplicate claim returns the current state.

	The returned context is bounded on purpose: company, requester, agent,
	prompt and tool metadata only. No credentials, no ERP records, no way to
	widen what the run is allowed to do.
	"""
	from nextgen_erp.agent_records import steps
	from nextgen_erp.domain_tools import schemas_for

	frappe.db.sql(f"select name from `tab{RUN_DOCTYPE}` where name=%s for update", run_id)
	run = get_run(run_id)
	if run.status in ("Queued", "Dispatched"):
		run.status = "Running"
		run.runtime_version = runtime_version
		run.claimed_at = now_datetime()
		run.started_at = run.started_at or now_datetime()
		run.save(ignore_permissions=True)
	allowed = run.allowed_tool_names()
	return {
		"contract_version": CONTRACT_VERSION,
		"run_id": run.name,
		"correlation_id": run.correlation_id,
		"company": run.company,
		"agent_type": run.agent_type,
		"agent_version": run.agent_version or f"{run.agent_type}-v1",
		"prompt_version": run.prompt_version or f"{run.agent_type}-prompt-v1",
		"trigger_type": run.trigger_type,
		"requested_by": run.requested_by,
		"execution_user": run.execution_user,
		"status": run.status,
		"shadow": bool(cint(run.shadow)),
		"model": run.model or None,
		"max_tool_calls": max(1, min(cint(run.max_tool_calls) or 6, 12)),
		"allowed_tools": allowed,
		"tool_schemas": schemas_for(run.agent_type, allowed),
		"input": run.input_json(),
		"next_sequence": steps.next_sequence(run.name),
		"expires_at": None,
		# v2. A parent workflow run carries its graph so the runtime can walk
		# it; a node run carries only its own identity.
		"workflow": run.workflow or None,
		"workflow_node": run.workflow_node or None,
		"parent_run": run.parent_run or None,
		"graph": _graph_for(run),
	}


def _graph_for(run) -> dict[str, Any] | None:
	"""The validated graph, but only for the run that orchestrates it."""
	if not run.workflow or run.workflow_node:
		return None
	from nextgen_erp.agent_gateway import graph as graph_utils

	workflow = frappe.get_doc("NextGen Agent Workflow", run.workflow)
	if not cint(workflow.enabled):
		frappe.throw(_("Workflow {0} is disabled").format(workflow.name), frappe.ValidationError)
	if workflow.company != run.company:
		frappe.throw(_("Workflow and run belong to different companies"), frappe.PermissionError)
	return graph_utils.parse(workflow.graph_json)


def complete(
	run_id: str, status: str, usage: dict[str, Any] | None = None, error: str | None = None
) -> None:
	"""Record a terminal (or Waiting Approval) outcome for a run."""
	frappe.db.sql(f"select name from `tab{RUN_DOCTYPE}` where name=%s for update", run_id)
	run = get_run(run_id)
	if run.status in TERMINAL_RUN_STATUSES:
		# A late or duplicated callback never reopens a finished run.
		return
	run.status = status
	run.ended_at = now_datetime()
	if usage is not None:
		run.usage_json = json.dumps(redact(usage), ensure_ascii=False, default=str)
	if error:
		run.error_summary = redact_error(error)
	run.save(ignore_permissions=True)


def mark_dispatched(run, *, attempts: int | None = None) -> None:
	run.db_set(
		{
			"status": "Dispatched" if run.status == "Queued" else run.status,
			"dispatch_attempts": cint(run.dispatch_attempts) + 1 if attempts is None else attempts,
			"last_dispatched_at": now_datetime(),
		},
		update_modified=False,
	)


def fail(run_id: str, error: str) -> None:
	complete(run_id, "Failed", error=error)


def reap_stale_runs(minutes: int = STALE_RUN_MINUTES) -> list[str]:
	"""Fail runs claimed long ago that never reported back.

	Runtime unavailability must leave an observable, recoverable state rather
	than a run stuck in Running forever.
	"""
	cutoff = add_to_date(now_datetime(), minutes=-max(1, minutes))
	stale = frappe.get_all(
		RUN_DOCTYPE,
		filters={"status": ["in", ["Dispatched", "Running"]], "modified": ["<", cutoff]},
		pluck="name",
	)
	for name in stale:
		fail(name, f"no runtime callback within {minutes} minutes")
	return stale
