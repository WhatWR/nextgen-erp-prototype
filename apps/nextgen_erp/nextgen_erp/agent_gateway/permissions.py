"""Identity and access at the gateway boundary.

Frappe derives the requester, the execution identity, the company and the tool
allowlist from the persisted run. Anything the client or the model supplies for
those is rejected rather than merged.

Two identities exist and they are never the same principal:

* the **requester** — the human whose access authorises the work, revalidated
  live on every tool call and required again for approval;
* the **runtime service identity** — a dedicated, restricted, non-Desk user used
  only for transport from the runtime back into Frappe.
"""

from __future__ import annotations

import frappe
from frappe import _

RUNTIME_ROLE = "NextGen Agent Runtime"


def current_user() -> str:
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	return user


def require_human() -> str:
	"""Reject Guest and the runtime service identity.

	Approval and execution require an authenticated human Frappe session; a
	service identity may never stand in for a reviewer.
	"""
	user = current_user()
	if is_service_identity(user):
		frappe.throw(
			_("The runtime service identity cannot perform human approvals"), frappe.PermissionError
		)
	return user


def require_service_identity() -> str:
	"""Only the restricted runtime service user may call the gateway API."""
	user = current_user()
	if user == "Administrator":
		# Autonomous work never runs as Administrator, even in development.
		frappe.throw(
			_("The Agent Runtime must not authenticate as Administrator"), frappe.PermissionError
		)
	if not is_service_identity(user):
		frappe.throw(
			_("This endpoint is reserved for the NextGen Agent Runtime service identity"),
			frappe.PermissionError,
		)
	return user


def is_service_identity(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	if user == "Administrator":
		# Administrator implicitly holds every role, including the runtime role.
		# It is a human superuser, never the service identity — without this,
		# Administrator could never approve a proposal.
		return False
	return RUNTIME_ROLE in frappe.get_roles(user)


def assert_run_execution_identity(run, service_user: str) -> None:
	"""The caller must be the execution identity the run was created with.

	Interactive runs may fall back to the requester as execution identity while
	no company service user is configured; those are not restricted here. A run
	that does name a service identity can only be driven by that identity.
	"""
	expected = run.execution_user
	if not expected or expected == service_user:
		return
	if is_service_identity(expected):
		frappe.throw(
			_("This run belongs to a different runtime service identity"), frappe.PermissionError
		)


def assert_agent_access(agent_key: str, user: str) -> None:
	"""Re-check the requester's live roles for the run's agent."""
	from nextgen_erp import agents

	agents.require_agent_access(agent_key, user)


def assert_can_read(doctype: str, name: str, user: str | None = None) -> None:
	if not frappe.has_permission(doctype, "read", doc=name, user=user or frappe.session.user):
		frappe.throw(
			_("You do not have permission to read {0} {1}").format(doctype, name),
			frappe.PermissionError,
		)


def ensure_runtime_role() -> None:
	"""Create the restricted role used by the runtime service user."""
	if frappe.db.exists("Role", RUNTIME_ROLE):
		return
	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": RUNTIME_ROLE,
			"desk_access": 0,
			"is_custom": 1,
		}
	).insert(ignore_permissions=True)
