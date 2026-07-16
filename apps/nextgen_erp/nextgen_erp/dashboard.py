"""Permission-aware data source for the standalone AI Cockpit desk page."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate


ALLOWED_ROLES = {
	"System Manager",
	"Sales Manager",
	"Sales User",
	"Purchase Manager",
	"Purchase User",
	"Stock Manager",
}


def _check_access() -> None:
	if frappe.session.user == "Guest" or not ALLOWED_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You are not permitted to view the AI Cockpit."), frappe.PermissionError)


def _can_read(doctype: str) -> bool:
	return bool(frappe.db.exists("DocType", doctype) and frappe.has_permission(doctype, "read"))


def _rows(doctype: str, fields: list[str], filters: dict | list | None = None, order_by: str | None = None):
	if not _can_read(doctype):
		return []
	return frappe.get_list(
		doctype,
		fields=fields,
		filters=filters or {},
		order_by=order_by,
		limit_page_length=0,
	)


def _month_key(value) -> str:
	return getdate(value).strftime("%Y-%m")


def _month_labels(months: int = 6) -> list[tuple[str, str]]:
	today = getdate(nowdate())
	result = []
	for offset in range(months - 1, -1, -1):
		year = today.year
		month = today.month - offset
		while month <= 0:
			month += 12
			year -= 1
		key = f"{year:04d}-{month:02d}"
		result.append((key, date(year, month, 1).strftime("%b")))
	return result


def _currency() -> str:
	company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_user_default("company")
	return (
		frappe.db.get_value("Company", company, "default_currency") if company else None
	) or frappe.db.get_single_value("Global Defaults", "default_currency") or "THB"


def _activity(kind: str, title: str, detail: str, doctype: str, name: str, modified, tone: str):
	return {
		"kind": kind,
		"title": title,
		"detail": detail,
		"doctype": doctype,
		"name": name,
		"modified": str(modified),
		"tone": tone,
	}


@frappe.whitelist()
def get_command_center_data(days: int = 30, company: str | None = None):
	"""Return compact, live ERP metrics for the combined AI dashboard."""
	_check_access()
	days = max(7, min(cint(days) or 30, 365))
	from_date = add_days(nowdate(), -(days - 1))
	company = company or frappe.defaults.get_user_default("Company") or frappe.defaults.get_user_default("company")

	company_filter = {"company": company} if company else {}
	sales_filters = {"transaction_date": [">=", from_date], "docstatus": 1, **company_filter}
	purchase_filters = {"transaction_date": [">=", from_date], "docstatus": 1, **company_filter}

	sales_orders = _rows(
		"Sales Order",
		["name", "transaction_date", "grand_total", "status", "customer", "modified"],
		sales_filters,
		"modified desc",
	)
	purchase_orders = _rows(
		"Purchase Order",
		["name", "transaction_date", "grand_total", "status", "supplier", "per_received", "modified"],
		purchase_filters,
		"modified desc",
	)
	intakes = _rows(
		"AI Order Intake",
		["name", "status", "total", "customer", "sales_order", "automation_mode", "modified"],
		{"creation": [">=", from_date]},
		"modified desc",
	)
	recommendations = _rows(
		"NextGen Procurement Recommendation",
		["name", "status", "item", "item_name", "projected_total", "purchase_order", "modified"],
		{"creation": [">=", from_date], **company_filter},
		"modified desc",
	)
	forecasts = _rows(
		"NextGen Procurement Forecast",
		["name", "item", "item_name", "forecast_date", "stockout_risk", "movement_class", "suggested_qty", "modified"],
		{"forecast_date": [">=", from_date], **company_filter},
		"forecast_date desc, modified desc",
	)

	latest_forecast = {}
	for row in forecasts:
		latest_forecast.setdefault(row.item, row)
	risk_counts = Counter((row.stockout_risk or "low") for row in latest_forecast.values())
	intake_counts = Counter((row.status or "Needs Review") for row in intakes)

	pending_intake = sum(intake_counts.get(status, 0) for status in ("Needs Review", "Awaiting Customer", "Payment Review"))
	pending_procurement = sum(
		1 for row in recommendations if row.status in ("Draft", "Suggested", "Needs Review", "Approved")
	)
	automated = [row for row in intakes if row.automation_mode == "automatic"]
	automation_success = sum(1 for row in automated if row.sales_order)
	automation_rate = round((automation_success / len(automated) * 100), 1) if automated else 0

	month_labels = _month_labels()
	sales_by_month = defaultdict(float)
	po_by_month = defaultdict(float)
	trend_start = f"{month_labels[0][0]}-01"
	for row in _rows(
		"Sales Order", ["transaction_date", "grand_total"], {"transaction_date": [">=", trend_start], "docstatus": 1, **company_filter}
	):
		sales_by_month[_month_key(row.transaction_date)] += flt(row.grand_total)
	for row in _rows(
		"Purchase Order", ["transaction_date", "grand_total"], {"transaction_date": [">=", trend_start], "docstatus": 1, **company_filter}
	):
		po_by_month[_month_key(row.transaction_date)] += flt(row.grand_total)

	activities = []
	for row in intakes[:4]:
		activities.append(_activity("Sales AI", row.name, row.status, "AI Order Intake", row.name, row.modified, "blue"))
	for row in recommendations[:4]:
		activities.append(
			_activity("Procurement AI", row.item_name or row.item or row.name, row.status, "NextGen Procurement Recommendation", row.name, row.modified, "green")
		)
	for row in sales_orders[:2]:
		activities.append(_activity("Sales Order", row.name, row.customer or row.status, "Sales Order", row.name, row.modified, "violet"))
	for row in purchase_orders[:2]:
		activities.append(_activity("Purchase Order", row.name, row.supplier or row.status, "Purchase Order", row.name, row.modified, "amber"))
	activities.sort(key=lambda row: row["modified"], reverse=True)

	return {
		"period_days": days,
		"company": company,
		"currency": _currency(),
		"kpis": {
			"sales_value": sum(flt(row.grand_total) for row in sales_orders),
			"sales_orders": len(sales_orders),
			"purchase_value": sum(flt(row.grand_total) for row in purchase_orders),
			"purchase_orders": len(purchase_orders),
			"attention": pending_intake + pending_procurement + risk_counts.get("high", 0),
			"automation_rate": automation_rate,
		},
		"trend": {
			"labels": [label for _, label in month_labels],
			"sales": [round(sales_by_month[key], 2) for key, _ in month_labels],
			"purchases": [round(po_by_month[key], 2) for key, _ in month_labels],
		},
		"intake_status": {
			"labels": ["Needs Review", "Awaiting Customer", "Reserved", "Paid", "Other"],
			"values": [
				intake_counts.get("Needs Review", 0),
				intake_counts.get("Awaiting Customer", 0),
				intake_counts.get("Reserved", 0),
				intake_counts.get("Paid", 0),
				sum(value for key, value in intake_counts.items() if key not in {"Needs Review", "Awaiting Customer", "Reserved", "Paid"}),
			],
		},
		"stock_risk": {
			"labels": ["High", "Medium", "Low"],
			"values": [risk_counts.get("high", 0), risk_counts.get("medium", 0), risk_counts.get("low", 0)],
		},
		"attention": {
			"sales": pending_intake,
			"procurement": pending_procurement,
			"stock": risk_counts.get("high", 0),
		},
		"activities": activities[:8],
		"generated_at": str(frappe.utils.now_datetime()),
	}
