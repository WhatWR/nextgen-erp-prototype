import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from nextgen_erp.agent_gateway import graph as graph_utils


class NextGenAgentWorkflow(Document):
	def validate(self):
		if not self.company:
			frappe.throw(_("A workflow without a company is invalid"))
		# Validation happens here so the executor never sees a malformed or
		# cyclic definition, and so a bad graph cannot be saved and scheduled.
		parsed = graph_utils.throw_on_error(self.graph_json, self.company)
		self.graph_json = json.dumps(parsed, ensure_ascii=False, default=str)

	def before_save(self):
		if not self.is_new():
			previous = self.get_doc_before_save()
			if previous and previous.graph_json != self.graph_json:
				self.graph_version = cint(self.graph_version) + 1
		self.graph_version = max(1, cint(self.graph_version))

	def graph(self) -> dict:
		return graph_utils.parse(self.graph_json)

	def ordered_nodes(self) -> list[dict]:
		return graph_utils.topological_order(self.graph())
