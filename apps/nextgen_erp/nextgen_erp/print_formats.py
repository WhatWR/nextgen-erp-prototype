"""Managed print formats shipped with NextGen ERP."""

from __future__ import annotations

from pathlib import Path

import frappe


CUSTOMER_INVOICE_PRINT_FORMAT = "NextGen Customer Invoice"


def _template(name: str) -> str:
	return (Path(__file__).parent / "templates" / "print_formats" / name).read_text(encoding="utf-8")


def ensure_print_formats() -> None:
	"""Create or refresh app-managed formats without changing the ERP default."""
	values = {
		"doc_type": "Sales Invoice",
		"print_format_for": "DocType",
		"print_format_type": "Jinja",
		"pdf_generator": "chrome",
		"custom_format": 1,
		"disabled": 0,
		"html": _template("customer_invoice.html"),
		"css": _template("customer_invoice.css"),
	}
	if frappe.db.exists("Print Format", CUSTOMER_INVOICE_PRINT_FORMAT):
		doc = frappe.get_doc("Print Format", CUSTOMER_INVOICE_PRINT_FORMAT)
		for field, value in values.items():
			setattr(doc, field, value)
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc(
			{
				"doctype": "Print Format",
				"name": CUSTOMER_INVOICE_PRINT_FORMAT,
				"standard": "No",
				**values,
			}
		).insert(ignore_permissions=True)
