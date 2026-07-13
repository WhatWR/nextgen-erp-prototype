"""Administrative provisioning helpers for a least-privilege integration user."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

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


def configure_local_environment(env_path: str) -> dict:
	"""Generate local-only API credentials and write an ignored, mode-0600 env file.

	This is an explicit Bench administrator command for local development. It is
	not whitelisted and must not be used as a production secret-management flow.
	The return value deliberately excludes all generated credentials.
	"""
	from frappe.core.doctype.user.user import generate_keys

	if frappe.session.user != "Administrator":
		frappe.throw("Run this provisioning helper as Administrator", frappe.PermissionError)
	path = Path(env_path).expanduser().resolve()
	service = create_service_user()
	keys = generate_keys(service["user"])
	intake_key = frappe.generate_hash(length=32)
	preferred_warehouse = "Stores - NG"
	warehouse = (
		preferred_warehouse
		if frappe.db.exists("Warehouse", preferred_warehouse)
		else frappe.db.get_value("Warehouse", {"is_group": 0, "disabled": 0}, "name") or ""
	)

	settings = frappe.get_single("NextGen Automation Settings")
	settings.external_service_url = "http://127.0.0.1:8200"
	settings.external_service_api_key = intake_key
	settings.save()

	values = {
		"ERPNEXT_URL": "http://127.0.0.1:8000",
		"ERPNEXT_API_KEY": keys["api_key"],
		"ERPNEXT_API_SECRET": keys["api_secret"],
		"ERPNEXT_WAREHOUSE": warehouse,
		"ORDER_INTAKE_API_KEY": intake_key,
		"ORDER_BACKEND": "erpnext",
		"LINE_CONFIG_SOURCE": "erpnext",
		"LINE_WORKFLOW_BACKEND": "erpnext",
		"ERPCLAW_BACKEND": "erpnext",
	}
	path.write_text(
		"# Generated for local development. Never commit this file.\n"
		+ "".join(f"{key}={shlex.quote(str(value))}\n" for key, value in values.items()),
		encoding="utf-8",
	)
	os.chmod(path, 0o600)
	frappe.db.commit()
	return {
		"configured": True,
		"user": service["user"],
		"env_path": str(path),
		"warehouse": warehouse,
		"secrets_returned": False,
	}
