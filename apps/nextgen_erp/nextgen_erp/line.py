"""LINE delivery owned by ERPNext so channel tokens never leave the site."""

from __future__ import annotations

import json

import frappe
from frappe.integrations.utils import make_post_request


LINE_API_BASE = "https://api.line.me"
LINE_DATA_API_BASE = "https://api-data.line.me"


def api_base() -> str:
	"""LINE Messaging API host; site config ``nextgen_line_api_base`` overrides it.

	The override exists for the local sandbox simulator only — never set it on
	a production site.
	"""
	return str(frappe.conf.get("nextgen_line_api_base") or LINE_API_BASE).rstrip("/")


def data_api_base() -> str:
	"""LINE content-download host; the same sandbox override redirects it."""
	return str(frappe.conf.get("nextgen_line_api_base") or LINE_DATA_API_BASE).rstrip("/")


def push_text(recipient: str, text: str, image_url: str | None = None) -> dict:
	settings = frappe.get_single("LINE Channel Settings")
	if not settings.enabled:
		return {"sent": False, "reason": "line_disabled"}
	token = settings.get_password("channel_access_token", raise_exception=False) or ""
	if not recipient or not token:
		frappe.log_error("LINE recipient or access token is missing", "NextGen LINE delivery")
		return {"sent": False, "reason": "missing_recipient_or_token"}
	messages = [{"type": "text", "text": text[:5000]}]
	if image_url:
		messages.append(
			{
				"type": "image",
				"originalContentUrl": image_url,
				"previewImageUrl": image_url,
			}
		)
	make_post_request(
		f"{api_base()}/v2/bot/message/push",
		headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
		data=json.dumps(
			{"to": recipient, "messages": messages},
			ensure_ascii=False,
		),
	)
	return {"sent": True}
