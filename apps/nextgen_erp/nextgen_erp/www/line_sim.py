"""Context for the LINE simulator demo page (``/line-sim``)."""

from __future__ import annotations

import frappe

from nextgen_erp import line_simulator

no_cache = 1

# Matches the catalog seeded by ``nextgen_erp.demo.run`` so nothing has to be
# typed live during a demo.
PRESETS = [
	{"label": "ถามราคา", "text": "M-150 ราคาเท่าไหร่"},
	{"label": "สั่งสินค้า", "text": "น้ำแดง 2 ลัง กับมาม่าต้มยำ 3 แพ็ก"},
	{"label": "ยืนยัน", "text": "ยืนยัน"},
]


def get_context(context):
	# guard() raises PermissionError for Guest, for a caller without the role,
	# and on any site where the simulator was not switched on.
	line_id = line_simulator.guard()
	customer = frappe.db.get_value("LINE Customer Map", line_id, "customer")

	context.no_cache = 1
	context.line_id = line_id
	context.delivery_line_id = line_simulator.DELIVERY_LINE_ID
	context.customer = customer
	context.customer_name = (
		frappe.db.get_value("Customer", customer, "customer_name") if customer else None
	)
	context.presets = PRESETS
	context.user_name = frappe.session.user
	return context
