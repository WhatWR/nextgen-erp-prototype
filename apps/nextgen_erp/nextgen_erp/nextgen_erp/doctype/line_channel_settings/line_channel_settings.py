# Copyright (c) 2026, NextGen and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class LINEChannelSettings(Document):
	def validate(self):
		if not self.selling_warehouse:
			return
		warehouse = frappe.db.get_value(
			"Warehouse",
			self.selling_warehouse,
			["company", "is_group", "disabled", "warehouse_type"],
			as_dict=True,
		)
		if not warehouse or warehouse.is_group or warehouse.disabled:
			frappe.throw(_("Selling Warehouse must be an active leaf warehouse"))
		if (warehouse.warehouse_type or "").casefold() == "transit":
			frappe.throw(_("A transit warehouse cannot be used as the LINE Selling Warehouse"))
		if not self.company:
			self.company = warehouse.company
		elif warehouse.company != self.company:
			frappe.throw(_("Selling Warehouse must belong to the selected Company"))
