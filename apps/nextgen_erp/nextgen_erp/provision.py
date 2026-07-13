"""Administrative provisioning helpers for a least-privilege integration user."""

from __future__ import annotations

import frappe


SERVICE_ROLES = [
	"NextGen Order Service",
	"Sales User",
	"Stock User",
	"Accounts User",
]


def create_service_user(email: str = "nextgen-order-service@local.invalid") -> dict:
	"""Create the API identity without a password or API secret.

	Generate the API key/secret interactively from the ERPNext User screen so the
	secret is shown only to the administrator and is never written to source.
	"""
	if frappe.session.user != "Administrator":
		frappe.throw("Run this provisioning helper as Administrator", frappe.PermissionError)
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
	else:
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "NextGen",
				"last_name": "Order Service",
				"enabled": 1,
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		).insert()
	existing = set(frappe.get_roles(email))
	user.add_roles(*[role for role in SERVICE_ROLES if role not in existing])
	return {"user": email, "roles": SERVICE_ROLES, "api_secret_generated": False}
