import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from nextgen_erp.agent_gateway import graph as graph_utils
from nextgen_erp.agent_gateway import workflows


class NextGenAgentWorkflow(Document):
	def validate(self):
		if not self.company:
			frappe.throw(_("A workflow without a company is invalid"))
		# Validation happens here so the executor never sees a malformed or
		# cyclic definition, and so a bad graph cannot be saved and scheduled.
		parsed = graph_utils.throw_on_error(self.graph_json, self.company)
		self.graph_json = json.dumps(parsed, ensure_ascii=False, default=str)
		self._validate_triggers()
		workflows.prime_schedule(self)

	def _validate_triggers(self):
		for row in self.get("triggers") or []:
			if row.trigger_type == "Schedule" and not (row.cron or "").strip():
				frappe.throw(_("A schedule trigger needs a cron expression"))
			if row.trigger_type == "Document Event":
				if not row.trigger_doctype:
					frappe.throw(_("A document event trigger needs a DocType"))
				if row.trigger_doctype.startswith("NextGen "):
					# A workflow that reacts to the gateway's own records would
					# be able to retrigger itself.
					frappe.throw(
						_("A workflow cannot be triggered by {0}").format(row.trigger_doctype)
					)
				if row.condition:
					try:
						parsed = json.loads(row.condition)
					except (TypeError, ValueError):
						frappe.throw(_("A trigger condition must be a JSON object"))
					if not isinstance(parsed, dict):
						frappe.throw(_("A trigger condition must be a JSON object"))

	def before_save(self):
		if not self.is_new():
			previous = self.get_doc_before_save()
			if previous and previous.graph_json != self.graph_json:
				self.graph_version = cint(self.graph_version) + 1
		self.graph_version = max(1, cint(self.graph_version))

	def on_update(self):
		# Document triggers are read on every ERP write, so the index is cached
		# and has to be dropped whenever the definitions change.
		workflows.clear_trigger_cache()

	def on_trash(self):
		workflows.clear_trigger_cache()

	def graph(self) -> dict:
		return graph_utils.parse(self.graph_json)

	def ordered_nodes(self) -> list[dict]:
		return graph_utils.topological_order(self.graph())
