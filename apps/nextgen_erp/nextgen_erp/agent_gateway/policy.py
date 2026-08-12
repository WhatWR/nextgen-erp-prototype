"""Company-scoped automation policy.

Every run, signal, proposal and result has an explicit company, and the policy
for that company decides whether automation is enabled at all, in which mode,
with which warehouses, items, suppliers and limits.

A company without a policy is treated as disabled Shadow. Absence of
configuration is never permission.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, flt

POLICY_DOCTYPE = "NextGen Automation Policy"
SHADOW = "Shadow"
APPROVAL_REQUIRED = "Approval Required"
AUTOMATIC_DRAFT = "Automatic Draft"

DISABLED_POLICY: dict[str, Any] = {
	"company": None,
	"enabled": False,
	"runtime_enabled": False,
	"default_mode": SHADOW,
	"max_tool_calls": 6,
	"proposal_expiry_minutes": 15,
	"minimum_data_quality_score": 0.8,
	"maximum_proposal_value": 0.0,
	"daily_auto_value_limit": 0.0,
	"maximum_price_variance_percent": 10.0,
	"allowed_warehouses": [],
	"allowed_item_groups": [],
	"item_allowlist": [],
	"supplier_allowlist": [],
	"writable_doctypes": [],
}


def get_policy_doc(company: str):
	if not company or not frappe.db.exists(POLICY_DOCTYPE, company):
		return None
	return frappe.get_doc(POLICY_DOCTYPE, company)


def snapshot(company: str) -> dict[str, Any]:
	"""Policy values recorded on the run and on every proposal it creates."""
	doc = get_policy_doc(company)
	if not doc:
		return {**DISABLED_POLICY, "company": company, "missing": True}
	return doc.as_snapshot()


def ensure_policy(company: str, **defaults: Any):
	"""Create a disabled Shadow policy for ``company`` if none exists.

	New companies never inherit another company's active policy; they start
	disabled and must be enabled deliberately.
	"""
	existing = get_policy_doc(company)
	if existing:
		return existing
	doc = frappe.get_doc(
		{
			"doctype": POLICY_DOCTYPE,
			"company": company,
			"enabled": 0,
			"runtime_enabled": 0,
			"default_mode": SHADOW,
			**defaults,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def default_company(user: str | None = None) -> str | None:
	user = user or frappe.session.user
	company = frappe.defaults.get_user_default("company", user) or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	return company or None


def assert_company_access(company: str, user: str | None = None) -> str:
	"""Reject any company the user cannot read, whoever suggested it."""
	user = user or frappe.session.user
	if not company:
		frappe.throw(_("A company is required"), frappe.ValidationError)
	if not frappe.db.exists("Company", company):
		frappe.throw(_("Unknown company: {0}").format(company), frappe.ValidationError)
	if not frappe.has_permission("Company", "read", doc=company, user=user):
		frappe.throw(
			_("You are not permitted to use company {0}").format(company), frappe.PermissionError
		)
	return company


def assert_warehouse_in_company(warehouse: str, company: str, user: str | None = None) -> str:
	"""A warehouse from another company is rejected even if a model asked for it."""
	row = frappe.db.get_value(
		"Warehouse", warehouse, ["company", "is_group", "disabled"], as_dict=True
	)
	if not row:
		frappe.throw(_("Unknown warehouse: {0}").format(warehouse), frappe.ValidationError)
	if row.company != company:
		frappe.throw(
			_("Warehouse {0} belongs to another company").format(warehouse), frappe.PermissionError
		)
	if cint(row.is_group) or cint(row.disabled):
		frappe.throw(
			_("Warehouse {0} must be a non-group, enabled warehouse").format(warehouse),
			frappe.ValidationError,
		)
	transit = frappe.db.get_value("Company", company, "default_in_transit_warehouse")
	if warehouse == transit:
		frappe.throw(_("The in-transit warehouse cannot be used here"), frappe.ValidationError)
	allowed = snapshot(company).get("allowed_warehouses") or []
	if allowed and warehouse not in allowed:
		frappe.throw(
			_("Warehouse {0} is not allowed by the automation policy for {1}").format(
				warehouse, company
			),
			frappe.PermissionError,
		)
	if not frappe.has_permission("Warehouse", "read", doc=warehouse, user=user or frappe.session.user):
		frappe.throw(_("You do not have permission to use this warehouse"), frappe.PermissionError)
	return warehouse


def runtime_enabled(company: str) -> bool:
	policy = snapshot(company)
	return bool(policy.get("enabled") and policy.get("runtime_enabled"))


def proposal_expiry_minutes(company: str) -> int:
	return max(1, cint(snapshot(company).get("proposal_expiry_minutes") or 15))


def max_tool_calls(company: str) -> int:
	return max(1, min(cint(snapshot(company).get("max_tool_calls") or 6), 12))


def creates_documents(company: str) -> bool:
	"""Shadow mode never creates an ERP document, only records and proposals."""
	policy = snapshot(company)
	return bool(policy.get("enabled")) and policy.get("default_mode") != SHADOW


def value_within_limit(company: str, value: float) -> bool:
	limit = flt(snapshot(company).get("maximum_proposal_value"))
	return limit <= 0 or flt(value) <= limit
