"""The versioned gateway API.

Two audiences, two identities, and no overlap between them:

* the **runtime service identity** may claim a run, append steps, run an
  allowlisted domain tool, request a proposal and complete a run;
* an **authenticated human** may start a run, read a trace, revalidate a
  proposal, approve, reject and execute.

The runtime can create a proposal but cannot approve or execute one, and there
is no gateway method that would let it try. Everything the runtime sends about
identity, company or permissions is ignored: those are read from the persisted
run.
"""

from __future__ import annotations

import json
import time
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint

from nextgen_erp.action_proposals import service as proposals
from nextgen_erp.agent_gateway import (
	contracts,
	correlation,
	dispatch,
	permissions,
	policy,
	workflows,
)
from nextgen_erp.agent_gateway.redaction import redact, redact_error
from nextgen_erp.agent_records import runs, steps

TERMINAL = contracts.TERMINAL_RUN_STATUSES


def _validate(schema: str, payload: dict[str, Any]) -> dict[str, Any]:
	try:
		return contracts.validate(schema, payload)
	except contracts.ContractError as exc:
		frappe.throw(_("Contract violation: {0}").format(str(exc)), frappe.ValidationError)


def _parse(value: Any, fallback: Any) -> Any:
	if isinstance(value, (dict, list)):
		return value
	try:
		return json.loads(value or "")
	except (TypeError, ValueError):
		return fallback


# ---------------------------------------------------------------------------
# runtime -> Frappe
# ---------------------------------------------------------------------------


@frappe.whitelist()
def claim_run(contract_version: str = "", run_id: str = "", runtime_version: str = ""):
	"""Claim a queued run and return its bounded execution context."""
	service = permissions.require_service_identity()
	payload = _validate(
		"claim_run_request",
		{
			"contract_version": contract_version,
			"run_id": run_id,
			"runtime_version": runtime_version,
		},
	)
	run = runs.get_run(payload["run_id"])
	permissions.assert_run_execution_identity(run, service)
	correlation.bind(run.correlation_id)
	context = runs.claim(run.name, payload["runtime_version"])
	return _validate("run_context", context)


@frappe.whitelist()
def record_step(
	contract_version: str = "",
	run_id: str = "",
	sequence: int = 0,
	step_type: str = "",
	operation: str = "",
	status: str = "",
	idempotency_key: str = "",
	sanitized_input=None,
	result=None,
	latency_ms=None,
	error=None,
):
	"""Append one ordered, sanitized step to a run's authoritative trace."""
	service = permissions.require_service_identity()
	payload = _validate(
		"record_step_request",
		{
			"contract_version": contract_version,
			"run_id": run_id,
			"sequence": cint(sequence),
			"step_type": step_type,
			"operation": operation,
			"status": status,
			"idempotency_key": idempotency_key,
			"sanitized_input": _parse(sanitized_input, None),
			"result": _parse(result, None),
			"latency_ms": cint(latency_ms) if latency_ms is not None else None,
			"error": error,
		},
	)
	run = runs.get_run(payload["run_id"])
	permissions.assert_run_execution_identity(run, service)
	correlation.bind(run.correlation_id)
	reference = steps.append(
		run.name,
		sequence=payload["sequence"],
		step_type=payload["step_type"],
		operation=payload["operation"],
		status=payload["status"],
		idempotency_key=payload["idempotency_key"],
		sanitized_input=payload.get("sanitized_input"),
		result=payload.get("result"),
		latency_ms=payload.get("latency_ms"),
		error=payload.get("error"),
		correlation_id=run.correlation_id,
	)
	return _validate("step_reference", reference)


@frappe.whitelist()
def complete_run(contract_version: str = "", run_id: str = "", status: str = "", usage=None, error=None):
	"""Record the run's outcome. A late callback never reopens a finished run."""
	service = permissions.require_service_identity()
	payload = _validate(
		"complete_run_request",
		{
			"contract_version": contract_version,
			"run_id": run_id,
			"status": status,
			"usage": _parse(usage, None),
			"error": error,
		},
	)
	run = runs.get_run(payload["run_id"])
	permissions.assert_run_execution_identity(run, service)
	correlation.bind(run.correlation_id)
	runs.complete(run.name, payload["status"], payload.get("usage"), payload.get("error"))
	return {"run_id": run.name, "status": payload["status"]}


@frappe.whitelist()
def execute_tool(
	contract_version: str = "",
	run_id: str = "",
	sequence: int = 0,
	tool_name: str = "",
	arguments=None,
	idempotency_key: str = "",
):
	"""Authorise and run one allowlisted domain tool, and record it.

	The requester, execution identity, company and tool allowlist come from the
	persisted run. Live permissions, company, warehouse, policy and resource
	versions are rechecked here on every call.
	"""
	from nextgen_erp.domain_tools import ToolRejected, execute as run_tool

	service = permissions.require_service_identity()
	payload = _validate(
		"execute_tool_request",
		{
			"contract_version": contract_version,
			"run_id": run_id,
			"sequence": cint(sequence),
			"tool_name": tool_name,
			"arguments": _parse(arguments, {}) or {},
			"idempotency_key": idempotency_key,
		},
	)
	run = runs.get_run(payload["run_id"])
	permissions.assert_run_execution_identity(run, service)
	correlation.bind(run.correlation_id)
	if run.status in TERMINAL:
		frappe.throw(_("This run has already finished"), frappe.ValidationError)

	replay = steps.find_by_key(payload["idempotency_key"])
	if replay:
		# Same key, same effect: return what was recorded instead of re-running.
		stored = steps.stored_result(replay) or {}
		return _validate(
			"tool_result",
			{
				"status": "ok" if replay.status == "Success" else "rejected",
				"tool_name": payload["tool_name"],
				"data": stored if isinstance(stored, dict) else {"result": stored},
				"warnings": [],
				"error": replay.error,
				"replayed": True,
			},
		)

	started = time.monotonic()
	try:
		data = run_tool(run, payload["tool_name"], payload["arguments"])
		status, error = "ok", None
	except ToolRejected as exc:
		data, status, error = {}, "rejected", redact_error(exc)
	except frappe.PermissionError as exc:
		data, status, error = {}, "rejected", redact_error(exc)
	except Exception as exc:
		frappe.db.rollback()
		frappe.log_error(title=f"NextGen agent tool {payload['tool_name']}", message=frappe.get_traceback())
		data, status, error = {}, "error", redact_error(exc)

	latency_ms = int((time.monotonic() - started) * 1000)
	steps.append(
		run.name,
		sequence=payload["sequence"],
		step_type="Tool",
		operation=payload["tool_name"],
		status={"ok": "Success", "rejected": "Rejected", "error": "Error"}[status],
		idempotency_key=payload["idempotency_key"],
		sanitized_input=payload["arguments"],
		result=data if status == "ok" else None,
		latency_ms=latency_ms,
		error=error,
		result_doctype="NextGen Action Proposal" if data.get("proposal_id") else None,
		result_name=data.get("proposal_id"),
		correlation_id=run.correlation_id,
	)
	warnings = data.get("warnings") if isinstance(data.get("warnings"), list) else []
	return _validate(
		"tool_result",
		{
			"status": status,
			"tool_name": payload["tool_name"],
			"data": redact(data),
			"warnings": [str(warning) for warning in warnings],
			"error": error,
			"replayed": False,
		},
	)


@frappe.whitelist()
def create_proposal(
	contract_version: str = "",
	run_id: str = "",
	action_type: str = "",
	company: str = "",
	preview=None,
	policy_snapshot=None,
	risk_level=None,
	expires_at=None,
	idempotency_key: str = "",
):
	"""Create an Action Proposal on behalf of a run. Approval is not implied."""
	service = permissions.require_service_identity()
	payload: dict[str, Any] = {
		"contract_version": contract_version,
		"run_id": run_id,
		"action_type": action_type,
		"company": company,
		"preview": _parse(preview, {}) or {},
		"idempotency_key": idempotency_key,
	}
	if policy_snapshot is not None:
		payload["policy_snapshot"] = _parse(policy_snapshot, {})
	if risk_level:
		payload["risk_level"] = risk_level
	if expires_at:
		payload["expires_at"] = expires_at
	_validate("create_proposal_request", payload)

	run = runs.get_run(payload["run_id"])
	permissions.assert_run_execution_identity(run, service)
	correlation.bind(run.correlation_id)
	# The company comes from the run; a payload that disagrees is rejected.
	if payload["company"] != run.company:
		frappe.throw(_("A proposal cannot leave the company of its run"), frappe.PermissionError)
	proposal, replayed = proposals.create_proposal(
		run,
		action_type=payload["action_type"],
		company=run.company,
		preview=payload["preview"],
		idempotency_key=payload["idempotency_key"],
		policy_snapshot=payload.get("policy_snapshot"),
		expires_at=payload.get("expires_at"),
		risk_level=payload.get("risk_level"),
	)
	return _validate(
		"proposal_reference",
		{
			"proposal_id": proposal.name,
			"status": proposal.status,
			"snapshot_hash": proposal.snapshot_hash,
			"expires_at": str(proposal.expires_at),
			"replayed": replayed,
		},
	)


# ---------------------------------------------------------------------------
# human -> Frappe
# ---------------------------------------------------------------------------


@frappe.whitelist()
def start_agent_run(
	agent_type: str,
	message: str | None = None,
	company: str | None = None,
	chat_session: str | None = None,
	page_context=None,
	trigger_type: str = "manual",
):
	"""Create a queued run for the authenticated user and dispatch it."""
	user = permissions.require_human()
	permissions.assert_agent_access(agent_type, user)
	company = policy.assert_company_access(company or policy.default_company(user), user)
	if not policy.runtime_enabled(company):
		frappe.throw(
			_("The Agent Runtime is not enabled for company {0}").format(company),
			frappe.ValidationError,
		)
	text = str(message or "").strip()
	if not text or len(text) > 4000:
		frappe.throw(_("Message must contain between 1 and 4,000 characters"))
	run = runs.create_run(
		agent_type=agent_type,
		company=company,
		requested_by=user,
		trigger_type=trigger_type,
		chat_session=chat_session,
		# Channel entry: one correlation ID is one intent, so a redelivered
		# request reuses the run instead of starting a second one.
		idempotency_key=f"run:{correlation.current()}",
		input_payload={
			"messages": [{"role": "user", "content": text}],
			"context": _parse(page_context, {}) or {},
		},
	)
	dispatch.enqueue_dispatch(run)
	return {"run_id": run.name, "correlation_id": run.correlation_id, "status": run.status}


@frappe.whitelist()
def get_run(run_id: str):
	"""Read one run and its ordered step trace."""
	user = permissions.current_user()
	run = runs.get_run(run_id)
	policy.assert_company_access(run.company, user)
	if run.requested_by != user and "System Manager" not in frappe.get_roles(user):
		frappe.throw(_("Agent run not found"), frappe.DoesNotExistError)
	trace = frappe.get_all(
		"NextGen Agent Step",
		filters={"run": run.name},
		fields=[
			"name",
			"sequence",
			"step_type",
			"operation",
			"status",
			"latency_ms",
			"error",
			"result_doctype",
			"result_name",
			"creation",
		],
		order_by="sequence asc",
		limit=500,
	)
	return {
		"run": {
			"name": run.name,
			"correlation_id": run.correlation_id,
			"company": run.company,
			"agent_type": run.agent_type,
			"status": run.status,
			"trigger_type": run.trigger_type,
			"requested_by": run.requested_by,
			"runtime_version": run.runtime_version,
			"model": run.model,
			"prompt_version": run.prompt_version,
			"error_summary": run.error_summary,
		},
		"steps": trace,
		"proposals": frappe.get_all(
			"NextGen Action Proposal",
			filters={"run": run.name},
			fields=["name", "action_type", "status", "expires_at", "snapshot_hash", "risk_level"],
			order_by="creation asc",
		),
	}


@frappe.whitelist()
def get_proposal(proposal_id: str):
	user = permissions.current_user()
	proposal = frappe.get_doc("NextGen Action Proposal", proposal_id)
	policy.assert_company_access(proposal.company, user)
	if proposal.requested_by != user and "System Manager" not in frappe.get_roles(user):
		frappe.throw(_("Action proposal not found"), frappe.DoesNotExistError)
	return {
		"proposal_id": proposal.name,
		"run": proposal.run,
		"company": proposal.company,
		"action_type": proposal.action_type,
		"status": proposal.status,
		"risk_level": proposal.risk_level,
		"expires_at": str(proposal.expires_at),
		"snapshot_hash": proposal.snapshot_hash,
		"preview": _parse(proposal.current_preview, {}),
		"warnings": _parse(proposal.warnings, []),
		"policy_snapshot": _parse(proposal.policy_snapshot, {}),
		"legacy_chat_action": proposal.legacy_chat_action,
	}


@frappe.whitelist()
def revalidate_proposal(proposal_id: str):
	"""Re-derive the preview from live ERP data without deciding anything."""
	user = permissions.require_human()
	proposal = frappe.get_doc("NextGen Action Proposal", proposal_id)
	policy.assert_company_access(proposal.company, user)
	live, issues, drifted = proposals.revalidate(proposal)
	return {
		"proposal_id": proposal.name,
		"preview": live,
		"issues": issues,
		"drifted": drifted,
		"snapshot_hash": proposal.snapshot_hash,
	}


@frappe.whitelist()
def approve_proposal(proposal_id: str, snapshot_hash: str | None = None):
	return proposals.approve(proposal_id, snapshot_hash)


@frappe.whitelist()
def reject_proposal(proposal_id: str, reason: str | None = None):
	return proposals.reject(proposal_id, reason)


@frappe.whitelist()
def execute_proposal(proposal_id: str, idempotency_key: str | None = None):
	return proposals.execute(proposal_id, idempotency_key)


# ---------------------------------------------------------------------------
# workflows
# ---------------------------------------------------------------------------


@frappe.whitelist()
def list_workflows(company: str | None = None):
	return workflows.list_workflows(company)


@frappe.whitelist()
def get_workflow(name: str):
	return workflows.get_workflow(name)


@frappe.whitelist()
def save_workflow(
	name: str | None = None,
	title: str | None = None,
	company: str | None = None,
	description: str | None = None,
	graph=None,
	enabled: int = 0,
):
	return workflows.save_workflow(
		name=name,
		title=title,
		company=company,
		description=description,
		graph=_parse(graph, None),
		enabled=cint(enabled),
	)


@frappe.whitelist()
def delete_workflow(name: str):
	return workflows.delete_workflow(name)


@frappe.whitelist()
def start_workflow_run(workflow: str, message: str | None = None):
	return workflows.start_workflow_run(workflow, message)


@frappe.whitelist()
def get_workflow_run(run_id: str):
	return workflows.get_workflow_run(run_id)


@frappe.whitelist()
def get_workflow_history(workflow: str, limit: int = 20):
	return workflows.run_history(workflow, cint(limit))


@frappe.whitelist()
def create_node_run(
	contract_version: str = "",
	parent_run_id: str = "",
	node_id: str = "",
	agent_type: str = "",
	node_prompt=None,
	input=None,
	idempotency_key: str = "",
):
	"""Runtime-only: create one child run for a workflow node."""
	payload = _validate(
		"create_node_run_request",
		{
			"contract_version": contract_version,
			"parent_run_id": parent_run_id,
			"node_id": node_id,
			"agent_type": agent_type,
			"node_prompt": node_prompt,
			"input": _parse(input, {}) or {},
			"idempotency_key": idempotency_key,
		},
	)
	context = workflows.create_node_run(
		parent_run_id=payload["parent_run_id"],
		node_id=payload["node_id"],
		agent_type=payload["agent_type"],
		input_payload=payload["input"],
		idempotency_key=payload["idempotency_key"],
		node_prompt=payload.get("node_prompt"),
	)
	return _validate("run_context", context)


@frappe.whitelist()
def get_runtime_health():
	"""Operational probe for Desk. Never exposes the token or the URL's secret."""
	permissions.require_human()
	return dispatch.runtime_health()


@frappe.whitelist()
def get_reconciliation_report(limit: int = 200, only_shadow: int = 0):
	"""Do the generic records still agree with the legacy chat records?

	Read-only. Used to decide whether the next rollout step is safe.
	"""
	user = permissions.require_human()
	if "System Manager" not in frappe.get_roles(user):
		frappe.throw(_("Only a System Manager can read the reconciliation report"), frappe.PermissionError)
	from nextgen_erp.agent_records import reconciliation

	return reconciliation.shadow_comparison_report(
		limit=max(1, min(cint(limit), 1000)), only_shadow=bool(cint(only_shadow))
	)
