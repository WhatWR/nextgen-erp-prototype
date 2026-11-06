"""A stand-in for the LINE app itself, so the flow can be demonstrated.

Everything between the two ends already works: ``order-intake`` verifies a
signed webhook and routes it, ERPNext creates the documents and pushes replies
through :mod:`nextgen_erp.line`. Only the two ends LINE itself provides are
missing — a way to *send* a customer message and somewhere for a pushed reply to
*land*. This module supplies both without a LINE Official Account, a public
HTTPS webhook or a channel access token, while the production path in between
runs unchanged.

``SIM_PREFIX`` is the single gate. A recipient that starts with it is simulated
and never reaches api.line.me; every other recipient behaves exactly as before.

DEMO ONLY. Every entry point is refused unless the site config flag
``nextgen_line_simulator`` is on, and it must stay off on a site that a real
LINE Official Account points at.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import uuid

import frappe
from frappe import _


SIM_PREFIX = "Usim-"
DELIVERY_LINE_ID = f"{SIM_PREFIX}delivery"
DEFAULT_INTAKE_URL = "http://order-intake:8200"
DEFAULT_ROLES = ("System Manager",)
HISTORY_LIMIT = 50
MAX_SLIP_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 2000
DOCTYPE = "NextGen LINE Sim Message"


# --------------------------------------------------------------------------- #
# Identity and gating
# --------------------------------------------------------------------------- #
def enabled() -> bool:
	"""The simulator is opt-in per site and defaults to off."""
	return bool(frappe.conf.get("nextgen_line_simulator"))


def is_simulated(recipient: str | None) -> bool:
	return bool(recipient) and str(recipient).startswith(SIM_PREFIX)


def sim_line_id(user: str | None = None) -> str:
	"""One stable simulated LINE identity per user, so presenters never collide."""
	user = user or frappe.session.user
	digest = hashlib.sha256(user.encode("utf-8")).hexdigest()[:8]
	return f"{SIM_PREFIX}{digest}"


def allowed_roles() -> list[str]:
	configured = frappe.conf.get("nextgen_line_simulator_roles")
	if isinstance(configured, str):
		configured = [configured]
	return [str(role) for role in (configured or DEFAULT_ROLES) if str(role).strip()]


def guard() -> str:
	"""Refuse unless the site opted in and the caller may drive real documents.

	The simulator creates genuine Sales Orders and Invoices, so it is never open
	to Guest and never open on a site that has not switched it on.
	"""
	if not enabled():
		frappe.throw(
			_("The LINE simulator is disabled on this site."),
			frappe.PermissionError,
		)
	if frappe.session.user == "Guest":
		frappe.throw(_("Please log in to use the LINE simulator."), frappe.PermissionError)
	if not set(frappe.get_roles()).intersection(allowed_roles()):
		frappe.throw(
			_("You are not allowed to use the LINE simulator."),
			frappe.PermissionError,
		)
	return sim_line_id()


# --------------------------------------------------------------------------- #
# Outbox — what ERPNext pushed instead of calling the LINE API
# --------------------------------------------------------------------------- #
def record_outbound(recipient: str, text: str, image_url: str | None = None) -> dict:
	"""Capture a push for a simulated recipient. Called from ``line.push_text``.

	This runs inside a queue worker, so the row has to be durable rather than
	held in the pushing process.
	"""
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"recipient": recipient,
			"direction": "Outbound",
			"message_type": "Text",
			"text": text or "",
			"image_url": image_url or None,
		}
	)
	doc.insert(ignore_permissions=True)
	return {"sent": True, "simulated": True, "message": doc.name}


def read_content(message_id: str) -> bytes:
	"""Bytes for a simulated image message, standing in for the LINE content API."""
	name = frappe.db.get_value(DOCTYPE, {"sim_message_id": message_id}, "name")
	if not name:
		frappe.throw(_("Simulated LINE message {0} was not found").format(message_id))
	doc = frappe.get_doc(DOCTYPE, name)
	if not doc.content_file:
		frappe.throw(_("Simulated LINE message {0} has no attachment").format(message_id))
	file_name = frappe.db.get_value("File", {"file_url": doc.content_file}, "name")
	if not file_name:
		frappe.throw(_("The stored slip for {0} is no longer available").format(message_id))
	return frappe.get_doc("File", file_name).get_content()


# --------------------------------------------------------------------------- #
# Inbound — a correctly signed webhook, exactly as LINE would send it
# --------------------------------------------------------------------------- #
def sign_line_body(secret: str, body: bytes) -> str:
	"""Signature exactly as LINE computes it (and order-intake verifies it)."""
	return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def build_line_webhook(
	line_id: str,
	*,
	text: str | None = None,
	image_message_id: str | None = None,
	event_id: str | None = None,
) -> dict:
	"""A minimal but correctly-shaped LINE webhook payload for one event.

	Kept in step with ``scripts/sandbox.py``; both have to satisfy the same
	``order_intake.line.verify_line_signature`` and the same event parsing.
	"""
	if text is None and image_message_id is None:
		raise ValueError("either text or image_message_id is required")
	message: dict = (
		{"type": "image", "id": image_message_id}
		if image_message_id
		else {"type": "text", "id": f"msg-{uuid.uuid4().hex[:12]}", "text": text}
	)
	return {
		"destination": "simulator",
		"events": [
			{
				"type": "message",
				"webhookEventId": event_id or f"sim-{uuid.uuid4().hex[:16]}",
				"source": {"type": "user", "userId": line_id},
				"message": message,
			}
		],
	}


def intake_url() -> str:
	return str(frappe.conf.get("nextgen_order_intake_url") or DEFAULT_INTAKE_URL).rstrip("/")


def _channel_secret() -> str:
	settings = frappe.get_single("LINE Channel Settings")
	secret = settings.get_password("channel_secret", raise_exception=False) or ""
	if not settings.enabled or not secret:
		frappe.throw(
			_(
				"Enable LINE Channel Settings and set a Channel Secret first. "
				"The simulator signs its webhook with it; no access token is needed."
			)
		)
	return secret


def _forward_webhook(payload: dict) -> dict:
	"""POST a signed webhook to order-intake and report what came back."""
	import requests

	body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
	# Signed before the try: a missing channel secret is a configuration error
	# and must surface as itself, not be relabelled as an unreachable service.
	signature = sign_line_body(_channel_secret(), body)
	try:
		response = requests.post(
			f"{intake_url()}/webhooks/line",
			data=body,
			headers={
				"Content-Type": "application/json",
				"X-Line-Signature": signature,
			},
			timeout=120,
		)
	except Exception as exc:
		frappe.log_error(frappe.get_traceback(), "NextGen LINE simulator forward failed")
		return {
			"delivered": False,
			"error": _("Order intake is unreachable at {0}: {1}").format(
				intake_url(), str(exc)[:200]
			),
		}
	try:
		parsed = response.json()
	except ValueError:
		parsed = {"raw": response.text[:500]}
	if response.status_code >= 400:
		return {
			"delivered": False,
			"status": response.status_code,
			"error": _("Order intake rejected the event ({0}): {1}").format(
				response.status_code, json.dumps(parsed, ensure_ascii=False)[:300]
			),
			"intake": parsed,
		}
	return {"delivered": True, "status": response.status_code, "intake": parsed}


def _record_inbound(
	line_id: str,
	*,
	text: str = "",
	message_type: str = "Text",
	sim_message_id: str | None = None,
	content_file: str | None = None,
):
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"recipient": line_id,
			"direction": "Inbound",
			"message_type": message_type,
			"text": text,
			"sim_message_id": sim_message_id,
			"content_file": content_file,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _looks_like_image(content: bytes) -> bool:
	"""Trust the bytes, not the filename: JPEG SOI or the PNG signature."""
	return content.startswith(b"\xff\xd8\xff") or content.startswith(b"\x89PNG\r\n\x1a\n")


# --------------------------------------------------------------------------- #
# Page API
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def send(text: str):
	"""Send one simulated customer message through the real intake path."""
	line_id = guard()
	text = (text or "").strip()
	if not text:
		frappe.throw(_("A message is required"))
	if len(text) > MAX_TEXT_CHARS:
		frappe.throw(_("A message may be at most {0} characters").format(MAX_TEXT_CHARS))
	doc = _record_inbound(line_id, text=text)
	# The webhook is handled synchronously by order-intake, which calls back into
	# this site; the inbound row has to be visible before that starts.
	frappe.db.commit()
	webhook = build_line_webhook(line_id, text=text, event_id=f"sim-{doc.name}")
	return {"message": doc.name, **_forward_webhook(webhook)}


@frappe.whitelist()
def send_image(filename: str, content_base64: str):
	"""Attach a payment slip, standing in for a LINE image message."""
	line_id = guard()
	try:
		content = base64.b64decode(content_base64 or "", validate=True)
	except (binascii.Error, ValueError):
		frappe.throw(_("The image could not be decoded"))
	if not content:
		frappe.throw(_("The image is empty"))
	if len(content) > MAX_SLIP_BYTES:
		frappe.throw(_("The image must be smaller than 5 MB"))
	if not _looks_like_image(content):
		frappe.throw(_("Only JPEG and PNG images are accepted"))
	message_id = f"sim-{uuid.uuid4().hex[:16]}"
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"line-sim-slip-{message_id}.jpg",
			"is_private": 1,
			"content": content,
		}
	).insert(ignore_permissions=True)
	doc = _record_inbound(
		line_id,
		text=filename or "payment slip",
		message_type="Image",
		sim_message_id=message_id,
		content_file=file_doc.file_url,
	)
	frappe.db.commit()
	webhook = build_line_webhook(line_id, image_message_id=message_id, event_id=f"sim-{doc.name}")
	return {"message": doc.name, "sim_message_id": message_id, **_forward_webhook(webhook)}


@frappe.whitelist()
def poll(recipient: str | None = None):
	"""The recent conversation for the caller's own identity or the delivery pane."""
	line_id = guard()
	target = (recipient or line_id).strip()
	if target not in (line_id, DELIVERY_LINE_ID):
		frappe.throw(_("You may only read your own simulated conversation"))
	rows = frappe.get_all(
		DOCTYPE,
		filters={"recipient": target},
		fields=[
			"name",
			"direction",
			"message_type",
			"text",
			"image_url",
			"sim_message_id",
			"content_file",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=HISTORY_LIMIT,
		ignore_permissions=True,
	)
	rows.reverse()
	return {"recipient": target, "messages": rows}


@frappe.whitelist()
def status():
	"""What the flow actually produced in ERPNext, for the demo side panel."""
	line_id = guard()
	# LINE Customer Map is named after the LINE id itself (autoname field:line_id).
	customer = frappe.db.get_value("LINE Customer Map", line_id, "customer")
	orders = frappe.get_all(
		"AI Order Intake",
		filters={"line_ref": line_id},
		fields=[
			"name",
			"status",
			"total",
			"customer",
			"sales_order",
			"sales_invoice",
			"delivery_note",
			"payment_entry",
			"payment_verification_status",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=5,
		ignore_permissions=True,
	)
	return {
		"line_id": line_id,
		"customer": customer,
		"customer_name": frappe.db.get_value("Customer", customer, "customer_name")
		if customer
		else None,
		"orders": orders,
	}


@frappe.whitelist()
def reset():
	"""Clear this identity's simulated conversation. ERP documents are untouched."""
	line_id = guard()
	frappe.db.delete(DOCTYPE, {"recipient": line_id})
	frappe.db.commit()
	return {"cleared": True, "line_id": line_id}
