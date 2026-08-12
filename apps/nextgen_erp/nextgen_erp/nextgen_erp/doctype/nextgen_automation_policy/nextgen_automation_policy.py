import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

LINE_FIELDS = (
	"allowed_warehouses",
	"allowed_item_groups",
	"item_allowlist",
	"supplier_allowlist",
	"writable_doctypes",
)


def parse_lines(value) -> list[str]:
	return [line.strip() for line in str(value or "").splitlines() if line.strip()]


class NextGenAutomationPolicy(Document):
	def validate(self):
		self._normalize_lines()
		self._validate_service_user()
		self._validate_warehouses()
		self._validate_writable_doctypes()
		self.max_tool_calls = max(1, min(cint(self.max_tool_calls or 6), 12))
		self.proposal_expiry_minutes = max(1, min(cint(self.proposal_expiry_minutes or 15), 1440))
		self.minimum_data_quality_score = min(max(flt(self.minimum_data_quality_score), 0), 1)

	def _normalize_lines(self):
		for field in LINE_FIELDS:
			lines = list(dict.fromkeys(parse_lines(self.get(field))))
			self.set(field, "\n".join(lines))

	def _validate_writable_doctypes(self):
		"""Configuration narrows the code allowlist and can never extend it."""
		from nextgen_erp.domain_tools.documents import WRITABLE_DOCTYPES, is_denied

		for doctype in parse_lines(self.writable_doctypes):
			if is_denied(doctype) or doctype not in WRITABLE_DOCTYPES:
				frappe.throw(
					_(
						"{0} can never be written by the assistant. Allowed document types are: {1}"
					).format(doctype, ", ".join(sorted(WRITABLE_DOCTYPES)))
				)

	def _validate_service_user(self):
		if not self.service_user:
			return
		if self.service_user == "Administrator":
			frappe.throw(_("The runtime service user must not be Administrator"))
		user = frappe.db.get_value(
			"User", self.service_user, ["enabled", "user_type"], as_dict=True
		)
		if not user or not cint(user.enabled):
			frappe.throw(_("The runtime service user must be an enabled User"))

	def _validate_warehouses(self):
		for warehouse in parse_lines(self.allowed_warehouses):
			row = frappe.db.get_value(
				"Warehouse", warehouse, ["company", "is_group", "disabled"], as_dict=True
			)
			if not row:
				frappe.throw(_("Unknown warehouse in this policy: {0}").format(warehouse))
			if row.company != self.company:
				# Company isolation starts at the policy: a warehouse from
				# another company can never enter this company's scope.
				frappe.throw(
					_("Warehouse {0} belongs to another company").format(warehouse)
				)
			if cint(row.is_group) or cint(row.disabled):
				frappe.throw(
					_("Warehouse {0} must be a non-group, enabled warehouse").format(warehouse)
				)

	def as_snapshot(self) -> dict:
		"""Policy values recorded on every proposal created under this policy."""
		return {
			"company": self.company,
			"enabled": bool(cint(self.enabled)),
			"runtime_enabled": bool(cint(self.runtime_enabled)),
			"default_mode": self.default_mode or "Shadow",
			"max_tool_calls": cint(self.max_tool_calls),
			"proposal_expiry_minutes": cint(self.proposal_expiry_minutes),
			"minimum_data_quality_score": flt(self.minimum_data_quality_score),
			"maximum_proposal_value": flt(self.maximum_proposal_value),
			"daily_auto_value_limit": flt(self.daily_auto_value_limit),
			"maximum_price_variance_percent": flt(self.maximum_price_variance_percent),
			"allowed_warehouses": parse_lines(self.allowed_warehouses),
			"allowed_item_groups": parse_lines(self.allowed_item_groups),
			"item_allowlist": parse_lines(self.item_allowlist),
			"supplier_allowlist": parse_lines(self.supplier_allowlist),
			"writable_doctypes": parse_lines(self.writable_doctypes),
		}
