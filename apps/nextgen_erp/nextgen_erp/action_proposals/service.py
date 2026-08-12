"""Proposal, approval and execution — the human decision boundary.

A write intent never becomes an ERP document directly. It becomes an Action
Proposal with an immutable original snapshot, a live preview, a policy
snapshot, an expiry and a snapshot hash. A human approves exactly one hash;
Frappe revalidates live ERP data immediately before execution, and material
drift replaces the proposal instead of quietly executing changed data.

**Phase 1 boundary.** Execution is delegated to the originating
``NextGen Chat Action`` through ``staff_chat.confirm_action`` so there is a
single, already-hardened write path with its own locking, revalidation and
idempotency. Proposals without a chat action are recorded and approvable but
not executable until Phase 2 adds its own deterministic executor.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime

from nextgen_erp.agent_gateway import permissions, policy
from nextgen_erp.agent_gateway.contracts import payload_hash
from nextgen_erp.agent_gateway.redaction import redact, redact_error

PROPOSAL_DOCTYPE = "NextGen Action Proposal"
DECISION_DOCTYPE = "NextGen Approval Decision"
CHAT_ACTION_DOCTYPE = "NextGen Chat Action"

TARGET_DOCTYPES = {
	"prepare_sales_order": "Sales Order",
	"prepare_purchase_order": "Purchase Order",
	"prepare_material_request": "Material Request",
}


def target_doctype(action_type: str, preview: dict | None = None) -> str | None:
	"""The fixed target for the copilots; the proposed doctype for a document."""
	if action_type == "prepare_document":
		return str((preview or {}).get("doctype") or "") or None
	return TARGET_DOCTYPES.get(action_type)

# NextGen Chat Action status -> proposal status, used while both records are
# dual-written during the transition release.
LEGACY_STATUS_MAP = {
	"Pending": "Pending Approval",
	"Executing": "Executing",
	"Completed": "Completed",
	"Expired": "Expired",
	"Failed": "Failed",
	"Cancelled": "Rejected",
}


class ProposalDrift(Exception):
	"""Live ERP data no longer matches the approved snapshot."""


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------


def snapshot_hash(preview: dict, policy_snapshot: dict, resource_versions: dict) -> str:
	"""One hash over everything the reviewer is deciding about."""
	return payload_hash(
		{
			"preview": preview or {},
			"policy": policy_snapshot or {},
			"resources": resource_versions or {},
		}
	)


def resource_versions(preview: dict) -> dict[str, Any]:
	"""Document versions and quantities the preview was derived from.

	Recomputing this before execution is how price, stock, supplier, UOM and
	document drift becomes visible instead of silently executing.
	"""
	versions: dict[str, Any] = {}
	for doctype, field in (("Customer", "customer"), ("Supplier", "supplier")):
		name = preview.get(field)
		if name:
			versions[f"{doctype}:{name}"] = str(
				frappe.db.get_value(doctype, name, "modified") or ""
			)
	for row in preview.get("items") or []:
		item_code = row.get("item_code")
		if not item_code:
			continue
		versions[f"Item:{item_code}"] = {
			"modified": str(frappe.db.get_value("Item", item_code, "modified") or ""),
			"qty": flt(row.get("qty")),
			"uom": row.get("uom"),
			"rate": flt(row.get("rate")),
			"warehouse": row.get("warehouse"),
		}
	versions["total"] = flt(preview.get("total"))
	return versions


def _risk_level(company: str, preview: dict, warnings: list[str]) -> str:
	total = flt(preview.get("total"))
	if warnings or not policy.value_within_limit(company, total):
		return "High"
	return "Low" if total <= 0 else "Medium"


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------


def create_proposal(
	run,
	*,
	action_type: str,
	company: str,
	preview: dict,
	idempotency_key: str,
	original_payload: dict | None = None,
	warnings: list[str] | None = None,
	policy_snapshot: dict | None = None,
	expires_at: str | None = None,
	confidence: float | None = None,
	risk_level: str | None = None,
	legacy_chat_action: str | None = None,
):
	"""Create (or return) the proposal for ``idempotency_key``."""
	existing = frappe.db.get_value(PROPOSAL_DOCTYPE, {"idempotency_key": idempotency_key}, "name")
	if existing:
		return frappe.get_doc(PROPOSAL_DOCTYPE, existing), True

	if company != run.company:
		frappe.throw(_("A proposal cannot leave the company of its run"), frappe.PermissionError)
	warnings = list(warnings or [])
	snapshot = policy_snapshot or policy.snapshot(company)
	versions = resource_versions(preview)
	minutes = cint(snapshot.get("proposal_expiry_minutes") or 15)
	doc = frappe.get_doc(
		{
			"doctype": PROPOSAL_DOCTYPE,
			"run": run.name,
			"company": company,
			"action_type": action_type,
			"target_doctype": target_doctype(action_type, preview),
			"risk_level": risk_level or _risk_level(company, preview, warnings),
			"status": "Pending Approval",
			"idempotency_key": idempotency_key,
			"expires_at": expires_at or add_to_date(now_datetime(), minutes=minutes),
			"requested_by": run.requested_by,
			"execution_user": run.execution_user,
			"confidence": flt(confidence),
			"original_proposal": _dump(redact(original_payload or {})),
			"current_preview": _dump(redact(preview)),
			"warnings": _dump(warnings),
			"policy_snapshot": _dump(snapshot),
			"resource_versions": _dump(versions),
			"snapshot_hash": snapshot_hash(preview, snapshot, versions),
			"legacy_chat_action": legacy_chat_action,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc, False


def from_tool_result(run, tool_name: str, arguments: dict, result: dict) -> dict[str, Any]:
	"""Turn a write tool's preview into a proposal and a sanitized tool result.

	When the tool also created a legacy ``NextGen Chat Action`` — every chat
	flow during the transition release — both records are written and linked.
	"""
	preview = result.get("preview") or {}
	legacy = result.get("action_id")
	if legacy:
		action = frappe.get_doc(CHAT_ACTION_DOCTYPE, legacy)
		payload = _load(action.proposal_payload, {})
		warnings = _load(action.warnings, [])
		confidence = flt(action.confidence)
		expires_at = action.expires_at
		key = f"proposal:{action.idempotency_key}"
	else:
		payload = result.get("proposal_payload") or arguments
		warnings = result.get("warnings") or preview.get("warnings") or []
		confidence = flt(result.get("confidence") or preview.get("confidence"))
		expires_at = None
		key = f"proposal:{run.name}:{tool_name}:{payload_hash(payload)[:16]}"

	proposal, replayed = create_proposal(
		run,
		action_type=tool_name,
		company=run.company,
		preview=preview,
		idempotency_key=key,
		original_payload=payload,
		warnings=warnings,
		expires_at=expires_at,
		confidence=confidence,
		legacy_chat_action=legacy,
	)
	if legacy and not run.legacy_chat_action:
		run.db_set("legacy_chat_action", legacy, update_modified=False)
	return {
		"proposal_id": proposal.name,
		"action_id": legacy,
		"status": proposal.status,
		"expires_at": str(proposal.expires_at),
		"snapshot_hash": proposal.snapshot_hash,
		"preview": preview,
		"warnings": warnings,
		"replayed": replayed,
	}


# ---------------------------------------------------------------------------
# revalidation
# ---------------------------------------------------------------------------


def revalidate(proposal) -> tuple[dict, list[str], bool]:
	"""Recompute the preview against live ERP data.

	Returns the live preview, the issues found, and whether the proposal has
	drifted materially from the snapshot the reviewer saw.
	"""
	preview = _load(proposal.current_preview, {})
	if proposal.action_type == "prepare_document":
		live, issues = _revalidate_document(proposal, preview)
	elif proposal.action_type == "prepare_sales_order":
		from nextgen_erp import staff_chat

		live, issues = staff_chat._revalidate(preview)
	else:
		from nextgen_erp import forecast, procurement

		action_like = SimpleNamespace(
			name=proposal.name,
			action_type=proposal.action_type,
			user=proposal.requested_by,
			preview=proposal.current_preview,
			warnings=proposal.warnings,
			status=proposal.status,
			expires_at=proposal.expires_at,
		)
		live, issues = procurement._revalidate_action(action_like, preview, forecast.get_settings())

	snapshot = policy.snapshot(proposal.company)
	versions = resource_versions(live)
	live_hash = snapshot_hash(live, snapshot, versions)
	drifted = bool(issues) or live_hash != proposal.snapshot_hash
	return live, list(issues), drifted


def _revalidate_document(proposal, preview: dict) -> tuple[dict, list[str]]:
	"""Re-run the rolled-back dry run under the requester's live permissions.

	Permission, policy or controller changes since the preview was built all
	surface here as issues, which supersede the proposal instead of executing.
	"""
	from nextgen_erp.domain_tools import documents
	from nextgen_erp.domain_tools.registry import acting_as

	payload = _load(proposal.original_proposal, {})
	doctype = str(payload.get("doctype") or "")
	try:
		with acting_as(proposal.requested_by):
			documents.assert_writable(doctype, proposal.company)
			values = documents.clean_values(doctype, payload.get("values"), proposal.company)
			live, warnings = documents.build_preview(doctype, values)
		return live, list(warnings)
	except documents.DocumentToolError as exc:
		return preview, [str(exc)]
	except frappe.PermissionError as exc:
		return preview, [str(exc) or _("Permission denied")]


def _store_revalidation(proposal, live: dict, issues: list[str]) -> str:
	snapshot = policy.snapshot(proposal.company)
	versions = resource_versions(live)
	new_hash = snapshot_hash(live, snapshot, versions)
	proposal.db_set(
		{
			"current_preview": _dump(redact(live)),
			"warnings": _dump(list(dict.fromkeys([*_load(proposal.warnings, []), *issues]))),
			"policy_snapshot": _dump(snapshot),
			"resource_versions": _dump(versions),
			"snapshot_hash": new_hash,
		},
		update_modified=False,
	)
	return new_hash


def supersede(proposal, reason: str) -> None:
	"""Drift never executes. The proposal is closed and a fresh one is required."""
	proposal.db_set({"status": "Superseded", "result_json": _dump({"reason": reason})})
	frappe.db.commit()


# ---------------------------------------------------------------------------
# decisions
# ---------------------------------------------------------------------------


def _load_for_decision(proposal_id: str):
	frappe.db.sql(f"select name from `tab{PROPOSAL_DOCTYPE}` where name=%s for update", proposal_id)
	if not frappe.db.exists(PROPOSAL_DOCTYPE, proposal_id):
		frappe.throw(_("Unknown action proposal: {0}").format(proposal_id), frappe.DoesNotExistError)
	proposal = frappe.get_doc(PROPOSAL_DOCTYPE, proposal_id)
	user = permissions.require_human()
	policy.assert_company_access(proposal.company, user)
	agent_type = frappe.db.get_value("NextGen Agent Run", proposal.run, "agent_type")
	if agent_type:
		permissions.assert_agent_access(agent_type, user)
	if proposal.requested_by != user and "System Manager" not in frappe.get_roles(user):
		frappe.throw(_("Action proposal not found"), frappe.DoesNotExistError)
	return proposal, user


def _record_decision(proposal, user: str, decision: str, reason: str | None = None) -> str:
	doc = frappe.get_doc(
		{
			"doctype": DECISION_DOCTYPE,
			"proposal": proposal.name,
			"company": proposal.company,
			"reviewer": user,
			"decision": decision,
			"decided_at": now_datetime(),
			"reviewed_snapshot_hash": proposal.snapshot_hash,
			"reason": reason,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def approve(proposal_id: str, snapshot_hash_seen: str | None = None) -> dict[str, Any]:
	"""Approve exactly the snapshot the reviewer saw, after live revalidation."""
	proposal, user = _load_for_decision(proposal_id)
	if proposal.status == "Approved":
		return {"proposal_id": proposal.name, "status": proposal.status, "already": True}
	if proposal.status != "Pending Approval":
		frappe.throw(_("This proposal is no longer pending approval"))
	if proposal.is_expired:
		proposal.db_set("status", "Expired")
		frappe.throw(_("This proposal expired. Create a fresh preview."))
	if snapshot_hash_seen and snapshot_hash_seen != proposal.snapshot_hash:
		frappe.throw(
			_("This approval refers to an older preview. Reload and review the current one.")
		)

	live, issues, drifted = revalidate(proposal)
	if drifted:
		_store_revalidation(proposal, live, issues)
		supersede(proposal, "revalidation_drift: " + "; ".join(issues)[:300])
		frappe.throw(
			_("ERP data changed after this preview was created. A fresh preview is required."),
			frappe.ValidationError,
		)

	decision_id = _record_decision(proposal, user, "Approve")
	proposal.db_set(
		{"status": "Approved", "approved_by": user, "approved_at": now_datetime()}
	)
	return {
		"proposal_id": proposal.name,
		"approval_id": decision_id,
		"status": "Approved",
		"snapshot_hash": proposal.snapshot_hash,
	}


def reject(proposal_id: str, reason: str | None = None) -> dict[str, Any]:
	proposal, user = _load_for_decision(proposal_id)
	if proposal.status in ("Rejected", "Superseded"):
		return {"proposal_id": proposal.name, "status": proposal.status, "already": True}
	if proposal.status not in ("Pending Approval", "Approved"):
		frappe.throw(_("This proposal can no longer be rejected"))
	decision_id = _record_decision(proposal, user, "Reject", reason)
	proposal.db_set("status", "Rejected")
	if proposal.legacy_chat_action:
		legacy_status = frappe.db.get_value(
			CHAT_ACTION_DOCTYPE, proposal.legacy_chat_action, "status"
		)
		if legacy_status == "Pending":
			frappe.db.set_value(
				CHAT_ACTION_DOCTYPE, proposal.legacy_chat_action, "status", "Cancelled"
			)
	return {"proposal_id": proposal.name, "approval_id": decision_id, "status": "Rejected"}


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


def _execute_via_chat_action(proposal) -> dict[str, Any]:
	"""The copilots' original write path, with its own locking and idempotency."""
	from nextgen_erp import staff_chat

	if not proposal.legacy_chat_action:
		frappe.throw(
			_("This proposal has no originating chat action and cannot be executed")
		)
	return staff_chat.confirm_action(proposal.legacy_chat_action)


def _execute_document(proposal) -> dict[str, Any]:
	"""Insert the proposed document through its own controller.

	The whole boundary is re-asserted here rather than trusted from preview
	time: denylist, code allowlist, company policy and the requester's live
	permissions are all checked again immediately before the write. The
	document is inserted as a draft and is never submitted.
	"""
	from nextgen_erp.domain_tools import documents
	from nextgen_erp.domain_tools.registry import acting_as

	payload = _load(proposal.original_proposal, {})
	doctype = str(payload.get("doctype") or "")
	with acting_as(proposal.requested_by):
		documents.assert_writable(doctype, proposal.company)
		values = documents.clean_values(doctype, payload.get("values"), proposal.company)
		doc = frappe.new_doc(doctype)
		doc.update(values)
		doc.insert()
	return {
		"document_type": doctype,
		"document_name": doc.name,
		"docstatus": cint(doc.docstatus),
	}


#: An approved proposal reaches ERP only through one of these. Adding an action
#: type without an executor makes it un-executable rather than unguarded.
EXECUTORS = {
	"prepare_sales_order": _execute_via_chat_action,
	"prepare_purchase_order": _execute_via_chat_action,
	"prepare_material_request": _execute_via_chat_action,
	"prepare_document": _execute_document,
}


def execute(proposal_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
	"""Execute an approved proposal inside a Frappe transaction."""
	proposal, user = _load_for_decision(proposal_id)
	if proposal.status == "Completed":
		return {
			"proposal_id": proposal.name,
			"status": proposal.status,
			"document_type": proposal.result_doctype,
			"document_name": proposal.result_name,
			"already": True,
		}
	if proposal.status != "Approved":
		frappe.throw(_("Only an approved proposal can be executed"))
	if proposal.is_expired:
		proposal.db_set("status", "Expired")
		frappe.throw(_("This proposal expired. Create a fresh preview."))
	if idempotency_key and idempotency_key != proposal.idempotency_key:
		frappe.throw(_("Execution idempotency key does not match this proposal"))

	live, issues, drifted = revalidate(proposal)
	if drifted:
		_store_revalidation(proposal, live, issues)
		supersede(proposal, "revalidation_drift: " + "; ".join(issues)[:300])
		frappe.throw(
			_("ERP data changed after approval. A fresh preview is required."), frappe.ValidationError
		)
	executor = EXECUTORS.get(proposal.action_type)
	if not executor:
		frappe.throw(_("No executor is registered for {0}").format(proposal.action_type))

	proposal.db_set("status", "Executing", update_modified=False)
	try:
		result = executor(proposal)
	except Exception as exc:
		proposal.db_set(
			{"status": "Failed", "result_json": _dump({"error": redact_error(exc)})}
		)
		frappe.db.commit()
		raise
	proposal.db_set(
		{
			"status": "Completed",
			"executed_at": now_datetime(),
			"result_doctype": result.get("document_type"),
			"result_name": result.get("document_name"),
			"result_json": _dump(redact(result)),
		}
	)
	frappe.db.commit()
	return {
		"proposal_id": proposal.name,
		"status": "Completed",
		"document_type": result.get("document_type"),
		"document_name": result.get("document_name"),
		"result": result,
	}


# ---------------------------------------------------------------------------
# compatibility and maintenance
# ---------------------------------------------------------------------------


def find_by_chat_action(action_name: str) -> str | None:
	return frappe.db.get_value(PROPOSAL_DOCTYPE, {"legacy_chat_action": action_name}, "name")


def sync_from_chat_action(action) -> str | None:
	"""Keep a dual-written proposal reconciled with its legacy chat action."""
	name = find_by_chat_action(action.name)
	if not name:
		return None
	status = LEGACY_STATUS_MAP.get(action.status)
	if not status:
		return name
	if action.status == "Cancelled" and "superseded_by" in (action.result_json or ""):
		status = "Superseded"
	updates: dict[str, Any] = {"status": status}
	if action.result_doctype:
		updates["result_doctype"] = action.result_doctype
		updates["result_name"] = action.result_name
	if action.status == "Completed":
		updates["executed_at"] = now_datetime()
	frappe.db.set_value(PROPOSAL_DOCTYPE, name, updates, update_modified=False)
	return name


def expire_due() -> list[str]:
	"""Expiry blocks execution; it is never silently extended."""
	due = frappe.get_all(
		PROPOSAL_DOCTYPE,
		filters={
			"status": ["in", ["Pending Approval", "Approved"]],
			"expires_at": ["<", now_datetime()],
		},
		pluck="name",
	)
	for name in due:
		frappe.db.set_value(PROPOSAL_DOCTYPE, name, "status", "Expired", update_modified=False)
	return due


def _dump(value: Any) -> str:
	return json.dumps(value, ensure_ascii=False, default=str)


def _load(value: Any, fallback: Any) -> Any:
	if isinstance(value, (dict, list)):
		return value
	try:
		return json.loads(value or "")
	except (TypeError, ValueError):
		return fallback
