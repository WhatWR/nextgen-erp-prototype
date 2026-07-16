"""Install and migration invariants for the NextGen ERP app."""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


SERVICE_ROLE = "NextGen Order Service"


def _ensure_role() -> None:
	if not frappe.db.exists("Role", SERVICE_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": SERVICE_ROLE, "desk_access": 0}).insert()


def _ensure_custom_fields() -> None:
	reference_field = {
		"fieldname": "custom_nextgen_external_reference",
		"label": "NextGen External Reference",
		"fieldtype": "Data",
		"unique": 1,
		"no_copy": 1,
		"read_only": 1,
		"hidden": 1,
	}
	create_custom_fields(
		{
			"Sales Order": [dict(reference_field)],
			"Delivery Note": [dict(reference_field)],
			"Sales Invoice": [dict(reference_field)],
			"Payment Entry": [dict(reference_field)],
			"Item": [
				{
					"fieldname": "custom_nextgen_aliases",
					"label": "Order Text Aliases",
					"fieldtype": "Small Text",
					"description": "JSON array or comma-separated Thai/customer names used by Order Intake",
					"insert_after": "item_name",
				}
			],
		},
		update=True,
	)


def before_install() -> None:
	_ensure_role()


def after_install() -> None:
	_ensure_role()
	_ensure_custom_fields()


def after_migrate() -> None:
	_ensure_role()
	_ensure_custom_fields()
	# Workspace Sidebar is database-backed rather than exported with the
	# standard Workspace JSON. Refresh it after migrations so every production
	# site exposes the complete Order Agent navigation without requiring users
	# to know the DocType names and find them through global search.
	from nextgen_erp.setup_doctypes import _desk_tile, _procurement_workspace

	_procurement_workspace()
	_desk_tile()
