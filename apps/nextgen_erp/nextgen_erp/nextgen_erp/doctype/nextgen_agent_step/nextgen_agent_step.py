import frappe
from frappe import _
from frappe.model.document import Document


class NextGenAgentStep(Document):
	def validate(self):
		if not self.run:
			frappe.throw(_("An agent step must belong to a run"))
		if not self.sequence or self.sequence < 1:
			frappe.throw(_("Agent step sequence must be a positive integer"))
		if not self.idempotency_key:
			frappe.throw(_("An agent step without an idempotency key is invalid"))
		if not self.correlation_id:
			self.correlation_id = frappe.db.get_value("NextGen Agent Run", self.run, "correlation_id")
