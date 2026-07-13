# Copyright (c) 2026, NextGen and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIOrderIntake(Document):
	def validate(self):
		if not self.is_new() and self.has_value_changed("status") and not self.flags.get(
			"nextgen_transition"
		):
			frappe.throw(_("Use the NextGen workflow actions to change intake status"))
		for row in self.items:
			if row.qty <= 0:
				frappe.throw(_("Every order quantity must be greater than zero"))
