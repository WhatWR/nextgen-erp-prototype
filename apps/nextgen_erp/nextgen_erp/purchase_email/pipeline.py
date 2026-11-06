"""Sync mailbox → extract → evaluate rules → persist review-queue drafts.

Human confirmation is mandatory before any Purchase Order is created. Export
creates a Draft PO only (never submits), matching procurement guardrails.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, now_datetime

from nextgen_erp.purchase_email.extractor import extract_order_fields
from nextgen_erp.purchase_email.fixtures import DEFAULT_RULES
from nextgen_erp.purchase_email.providers import (
	ProviderNotConfigured,
	build_provider,
)
from nextgen_erp.purchase_email.rules import RuleSpec, evaluate_rules

SETTINGS_DOCTYPE = "NextGen PO Email Settings"
RULE_DOCTYPE = "NextGen PO Email Rule"
INTAKE_DOCTYPE = "NextGen PO Email Intake"


def _password(doc, field: str) -> str:
	try:
		return doc.get_password(field) or ""
	except Exception:
		return ""


def get_settings_dict() -> dict[str, Any]:
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return {
			"enabled": 1,
			"provider": "Mock",
			"use_mock_fixtures": 1,
			"watch_folder": "INBOX/Purchase Orders",
			"connection_status": "Connected",
		}
	doc = frappe.get_single(SETTINGS_DOCTYPE)
	return {
		"enabled": cint(doc.get("enabled")),
		"provider": doc.get("provider") or "Mock",
		"use_mock_fixtures": cint(doc.get("use_mock_fixtures")),
		"mailbox_email": doc.get("mailbox_email") or "",
		"watch_folder": doc.get("watch_folder") or "INBOX/Purchase Orders",
		"company": doc.get("company") or "",
		"oauth_client_id": doc.get("oauth_client_id") or "",
		"oauth_client_secret": _password(doc, "oauth_client_secret"),
		"oauth_refresh_token": _password(doc, "oauth_refresh_token"),
		"oauth_tenant_id": doc.get("oauth_tenant_id") or "common",
		"imap_host": doc.get("imap_host") or "",
		"imap_port": cint(doc.get("imap_port") or 993),
		"imap_username": doc.get("imap_username") or "",
		"imap_password": _password(doc, "imap_password"),
		"imap_use_ssl": cint(doc.get("imap_use_ssl") if doc.get("imap_use_ssl") is not None else 1),
		"connection_status": doc.get("connection_status") or "Not Connected",
		"last_sync_at": doc.get("last_sync_at"),
		"last_sync_note": doc.get("last_sync_note") or "",
	}


def save_settings(values: dict[str, Any] | None = None) -> dict[str, Any]:
	values = values or {}
	doc = frappe.get_single(SETTINGS_DOCTYPE)
	for field in (
		"enabled",
		"provider",
		"use_mock_fixtures",
		"mailbox_email",
		"watch_folder",
		"company",
		"oauth_client_id",
		"oauth_tenant_id",
		"imap_host",
		"imap_port",
		"imap_username",
		"imap_use_ssl",
	):
		if field in values:
			doc.set(field, values[field])
	for secret in ("oauth_client_secret", "oauth_refresh_token", "imap_password"):
		if values.get(secret):
			doc.set(secret, values[secret])

	doc.save(ignore_permissions=True)
	settings_preview = get_settings_dict()
	provider = build_provider(settings_preview)
	doc.connection_status = provider.connection_status()
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return get_connection_status()


def get_connection_status() -> dict[str, Any]:
	settings = get_settings_dict()
	provider = build_provider(settings)
	hint = provider.connect_hint()
	return {
		**hint,
		"enabled": bool(settings.get("enabled")),
		"use_mock_fixtures": bool(settings.get("use_mock_fixtures")),
		"watch_folder": settings.get("watch_folder"),
		"mailbox_email": settings.get("mailbox_email"),
		"provider_setting": settings.get("provider"),
		"last_sync_at": settings.get("last_sync_at"),
		"last_sync_note": settings.get("last_sync_note"),
		"company": settings.get("company"),
		"has_oauth_client": bool(settings.get("oauth_client_id")),
		"has_oauth_secret": bool(settings.get("oauth_client_secret")),
		"has_refresh_token": bool(settings.get("oauth_refresh_token")),
		"has_imap_password": bool(settings.get("imap_password")),
	}


def ensure_default_rules() -> list[str]:
	created: list[str] = []
	if not frappe.db.exists("DocType", RULE_DOCTYPE):
		return created
	for row in DEFAULT_RULES:
		if frappe.db.exists(RULE_DOCTYPE, row["rule_name"]):
			continue
		doc = frappe.get_doc(
			{
				"doctype": RULE_DOCTYPE,
				"enabled": 1,
				**row,
			}
		)
		doc.insert(ignore_permissions=True)
		created.append(doc.name)
	if created:
		frappe.db.commit()
	return created


def list_rules() -> list[dict[str, Any]]:
	ensure_default_rules()
	if not frappe.db.exists("DocType", RULE_DOCTYPE):
		return [dict(row, enabled=1) for row in DEFAULT_RULES]
	return frappe.get_all(
		RULE_DOCTYPE,
		fields=[
			"name",
			"rule_name",
			"enabled",
			"priority",
			"rule_type",
			"action",
			"field_name",
			"compare_op",
			"value",
			"message",
		],
		order_by="priority asc, creation asc",
	)


def save_rule(payload: dict[str, Any]) -> dict[str, Any]:
	payload = dict(payload or {})
	name = payload.pop("name", None)
	if name and frappe.db.exists(RULE_DOCTYPE, name):
		doc = frappe.get_doc(RULE_DOCTYPE, name)
		doc.update(payload)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc({"doctype": RULE_DOCTYPE, **payload})
		doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.as_dict()


def delete_rule(name: str) -> None:
	if frappe.db.exists(RULE_DOCTYPE, name):
		frappe.delete_doc(RULE_DOCTYPE, name, ignore_permissions=True)
		frappe.db.commit()


def _load_rule_specs() -> list[RuleSpec]:
	rows = list_rules()
	return [
		RuleSpec.from_mapping(row)
		for row in rows
		if cint(row.get("enabled") if "enabled" in row else 1)
	]


def _resolve_supplier(vendor_name: str) -> str | None:
	if not vendor_name:
		return None
	if frappe.db.exists("Supplier", vendor_name):
		return vendor_name
	found = frappe.db.get_value("Supplier", {"supplier_name": vendor_name}, "name")
	return found


def _status_from_decision(decision: str) -> str:
	return {
		"Approve": "Needs Review",
		"Flag": "Flagged",
		"Reject": "Rejected",
		"Needs Review": "Needs Review",
	}.get(decision, "Needs Review")


def process_message(message: dict[str, Any], provider_name: str = "Mock") -> dict[str, Any]:
	message_id = str(message.get("message_id") or "").strip()
	if not message_id:
		frappe.throw(_("Email message_id is required"))

	existing = frappe.db.get_value(INTAKE_DOCTYPE, {"source_message_id": message_id}, "name")
	if existing:
		return frappe.get_doc(INTAKE_DOCTYPE, existing).as_dict()

	extracted = extract_order_fields(
		str(message.get("subject") or ""),
		str(message.get("body") or ""),
	)
	evaluation = evaluate_rules(_load_rule_specs(), extracted)

	doc = frappe.get_doc(
		{
			"doctype": INTAKE_DOCTYPE,
			"source_message_id": message_id,
			"from_email": message.get("from_email"),
			"subject": message.get("subject"),
			"received_at": get_datetime(message.get("received_at")) if message.get("received_at") else now_datetime(),
			"provider": provider_name,
			"raw_body": message.get("body"),
			"vendor_name": extracted.get("vendor_name"),
			"supplier": _resolve_supplier(str(extracted.get("vendor_name") or "")),
			"po_reference": extracted.get("po_reference"),
			"currency": extracted.get("currency") or "THB",
			"total_amount": flt(extracted.get("total_amount")),
			"confidence": flt(extracted.get("confidence")),
			"extracted_json": extracted.get("extracted_json"),
			"rule_decision": evaluation.decision,
			"rule_summary": evaluation.summary,
			"status": _status_from_decision(evaluation.decision),
			"items": [
				{
					"sku": row.get("sku"),
					"item_name": row.get("item_name"),
					"qty": flt(row.get("qty")),
					"uom": row.get("uom") or "Nos",
					"rate": flt(row.get("rate")),
					"amount": flt(row.get("amount")),
				}
				for row in extracted.get("items") or []
			],
			"rule_results": [
				{
					"rule": result.rule_name if frappe.db.exists(RULE_DOCTYPE, result.rule_name) else None,
					"rule_name": result.rule_name,
					"action": result.action,
					"passed": 1 if result.passed else 0,
					"message": result.message,
				}
				for result in evaluation.results
			],
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.as_dict()


def sync_inbox(limit: int = 20) -> dict[str, Any]:
	settings = get_settings_dict()
	if not settings.get("enabled"):
		frappe.throw(_("PO Email Intake is disabled in NextGen PO Email Settings"))

	ensure_default_rules()
	provider = build_provider(settings)
	folder = str(settings.get("watch_folder") or "INBOX/Purchase Orders")
	created: list[str] = []
	skipped = 0
	note = ""

	try:
		messages = provider.list_messages(folder=folder, limit=limit)
	except ProviderNotConfigured as exc:
		# Fall back to fixtures so demos stay end-to-end without secrets.
		fallback = build_provider({"provider": "Mock", "use_mock_fixtures": 1})
		messages = fallback.list_messages(folder=folder, limit=limit)
		note = f"{exc} Fell back to mock fixtures ({len(messages)} messages)."
		provider = fallback

	for message in messages:
		payload = message.as_dict() if hasattr(message, "as_dict") else dict(message)
		existing = frappe.db.get_value(
			INTAKE_DOCTYPE, {"source_message_id": payload["message_id"]}, "name"
		)
		if existing:
			skipped += 1
			continue
		doc = process_message(payload, provider_name=provider.name)
		created.append(doc.get("name"))

	if not note:
		note = f"Synced {len(created)} new, skipped {skipped} existing from {provider.name}."

	if frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		doc = frappe.get_single(SETTINGS_DOCTYPE)
		doc.connection_status = provider.connection_status()
		doc.last_sync_at = now_datetime()
		doc.last_sync_note = note
		doc.save(ignore_permissions=True)
		frappe.db.commit()

	return {
		"created": created,
		"skipped": skipped,
		"provider": provider.name,
		"note": note,
		"folder": folder,
	}


def list_intake(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
	filters: dict[str, Any] = {}
	if status:
		filters["status"] = status
	names = frappe.get_all(
		INTAKE_DOCTYPE,
		filters=filters,
		pluck="name",
		order_by="modified desc",
		limit_page_length=limit,
	)
	return [frappe.get_doc(INTAKE_DOCTYPE, name).as_dict() for name in names]


def decide_intake(name: str, decision: str, note: str | None = None) -> dict[str, Any]:
	decision = (decision or "").strip().title()
	if decision not in {"Approved", "Rejected"}:
		frappe.throw(_("Decision must be Approved or Rejected"))
	doc = frappe.get_doc(INTAKE_DOCTYPE, name)
	if doc.status in {"Exported"}:
		frappe.throw(_("Intake {0} was already exported").format(name))
	doc.status = decision
	doc.reviewer = frappe.session.user
	doc.decision_note = note or doc.decision_note
	doc.rule_decision = "Approve" if decision == "Approved" else "Reject"
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return doc.as_dict()


def export_to_purchase_order(name: str) -> dict[str, Any]:
	"""Create a Draft Purchase Order from an approved intake. Never submits."""
	doc = frappe.get_doc(INTAKE_DOCTYPE, name)
	if doc.status not in {"Approved", "Needs Review", "Flagged"}:
		frappe.throw(_("Only reviewable/approved intakes can export to a Purchase Order"))
	if doc.purchase_order:
		return {"intake": doc.name, "purchase_order": doc.purchase_order, "status": doc.status}

	supplier = doc.supplier or _resolve_supplier(doc.vendor_name or "")
	if not supplier:
		# Keep the prototype demoable without a pre-seeded Supplier master.
		supplier_name = doc.vendor_name or f"PO Email Vendor {doc.name}"
		if frappe.db.exists("DocType", "Supplier") and not frappe.db.exists("Supplier", supplier_name):
			supplier_doc = frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": supplier_name,
					"supplier_group": frappe.db.get_single_value("Buying Settings", "supplier_group")
					or "All Supplier Groups",
				}
			)
			try:
				supplier_doc.insert(ignore_permissions=True)
				supplier = supplier_doc.name
			except Exception:
				frappe.log_error(title="PO email supplier create", message=frappe.get_traceback())
				supplier = None
		else:
			supplier = supplier_name if frappe.db.exists("Supplier", supplier_name) else None

	if not supplier:
		frappe.throw(
			_("Map a Supplier on the intake before export (vendor: {0})").format(doc.vendor_name or "-")
		)

	company = frappe.get_cached_value(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE, "company")
	if not company:
		company = frappe.db.get_single_value("Global Defaults", "default_company")
	if not company:
		company = frappe.db.get_value("Company", {}, "name")

	items = []
	for row in doc.items:
		item_code = row.item_code
		if not item_code and row.sku and frappe.db.exists("Item", row.sku):
			item_code = row.sku
		if not item_code:
			# Draft PO requires Item rows; skip unresolved SKUs with a clear error.
			frappe.throw(_("Resolve item for SKU {0} before export").format(row.sku or "-"))
		items.append(
			{
				"item_code": item_code,
				"qty": flt(row.qty) or 1,
				"rate": flt(row.rate),
				"uom": row.uom or None,
				"schedule_date": frappe.utils.today(),
			}
		)

	po = frappe.get_doc(
		{
			"doctype": "Purchase Order",
			"supplier": supplier,
			"company": company,
			"schedule_date": frappe.utils.today(),
			"items": items,
		}
	)
	po.insert(ignore_permissions=True)

	doc.purchase_order = po.name
	doc.status = "Exported"
	doc.exported_at = now_datetime()
	doc.reviewer = doc.reviewer or frappe.session.user
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"intake": doc.name, "purchase_order": po.name, "status": doc.status}


def overview() -> dict[str, Any]:
	ensure_default_rules()
	counts = {
		"Needs Review": 0,
		"Flagged": 0,
		"Approved": 0,
		"Rejected": 0,
		"Exported": 0,
		"Draft": 0,
	}
	if frappe.db.exists("DocType", INTAKE_DOCTYPE):
		for row in frappe.get_all(INTAKE_DOCTYPE, fields=["status", "count(name) as total"], group_by="status"):
			counts[row.status] = cint(row.total)
	return {
		"connection": get_connection_status(),
		"rules": list_rules(),
		"counts": counts,
		"queue": list_intake(limit=25),
	}
