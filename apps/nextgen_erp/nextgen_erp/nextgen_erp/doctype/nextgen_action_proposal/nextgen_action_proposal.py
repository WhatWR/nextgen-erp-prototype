import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

from nextgen_erp.agent_gateway.contracts import PROPOSAL_STATUSES

OPEN_STATUSES = ("Pending Approval", "Approved", "Executing")


class NextGenActionProposal(Document):
	def validate(self):
		if self.status not in PROPOSAL_STATUSES:
			frappe.throw(_("Unknown action proposal status: {0}").format(self.status))
		if not self.company:
			frappe.throw(_("An action proposal without a company is invalid"))
		if not self.expires_at:
			frappe.throw(_("An action proposal must have an expiry"))
		if not self.snapshot_hash:
			frappe.throw(_("An action proposal must carry a snapshot hash"))
		run_company = frappe.db.get_value("NextGen Agent Run", self.run, "company")
		if run_company and run_company != self.company:
			frappe.throw(_("A proposal cannot belong to a different company than its run"))

	@property
	def is_expired(self) -> bool:
		return get_datetime(self.expires_at) < now_datetime()

	@property
	def is_open(self) -> bool:
		return self.status in OPEN_STATUSES and not self.is_expired
