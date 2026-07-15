"""Whitelisted surface for the LINE AI assistant.

The external order-intake service runs the LLM loop; ERPNext stays the system
of record and the only holder of secrets. Every method here:

* requires the scoped service role (same gate as ``nextgen_erp.api``),
* is keyed by the **verified** LINE sender id from the webhook — the model can
  never choose whose data it reads or who receives a message, and
* reuses the existing notification/link machinery in ``nextgen_erp.api`` so no
  new invoice/QR/delivery-note logic exists on this path.

Dotted path for callers: ``nextgen_erp.ai.<method>``.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import today

from nextgen_erp.api import (
	_queue_line_notification,
	_require_service_role,
	make_delivery_note_download_url,
)

# One free-text answer per LINE webhook event; receipts are namespaced so they
# can never collide with the confirmation receipts written by handle_line_reply.
AI_EVENT_PREFIX = "ai:"
MAX_ANSWER_CHARS = 4000
RECENT_ORDERS_LIMIT = 5
DEFAULT_MAX_TOOL_CALLS = 4
DEFAULT_DAILY_MESSAGE_CAP = 200
PAYMENT_RESEND_STATUSES = ("Awaiting Payment", "Payment Review")


@frappe.whitelist()
def get_ai_config():
	"""Return NextGen AI Settings for the authenticated external service.

	The API key is returned decrypted; only expose to a scoped API user
	(same contract as ``nextgen_erp.api.get_line_config``).
	"""
	_require_service_role()
	s = frappe.get_single("NextGen AI Settings")
	return {
		"enabled": bool(s.enabled),
		"gateway_url": (s.gateway_url or "").strip(),
		"api_key": s.get_password("api_key", raise_exception=False) or "",
		"chat_model": (s.chat_model or "").strip(),
		"embeddings_model": (s.embeddings_model or "").strip(),
		"max_tool_calls": int(s.max_tool_calls or DEFAULT_MAX_TOOL_CALLS),
		"daily_message_cap": int(
			DEFAULT_DAILY_MESSAGE_CAP if s.daily_message_cap is None else s.daily_message_cap
		),
	}


@frappe.whitelist()
def get_knowledge_articles(modified_since: str | None = None):
	"""Enabled knowledge articles for the assistant's retrieval index."""
	_require_service_role()
	filters: dict = {"enabled": 1}
	if modified_since:
		filters["modified"] = [">", modified_since]
	articles = frappe.get_all(
		"NextGen Knowledge Article",
		filters=filters,
		fields=["name", "title", "tags", "content", "modified"],
		order_by="modified desc",
	)
	return {"articles": articles}


def _owned_intake(name: str, line_id: str):
	"""Load an intake only if it belongs to this verified LINE sender."""
	if not name or not line_id:
		frappe.throw(_("An intake name and LINE sender are required"))
	doc = frappe.get_doc("AI Order Intake", name)
	if (doc.line_ref or "") != line_id:
		frappe.throw(
			_("Intake {0} does not belong to this LINE customer").format(name),
			frappe.PermissionError,
		)
	return doc


@frappe.whitelist()
def get_customer_context(line_id: str):
	"""The verified sender's customer mapping and recent orders — nothing else."""
	_require_service_role()
	if not line_id:
		frappe.throw(_("A LINE sender id is required"))
	customer = frappe.db.get_value("LINE Customer Map", line_id, "customer")
	orders = frappe.get_all(
		"AI Order Intake",
		filters={"line_ref": line_id},
		fields=[
			"name",
			"status",
			"total",
			"sales_order",
			"sales_invoice",
			"delivery_note",
			"payment_entry",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=RECENT_ORDERS_LIMIT,
	)
	return {
		"line_id": line_id,
		"customer": customer,
		"customer_name": frappe.db.get_value("Customer", customer, "customer_name") if customer else None,
		"orders": orders,
	}


@frappe.whitelist()
def resend_payment_request(name: str, line_id: str):
	"""Re-send the invoice link and PromptPay QR for the sender's own open invoice."""
	_require_service_role()
	doc = _owned_intake(name, line_id)
	if doc.status not in PAYMENT_RESEND_STATUSES or not doc.sales_invoice:
		frappe.throw(
			_("Intake {0} has no open invoice awaiting payment (status: {1})").format(name, doc.status)
		)
	_queue_line_notification(doc)
	return {"name": doc.name, "status": doc.status, "sales_invoice": doc.sales_invoice, "sent": True}


@frappe.whitelist()
def resend_delivery_note(name: str, line_id: str):
	"""Send the signed delivery-note link for the sender's own delivered order."""
	_require_service_role()
	doc = _owned_intake(name, line_id)
	if not doc.delivery_note:
		frappe.throw(_("Intake {0} has no delivery note yet (status: {1})").format(name, doc.status))
	link = make_delivery_note_download_url(doc.delivery_note)
	frappe.enqueue(
		"nextgen_erp.line.push_text",
		queue="short",
		enqueue_after_commit=True,
		recipient=doc.line_ref,
		text=(
			f"ใบส่งสินค้าสำหรับออเดอร์ {doc.name}\n"
			f"Delivery Note: {doc.delivery_note}\n{link}"
		),
	)
	return {"name": doc.name, "delivery_note": doc.delivery_note, "sent": True}


def _answers_sent_today() -> int:
	return frappe.db.count(
		"LINE Event Receipt",
		{"event_id": ["like", f"{AI_EVENT_PREFIX}%"], "creation": [">=", today()]},
	)


@frappe.whitelist()
def send_line_answer(line_id: str, text: str, event_id: str | None = None):
	"""Deliver one assistant answer to the verified sender, idempotent per event.

	This is the assistant's only free-text output channel; the channel access
	token stays inside ERPNext (``nextgen_erp.line.push_text``).
	"""
	_require_service_role()
	text = (text or "").strip()
	if not line_id or not text:
		frappe.throw(_("A LINE recipient and answer text are required"))

	receipt_id = f"{AI_EVENT_PREFIX}{event_id}" if event_id else None
	if receipt_id:
		existing = frappe.db.get_value("LINE Event Receipt", receipt_id, "result_json")
		if existing:
			result = json.loads(existing)
			result["duplicate"] = True
			return result

	raw_cap = frappe.db.get_single_value("NextGen AI Settings", "daily_message_cap")
	cap = DEFAULT_DAILY_MESSAGE_CAP if raw_cap is None else int(raw_cap)
	if cap and _answers_sent_today() >= cap:
		frappe.log_error(
			f"AI daily answer cap ({cap}) reached; dropping answer to {line_id}",
			"NextGen AI assistant",
		)
		return {"sent": False, "reason": "daily_cap_reached"}

	frappe.enqueue(
		"nextgen_erp.line.push_text",
		queue="short",
		enqueue_after_commit=True,
		recipient=line_id,
		text=text[:MAX_ANSWER_CHARS],
	)
	response = {"sent": True}
	if receipt_id:
		frappe.get_doc(
			{
				"doctype": "LINE Event Receipt",
				"event_id": receipt_id,
				"line_id": line_id,
				"result_json": json.dumps(response, ensure_ascii=False),
			}
		).insert()
	return response
