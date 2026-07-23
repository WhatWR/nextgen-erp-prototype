import frappe
from frappe.utils import get_datetime, now_datetime

from nextgen_erp.webshop import _token_hash


def get_context(context):
	token = (frappe.form_dict.get("token") or "").strip()
	session = None
	if token:
		session = frappe.db.get_value(
			"NextGen Shop Session",
			{"token_hash": _token_hash(token)},
			["expires_at", "consumed_at"],
			as_dict=True,
		)

	context.no_cache = 1
	context.title = "เปิดแคตตาล็อกสินค้า"
	context.token = token
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.link_is_valid = bool(
		session
		and not session.consumed_at
		and get_datetime(session.expires_at) >= now_datetime()
	)
	return context
