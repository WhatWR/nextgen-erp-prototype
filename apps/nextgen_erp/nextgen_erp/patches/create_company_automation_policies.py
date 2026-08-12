"""Convert the global procurement settings into per-company automation policies.

The current default company inherits the existing configuration so its
behaviour does not change. Every other company gets a **disabled Shadow**
policy: an active policy is never copied across companies automatically,
because a company that was never reviewed must not start creating documents.

Safe to rerun.
"""

import frappe
from frappe.utils import cint, flt

from nextgen_erp.agent_gateway import policy

SETTINGS = "NextGen Procurement Settings"


def _default_company() -> str | None:
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if company and frappe.db.exists("Company", company):
		return company
	return frappe.db.get_value("Company", {}, "name", order_by="creation asc")


def _from_procurement_settings() -> dict:
	if not frappe.db.exists("DocType", SETTINGS):
		return {}
	doc = frappe.get_single(SETTINGS)
	mode = (doc.get("automation_mode") or "Shadow").strip()
	return {
		# "Automatic" in the legacy single settings could submit documents.
		# The company policy has no such mode; the closest safe equivalent is
		# draft creation, which still requires a human to submit.
		"default_mode": "Automatic Draft" if mode == "Automatic" else mode,
		"enabled": cint(doc.get("enable_procurement_copilot")),
		"minimum_data_quality_score": flt(doc.get("minimum_data_quality_score") or 0.8),
		"maximum_proposal_value": flt(doc.get("maximum_po_value") or 0),
		"daily_auto_value_limit": flt(doc.get("daily_auto_spend_limit") or 0),
		"maximum_price_variance_percent": flt(doc.get("maximum_price_variance_percent") or 10),
		"allowed_warehouses": doc.get("allowed_warehouses") or "",
		"allowed_item_groups": doc.get("allowed_item_groups") or "",
		"item_allowlist": doc.get("auto_item_allowlist") or "",
		"supplier_allowlist": doc.get("approved_suppliers") or "",
	}


def execute():
	if not frappe.db.exists("DocType", policy.POLICY_DOCTYPE):
		return
	default_company = _default_company()
	inherited = _from_procurement_settings() if default_company else {}
	for company in frappe.get_all("Company", pluck="name"):
		existing = policy.get_policy_doc(company)
		if existing:
			continue
		if company == default_company and inherited:
			values = dict(inherited)
			# Warehouses configured globally may belong to another company; the
			# policy controller rejects those, so keep only this company's.
			values["allowed_warehouses"] = "\n".join(
				warehouse
				for warehouse in str(values.get("allowed_warehouses") or "").splitlines()
				if warehouse.strip()
				and frappe.db.get_value("Warehouse", warehouse.strip(), "company") == company
			)
		else:
			values = {"enabled": 0, "default_mode": "Shadow"}
		policy.ensure_policy(company, **values)
		frappe.db.commit()
