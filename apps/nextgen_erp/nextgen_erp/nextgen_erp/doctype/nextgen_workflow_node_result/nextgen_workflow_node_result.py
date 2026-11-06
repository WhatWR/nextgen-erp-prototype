import json

from frappe.model.document import Document


class NextGenWorkflowNodeResult(Document):
	def envelope(self) -> dict:
		"""The stored output, as a downstream node reads it."""
		try:
			value = json.loads(self.output_json or "{}")
		except (TypeError, ValueError):
			return {}
		return value if isinstance(value, dict) else {}
