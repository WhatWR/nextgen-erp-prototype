"""LINE-linked Webshop handoff, catalog publishing, and idempotent checkout."""

from __future__ import annotations

import hashlib
import secrets
from urllib.parse import quote, urlparse

import frappe
from frappe import _
from frappe.utils import add_to_date, flt, get_datetime, getdate, now_datetime, nowdate

from nextgen_erp.api import (
	_queue_line_notification,
	_require_operations_role,
	_require_service_role,
	_resolve_or_create_line_customer,
	_selling_rate,
	public_base_url,
)


def _token_hash(token: str) -> str:
	return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _portal_email(line_ref: str) -> str:
	digest = hashlib.sha256(line_ref.encode("utf-8")).hexdigest()[:24]
	return f"line-{digest}@line.nextgen.invalid"


def _ensure_portal_user(session) -> str:
	email = _portal_email(session.line_ref)
	if not frappe.db.exists("User", email):
		display_name = (
			frappe.db.get_value("LINE Customer Map", session.line_ref, "display_name")
			or frappe.db.get_value("Customer", session.customer, "customer_name")
			or "LINE Customer"
		)
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": display_name[:140],
				"enabled": 1,
				"user_type": "Website User",
				"send_welcome_email": 0,
			}
		)
		user.flags.no_welcome_mail = True
		user.insert(ignore_permissions=True)
		if frappe.db.exists("Role", "Customer"):
			user.add_roles("Customer")

	if not frappe.db.exists("Contact", {"user": email}):
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": frappe.db.get_value("User", email, "first_name") or "LINE Customer",
				"user": email,
			}
		)
		contact.append(
			"links",
			{"link_doctype": "Customer", "link_name": session.customer},
		)
		contact.insert(ignore_permissions=True)
	return email


@frappe.whitelist()
def create_line_shop_link(
	line_id: str,
	event_id: str | None = None,
	target_route: str | None = None,
) -> dict:
	"""Create a one-use, ten-minute catalog handoff for a verified LINE sender."""
	_require_service_role()
	mapping = _resolve_or_create_line_customer(line_id, create_if_missing=True)
	token = secrets.token_urlsafe(32)
	nonce = secrets.token_hex(16)
	expires_at = add_to_date(now_datetime(), minutes=10)
	target = "/all-products"
	if target_route:
		parsed_target = urlparse(target_route)
		if parsed_target.scheme or parsed_target.netloc:
			origin = urlparse(public_base_url())
			if (
				parsed_target.scheme == origin.scheme
				and parsed_target.netloc == origin.netloc
				and parsed_target.path.startswith("/")
			):
				target = parsed_target.path
		elif target_route.startswith("/") and not target_route.startswith("//"):
			target = target_route
	frappe.get_doc(
		{
			"doctype": "NextGen Shop Session",
			"token_hash": _token_hash(token),
			"nonce": nonce,
			"line_ref": line_id,
			"customer": mapping["customer"],
			"expires_at": expires_at,
			"event_id": event_id,
			"target_route": target,
		}
	).insert(ignore_permissions=True)
	url = (
		f"{public_base_url()}/nextgen-shop-entry"
		f"?token={quote(token)}"
	)
	return {"url": url, "expires_at": str(expires_at)}


@frappe.whitelist(allow_guest=True)
def open_line_shop(token: str):
	"""Consume a handoff after an explicit POST and open the catalog.

	LINE and other chat clients may fetch links to build previews.  A GET must
	therefore never consume the one-use token or establish a customer session.
	Older links that still target this method are redirected to the safe landing
	page and remain usable.
	"""
	request = getattr(frappe.local, "request", None)
	if str(getattr(request, "method", "GET")).upper() != "POST":
		frappe.local.response.type = "redirect"
		frappe.local.response.location = f"/nextgen-shop-entry?token={quote(token or '')}"
		return None

	token_hash = _token_hash(token or "")
	name = frappe.db.get_value("NextGen Shop Session", {"token_hash": token_hash}, "name")
	if not name:
		frappe.throw(_("This shop link is invalid or expired"), frappe.PermissionError)
	frappe.db.sql("select name from `tabNextGen Shop Session` where name=%s for update", name)
	session = frappe.get_doc("NextGen Shop Session", name)
	if session.consumed_at or get_datetime(session.expires_at) < now_datetime():
		frappe.throw(_("This shop link is invalid or expired"), frappe.PermissionError)
	user = _ensure_portal_user(session)
	session.portal_user = user
	session.consumed_at = now_datetime()
	session.consumed_ip = getattr(frappe.local, "request_ip", "") or ""
	session.save(ignore_permissions=True)
	frappe.local.login_manager.login_as(user)
	frappe.local.response.type = "redirect"
	frappe.local.response.location = session.target_route or "/all-products"
	return None


def _current_shop_session(user: str):
	name = frappe.db.get_value(
		"NextGen Shop Session",
		{"portal_user": user, "consumed_at": ["is", "set"]},
		"name",
		order_by="consumed_at desc",
	)
	if not name:
		frappe.throw(_("Open the product catalog from LINE before checkout"), frappe.PermissionError)
	return frappe.get_doc("NextGen Shop Session", name)


def _checkout_response(doc, *, already: bool = False) -> dict:
	from nextgen_erp.api import make_invoice_download_url, make_promptpay_qr_url

	invoice_url = make_invoice_download_url(doc.sales_invoice, doc.line_ref) if doc.sales_invoice else None
	qr_url = make_promptpay_qr_url(doc.sales_invoice, doc.line_ref) if doc.sales_invoice else None
	return {
		"name": doc.name,
		"status": doc.status,
		"sales_order": doc.sales_order,
		"sales_invoice": doc.sales_invoice,
		"invoice_url": invoice_url,
		"qr_url": qr_url,
		"already": already,
	}


@frappe.whitelist()
def checkout_line_cart(quotation: str | None = None) -> dict:
	"""Consume one LINE customer's Webshop Quotation into the shared order flow."""
	if frappe.session.user in {"Guest", "Administrator"}:
		frappe.throw(_("A LINE-linked website session is required"), frappe.PermissionError)
	if not frappe.db.exists("DocType", "Website Item"):
		frappe.throw(_("Webshop is not installed"))
	session = _current_shop_session(frappe.session.user)
	if not quotation:
		from webshop.webshop.shopping_cart.cart import _get_cart_quotation

		quotation = _get_cart_quotation(party=session.customer).name
	frappe.db.sql("select name from `tabQuotation` where name=%s for update", quotation)
	cart = frappe.get_doc("Quotation", quotation)
	key = f"webshop:{cart.name}"
	existing = frappe.db.get_value("AI Order Intake", {"idempotency_key": key}, "name")
	if existing:
		return _checkout_response(frappe.get_doc("AI Order Intake", existing), already=True)
	if cart.docstatus != 0 or cart.party_name != session.customer:
		frappe.throw(_("This cart is not available for checkout"), frappe.PermissionError)
	if not cart.items:
		frappe.throw(_("The cart is empty"))
	if not (cart.shipping_address_name or cart.customer_address):
		frappe.throw(_("Set a shipping or billing address before checkout"))

	items = []
	revalidated_total = 0.0
	for row in cart.items:
		rate = _selling_rate(row.item_code, session.customer, cart.selling_price_list)
		if rate <= 0:
			frappe.throw(_("No valid selling price is configured for Item {0}").format(row.item_code))
		if abs(flt(row.rate) - rate) > 0.01:
			frappe.throw(
				_("The price of {0} changed. Refresh the cart before checkout.").format(row.item_code)
			)
		items.append(
			{
				"item_code": row.item_code,
				"qty": row.qty,
				"uom": row.uom,
				"rate": rate,
				"raw_text": row.item_name,
				"confidence": 1,
			}
		)
		revalidated_total += flt(row.qty) * rate
	if abs(flt(cart.grand_total) - revalidated_total) > 0.01:
		frappe.throw(
			_(
				"This cart contains taxes, shipping, or adjustments that are not supported "
				"by LINE-linked checkout yet."
			)
		)

	from nextgen_erp.api import (
		approve_ai_order_intake,
		create_ai_order_intake,
		record_customer_confirmation,
	)

	previous = getattr(frappe.local.flags, "nextgen_web_checkout", False)
	previous_suppress = getattr(
		frappe.local.flags, "nextgen_suppress_line_notification", False
	)
	frappe.local.flags.nextgen_web_checkout = True
	frappe.local.flags.nextgen_suppress_line_notification = True
	try:
		created = create_ai_order_intake(
			{
				"idempotency_key": key,
				"merchant": "webshop",
				"line_ref": session.line_ref,
				"customer": session.customer,
				"source_channel": "webshop",
				"source_text": f"Webshop cart {cart.name}",
				"confidence": 1,
				"automation_mode": "human_review",
				"items": items,
			}
		)
		doc = frappe.get_doc("AI Order Intake", created["name"])
		doc.checkout_idempotency_key = key
		doc.save(ignore_permissions=True)
		if doc.status in {"Needs Review", "Ready"}:
			approve_ai_order_intake(doc.name, reviewer=frappe.session.user, note="Explicit Webshop checkout")
		record_customer_confirmation(doc.name, confirmed=1)
	finally:
		frappe.local.flags.nextgen_web_checkout = previous
		frappe.local.flags.nextgen_suppress_line_notification = previous_suppress
	doc = frappe.get_doc("AI Order Intake", created["name"])
	if frappe.get_meta("Quotation").has_field("custom_nextgen_checkout_reference"):
		cart.custom_nextgen_checkout_reference = doc.name
	cart.flags.ignore_permissions = True
	cart.submit()
	if hasattr(frappe.local, "cookie_manager"):
		frappe.local.cookie_manager.delete_cookie("cart_count")
	_queue_line_notification(doc)
	return _checkout_response(doc)


@frappe.whitelist()
def place_order():
	"""Compatibility override for Webshop's native Place Order action."""
	return checkout_line_cart()["sales_order"]


def _route_for_item(item_code: str, item_name: str) -> str:
	base = "-".join((item_name or item_code).casefold().split())
	base = "".join(character for character in base if character.isalnum() or character in "-_")
	return f"{base.strip('-')}-{hashlib.sha1(item_code.encode()).hexdigest()[:6]}"


@frappe.whitelist()
def publish_catalog_items(
	dry_run: int = 1,
	limit: int = 100,
	start_after: str | None = None,
	price_list: str | None = None,
) -> dict:
	"""Idempotently publish priced, enabled sales Items as Website Items."""
	_require_operations_role()
	if not frappe.db.exists("DocType", "Website Item"):
		frappe.throw(_("Install the Webshop app before publishing the catalog"))
	price_list = price_list or frappe.db.get_single_value("Selling Settings", "selling_price_list")
	if not price_list:
		frappe.throw(_("Configure a default selling price list first"))
	limit = min(max(int(limit or 100), 1), 1000)
	filters = {
		"disabled": 0,
		"is_sales_item": 1,
		"item_code": [">", start_after or ""],
	}
	items = frappe.get_all(
		"Item",
		filters=filters,
		fields=["item_code", "item_name", "description", "item_group", "stock_uom", "image"],
		order_by="item_code asc",
		limit_page_length=limit,
	)
	codes = [row.item_code for row in items]
	price_rows = (
		frappe.get_all(
			"Item Price",
			filters={
				"item_code": ["in", codes],
				"price_list": price_list,
				"selling": 1,
				"price_list_rate": [">", 0],
			},
			fields=["item_code", "valid_from", "valid_upto"],
			limit_page_length=max(len(codes) * 20, 1),
		)
		if codes
		else []
	)
	today = getdate(nowdate())
	priced = {
		row.item_code
		for row in price_rows
		if (not row.valid_from or getdate(row.valid_from) <= today)
		and (not row.valid_upto or getdate(row.valid_upto) >= today)
	}
	website_warehouse = frappe.db.get_single_value("LINE Channel Settings", "selling_warehouse")
	if not website_warehouse:
		company = frappe.db.get_single_value("Webshop Settings", "company")
		from nextgen_erp.api import _default_warehouse

		website_warehouse = _default_warehouse(company) if company else None
	result = {"eligible": 0, "created": 0, "updated": 0, "skipped_no_price": 0, "errors": []}
	for item in items:
		if item.item_code not in priced:
			result["skipped_no_price"] += 1
			continue
		result["eligible"] += 1
		existing = frappe.db.get_value("Website Item", {"item_code": item.item_code}, "name")
		if int(dry_run or 0):
			result["updated" if existing else "created"] += 1
			continue
		try:
			doc = frappe.get_doc("Website Item", existing) if existing else frappe.new_doc("Website Item")
			values = {
				"item_code": item.item_code,
				"item_name": item.item_name,
				"web_item_name": item.item_name,
				"description": item.description,
				"item_group": item.item_group,
				"stock_uom": item.stock_uom,
				"website_image": item.image,
				"website_warehouse": website_warehouse,
				"route": _route_for_item(item.item_code, item.item_name),
				"published": 1,
			}
			for field, value in values.items():
				if doc.meta.has_field(field):
					doc.set(field, value)
			doc.save(ignore_permissions=True) if existing else doc.insert(ignore_permissions=True)
			result["updated" if existing else "created"] += 1
		except Exception as exc:
			result["errors"].append({"item_code": item.item_code, "error": str(exc)[:300]})
	result["next_start_after"] = items[-1].item_code if len(items) == limit else None
	result["dry_run"] = bool(int(dry_run or 0))
	return result


@frappe.whitelist()
def catalog_readiness() -> dict:
	"""Operational preflight for the public storefront and payment links."""
	_require_operations_role()
	from nextgen_erp.api import _promptpay_payload

	checks = {
		"webshop_installed": frappe.db.exists("DocType", "Website Item"),
		"payments_installed": "payments" in frappe.get_installed_apps(),
		"promptpay_configured": bool(
			frappe.db.get_single_value("NextGen Payment Settings", "promptpay_id")
		),
		"typhoon_configured": bool(
			frappe.get_single("NextGen Payment Settings").get_password(
				"slip_verification_api_key", raise_exception=False
			)
		),
	}
	try:
		checks["public_base_url"] = public_base_url()
		checks["stable_https_url"] = checks["public_base_url"].startswith("https://")
	except ValueError as exc:
		checks["stable_https_url"] = False
		checks["public_base_url_error"] = str(exc)
	webhook_url = (
		frappe.db.get_single_value("LINE Channel Settings", "webhook_url") or ""
	).rstrip("/")
	expected_webhook = (
		f"{checks.get('public_base_url', '').rstrip('/')}/webhooks/line"
		if checks.get("public_base_url")
		else ""
	)
	checks["line_webhook_url"] = webhook_url
	checks["webhook_matches_public_url"] = bool(
		webhook_url and expected_webhook and webhook_url == expected_webhook
	)
	try:
		from frappe.utils.background_jobs import get_workers

		checks["worker_count"] = len(get_workers())
		checks["workers_available"] = checks["worker_count"] > 0
	except Exception as exc:
		checks["worker_count"] = 0
		checks["workers_available"] = False
		checks["worker_check_error"] = str(exc)[:200]
	promptpay = frappe.db.get_single_value("NextGen Payment Settings", "promptpay_id")
	if promptpay:
		try:
			_promptpay_payload(promptpay, 1)
			checks["promptpay_valid"] = True
		except Exception:
			checks["promptpay_valid"] = False
	checks["ready"] = all(
		checks.get(key)
		for key in (
			"webshop_installed",
			"payments_installed",
			"stable_https_url",
			"promptpay_configured",
			"promptpay_valid",
			"webhook_matches_public_url",
			"workers_available",
		)
	)
	return checks


def cleanup_expired_payment_slips() -> dict:
	"""Apply the configured retention period to private images after completion."""
	days = int(
		frappe.db.get_single_value("NextGen Payment Settings", "slip_retention_days")
		or 365
	)
	if days <= 0:
		return {"deleted": 0, "disabled": True}
	cutoff = add_to_date(now_datetime(), days=-days)
	rows = frappe.get_all(
		"AI Order Intake",
		filters={
			"status": ["in", ["Delivered", "Paid", "Rejected"]],
			"payment_slip": ["is", "set"],
			"modified": ["<", cutoff],
		},
		fields=["name", "payment_slip"],
		limit_page_length=200,
	)
	deleted = 0
	for row in rows:
		file_name = frappe.db.get_value(
			"File",
			{
				"file_url": row.payment_slip,
				"attached_to_doctype": "AI Order Intake",
				"attached_to_name": row.name,
				"is_private": 1,
			},
			"name",
		)
		if file_name:
			frappe.delete_doc("File", file_name, ignore_permissions=True)
			deleted += 1
		frappe.db.set_value(
			"AI Order Intake",
			row.name,
			"payment_slip",
			None,
			update_modified=False,
		)
	return {"deleted": deleted, "cutoff": str(cutoff)}
