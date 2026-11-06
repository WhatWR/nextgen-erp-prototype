"""Whitelisted Desk API for PO-from-email automation."""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _

from nextgen_erp.purchase_email import pipeline


def _as_dict(value: Any) -> dict[str, Any]:
	if value is None:
		return {}
	if isinstance(value, dict):
		return value
	if isinstance(value, str):
		value = value.strip()
		if not value:
			return {}
		return json.loads(value)
	return dict(value)


@frappe.whitelist()
def get_overview():
	"""Dashboard payload for the PO Email Intake page."""
	return pipeline.overview()


@frappe.whitelist()
def get_connection_status():
	return pipeline.get_connection_status()


@frappe.whitelist()
def save_connection(values=None):
	"""Persist mailbox settings and refresh connection status."""
	return pipeline.save_settings(_as_dict(values))


@frappe.whitelist()
def list_rules():
	return pipeline.list_rules()


@frappe.whitelist()
def save_rule(rule=None):
	return pipeline.save_rule(_as_dict(rule))


@frappe.whitelist()
def delete_rule(name: str):
	pipeline.delete_rule(name)
	return {"ok": True}


@frappe.whitelist()
def sync_inbox(limit: int = 20):
	return pipeline.sync_inbox(limit=int(limit or 20))


@frappe.whitelist()
def list_intake(status: str | None = None, limit: int = 50):
	return pipeline.list_intake(status=status or None, limit=int(limit or 50))


@frappe.whitelist()
def decide_intake(name: str, decision: str, note: str | None = None):
	return pipeline.decide_intake(name=name, decision=decision, note=note)


@frappe.whitelist()
def export_to_purchase_order(name: str):
	return pipeline.export_to_purchase_order(name)


@frappe.whitelist(allow_guest=False)
def oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
	"""OAuth redirect landing.

	The MVP records the authorization code for an operator to exchange manually
	(or via a follow-up token exchange) — we never store client secrets in git.
	"""
	if error:
		frappe.respond_as_web_page(
			_("Mailbox connect failed"),
			_("Provider returned: {0}").format(error),
			http_status_code=400,
		)
		return

	frappe.respond_as_web_page(
		_("Mailbox authorization received"),
		_(
			"Provider: {0}. Authorization code received. Exchange it for a refresh "
			"token and paste that token into NextGen PO Email Settings → OAuth Refresh Token, "
			"then turn off Use Mock Fixtures."
		).format(state or "unknown"),
		indicator_color="green",
	)
