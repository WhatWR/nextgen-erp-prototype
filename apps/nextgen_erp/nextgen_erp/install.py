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
			"Quotation": [
				{
					"fieldname": "custom_nextgen_checkout_reference",
					"label": "NextGen Checkout Reference",
					"fieldtype": "Link",
					"options": "AI Order Intake",
					"unique": 1,
					"no_copy": 1,
					"read_only": 1,
					"hidden": 1,
				}
			],
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


def _ensure_webshop_defaults() -> None:
	"""Enable catalog/cart defaults without enabling Webshop's separate payment checkout."""
	if not frappe.db.exists("DocType", "Webshop Settings"):
		return
	settings = frappe.get_single("Webshop Settings")
	company = settings.company or frappe.defaults.get_user_default(
		"company"
	) or frappe.db.get_single_value("Global Defaults", "default_company")
	price_list = settings.price_list or frappe.db.get_single_value(
		"Selling Settings", "selling_price_list"
	)
	customer_group = settings.default_customer_group or frappe.db.get_value(
		"Customer Group", {"is_group": 0}, "name", order_by="creation asc"
	)
	if not (company and price_list and customer_group):
		return
	settings.company = company
	settings.price_list = price_list
	settings.default_customer_group = customer_group
	settings.enabled = 1
	settings.show_price = 1
	settings.show_stock_availability = 1
	settings.show_quantity_in_website = 1
	# NextGen owns checkout/payment. Keep native payment checkout disabled; the
	# web bundle replaces Request for Quote with the idempotent NextGen action.
	settings.enable_checkout = 0
	settings.save_quotations_as_draft = 1
	settings.save(ignore_permissions=True)


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


def ensure_thai_tax_settings(company: str | None = None) -> None:
	"""Keep GL posting working when erpnext_thailand is installed.

	erpnext_thailand hooks GL Entry.after_insert and calls get_thai_tax_settings()
	*before* checking the voucher type, so any GL-generating transaction (invoice,
	payment, receipt) throws until the company has a Thai Tax Settings row. Wire
	one to the company's existing Tax account so the base order-to-cash and
	procurement flows keep working. No-op when the app is absent, the row already
	exists, or no Tax account is configured. On a proper Thai chart of accounts,
	point the four fields at the real output/input (and undue) VAT accounts.
	"""
	if not frappe.db.exists("DocType", "Thai Tax Settings"):
		return
	company = company or frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	if not company:
		return
	settings = frappe.get_single("Thai Tax Settings")
	if any(row.company == company for row in settings.company_accounts):
		return
	tax_account = frappe.db.get_value(
		"Account", {"company": company, "account_type": "Tax", "is_group": 0}, "name"
	)
	if not tax_account:
		return
	settings.append(
		"company_accounts",
		{
			"company": company,
			"sales_tax_account": tax_account,
			"sales_tax_account_undue": tax_account,
			"purchase_tax_account": tax_account,
			"purchase_tax_account_undue": tax_account,
		},
	)
	settings.save(ignore_permissions=True)


def before_install() -> None:
	_ensure_role()


def after_install() -> None:
	_ensure_role()
	_ensure_custom_fields()
	_ensure_backorder_default()
	ensure_thai_tax_settings()
	_ensure_webshop_defaults()
	from nextgen_erp.print_formats import ensure_print_formats

	ensure_print_formats()


def after_migrate() -> None:
	_ensure_role()
	_ensure_custom_fields()
	_ensure_backorder_default()
	ensure_thai_tax_settings()
	_ensure_webshop_defaults()
	from nextgen_erp.print_formats import ensure_print_formats

	ensure_print_formats()
	# Workspace Sidebar is database-backed rather than exported with the
	# standard Workspace JSON. Refresh it after migrations so every production
	# site exposes the complete Order Agent navigation without requiring users
	# to know the DocType names and find them through global search.
	from nextgen_erp.setup_doctypes import _desk_tile, _procurement_workspace

	_procurement_workspace()
	_desk_tile()
