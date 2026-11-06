from frappe.model.document import Document


class NextGenLINESimMessage(Document):
	"""One message in a simulated LINE conversation.

	Outbound rows exist because ``nextgen_erp.line.push_text`` runs in a queue
	worker: the simulator page cannot read anything the pushing process only
	held in memory.
	"""

	pass
