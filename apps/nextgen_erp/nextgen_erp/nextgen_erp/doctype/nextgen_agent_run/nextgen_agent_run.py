import json

import frappe
from frappe import _
from frappe.model.document import Document

from nextgen_erp.agent_gateway.contracts import RUN_STATUSES, TERMINAL_RUN_STATUSES

# Interactive triggers carry a human's own session, so Administrator is a
# legitimate requester there. Autonomous work must use a restricted service
# identity instead.
INTERACTIVE_TRIGGERS = ("chat", "manual")

# A run may only move forward. Frappe owns the state machine; a late or
# duplicated runtime callback can never resurrect a finished run.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
	# Waiting Approval is reachable straight from Queued for an embedded run,
	# which produces its proposal in-process and is never dispatched. Terminal
	# states stay closed, so this cannot resurrect a finished run.
	"Queued": {"Dispatched", "Running", "Waiting Approval", "Cancelled", "Failed"},
	"Dispatched": {"Running", "Queued", "Cancelled", "Failed"},
	"Running": {"Waiting Approval", "Completed", "Failed", "Cancelled"},
	"Waiting Approval": {"Completed", "Failed", "Cancelled"},
	"Completed": set(),
	"Failed": set(),
	"Cancelled": set(),
}


class NextGenAgentRun(Document):
	def validate(self):
		if self.status not in RUN_STATUSES:
			frappe.throw(_("Unknown agent run status: {0}").format(self.status))
		if not self.company:
			frappe.throw(_("An agent run without a company is invalid"))
		if self.execution_user == "Administrator" and self.trigger_type not in INTERACTIVE_TRIGGERS:
			frappe.throw(_("Autonomous agent runs must not execute as Administrator"))
		self._validate_transition()

	def allowed_tool_names(self) -> list[str]:
		"""Tool allowlist derived by Frappe. Never taken from a request payload."""
		try:
			names = json.loads(self.allowed_tools or "[]")
		except (TypeError, ValueError):
			return []
		return [str(name) for name in names] if isinstance(names, list) else []

	def input_json(self) -> dict:
		try:
			payload = json.loads(self.input_payload or "{}")
		except (TypeError, ValueError):
			return {}
		return payload if isinstance(payload, dict) else {}

	def _validate_transition(self):
		if self.is_new():
			return
		previous = self.get_doc_before_save()
		if not previous or previous.status == self.status:
			return
		if self.status not in ALLOWED_TRANSITIONS.get(previous.status, set()):
			frappe.throw(
				_("Cannot move agent run from {0} to {1}").format(previous.status, self.status)
			)

	@property
	def is_final(self) -> bool:
		return self.status in TERMINAL_RUN_STATUSES
