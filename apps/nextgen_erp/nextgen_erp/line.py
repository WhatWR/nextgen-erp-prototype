"""LINE delivery owned by ERPNext so channel tokens never leave the site."""

from __future__ import annotations

import json

import frappe
from frappe.integrations.utils import make_post_request


LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def push_text(recipient: str, text: str) -> dict:
	settings = frappe.get_single("LINE Channel Settings")
	if not settings.enabled:
		return {"sent": False, "reason": "line_disabled"}
	token = settings.get_password("channel_access_token", raise_exception=False) or ""
	if not recipient or not token:
		frappe.log_error("LINE recipient or access token is missing", "NextGen LINE delivery")
		return {"sent": False, "reason": "missing_recipient_or_token"}
	make_post_request(
		LINE_PUSH_URL,
		headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
		data=json.dumps(
			{"to": recipient, "messages": [{"type": "text", "text": text[:5000]}]},
			ensure_ascii=False,
		),
	)
	return {"sent": True}
