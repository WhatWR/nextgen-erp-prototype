"""Outbound HTTP connections a workflow may use.

A ``source.api`` node names a connection; it never carries a URL, a header or a
credential of its own. That keeps the reachable surface a deliberate,
company-scoped configuration decision rather than something a graph — or
anyone who can edit one — can widen by typing a different address.
"""

from fnmatch import fnmatch
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

MAX_TIMEOUT_SECONDS = 30
MAX_RESPONSE_KB = 4096


class NextGenAgentConnection(Document):
	def validate(self):
		self.base_url = (self.base_url or "").strip().rstrip("/")
		parsed = urlparse(self.base_url)
		if parsed.scheme not in ("http", "https") or not parsed.netloc:
			frappe.throw(_("Base URL must be an absolute http or https URL"))
		if parsed.scheme == "http" and not frappe.conf.get("developer_mode"):
			# Credentials and ERP data must not travel in clear text.
			frappe.throw(_("Base URL must use https"))
		if parsed.query or parsed.fragment:
			frappe.throw(_("Base URL may not carry a query string or fragment"))
		if not self.allowed_path_patterns():
			frappe.throw(_("List at least one allowed path"))
		if self.auth_type == "Header" and not (self.header_name or "").strip():
			frappe.throw(_("A header name is required for header authentication"))
		self.timeout_seconds = max(1, min(cint(self.timeout_seconds) or 10, MAX_TIMEOUT_SECONDS))
		self.max_response_kb = max(1, min(cint(self.max_response_kb) or 512, MAX_RESPONSE_KB))

	def allowed_path_patterns(self) -> list[str]:
		return [line.strip() for line in (self.allowed_paths or "").splitlines() if line.strip()]

	def allows(self, path: str) -> bool:
		path = "/" + (path or "").lstrip("/")
		return any(fnmatch(path, pattern) for pattern in self.allowed_path_patterns())
