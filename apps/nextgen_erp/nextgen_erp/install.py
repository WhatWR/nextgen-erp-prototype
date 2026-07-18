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


def _ensure_backorder_default() -> None:
	"""Default 'Allow Invoicing Without Full Stock Reservation' to ON.

	get_single_value coerces an unset Check to 0, so the field's "1" default is
	never applied on existing singletons. Write an explicit Singles row when the
	field has never been set; a user who later unticks it (writing 0) is not
	overridden, because the row then exists.
	"""
	if not frappe.db.exists("DocType", "NextGen Automation Settings"):
		return
	already_set = frappe.db.sql(
		"select 1 from tabSingles where doctype=%s and field=%s limit 1",
		("NextGen Automation Settings", "allow_backorder_invoicing"),
	)
	if not already_set:
		frappe.db.set_single_value("NextGen Automation Settings", "allow_backorder_invoicing", 1)


def before_install() -> None:
	_ensure_role()


def after_install() -> None:
	_ensure_role()
	_ensure_custom_fields()
	_ensure_backorder_default()
	from nextgen_erp.print_formats import ensure_print_formats

	ensure_print_formats()


def after_migrate() -> None:
	_ensure_role()
	_ensure_custom_fields()
	_ensure_backorder_default()
	from nextgen_erp.print_formats import ensure_print_formats

	ensure_print_formats()
	# Workspace Sidebar is database-backed rather than exported with the
	# standard Workspace JSON. Refresh it after migrations so every production
	# site exposes the complete Order Agent navigation without requiring users
	# to know the DocType names and find them through global search.
	from nextgen_erp.setup_doctypes import _desk_tile, _procurement_workspace

	_procurement_workspace()
	_desk_tile()
