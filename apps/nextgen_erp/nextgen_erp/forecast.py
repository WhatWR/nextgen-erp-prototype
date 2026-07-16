"""Deterministic procurement forecast engine (formula ``nextgen-procurement-v1``).

Typhoon never calculates forecasts. This module reads ERPNext data, applies a
documented deterministic formula and returns the result together with its
assumptions, source date range, data-quality score and warnings, so the LLM
and the UI can only ever quote numbers that came from here.

Formula v1 (all quantities in the item's stock UOM):

	demand_30/60/90        = submitted Sales Order qty in the last 30/60/90 days
	average_daily_demand   = 0.5*(d30/30) + 0.3*(d60/60) + 0.2*(d90/90)
	                         (weights renormalised over the windows that have
	                         history coverage)
	lead_time_demand       = average_daily_demand * supplier_lead_time_days
	safety_stock           = max(item.safety_stock,
	                             average_daily_demand * safety_stock_days)
	reorder_point          = lead_time_demand + safety_stock
	projected_available    = actual_qty + incoming_qty(open PO) - reserved_qty
	target_stock           = average_daily_demand * horizon_days + safety_stock
	suggested_qty          = max(0, target_stock - projected_available)
	                         rounded up to MOQ and order multiple
	days_of_supply         = projected_available / average_daily_demand
	stockout_date          = today + (actual - reserved) / average_daily_demand

The weights, horizon, safety-stock days, minimum history and default lead time
come from ``NextGen Procurement Settings``. The data-quality score is rule
based (documented penalties below), not model confidence.
"""

from __future__ import annotations

import json
import math
from typing import Any

import frappe
from frappe.utils import add_days, add_to_date, cint, flt, getdate, now_datetime, nowdate

FORMULA_VERSION = "nextgen-procurement-v1"
DEMAND_WINDOWS = (30, 60, 90)
WINDOW_WEIGHTS = {30: 0.5, 60: 0.3, 90: 0.2}

# Rule-based data quality penalties. The score is 1.0 minus every triggered
# penalty, clamped to [0, 1]. This is a measurable data-quality score, not an
# "AI confidence".
QUALITY_PENALTIES = {
	"no_history": 0.45,
	"short_history": 0.20,
	"no_supplier": 0.15,
	"no_purchase_price": 0.10,
	"no_lead_time": 0.10,
	"negative_stock": 0.15,
	"demand_spike": 0.10,
	"stale_data": 0.10,
	"item_disabled": 0.50,
}


def get_settings() -> dict[str, Any]:
	doc = frappe.get_single("NextGen Procurement Settings")

	def _lines(value):
		return [line.strip() for line in str(value or "").splitlines() if line.strip()]

	return {
		"enabled": bool(cint(doc.get("enable_procurement_copilot"))),
		"model": (doc.get("procurement_model") or "").strip(),
		"default_buying_warehouse": (doc.get("default_buying_warehouse") or "").strip(),
		"horizon_days": max(7, cint(doc.get("default_forecast_horizon_days") or 30)),
		"history_window_days": max(30, cint(doc.get("history_window_days") or 90)),
		"safety_stock_days": max(0, cint(doc.get("safety_stock_days") or 7)),
		"minimum_data_days": max(1, cint(doc.get("minimum_data_days") or 14)),
		"default_lead_time_days": max(1, cint(doc.get("default_supplier_lead_time_days") or 7)),
		"automation_mode": (doc.get("automation_mode") or "Shadow").strip() or "Shadow",
		"enable_scheduled_forecast": bool(cint(doc.get("enable_scheduled_forecast"))),
		"maximum_po_value": flt(doc.get("maximum_po_value") or 0),
		"maximum_price_variance_percent": flt(doc.get("maximum_price_variance_percent") or 10),
		"minimum_data_quality_score": flt(doc.get("minimum_data_quality_score") or 0.8),
		"daily_auto_spend_limit": flt(doc.get("daily_auto_spend_limit") or 0),
		"allowed_item_groups": _lines(doc.get("allowed_item_groups")),
		"allowed_warehouses": _lines(doc.get("allowed_warehouses")),
		"auto_item_allowlist": _lines(doc.get("auto_item_allowlist")),
		"approved_suppliers": _lines(doc.get("approved_suppliers")),
		"allow_direct_po_submission": bool(cint(doc.get("allow_direct_po_submission"))),
	}


def default_buying_warehouse(company: str | None = None) -> str | None:
	company = company or frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	if not company:
		return None
	settings = frappe.get_single("NextGen Procurement Settings")
	allowed = [
		line.strip()
		for line in str(settings.get("allowed_warehouses") or "").splitlines()
		if line.strip()
	]
	transit = frappe.db.get_value("Company", company, "default_in_transit_warehouse")
	preferred = [
		settings.get("default_buying_warehouse"),
		*allowed,
		frappe.defaults.get_user_default("Warehouse"),
		frappe.db.get_single_value("Stock Settings", "default_warehouse"),
	]
	for warehouse in preferred:
		if warehouse and warehouse != transit and frappe.db.exists(
			"Warehouse",
			{"name": warehouse, "company": company, "is_group": 0, "disabled": 0},
		):
			return warehouse
	rows = frappe.get_all(
		"Warehouse",
		filters={"company": company, "is_group": 0, "disabled": 0},
		fields=["name", "warehouse_name", "creation"],
	)
	rows = [row for row in rows if row.name != transit]
	rows.sort(
		key=lambda row: (
			0 if "store" in str(row.warehouse_name or "").casefold() else 1,
			row.creation,
		)
	)
	return rows[0].name if rows else None


def _demand_history(item_code: str, history_days: int) -> dict[str, Any]:
	"""Submitted Sales Order demand per day over the history window."""
	cutoff = add_days(nowdate(), -history_days)
	rows = frappe.db.sql(
		"""
		select so.transaction_date as day, sum(soi.stock_qty) as qty
		from `tabSales Order Item` soi
		join `tabSales Order` so on so.name = soi.parent
		where soi.item_code = %(item)s
			and so.docstatus = 1
			and so.transaction_date >= %(cutoff)s
		group by so.transaction_date
		order by so.transaction_date asc
		""",
		{"item": item_code, "cutoff": cutoff},
		as_dict=True,
	)
	today = getdate(nowdate())
	windows = {}
	for window in DEMAND_WINDOWS:
		start = getdate(add_days(nowdate(), -window))
		windows[window] = sum(flt(row.qty) for row in rows if getdate(row.day) > start)
	first_day = getdate(rows[0].day) if rows else None
	return {
		"daily": [{"day": str(row.day), "qty": flt(row.qty)} for row in rows],
		"windows": {str(window): flt(windows[window]) for window in DEMAND_WINDOWS},
		"order_days": len(rows),
		"max_daily_qty": max((flt(row.qty) for row in rows), default=0.0),
		"first_demand_date": str(first_day) if first_day else None,
		"history_coverage_days": (today - first_day).days if first_day else 0,
		"source": "Sales Order (docstatus=1)",
		"from_date": str(cutoff),
		"to_date": str(nowdate()),
	}


def _stock_position(item_code: str, warehouse: str | None) -> dict[str, Any]:
	filters: dict[str, Any] = {"item_code": item_code}
	if warehouse:
		filters["warehouse"] = warehouse
	bins = frappe.get_all(
		"Bin",
		filters=filters,
		fields=["warehouse", "actual_qty", "reserved_qty", "ordered_qty", "indented_qty", "projected_qty"],
	)
	return {
		"warehouse": warehouse,
		"actual_qty": sum(flt(b.actual_qty) for b in bins),
		"reserved_qty": sum(flt(b.reserved_qty) for b in bins),
		"ordered_qty": sum(flt(b.ordered_qty) for b in bins),
		"indented_qty": sum(flt(b.indented_qty) for b in bins),
		"projected_qty": sum(flt(b.projected_qty) for b in bins),
	}


def _open_purchase_orders(item_code: str, limit: int = 10) -> list[dict[str, Any]]:
	rows = frappe.db.sql(
		"""
		select po.name, po.supplier, po.status, po.transaction_date, poi.schedule_date,
			poi.qty, poi.received_qty, poi.stock_qty, poi.rate, poi.uom
		from `tabPurchase Order Item` poi
		join `tabPurchase Order` po on po.name = poi.parent
		where poi.item_code = %(item)s
			and po.docstatus = 1
			and po.status not in ('Closed', 'Completed', 'Cancelled')
			and poi.received_qty < poi.qty
		order by poi.schedule_date asc
		limit %(limit)s
		""",
		{"item": item_code, "limit": limit},
		as_dict=True,
	)
	return [
		{
			"purchase_order": row.name,
			"supplier": row.supplier,
			"status": row.status,
			"transaction_date": str(row.transaction_date),
			"schedule_date": str(row.schedule_date) if row.schedule_date else None,
			"pending_qty": flt(row.qty) - flt(row.received_qty),
			"rate": flt(row.rate),
			"uom": row.uom,
		}
		for row in rows
	]


def _purchase_price_history(item_code: str, limit: int = 6) -> dict[str, Any]:
	"""Recent purchase lots (submitted POs) for lot-level cost context."""
	rows = frappe.db.sql(
		"""
		select po.name, po.supplier, po.transaction_date, poi.rate, poi.qty, poi.uom
		from `tabPurchase Order Item` poi
		join `tabPurchase Order` po on po.name = poi.parent
		where poi.item_code = %(item)s and po.docstatus = 1
		order by po.transaction_date desc, po.creation desc
		limit %(limit)s
		""",
		{"item": item_code, "limit": limit},
		as_dict=True,
	)
	lots = [
		{
			"purchase_order": row.name,
			"supplier": row.supplier,
			"date": str(row.transaction_date),
			"rate": flt(row.rate),
			"qty": flt(row.qty),
			"uom": row.uom,
		}
		for row in rows
	]
	last_rate = lots[0]["rate"] if lots else 0.0
	previous_rate = lots[1]["rate"] if len(lots) > 1 else 0.0
	variance_percent = (
		round((last_rate - previous_rate) / previous_rate * 100, 2) if previous_rate else 0.0
	)
	price_list_rate = flt(
		frappe.db.get_value(
			"Item Price",
			{"item_code": item_code, "buying": 1},
			"price_list_rate",
			order_by="valid_from desc, modified desc",
		)
	)
	return {
		"lots": lots,
		"last_purchase_rate": last_rate,
		"previous_purchase_rate": previous_rate,
		"price_variance_percent": variance_percent,
		"buying_price_list_rate": price_list_rate,
	}


def _supplier_context(item_code: str, settings: dict) -> dict[str, Any]:
	item = frappe.db.get_value(
		"Item",
		item_code,
		["lead_time_days", "safety_stock", "min_order_qty"],
		as_dict=True,
	) or frappe._dict()
	default_supplier = frappe.db.get_value(
		"Item Default", {"parent": item_code, "parenttype": "Item"}, "default_supplier"
	)
	suppliers = frappe.get_all(
		"Item Supplier",
		filters={"parent": item_code, "parenttype": "Item"},
		fields=["supplier", "supplier_part_no"],
		limit=10,
	)
	supplier_names = [row.supplier for row in suppliers]
	if default_supplier and default_supplier not in supplier_names:
		supplier_names.insert(0, default_supplier)
	lead_time = cint(item.lead_time_days) or 0
	return {
		"default_supplier": default_supplier,
		"suppliers": supplier_names,
		"lead_time_days": lead_time or None,
		"lead_time_source": "Item.lead_time_days" if lead_time else "settings default",
		"effective_lead_time_days": lead_time or settings["default_lead_time_days"],
		"item_safety_stock": flt(item.safety_stock),
		"min_order_qty": flt(item.min_order_qty),
	}


def collect_inputs(
	item_code: str, warehouse: str | None = None, settings: dict | None = None
) -> dict[str, Any]:
	"""Read every ERP signal formula v1 needs. Raises for unknown items."""
	settings = settings or get_settings()
	item = frappe.db.get_value(
		"Item",
		item_code,
		["name", "item_name", "stock_uom", "disabled", "is_purchase_item", "item_group", "end_of_life"],
		as_dict=True,
	)
	if not item:
		frappe.throw(frappe._("Unknown item: {0}").format(item_code))
	order_multiple = 0.0
	if frappe.get_meta("Item").has_field("custom_order_multiple"):
		order_multiple = flt(frappe.db.get_value("Item", item_code, "custom_order_multiple"))
	last_movement = frappe.db.get_value(
		"Stock Ledger Entry",
		{"item_code": item_code, "is_cancelled": 0},
		"posting_date",
		order_by="posting_date desc",
	)
	return {
		"item_code": item.name,
		"item_name": item.item_name,
		"stock_uom": item.stock_uom,
		"item_group": item.item_group,
		"disabled": cint(item.disabled),
		"is_purchase_item": cint(item.is_purchase_item),
		"end_of_life": str(item.end_of_life) if item.end_of_life else None,
		"order_multiple": order_multiple,
		"last_movement_date": str(last_movement) if last_movement else None,
		"demand": _demand_history(item_code, settings["history_window_days"]),
		"stock": _stock_position(item_code, warehouse),
		"open_purchase_orders": _open_purchase_orders(item_code),
		"prices": _purchase_price_history(item_code),
		"supplier": _supplier_context(item_code, settings),
		"as_of": str(now_datetime()),
	}


def _round_up_to(value: float, step: float) -> float:
	if step <= 0 or value <= 0:
		return max(0.0, value)
	return math.ceil(round(value / step, 6)) * step


def compute_forecast(inputs: dict, settings: dict, horizon_days: int | None = None) -> dict[str, Any]:
	"""Pure, deterministic formula v1. Same inputs always give the same result."""
	horizon = max(7, cint(horizon_days) or settings["horizon_days"])
	warnings: list[str] = []
	penalties: list[str] = []

	demand = inputs["demand"]
	windows = {int(k): flt(v) for k, v in demand["windows"].items()}
	coverage = cint(demand.get("history_coverage_days"))

	if windows.get(90, 0) <= 0 and not demand.get("daily"):
		warnings.append("ไม่มีประวัติการขายในช่วง 90 วัน — ค่าพยากรณ์อาจไม่น่าเชื่อถือ")
		penalties.append("no_history")
	elif coverage < settings["minimum_data_days"]:
		warnings.append(
			f"ประวัติการขายมีเพียง {coverage} วัน (ขั้นต่ำ {settings['minimum_data_days']} วัน)"
		)
		penalties.append("short_history")

	# Weighted average daily demand, renormalised over covered windows.
	usable = {w: WINDOW_WEIGHTS[w] for w in DEMAND_WINDOWS if coverage >= min(w, 30) or windows.get(w)}
	if not usable:
		usable = {30: 1.0}
	weight_total = sum(usable.values())
	average_daily_demand = sum(
		WINDOW_WEIGHTS[w] * (windows.get(w, 0.0) / w) for w in usable
	) / weight_total if weight_total else 0.0
	average_daily_demand = round(average_daily_demand, 4)

	# Spike detection compares a single day against the average *order-day*
	# size, not the calendar-daily average — wholesale customers ordering big
	# lots once a week is normal, one order 3x the usual lot is not.
	max_daily = flt(demand.get("max_daily_qty"))
	order_days = cint(demand.get("order_days"))
	total_window = windows.get(max(DEMAND_WINDOWS), 0.0)
	average_order_day = (total_window / order_days) if order_days else 0.0
	if order_days >= 3 and average_order_day > 0 and max_daily > 3 * average_order_day:
		warnings.append(
			f"พบ demand spike ผิดปกติ (สูงสุด {max_daily:g}/วัน เทียบขนาดออเดอร์เฉลี่ย {average_order_day:.1f})"
		)
		penalties.append("demand_spike")

	if inputs.get("disabled"):
		warnings.append("สินค้าถูกปิดใช้งาน (disabled) — ไม่ควรสั่งซื้อเพิ่ม")
		penalties.append("item_disabled")
	if inputs.get("end_of_life") and str(inputs["end_of_life"]) <= nowdate():
		warnings.append("สินค้าเลย End of Life แล้ว")
		penalties.append("item_disabled")
	if not inputs.get("is_purchase_item"):
		warnings.append("สินค้านี้ไม่ได้ตั้งค่าเป็น Purchase Item")

	supplier = inputs["supplier"]
	if not supplier.get("suppliers"):
		warnings.append("ยังไม่มี supplier ผูกกับสินค้า — กรุณาระบุ supplier ก่อนสั่งซื้อ")
		penalties.append("no_supplier")
	if not supplier.get("lead_time_days"):
		warnings.append(
			f"ไม่พบ lead time ของสินค้า ใช้ค่าเริ่มต้น {settings['default_lead_time_days']} วัน"
		)
		penalties.append("no_lead_time")

	prices = inputs["prices"]
	if not prices.get("last_purchase_rate") and not prices.get("buying_price_list_rate"):
		warnings.append("ไม่พบราคาซื้อ (ไม่มี PO เดิมและไม่มี buying price list)")
		penalties.append("no_purchase_price")
	variance = flt(prices.get("price_variance_percent"))
	if abs(variance) > settings["maximum_price_variance_percent"]:
		warnings.append(f"ราคาซื้อรอบล่าสุดเปลี่ยน {variance:+.2f}% จากรอบก่อน")

	stock = inputs["stock"]
	actual = flt(stock.get("actual_qty"))
	reserved = flt(stock.get("reserved_qty"))
	if actual < 0:
		warnings.append(f"สต๊อกติดลบ ({actual:g}) — ข้อมูล ERP อาจไม่ตรงกับของจริง")
		penalties.append("negative_stock")
	if inputs.get("last_movement_date"):
		stale_cutoff = add_days(nowdate(), -settings["history_window_days"])
		if str(inputs["last_movement_date"]) < str(stale_cutoff):
			warnings.append("ไม่มีความเคลื่อนไหวสต๊อกในช่วง history window — ข้อมูลอาจล้าสมัย")
			penalties.append("stale_data")

	incoming = sum(flt(row["pending_qty"]) for row in inputs["open_purchase_orders"])
	if incoming > 0:
		warnings.append(f"มี PO ค้างรับอยู่ {incoming:g} {inputs.get('stock_uom') or ''}".strip())

	lead_time_days = supplier["effective_lead_time_days"]
	lead_time_demand = round(average_daily_demand * lead_time_days, 4)
	safety_stock = round(
		max(flt(supplier.get("item_safety_stock")), average_daily_demand * settings["safety_stock_days"]), 4
	)
	reorder_point = round(lead_time_demand + safety_stock, 4)
	projected_available = round(actual + incoming - reserved, 4)
	target_stock = round(average_daily_demand * horizon + safety_stock, 4)

	raw_suggested = max(0.0, target_stock - projected_available)
	moq = flt(supplier.get("min_order_qty"))
	order_multiple = flt(inputs.get("order_multiple"))
	suggested_qty = raw_suggested
	if suggested_qty > 0:
		if moq > 0 and suggested_qty < moq:
			suggested_qty = moq
			warnings.append(f"ปรับจำนวนขึ้นตาม MOQ {moq:g}")
		if order_multiple > 0:
			rounded = _round_up_to(suggested_qty, order_multiple)
			if rounded != suggested_qty:
				warnings.append(f"ปัดจำนวนขึ้นตาม order multiple {order_multiple:g}")
			suggested_qty = rounded
	suggested_qty = round(suggested_qty, 4)
	if inputs.get("disabled") or (
		inputs.get("end_of_life") and str(inputs["end_of_life"]) <= nowdate()
	):
		suggested_qty = 0.0

	days_of_supply = (
		round(projected_available / average_daily_demand, 1) if average_daily_demand > 0 else None
	)
	on_hand_available = actual - reserved
	stockout_date = (
		str(add_days(nowdate(), int(on_hand_available / average_daily_demand)))
		if average_daily_demand > 0 and on_hand_available > 0
		else (nowdate() if average_daily_demand > 0 else None)
	)

	if average_daily_demand <= 0:
		movement_class = "no-movement"
	elif days_of_supply is not None and days_of_supply <= horizon:
		movement_class = "fast-moving"
	elif days_of_supply is not None and days_of_supply > 2 * horizon:
		movement_class = "slow-moving"
	else:
		movement_class = "normal"
	if movement_class == "slow-moving" and raw_suggested <= 0:
		warnings.append("สินค้าหมุนช้าและสต๊อกยังพอ — ไม่จำเป็นต้องสั่งซื้อ")

	stockout_risk = "high" if (
		average_daily_demand > 0 and projected_available < reorder_point
	) else ("medium" if average_daily_demand > 0 and days_of_supply is not None and days_of_supply < 2 * horizon else "low")

	score = 1.0
	for key in set(penalties):
		score -= QUALITY_PENALTIES.get(key, 0.0)
	data_quality_score = round(min(1.0, max(0.0, score)), 2)

	return {
		"formula_version": FORMULA_VERSION,
		"item_code": inputs["item_code"],
		"item_name": inputs.get("item_name"),
		"stock_uom": inputs.get("stock_uom"),
		"warehouse": stock.get("warehouse"),
		"forecast_date": nowdate(),
		"horizon_days": horizon,
		"demand_30": windows.get(30, 0.0),
		"demand_60": windows.get(60, 0.0),
		"demand_90": windows.get(90, 0.0),
		"average_daily_demand": average_daily_demand,
		"lead_time_days": lead_time_days,
		"lead_time_source": supplier.get("lead_time_source"),
		"lead_time_demand": lead_time_demand,
		"safety_stock": safety_stock,
		"reorder_point": reorder_point,
		"actual_qty": actual,
		"reserved_qty": reserved,
		"incoming_qty": round(incoming, 4),
		"projected_available": projected_available,
		"target_stock": target_stock,
		"suggested_qty": suggested_qty,
		"days_of_supply": days_of_supply,
		"stockout_date": stockout_date,
		"stockout_risk": stockout_risk,
		"movement_class": movement_class,
		"default_supplier": supplier.get("default_supplier"),
		"suppliers": supplier.get("suppliers") or [],
		"min_order_qty": moq,
		"order_multiple": order_multiple,
		"last_purchase_rate": flt(prices.get("last_purchase_rate")),
		"previous_purchase_rate": flt(prices.get("previous_purchase_rate")),
		"price_variance_percent": variance,
		"buying_price_list_rate": flt(prices.get("buying_price_list_rate")),
		"recent_purchase_lots": prices.get("lots") or [],
		"open_purchase_orders": inputs["open_purchase_orders"],
		"data_quality_score": data_quality_score,
		"data_quality_penalties": sorted(set(penalties)),
		"warnings": warnings,
		"assumptions": {
			"demand_source": demand.get("source"),
			"history_from": demand.get("from_date"),
			"history_to": demand.get("to_date"),
			"history_coverage_days": coverage,
			"window_weights": {str(k): v for k, v in WINDOW_WEIGHTS.items()},
			"safety_stock_days": settings["safety_stock_days"],
			"minimum_data_days": settings["minimum_data_days"],
			"default_lead_time_days": settings["default_lead_time_days"],
		},
	}


def forecast_item(
	item_code: str,
	warehouse: str | None = None,
	horizon_days: int | None = None,
	settings: dict | None = None,
	save_snapshot: bool = False,
) -> dict[str, Any]:
	settings = settings or get_settings()
	warehouse = warehouse or default_buying_warehouse()
	inputs = collect_inputs(item_code, warehouse, settings)
	result = compute_forecast(inputs, settings, horizon_days)
	if save_snapshot:
		result["snapshot"] = store_snapshot(result)
	return result


def _require_forecast_role() -> None:
	"""Allow operational users to create derived snapshots, never buying documents."""
	user = frappe.session.user
	allowed = {"System Manager", "Purchase Manager", "Purchase User", "Stock Manager"}
	if user != "Administrator" and not allowed.intersection(frappe.get_roles(user)):
		frappe.throw(
			frappe._("You do not have permission to generate procurement forecasts"),
			frappe.PermissionError,
		)


@frappe.whitelist()
def generate_forecasts(
	item_codes: str | list[str] | None = None,
	warehouse: str | None = None,
	horizon_days: int | None = None,
	limit: int = 50,
) -> dict[str, Any]:
	"""Generate immutable forecast snapshots from live ERP data only.

	This manual endpoint deliberately does not create recommendations, Material
	Requests, Purchase Orders, or any submitted document. Those remain behind the
	chat preview/confirmation workflow.
	"""
	_require_forecast_role()
	settings = get_settings()
	if not settings["enabled"]:
		frappe.throw(frappe._("Enable the Procurement Copilot first"))

	if isinstance(item_codes, str):
		try:
			parsed = json.loads(item_codes)
		except (TypeError, json.JSONDecodeError):
			parsed = [part.strip() for part in item_codes.split(",") if part.strip()]
		item_codes = parsed if isinstance(parsed, list) else [parsed]
	item_codes = list(dict.fromkeys(str(item).strip() for item in (item_codes or []) if str(item).strip()))
	limit = min(max(cint(limit or 50), 1), 100)
	if not item_codes:
		item_codes = candidate_items(settings, limit=limit)
	else:
		item_codes = item_codes[:limit]

	warehouse = (warehouse or "").strip() or default_buying_warehouse()
	if warehouse and not frappe.db.exists(
		"Warehouse", {"name": warehouse, "is_group": 0, "disabled": 0}
	):
		frappe.throw(frappe._("Select an active, non-group Warehouse"))

	generated: list[dict[str, Any]] = []
	failed: list[dict[str, str]] = []
	for item_code in item_codes:
		if not frappe.db.exists(
			"Item", {"name": item_code, "disabled": 0, "is_stock_item": 1, "is_purchase_item": 1}
		):
			failed.append({"item_code": item_code, "error": "Item is not an active stock purchase item"})
			continue
		try:
			result = forecast_item(item_code, warehouse, horizon_days, settings, save_snapshot=True)
			generated.append(
				{
					"item_code": item_code,
					"snapshot": result["snapshot"],
					"suggested_qty": result["suggested_qty"],
					"stockout_risk": result["stockout_risk"],
				}
			)
		except Exception as exc:
			frappe.log_error(
				title=f"Manual procurement forecast {item_code}", message=frappe.get_traceback()
			)
			failed.append({"item_code": item_code, "error": str(exc)[:240]})

	return {
		"generated": len(generated),
		"failed": len(failed),
		"forecasts": generated,
		"errors": failed,
		"warehouse": warehouse,
		"horizon_days": cint(horizon_days) or settings["horizon_days"],
	}


def store_snapshot(result: dict) -> str:
	"""Persist an immutable forecast snapshot and return its name."""
	company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	doc = frappe.get_doc(
		{
			"doctype": "NextGen Procurement Forecast",
			"item": result["item_code"],
			"item_name": result.get("item_name"),
			"company": company,
			"warehouse": result.get("warehouse"),
			"forecast_date": result.get("forecast_date") or nowdate(),
			"horizon_days": result.get("horizon_days"),
			"formula_version": result.get("formula_version") or FORMULA_VERSION,
			"average_daily_demand": flt(result.get("average_daily_demand")),
			"suggested_qty": flt(result.get("suggested_qty")),
			"reorder_point": flt(result.get("reorder_point")),
			"projected_available": flt(result.get("projected_available")),
			"stockout_risk": result.get("stockout_risk"),
			"movement_class": result.get("movement_class"),
			"data_quality_score": flt(result.get("data_quality_score")),
			"metrics": json.dumps(result, ensure_ascii=False, default=str),
			"warnings": json.dumps(result.get("warnings") or [], ensure_ascii=False),
		}
	).insert(ignore_permissions=True)
	return doc.name


def candidate_items(settings: dict, limit: int = 500) -> list[str]:
	"""Items the scheduled forecast should analyze."""
	filters: dict[str, Any] = {"disabled": 0, "is_stock_item": 1, "is_purchase_item": 1}
	if settings["allowed_item_groups"]:
		filters["item_group"] = ["in", settings["allowed_item_groups"]]
	return frappe.get_all("Item", filters=filters, pluck="name", limit=limit, order_by="name asc")


def expire_stale_snapshot_data() -> None:
	"""Snapshots older than a year add noise without audit value."""
	cutoff = add_to_date(now_datetime(), days=-365)
	frappe.db.delete("NextGen Procurement Forecast", {"creation": ["<", cutoff]})
