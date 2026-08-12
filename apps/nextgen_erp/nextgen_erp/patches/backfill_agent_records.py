"""Backfill every NextGen Chat Action into one Agent Run and one Action Proposal.

Nothing is deleted or rewritten: the original Chat Action stays authoritative
for the legacy flow and both new records link back to it, so existing approvals
and audit history remain recoverable.

The proposal reuses the same idempotency key the live dual-write path uses, so
a backfilled record and a newly written one converge instead of forking. Safe
to rerun.
"""

import json

import frappe
from frappe.utils import cint, flt

from nextgen_erp.action_proposals.service import snapshot_hash
from nextgen_erp.agent_gateway import policy

BATCH = 200

# Legacy status -> (agent run status, action proposal status)
STATUS_MAP = {
	"Pending": ("Waiting Approval", "Pending Approval"),
	"Executing": ("Running", "Executing"),
	"Completed": ("Completed", "Completed"),
	"Cancelled": ("Completed", "Rejected"),
	"Expired": ("Completed", "Expired"),
	"Failed": ("Failed", "Failed"),
}

TARGET_DOCTYPES = {
	"prepare_sales_order": "Sales Order",
	"prepare_purchase_order": "Purchase Order",
	"prepare_material_request": "Material Request",
}


def _load(value, fallback):
	if isinstance(value, (dict, list)):
		return value
	try:
		return json.loads(value or "")
	except (TypeError, ValueError):
		return fallback


def _dump(value) -> str:
	return json.dumps(value, ensure_ascii=False, default=str)


def _default_company() -> str | None:
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if company and frappe.db.exists("Company", company):
		return company
	return frappe.db.get_value("Company", {}, "name", order_by="creation asc")


def _backfill_one(action, default_company: str | None) -> bool:
	preview = _load(action.preview, {})
	warnings = _load(action.warnings, [])
	payload = _load(action.proposal_payload, {})
	company = preview.get("company") or default_company
	if not company or not frappe.db.exists("Company", company):
		# Without a company the record cannot satisfy the isolation invariant.
		return False

	run_status, proposal_status = STATUS_MAP.get(action.status, ("Completed", "Expired"))
	run_key = f"run:backfill:{action.name}"
	run_name = frappe.db.get_value("NextGen Agent Run", {"idempotency_key": run_key}, "name")
	if not run_name:
		run = frappe.get_doc(
			{
				"doctype": "NextGen Agent Run",
				"correlation_id": f"backfill-{action.name}",
				"idempotency_key": run_key,
				"company": company,
				"agent_type": action.agent_type or "sales",
				"agent_version": f"{action.agent_type or 'sales'}-v0-legacy",
				"trigger_type": "chat",
				"requested_by": action.user,
				"execution_user": action.user,
				"status": run_status,
				"shadow": 0,
				"prompt_version": "legacy-embedded",
				"runtime_version": "legacy-embedded",
				"max_tool_calls": 6,
				"allowed_tools": _dump([action.action_type]),
				"input_payload": _dump({"messages": [], "context": {"legacy": True}}),
				"chat_session": action.session,
				"legacy_chat_action": action.name,
				"started_at": action.creation,
				"ended_at": action.modified,
			}
		)
		run.flags.ignore_permissions = True
		run.insert(ignore_permissions=True)
		run_name = run.name

	proposal_key = f"proposal:{action.idempotency_key}"
	if frappe.db.exists("NextGen Action Proposal", {"idempotency_key": proposal_key}):
		return True

	snapshot = policy.snapshot(company)
	versions = {"backfill": True, "chat_action": action.name}
	proposal = frappe.get_doc(
		{
			"doctype": "NextGen Action Proposal",
			"run": run_name,
			"company": company,
			"action_type": action.action_type,
			"target_doctype": TARGET_DOCTYPES.get(action.action_type),
			"risk_level": "High" if warnings else "Medium",
			"status": proposal_status,
			"idempotency_key": proposal_key,
			"expires_at": action.expires_at,
			"requested_by": action.user,
			"execution_user": action.user,
			"confidence": flt(action.confidence),
			"original_proposal": _dump(payload),
			"current_preview": _dump(preview),
			"warnings": _dump(warnings),
			"policy_snapshot": _dump(snapshot),
			"resource_versions": _dump(versions),
			"snapshot_hash": snapshot_hash(preview, snapshot, versions),
			"legacy_chat_action": action.name,
			"result_doctype": action.result_doctype,
			"result_name": action.result_name,
			"result_json": action.result_json,
			"approved_by": action.user if action.status == "Completed" else None,
			"approved_at": action.confirmed_at,
			"executed_at": action.confirmed_at if action.status == "Completed" else None,
		}
	)
	proposal.flags.ignore_permissions = True
	proposal.insert(ignore_permissions=True)
	return True


def execute():
	for doctype in ("NextGen Agent Run", "NextGen Action Proposal", "NextGen Chat Action"):
		if not frappe.db.exists("DocType", doctype):
			return
	default_company = _default_company()
	start = 0
	while True:
		names = frappe.get_all(
			"NextGen Chat Action",
			pluck="name",
			order_by="creation asc",
			limit_start=start,
			limit_page_length=BATCH,
		)
		if not names:
			break
		for name in names:
			action = frappe.get_doc("NextGen Chat Action", name)
			_backfill_one(action, default_company)
		frappe.db.commit()
		start += BATCH


def reconciliation_report() -> dict:
	"""Counts used to verify the backfill before and after cutover."""
	actions = cint(frappe.db.count("NextGen Chat Action"))
	backfilled = cint(
		frappe.db.count("NextGen Action Proposal", {"legacy_chat_action": ["is", "set"]})
	)
	unmatched = frappe.db.sql(
		"""
		select a.name
		from `tabNextGen Chat Action` a
		left join `tabNextGen Action Proposal` p on p.legacy_chat_action = a.name
		where p.name is null
		limit 50
		""",
		pluck=True,
	)
	return {
		"chat_actions": actions,
		"linked_proposals": backfilled,
		"unmatched_chat_actions": unmatched,
		"reconciled": not unmatched,
	}
