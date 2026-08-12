"""Compare generic agent records against the legacy chat records.

Rollout steps 3 to 7 depend on being able to answer one question repeatedly:
does the new path agree with the old one? This module compares, for every run
linked to a ``NextGen Chat Action``, the fields that would matter if they
disagreed — agent, company, requester, chosen write tool, preview hash and
error classification — and reports the mismatches.

It reads only. Nothing here changes a record or a decision.
"""

from __future__ import annotations

import json
from typing import Any

import frappe

from nextgen_erp.agent_gateway.contracts import payload_hash

COMPARED_FIELDS = ("agent", "company", "requested_by", "write_tool", "preview_hash", "error_class")


def _load(value: Any, fallback: Any) -> Any:
	if isinstance(value, (dict, list)):
		return value
	try:
		return json.loads(value or "")
	except (TypeError, ValueError):
		return fallback


def _error_class(status: str) -> str:
	return "error" if status in ("Failed", "Expired") else "ok"


def compare_run_to_legacy(run_name: str) -> dict[str, Any]:
	"""Compare one run and its proposal with the chat action they shadow."""
	run = frappe.get_doc("NextGen Agent Run", run_name)
	if not run.legacy_chat_action:
		return {"run": run_name, "comparable": False, "reason": "no legacy chat action"}
	action = frappe.get_doc("NextGen Chat Action", run.legacy_chat_action)
	proposal_name = frappe.db.get_value(
		"NextGen Action Proposal", {"run": run.name}, "name", order_by="creation desc"
	)
	proposal = frappe.get_doc("NextGen Action Proposal", proposal_name) if proposal_name else None

	write_tools = frappe.get_all(
		"NextGen Agent Step",
		filters={"run": run.name, "step_type": "Tool", "status": "Success"},
		fields=["operation"],
		order_by="sequence asc",
		pluck="operation",
	)
	write_tool = next(
		(name for name in reversed(write_tools) if name.startswith("prepare_")),
		proposal.action_type if proposal else None,
	)

	observed = {
		"agent": run.agent_type,
		"company": run.company,
		"requested_by": run.requested_by,
		"write_tool": write_tool,
		"preview_hash": payload_hash(_load(proposal.current_preview, {})) if proposal else None,
		"error_class": _error_class(run.status),
	}
	expected = {
		"agent": action.agent_type,
		"company": (_load(action.preview, {}) or {}).get("company") or run.company,
		"requested_by": action.user,
		"write_tool": action.action_type,
		"preview_hash": payload_hash(_load(action.preview, {})),
		"error_class": _error_class(action.status),
	}
	mismatches = {
		field: {"run": observed[field], "legacy": expected[field]}
		for field in COMPARED_FIELDS
		if observed[field] != expected[field]
	}
	return {
		"run": run.name,
		"chat_action": action.name,
		"proposal": proposal_name,
		"comparable": True,
		"matched": not mismatches,
		"mismatches": mismatches,
	}


def shadow_comparison_report(limit: int = 200, only_shadow: bool = False) -> dict[str, Any]:
	"""Aggregate comparison used to decide whether a rollout step is safe."""
	filters: dict[str, Any] = {"legacy_chat_action": ["is", "set"]}
	if only_shadow:
		filters["shadow"] = 1
	names = frappe.get_all(
		"NextGen Agent Run", filters=filters, pluck="name", order_by="creation desc", limit=limit
	)
	comparisons = [compare_run_to_legacy(name) for name in names]
	mismatched = [row for row in comparisons if row.get("comparable") and not row["matched"]]
	by_field: dict[str, int] = {}
	for row in mismatched:
		for field in row["mismatches"]:
			by_field[field] = by_field.get(field, 0) + 1
	return {
		"compared": len(comparisons),
		"matched": len(comparisons) - len(mismatched),
		"mismatched": len(mismatched),
		"mismatches_by_field": by_field,
		"examples": mismatched[:10],
		"reconciled": not mismatched,
	}
