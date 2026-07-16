from frappe.model.document import Document


class NextGenProcurementForecast(Document):
	"""Immutable forecast snapshot. Rows are created by the forecast engine and
	never edited afterwards."""

	def validate(self):
		if not self.is_new():
			import frappe
			from frappe import _

			frappe.throw(_("Forecast snapshots are immutable"))
