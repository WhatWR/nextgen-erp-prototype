import frappe
from frappe import _
from frappe.model.document import Document

from nextgen_erp.agent_gateway import permissions


class NextGenApprovalDecision(Document):
	def validate(self):
		if not self.reviewer or self.reviewer == "Guest":
			frappe.throw(_("An approval decision requires an authenticated reviewer"))
		proposal = frappe.db.get_value(
			"NextGen Action Proposal",
			self.proposal,
			["company", "execution_user"],
			as_dict=True,
		)
		if not proposal:
			frappe.throw(_("Approval decision must reference an existing proposal"))
		if not self.company:
			self.company = proposal.company
		if self.company != proposal.company:
			frappe.throw(_("An approval decision cannot change the proposal's company"))
		if proposal.execution_user == self.reviewer and permissions.is_service_identity(self.reviewer):
			# A *service* identity can never review its own proposal. A human
			# reviewing their own request is the normal flow: preparing and
			# approving are two deliberate steps by the same person, which is
			# exactly what the preview card is for.
			frappe.throw(_("The runtime service identity cannot approve its own proposal"))
