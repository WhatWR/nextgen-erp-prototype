"""NextGen ERP whitelisted API.

Two layers:

* Order-to-cash primitives (`create_sales_order`, `deliver_and_invoice`,
  `record_payment`) — the exact contract the external order-intake service's
  ``ERPNextAdapter`` speaks. They create and submit real ERPNext documents.
* Intake orchestration (`create_ai_order_intake`, `approve_ai_order_intake`,
  `record_customer_confirmation`, `progress_delivery`, `progress_payment`) —
  drives an AI Order Intake record through its lifecycle, calling the primitives
  at the right transitions. These are what the Desk buttons and the external
  service call.

``external_reference`` (the intake name) is the idempotency anchor; callers
guard re-entry by checking the linked document fields on the intake.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import tempfile
import time
from urllib.parse import quote, urlparse

import frappe
import requests
from frappe import _
from frappe.utils import flt, getdate, get_url, now_datetime, nowdate


SERVICE_ROLE = "NextGen Order Service"
OPERATIONS_ROLES = {"System Manager", "Sales Manager", "Sales User", SERVICE_ROLE}
EXTERNAL_REFERENCE_FIELD = "custom_nextgen_external_reference"


def _trusted_web_checkout() -> bool:
    return bool(getattr(frappe.local.flags, "nextgen_web_checkout", False))


@frappe.whitelist()
def setup_complete_with_thailand_defaults(args):
    """Complete the ERPNext wizard even if its earlier regional slide was lost.

    ERPNext v16's final organization slide can be reopened without the values
    from the earlier regional slide.  Core ERPNext then dereferences a missing
    country while installing fixtures.  Keep every submitted value and fill
    only the missing Thailand-first defaults required by the setup stages.
    """
    from frappe.desk.page.setup_wizard.setup_wizard import get_language_code, setup_complete

    if isinstance(args, str):
        args = json.loads(args)
    values = dict(args or {})
    defaults = {
        "language": "English",
        "country": "Thailand",
        "timezone": "Asia/Bangkok",
        "currency": "THB",
        "chart_of_accounts": "Standard",
        "domain": "Distribution",
        "setup_demo": 0,
        "enable_telemetry": 0,
    }
    for key, value in defaults.items():
        if values.get(key) in (None, ""):
            values[key] = value

    # A failed first attempt may leave the Frappe setup stage marked complete
    # while these Single DocType values are still empty.  In that state core
    # setup replaces the valid request values with the empty stored values
    # before ERPNext installs its fixtures.  Persist the regional values first
    # so both a fresh setup and a retry use the same non-empty configuration.
    system_settings = frappe.get_single("System Settings")
    system_settings.update(
        {
            "language": get_language_code(values["language"]) or "en",
            "country": values["country"],
            "time_zone": values["timezone"],
            "currency": values["currency"],
        }
    )
    system_settings.save(ignore_permissions=True)

    return setup_complete(values)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _default_warehouse(company: str) -> str | None:
    wh = frappe.db.get_value(
        "Warehouse", {"company": company, "is_group": 0}, "name", order_by="creation asc"
    )
    return wh


def _as_list(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return []
    return value or []


def _stock_requirements(items) -> dict[str, float]:
    """Convert requested selling UOM quantities to stock-UOM quantities."""
    from erpnext.stock.get_item_details import get_conversion_factor

    requirements: dict[str, float] = {}
    for row in _as_list(items):
        item_code = row.get("item_code") or row.get("sku")
        if not item_code:
            continue
        qty = flt(row.get("qty"))
        uom = row.get("uom") or frappe.db.get_value("Item", item_code, "stock_uom")
        conversion = flt(get_conversion_factor(item_code, uom).get("conversion_factor") or 1)
        requirements[item_code] = requirements.get(item_code, 0) + qty * conversion
    return requirements


def _active_sales_warehouses(company: str) -> list[str]:
    """Return active leaf warehouses, excluding every transit warehouse."""
    transit = frappe.db.get_value("Company", company, "default_in_transit_warehouse")
    rows = frappe.get_all(
        "Warehouse",
        filters={"company": company, "is_group": 0, "disabled": 0},
        fields=["name", "warehouse_type", "creation"],
        order_by="creation asc",
    )
    return [
        row.name
        for row in rows
        if row.name != transit and (row.warehouse_type or "").casefold() != "transit"
    ]


def _warehouse_can_fulfil(warehouse: str, requirements: dict[str, float]) -> bool:
    from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
        get_available_qty_to_reserve,
    )

    return all(
        flt(get_available_qty_to_reserve(item_code, warehouse)) + 0.000001 >= required
        for item_code, required in requirements.items()
    )


def _candidate_sales_warehouses(company: str, items) -> list[str]:
    """Ordered, valid non-transit selling warehouses for a company/order.

    Prefers a single explicitly requested warehouse, then the LINE selling
    warehouse, then every active non-transit warehouse.
    """
    candidates: list[str] = []
    requested = {
        (row.get("warehouse") or "").strip()
        for row in _as_list(items)
        if (row.get("warehouse") or "").strip()
    }
    if len(requested) == 1:
        candidates.extend(requested)

    settings = frappe.get_single("LINE Channel Settings")
    if settings.get("company") == company and settings.get("selling_warehouse"):
        candidates.append(settings.selling_warehouse)
    candidates.extend(_active_sales_warehouses(company))

    valid: list[str] = []
    for warehouse in dict.fromkeys(candidates):
        if not frappe.db.exists(
            "Warehouse",
            {"name": warehouse, "company": company, "is_group": 0, "disabled": 0},
        ):
            continue
        warehouse_type = frappe.db.get_value("Warehouse", warehouse, "warehouse_type")
        if (warehouse_type or "").casefold() == "transit":
            continue
        valid.append(warehouse)
    return valid


def _insufficient_stock_message(company: str, items) -> str:
    """A clear, itemised Thai reason why an order cannot be fully reserved."""
    from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
        get_available_qty_to_reserve,
    )

    candidates = _candidate_sales_warehouses(company, items)
    if not candidates:
        return _("ไม่พบคลังสินค้าที่ใช้งานได้ (non-transit) ในบริษัท {0}").format(company)
    lines: list[str] = []
    for item_code, required in _stock_requirements(items).items():
        available = max(
            (flt(get_available_qty_to_reserve(item_code, wh)) for wh in candidates), default=0.0
        )
        if available + 0.000001 >= required:
            continue
        item_name = frappe.db.get_value("Item", item_code, "item_name") or item_code
        lines.append(
            _("{0} ({1}) ต้องการ {2:g} มีพร้อมจอง {3:g}").format(
                item_name, item_code, required, available
            )
        )
    detail = "; ".join(lines) or _("สต๊อกไม่พอสำหรับการจอง")
    return _(
        "ไม่สามารถจองสต๊อกในบริษัท {0} ได้: {1} "
        "เปิด 'Allow Invoicing Without Full Stock Reservation' ใน NextGen Automation Settings "
        "เพื่อออกใบแจ้งหนี้แบบสั่งของ (backorder)"
    ).format(company, detail)


def _select_sales_warehouse(
    company: str, items, *, raise_on_missing: bool = True, allow_backorder: bool = False
) -> str | None:
    """Select one non-transit warehouse for an order.

    Returns a warehouse that can fully reserve the order. With ``allow_backorder``
    it falls back to a valid non-transit warehouse even without reservable stock
    (so a backorder Sales Order can still be created).
    """
    requirements = _stock_requirements(items)
    if not requirements and raise_on_missing and not allow_backorder:
        frappe.throw(_("No stock items were supplied for warehouse selection"))

    candidates = _candidate_sales_warehouses(company, items)
    for warehouse in candidates:
        if _warehouse_can_fulfil(warehouse, requirements):
            return warehouse

    if allow_backorder and candidates:
        return candidates[0]
    if raise_on_missing:
        frappe.throw(_insufficient_stock_message(company, items))
    return None


def _select_sales_fulfilment(
    items, *, use_line_settings: bool = True, allow_backorder: bool = False
) -> tuple[str, str, bool]:
    """Resolve (company, warehouse, can_reserve) from LINE settings or live stock.

    ``can_reserve`` is False only when ``allow_backorder`` let us fall back to a
    warehouse that cannot currently reserve the full order.
    """
    settings = frappe.get_single("LINE Channel Settings")
    configured_company = settings.get("company") if use_line_settings else None
    if configured_company:
        warehouse = _select_sales_warehouse(configured_company, items, raise_on_missing=False)
        if warehouse:
            return configured_company, warehouse, True
        if allow_backorder:
            warehouse = _select_sales_warehouse(
                configured_company, items, raise_on_missing=False, allow_backorder=True
            )
            if warehouse:
                return configured_company, warehouse, False
        frappe.throw(_insufficient_stock_message(configured_company, items))

    default_company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
        "Global Defaults", "default_company"
    )
    companies = frappe.get_all("Company", pluck="name", order_by="creation asc")
    ordered_companies = list(dict.fromkeys(([default_company] if default_company else []) + companies))
    for company in ordered_companies:
        warehouse = _select_sales_warehouse(company, items, raise_on_missing=False)
        if warehouse:
            return company, warehouse, True

    if allow_backorder:
        for company in ordered_companies:
            warehouse = _select_sales_warehouse(
                company, items, raise_on_missing=False, allow_backorder=True
            )
            if warehouse:
                return company, warehouse, False

    frappe.throw(
        _insufficient_stock_message(ordered_companies[0], items)
        if ordered_companies
        else _("No company has a non-transit warehouse for this order")
    )


def _require_any_role(allowed: set[str]) -> None:
    if _trusted_web_checkout():
        return
    if frappe.session.user == "Administrator":
        return
    roles = set(frappe.get_roles(frappe.session.user))
    if not roles.intersection(allowed):
        frappe.throw(_("You are not permitted to run this NextGen operation"), frappe.PermissionError)


def _require_service_role() -> None:
    _require_any_role({"System Manager", SERVICE_ROLE})


def _require_operations_role() -> None:
    _require_any_role(OPERATIONS_ROLES)


def _lock_intake(name: str):
    """Lock one intake for the rest of the request transaction."""
    frappe.db.sql("select name from `tabAI Order Intake` where name=%s for update", name)
    return frappe.get_doc("AI Order Intake", name)


def _save_intake(doc) -> None:
    doc.flags.nextgen_transition = True
    doc.save()


BACKORDER_NOTE = "ขายแบบสั่งของ - ออกใบแจ้งหนี้โดยไม่ได้จองสต๊อก (backorder)"


def _note_backorder(doc) -> None:
    """Flag an intake that was invoiced without a stock reservation."""
    reasons = _as_list(doc.exception_reasons)
    if BACKORDER_NOTE not in reasons:
        reasons.append(BACKORDER_NOTE)
    doc.exception_reasons = json.dumps(reasons, ensure_ascii=False)


def _existing_by_reference(doctype: str, external_reference: str):
    if not external_reference or not frappe.get_meta(doctype).has_field(EXTERNAL_REFERENCE_FIELD):
        return None
    name = frappe.db.get_value(doctype, {EXTERNAL_REFERENCE_FIELD: external_reference}, "name")
    return frappe.get_doc(doctype, name) if name else None


def _set_external_reference(doc, external_reference: str) -> None:
    if frappe.get_meta(doc.doctype).has_field(EXTERNAL_REFERENCE_FIELD):
        doc.set(EXTERNAL_REFERENCE_FIELD, external_reference)


def _selling_rate(item_code: str, customer: str, price_list: str | None = None) -> float:
    """Use ERPNext's selling price, never an AI supplied rate."""
    price_list = price_list or frappe.db.get_value("Customer", customer, "default_price_list")
    price_list = price_list or frappe.db.get_single_value("Selling Settings", "selling_price_list")
    if price_list:
        candidates = frappe.get_all(
            "Item Price",
            filters={"item_code": item_code, "price_list": price_list, "selling": 1},
            fields=["price_list_rate", "valid_from", "valid_upto"],
            order_by="valid_from desc, modified desc",
            limit_page_length=20,
        )
        today = getdate(nowdate())
        for price in candidates:
            if price.valid_from and getdate(price.valid_from) > today:
                continue
            if price.valid_upto and getdate(price.valid_upto) < today:
                continue
            return flt(price.price_list_rate)
    return flt(frappe.db.get_value("Item", item_code, "standard_rate"))


def _line_customer_identity(line_id: str, display_name: str | None = None) -> tuple[str, str]:
    """Return a privacy-safe, deterministic ERPNext customer identity for a LINE user."""
    line_id = (line_id or "").strip()
    if not line_id.startswith("U"):
        frappe.throw(_("Automatic customer creation is only available for a LINE user ID"))
    digest = hashlib.sha256(line_id.encode("utf-8")).hexdigest()[:10].upper()
    clean_display_name = " ".join((display_name or "").strip().split())[:80]
    label = clean_display_name or _("LINE Customer")
    return f"{label} [{digest}]", clean_display_name or f"LINE {digest}"


def _resolve_or_create_line_customer(
    line_id: str,
    *,
    create_if_missing: bool = False,
    display_name: str | None = None,
) -> dict:
    """Resolve one LINE user to exactly one ERPNext Customer.

    Customer and map names are deterministic, so webhook retries and concurrent
    delivery cannot create a second customer for the same LINE account.
    """
    line_id = (line_id or "").strip()
    if not line_id:
        frappe.throw(_("LINE ID is required"))

    customer = frappe.db.get_value("LINE Customer Map", line_id, "customer")
    if customer:
        return {"line_id": line_id, "customer": customer, "created": False}
    if not create_if_missing:
        return {"line_id": line_id, "customer": None, "created": False}

    customer_name, map_display_name = _line_customer_identity(line_id, display_name)
    customer = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
    customer_created = False
    if not customer:
        customer_doc = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": customer_name,
                "customer_type": "Individual",
                "customer_group": frappe.db.get_value(
                    "Customer Group", {"is_group": 0}, "name", order_by="creation asc"
                )
                or "All Customer Groups",
                "territory": frappe.db.get_value(
                    "Territory", {"is_group": 0}, "name", order_by="creation asc"
                )
                or "All Territories",
            }
        )
        try:
            customer_doc.insert(ignore_permissions=True)
            customer = customer_doc.name
            customer_created = True
        except frappe.DuplicateEntryError:
            customer = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
            if not customer:
                raise

    try:
        frappe.get_doc(
            {
                "doctype": "LINE Customer Map",
                "line_id": line_id,
                "customer": customer,
                "display_name": map_display_name,
            }
        ).insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        # A webhook retry may have won the race. Always use the established map.
        mapped_customer = frappe.db.get_value("LINE Customer Map", line_id, "customer")
        if not mapped_customer:
            raise
        customer = mapped_customer

    return {
        "line_id": line_id,
        "customer": customer,
        "created": customer_created,
    }


def _row_label(row) -> str:
    """Customer-facing item label; unresolved rows fall back to the raw text, never None."""
    return row.item_name or row.item or (row.raw_text or "").strip() or _("Unidentified item")


def _line_message(doc) -> str:
    item_text = ", ".join(f"{_row_label(row)} x {row.qty:g} {row.uom or ''}".strip() for row in doc.items)
    if doc.status == "Needs Review":
        exceptions = _as_list(doc.exception_reasons)
        inventory_not_found = "ไม่พบสินค้าที่ตรงกับข้อความ"
        if inventory_not_found in exceptions or any(
            row.exception_reason == inventory_not_found for row in doc.items
        ):
            missing_lines = [
                (row.raw_text or "").strip()
                for row in doc.items
                if row.exception_reason == inventory_not_found and (row.raw_text or "").strip()
            ]
            missing_text = ", ".join(dict.fromkeys(missing_lines)) or (doc.source_text or "").strip()
            return (
                f"ขออภัยค่ะ ไม่พบสินค้าที่ตรงกับข้อความในออเดอร์ {doc.name}"
                f"{f': {missing_text}' if missing_text else ''}\n"
                "กรุณาตรวจสอบชื่อสินค้าแล้วส่งรายการใหม่ หรือรอเจ้าหน้าที่ช่วยตรวจสอบค่ะ"
            )
        return f"รับออเดอร์แล้วค่ะ เลขที่ {doc.name} ระบบกำลังให้เจ้าหน้าที่ตรวจสอบรายการ: {item_text}"
    if doc.status == "Awaiting Customer":
        return f"กรุณายืนยันออเดอร์ {doc.name}: {item_text} ยอดประมาณ {doc.total:,.2f} บาท ตอบ ‘ยืนยัน’ หรือ ‘ยกเลิก’"
    if doc.status == "Reserved":
        return f"ยืนยันออเดอร์ {doc.name} แล้ว สินค้าถูกจองและกำลังสร้างใบแจ้งหนี้ค่ะ"
    if doc.status == "Awaiting Payment":
        link = _safe_invoice_download_url(doc.sales_invoice, doc.line_ref) if doc.sales_invoice else ""
        settings = frappe.get_single("NextGen Payment Settings")
        if settings.promptpay_id:
            promptpay = f" PromptPay: {settings.promptpay_id}"
            payment_instruction = "กรุณาสแกน QR และแนบรูปสลิปในแชตนี้ค่ะ"
        else:
            promptpay = ""
            payment_instruction = (
                "ยังไม่สามารถแสดง QR ได้ เนื่องจากร้านค้ายังไม่ได้ตั้งค่า PromptPay "
                "กรุณารอเจ้าหน้าที่แจ้งช่องทางชำระเงินค่ะ"
            )
        if getattr(doc, "payment_verification_status", "") == "Rejected":
            payment_instruction = (
                "สลิปก่อนหน้ายังไม่ผ่านการตรวจสอบ "
                f"{payment_instruction}"
            )
        invoice_line = link or "ลิงก์ใบแจ้งหนี้ยังไม่พร้อม กรุณาติดต่อเจ้าหน้าที่เพื่อขอส่งใหม่ค่ะ"
        return (
            f"ยืนยันออเดอร์ {doc.name} แล้ว ใบแจ้งหนี้ {doc.sales_invoice} "
            f"ยอด {doc.total:,.2f} บาท{promptpay}\n{invoice_line}\n"
            f"{payment_instruction}"
        ).strip()
    if doc.status == "Payment Review":
        return f"ได้รับสลิปสำหรับออเดอร์ {doc.name} แล้ว เจ้าหน้าที่กำลังตรวจสอบการชำระเงินค่ะ"
    if doc.status == "Ready for Delivery":
        return f"ตรวจสอบการชำระเงินออเดอร์ {doc.name} สำเร็จแล้ว กำลังส่งสินค้าให้ทีมจัดส่งค่ะ"
    if doc.status == "Delivered":
        return f"จัดส่งออเดอร์ {doc.name} เรียบร้อยแล้ว ขอบคุณที่ใช้บริการค่ะ"
    if doc.status == "Paid":
        link = _safe_invoice_download_url(doc.sales_invoice, doc.line_ref) if doc.sales_invoice else ""
        return f"ได้รับชำระเงินออเดอร์ {doc.name} แล้ว ขอบคุณค่ะ ใบเสร็จ/ใบแจ้งหนี้: {link}".strip()
    if doc.status == "Rejected":
        return f"ยกเลิกออเดอร์ {doc.name} แล้วค่ะ"
    return f"ออเดอร์ {doc.name}: {doc.status}"


def _queue_line_notification(doc) -> None:
    if getattr(frappe.local.flags, "nextgen_suppress_line_notification", False):
        return
    if not doc.line_ref:
        return
    image_url = None
    if doc.status == "Awaiting Payment" and doc.sales_invoice:
        try:
            image_url = make_promptpay_qr_url(doc.sales_invoice, doc.line_ref)
        except ValueError:
            frappe.log_error(
                "Cannot create public PromptPay QR URL; check NextGen Payment Settings Public Base URL",
                "NextGen public URL unavailable",
            )
    frappe.enqueue(
        "nextgen_erp.line.push_text",
        queue="short",
        enqueue_after_commit=True,
        recipient=doc.line_ref,
        text=_line_message(doc),
        image_url=image_url,
    )


def _queue_delivery_team_notification(doc) -> None:
    settings = frappe.get_single("NextGen Payment Settings")
    recipient = settings.delivery_team_line_id
    if not recipient or not doc.delivery_note:
        return
    link = make_delivery_note_download_url(doc.delivery_note)
    frappe.enqueue(
        "nextgen_erp.line.push_text",
        queue="short",
        enqueue_after_commit=True,
        recipient=recipient,
        text=(
            f"ออเดอร์พร้อมจัดส่ง {doc.name}\n"
            f"Delivery Note: {doc.delivery_note}\n"
            f"ลูกค้า: {doc.customer}\n{link}"
        ),
    )


# --------------------------------------------------------------------------- #
# Order-to-cash primitives (ERPNextAdapter contract)
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def create_sales_order(
    external_reference: str,
    customer: str,
    company: str,
    currency: str = "THB",
    delivery_date: str | None = None,
    items=None,
    reserve_stock: int = 1,
):
    """Create + submit a Sales Order; optionally create/submit a Pick List.

    Returns {"sales_order": name, "pick_list": name|None}.
    """
    _require_operations_role()
    items = _as_list(items)
    if not items:
        frappe.throw(_("create_sales_order requires at least one item"))
    existing = _existing_by_reference("Sales Order", external_reference)
    if existing:
        pick_list = frappe.db.get_value(
            "Pick List Item", {"sales_order": existing.name, "docstatus": ["<", 2]}, "parent"
        )
        if int(reserve_stock or 0) and not pick_list:
            pick_list = _make_pick_list(existing.name)
        return {"sales_order": existing.name, "pick_list": pick_list, "already": True}

    delivery_date = delivery_date or nowdate()
    # When reserving, require a warehouse that can fully fulfil the order. When
    # not reserving (backorder), any valid non-transit warehouse is acceptable.
    warehouse = _select_sales_warehouse(
        company, items, allow_backorder=not int(reserve_stock or 0)
    )

    so = frappe.new_doc("Sales Order")
    so.customer = customer
    so.company = company
    so.currency = currency
    so.conversion_rate = 1
    so.transaction_date = nowdate()
    so.delivery_date = delivery_date
    so.order_type = "Sales"
    so.po_no = external_reference
    so.set_warehouse = warehouse
    # ERPNext does not allow creating a Pick List after stock is reserved on the
    # Sales Order. We therefore create the Pick List first and reserve against it.
    so.reserve_stock = 0
    _set_external_reference(so, external_reference)
    for row in items:
        item_code = row.get("item_code") or row.get("sku")
        if not item_code or not frappe.db.exists("Item", item_code):
            frappe.throw(_("Unknown ERPNext Item: {0}").format(item_code or "(blank)"))
        rate = _selling_rate(item_code, customer)
        if rate <= 0:
            frappe.throw(_("No valid selling price is configured for Item {0}").format(item_code))
        so.append(
            "items",
            {
                "item_code": item_code,
                "qty": flt(row.get("qty")),
                "uom": row.get("uom"),
                "rate": rate,
                "warehouse": warehouse,
                "delivery_date": delivery_date,
                # Header stays off so ERPNext permits Pick List creation; the
                # row flag allows reservation entries to be created from that Pick List.
                "reserve_stock": 1 if int(reserve_stock or 0) else 0,
            },
        )
    try:
        so.insert()
    except frappe.DuplicateEntryError:
        existing = _existing_by_reference("Sales Order", external_reference)
        if not existing:
            raise
        pick_list = frappe.db.get_value(
            "Pick List Item", {"sales_order": existing.name, "docstatus": ["<", 2]}, "parent"
        )
        return {"sales_order": existing.name, "pick_list": pick_list, "already": True}
    so.submit()

    pick_list = None
    if int(reserve_stock or 0):
        pick_list = _make_pick_list(so.name)

    return {"sales_order": so.name, "pick_list": pick_list}


def _make_pick_list(sales_order: str) -> str:
    """Create a Pick List or fail the whole transaction."""
    from erpnext.selling.doctype.sales_order.sales_order import create_pick_list

    pl = create_pick_list(sales_order)
    if not pl.locations:
        frappe.throw(_("ERPNext could not allocate stock to a Pick List for {0}").format(sales_order))
    pl.pick_manually = 1
    for row in pl.locations:
        row.picked_qty = row.stock_qty
    pl.insert()
    pl.submit()
    pl.create_stock_reservation_entries(notify=False)
    expected = sum(flt(row.picked_qty or row.stock_qty) for row in pl.locations)
    reserved = sum(
        flt(value)
        for value in frappe.get_all(
            "Stock Reservation Entry",
            filters={
                "from_voucher_type": "Pick List",
                "from_voucher_no": pl.name,
                "docstatus": 1,
            },
            pluck="reserved_qty",
        )
    )
    if expected <= 0 or reserved + 0.000001 < expected:
        frappe.throw(
            _("Full stock reservation could not be created for Pick List {0}: reserved {1} of {2}").format(
                pl.name, reserved, expected
            )
        )
    return pl.name


@frappe.whitelist()
def create_invoice_from_sales_order(external_reference: str, sales_order: str):
    """Create and submit a Sales Invoice before delivery (payment-first flow)."""
    _require_operations_role()
    invoice_ref = f"{external_reference}:invoice"
    existing = _existing_by_reference("Sales Invoice", invoice_ref)
    if existing:
        return {"sales_invoice": existing.name, "already": True}

    from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

    invoice = make_sales_invoice(sales_order)
    _set_external_reference(invoice, invoice_ref)
    invoice.insert()
    invoice.submit()
    return {"sales_invoice": invoice.name}


@frappe.whitelist()
def create_draft_delivery_note(external_reference: str, sales_order: str):
    """Create a draft Delivery Note after payment; delivery submits it later."""
    _require_operations_role()
    delivery_ref = f"{external_reference}:delivery"
    existing = _existing_by_reference("Delivery Note", delivery_ref)
    if existing:
        return {"delivery_note": existing.name, "already": True}

    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

    delivery_note = make_delivery_note(sales_order)
    _set_external_reference(delivery_note, delivery_ref)
    delivery_note.insert()
    return {"delivery_note": delivery_note.name}


@frappe.whitelist()
def deliver_and_invoice(
    external_reference: str,
    sales_order: str,
    pick_list: str | None = None,
    items=None,
):
    """Create + submit a Delivery Note from the SO, then a Sales Invoice.

    Returns {"delivery_note": name, "sales_invoice": name}.
    """
    _require_operations_role()
    delivery_ref = f"{external_reference}:delivery"
    invoice_ref = f"{external_reference}:invoice"
    existing_dn = _existing_by_reference("Delivery Note", delivery_ref)
    existing_si = _existing_by_reference("Sales Invoice", invoice_ref)
    if existing_dn and existing_si:
        return {
            "delivery_note": existing_dn.name,
            "sales_invoice": existing_si.name,
            "already": True,
        }

    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

    dn = existing_dn or make_delivery_note(sales_order)
    if not existing_dn:
        _set_external_reference(dn, delivery_ref)
        dn.insert()
        dn.submit()

    si = existing_si or make_sales_invoice(dn.name)
    if not existing_si:
        _set_external_reference(si, invoice_ref)
        si.insert()
        si.submit()

    return {"delivery_note": dn.name, "sales_invoice": si.name}


@frappe.whitelist()
def record_payment(
    external_reference: str,
    company: str,
    customer: str,
    currency: str = "THB",
    amount: float = 0,
    reference_no: str | None = None,
    reference_date: str | None = None,
    sales_invoice: str | None = None,
):
    """Create + submit a Payment Entry allocated to the Sales Invoice.

    Returns {"payment_entry": name}.
    """
    _require_operations_role()
    existing = _existing_by_reference("Payment Entry", external_reference)
    if existing:
        return {"payment_entry": existing.name, "already": True}
    if not sales_invoice:
        frappe.throw(_("sales_invoice is required"))

    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    pe = get_payment_entry("Sales Invoice", sales_invoice)
    _set_external_reference(pe, external_reference)
    pe.reference_no = reference_no or external_reference
    pe.reference_date = reference_date or nowdate()
    invoice_outstanding = flt(frappe.db.get_value("Sales Invoice", sales_invoice, "outstanding_amount"))
    if invoice_outstanding <= 0:
        frappe.throw(_("Sales Invoice {0} has no outstanding amount").format(sales_invoice))
    if amount and abs(flt(amount) - invoice_outstanding) > 0.01:
        frappe.throw(
            _("Payment amount {0} does not match invoice outstanding amount {1}").format(
                flt(amount), invoice_outstanding
            )
        )
    pe.paid_amount = invoice_outstanding
    pe.received_amount = invoice_outstanding
    for reference in pe.references:
        if reference.reference_doctype == "Sales Invoice" and reference.reference_name == sales_invoice:
            reference.allocated_amount = invoice_outstanding
    pe.insert()
    pe.submit()

    return {"payment_entry": pe.name}


# --------------------------------------------------------------------------- #
# Intake orchestration
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def create_ai_order_intake(payload=None):
    """Create an AI Order Intake from the external service (idempotent).

    payload keys: idempotency_key (reqd), merchant, line_ref, customer,
    source_channel, source_text, confidence, total, automation_mode,
    exception_reasons (list), items (list of line dicts).
    """
    _require_service_role()
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload = payload or {}
    key = payload.get("idempotency_key")
    if not key:
        frappe.throw(_("idempotency_key is required"))

    existing = frappe.db.get_value("AI Order Intake", {"idempotency_key": key}, "name")
    if existing:
        return {"name": existing, "created": False, "status": frappe.db.get_value("AI Order Intake", existing, "status")}

    exceptions = _as_list(payload.get("exception_reasons"))
    doc = frappe.new_doc("AI Order Intake")
    doc.idempotency_key = key
    doc.merchant = payload.get("merchant") or "demo"
    doc.line_ref = payload.get("line_ref")
    doc.customer = payload.get("customer")
    doc.source_channel = payload.get("source_channel") or "simulator"
    if not doc.customer and doc.source_channel == "line" and doc.line_ref:
        mapping = _resolve_or_create_line_customer(
            doc.line_ref,
            create_if_missing=True,
            display_name=payload.get("line_display_name"),
        )
        doc.customer = mapping.get("customer")
    doc.source_text = payload.get("source_text")
    doc.confidence = flt(payload.get("confidence"))
    requested_automation = payload.get("automation_mode") == "automatic"
    threshold = flt(
        frappe.db.get_single_value("NextGen Automation Settings", "confidence_threshold") or 0.95
    )
    auto_enabled = bool(
        frappe.db.get_single_value("NextGen Automation Settings", "auto_confirm")
    )
    doc.automation_mode = "human_review"
    doc.exception_reasons = json.dumps(exceptions, ensure_ascii=False) if exceptions else None
    calculated_total = 0.0
    for row in _as_list(payload.get("items")):
        item_code = row.get("item_code") or row.get("sku")
        rate = _selling_rate(item_code, doc.customer) if item_code and doc.customer else 0.0
        qty = flt(row.get("qty"))
        amount = qty * rate
        calculated_total += amount
        doc.append(
            "items",
            {
                "raw_text": row.get("raw_text"),
                "item": item_code,
                "qty": qty,
                "uom": row.get("uom"),
                "rate": rate,
                "amount": amount,
                "confidence": flt(row.get("confidence")),
                "exception_reason": row.get("exception_reason"),
            },
        )
    doc.total = calculated_total
    auto = bool(
        auto_enabled
        and requested_automation
        and doc.customer
        and doc.items
        and not exceptions
        and doc.confidence >= threshold
        and all(row.item and not row.exception_reason for row in doc.items)
    )
    doc.automation_mode = "automatic" if auto else "human_review"
    doc.status = "Awaiting Customer" if auto else "Needs Review"
    try:
        doc.insert()
    except frappe.DuplicateEntryError:
        existing = frappe.db.get_value("AI Order Intake", {"idempotency_key": key}, "name")
        if not existing:
            raise
        return {
            "name": existing,
            "created": False,
            "status": frappe.db.get_value("AI Order Intake", existing, "status"),
        }
    _queue_line_notification(doc)
    return {"name": doc.name, "created": True, "status": doc.status}


@frappe.whitelist()
def approve_ai_order_intake(name: str, reviewer: str | None = None, note: str | None = None):
    """Human approval — move a reviewed intake to Awaiting Customer."""
    _require_operations_role()
    doc = _lock_intake(name)
    if doc.status in (
        "Reserved",
        "Awaiting Payment",
        "Payment Review",
        "Ready for Delivery",
        "Delivered",
        "Paid",
    ):
        return {"name": name, "status": doc.status, "already": True}
    if not doc.customer and doc.source_channel == "line" and doc.line_ref:
        mapping = _resolve_or_create_line_customer(doc.line_ref, create_if_missing=True)
        doc.customer = mapping.get("customer")
    doc.status = "Awaiting Customer"
    doc.reviewer = reviewer or frappe.session.user
    if note:
        doc.decision_note = note
    if not doc.customer or not doc.items or any(not row.item for row in doc.items):
        frappe.throw(_("Resolve the customer and every item before approval"))
    doc.total = 0
    for row in doc.items:
        row.rate = _selling_rate(row.item, doc.customer)
        row.amount = flt(row.qty) * flt(row.rate)
        doc.total += row.amount
    _save_intake(doc)
    _queue_line_notification(doc)
    return {"name": name, "status": doc.status}


@frappe.whitelist()
def record_customer_confirmation(name: str, confirmed: int = 1):
    """Customer confirmation — reserve stock, invoice, and request payment."""
    _require_operations_role()
    doc = _lock_intake(name)
    if not int(confirmed or 0):
        if doc.status == "Rejected":
            return {"name": name, "status": doc.status, "already": True}
        doc.status = "Rejected"
        _save_intake(doc)
        _queue_line_notification(doc)
        return {"name": name, "status": doc.status}

    if doc.sales_invoice:
        # Confirmation can be retried when the original LINE delivery failed.
        # Re-send the same signed invoice/QR without creating any new document.
        _queue_line_notification(doc)
        return {
            "name": name,
            "status": doc.status,
            "sales_order": doc.sales_order,
            "sales_invoice": doc.sales_invoice,
            "already": True,
        }
    if doc.status not in ("Awaiting Customer", "Reserved"):
        frappe.throw(_("Intake must be Awaiting Customer before confirmation"))

    items = [
        {"item_code": r.item, "qty": r.qty, "uom": r.uom, "rate": r.rate}
        for r in doc.items
    ]
    # Defaults ON via install._ensure_backorder_default(), which writes an
    # explicit Singles row (get_single_value coerces an unset Check to 0, so the
    # field's "1" default alone would not take effect on existing sites).
    allow_backorder = bool(
        frappe.db.get_single_value("NextGen Automation Settings", "allow_backorder_invoicing")
    )
    company, warehouse, can_reserve = _select_sales_fulfilment(
        items,
        use_line_settings=doc.source_channel == "line",
        allow_backorder=allow_backorder,
    )
    for row in items:
        row["warehouse"] = warehouse
    result = {}
    if not doc.sales_order:
        result = create_sales_order(
            external_reference=doc.name,
            customer=doc.customer,
            company=company,
            currency="THB",
            delivery_date=nowdate(),
            items=items,
            reserve_stock=1 if can_reserve else 0,
        )
        doc.sales_order = result["sales_order"]
        doc.pick_list = result.get("pick_list")
        # Only claim "Reserved" when stock was actually reserved; a backorder
        # goes straight to invoicing without implying a reservation exists.
        if can_reserve:
            doc.status = "Reserved"
        else:
            _note_backorder(doc)
            doc.status = "Awaiting Payment"
        _save_intake(doc)
    invoice = create_invoice_from_sales_order(doc.name, doc.sales_order)
    doc.sales_invoice = invoice["sales_invoice"]
    doc.status = "Awaiting Payment"
    _save_intake(doc)
    _queue_line_notification(doc)
    return {"name": name, "status": doc.status, **result, **invoice}


@frappe.whitelist()
def progress_delivery(name: str):
    """Compatibility action: move a legacy Reserved intake to payment-first invoicing."""
    _require_operations_role()
    doc = _lock_intake(name)
    if doc.sales_invoice:
        return {
            "name": name,
            "status": doc.status,
            "sales_invoice": doc.sales_invoice,
            "already": True,
        }
    if doc.status != "Reserved":
        frappe.throw(_("Intake must be Reserved before delivery"))
    result = create_invoice_from_sales_order(doc.name, doc.sales_order)
    doc.sales_invoice = result["sales_invoice"]
    doc.status = "Awaiting Payment"
    _save_intake(doc)
    _queue_line_notification(doc)
    return {"name": name, "status": doc.status, **result}


@frappe.whitelist()
def progress_payment(name: str, reference_no: str | None = None):
    """Allocate verified payment, prepare a draft Delivery Note, notify both parties."""
    _require_operations_role()
    doc = _lock_intake(name)
    if doc.payment_entry and doc.delivery_note:
        return {
            "name": name,
            "status": doc.status,
            "payment_entry": doc.payment_entry,
            "delivery_note": doc.delivery_note,
            "already": True,
        }
    if doc.status not in ("Awaiting Payment", "Payment Review"):
        frappe.throw(_("Intake must be Awaiting Payment before recording payment"))
    company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
        "Global Defaults", "default_company"
    )
    result = {}
    if not doc.payment_entry:
        result = record_payment(
            external_reference=doc.name,
            company=company,
            customer=doc.customer,
            currency="THB",
            amount=doc.total,
            reference_no=reference_no,
            sales_invoice=doc.sales_invoice,
        )
        doc.payment_entry = result["payment_entry"]
    delivery = create_draft_delivery_note(doc.name, doc.sales_order)
    doc.delivery_note = delivery["delivery_note"]
    doc.status = "Ready for Delivery"
    _save_intake(doc)
    _queue_line_notification(doc)
    _queue_delivery_team_notification(doc)
    return {"name": name, "status": doc.status, **result, **delivery}


@frappe.whitelist()
def complete_delivery(name: str):
    """Delivery-team completion submits the prepared Delivery Note."""
    _require_operations_role()
    doc = _lock_intake(name)
    if doc.status == "Delivered":
        return {
            "name": name,
            "status": doc.status,
            "delivery_note": doc.delivery_note,
            "already": True,
        }
    if doc.status != "Ready for Delivery" or not doc.delivery_note:
        frappe.throw(_("Intake must be Ready for Delivery"))
    delivery_note = frappe.get_doc("Delivery Note", doc.delivery_note)
    if delivery_note.docstatus == 0:
        delivery_note.submit()
    doc.status = "Delivered"
    _save_intake(doc)
    _queue_line_notification(doc)
    return {"name": name, "status": doc.status, "delivery_note": doc.delivery_note}


# --------------------------------------------------------------------------- #
# Integrations — LINE config (read by the external service) + quick intake
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def get_line_config():
    """Return LINE Channel Settings for the authenticated external service.

    Password fields are returned decrypted; only expose to a scoped API user.
    """
    _require_service_role()
    s = frappe.get_single("LINE Channel Settings")
    return {
        "enabled": bool(s.enabled),
        "merchant": s.merchant or "demo",
        "channel_id": s.channel_id or "",
        "channel_secret": s.get_password("channel_secret", raise_exception=False) or "",
        "webhook_url": s.webhook_url or "",
    }


@frappe.whitelist()
def resolve_line_customer(
    line_id: str,
    create_if_missing: int = 0,
    display_name: str | None = None,
):
    """Resolve a verified LINE sender, optionally onboarding a new customer."""
    _require_service_role()
    return _resolve_or_create_line_customer(
        line_id,
        create_if_missing=bool(int(create_if_missing or 0)),
        display_name=display_name,
    )


@frappe.whitelist()
def get_automation_settings():
    _require_service_role()
    return {
        "confidence_threshold": flt(
            frappe.db.get_single_value("NextGen Automation Settings", "confidence_threshold")
            or 0.95
        ),
        "auto_route_high_confidence": bool(
            frappe.db.get_single_value("NextGen Automation Settings", "auto_confirm")
        ),
    }


@frappe.whitelist()
def handle_line_reply(line_id: str, text: str, event_id: str | None = None):
    """Apply a LINE yes/no reply to the customer's current confirmation request."""
    _require_service_role()
    if event_id:
        existing = frappe.db.get_value("LINE Event Receipt", event_id, "result_json")
        if existing:
            result = json.loads(existing)
            result["duplicate"] = True
            return result
    normalized = "".join((text or "").strip().lower().split())
    yes = {"ยืนยัน", "ตกลง", "โอเค", "ok", "yes", "confirm", "ใช่"}
    no = {"ยกเลิก", "ไม่เอา", "ไม่ยืนยัน", "cancel", "no"}
    if normalized not in yes | no:
        return {"handled": False}
    name = frappe.db.get_value(
        "AI Order Intake",
        {"line_ref": line_id, "status": "Awaiting Customer"},
        "name",
        order_by="modified desc",
    )
    if not name:
        # The first confirmation may have completed ERP documents while its
        # asynchronous LINE push failed. Treat a new affirmative reply as a
        # request to resend the existing invoice, never as a new order.
        if normalized in yes:
            name = frappe.db.get_value(
                "AI Order Intake",
                {
                    "line_ref": line_id,
                    "status": "Awaiting Payment",
                    "sales_invoice": ["is", "set"],
                },
                "name",
                order_by="modified desc",
            )
            if name:
                doc = frappe.get_doc("AI Order Intake", name)
                _queue_line_notification(doc)
                response = {
                    "handled": True,
                    "name": name,
                    "status": doc.status,
                    "sales_order": doc.sales_order,
                    "sales_invoice": doc.sales_invoice,
                    "already": True,
                    "resent": True,
                }
                if event_id:
                    frappe.get_doc(
                        {
                            "doctype": "LINE Event Receipt",
                            "event_id": event_id,
                            "line_id": line_id,
                            "result_json": json.dumps(response, ensure_ascii=False),
                        }
                    ).insert()
                return response
        return {"handled": False}
    result = record_customer_confirmation(name, confirmed=1 if normalized in yes else 0)
    response = {"handled": True, **result}
    if event_id:
        frappe.get_doc(
            {
                "doctype": "LINE Event Receipt",
                "event_id": event_id,
                "line_id": line_id,
                "result_json": json.dumps(response, ensure_ascii=False),
            }
        ).insert()
    return response


def _first_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return " ".join(match.group(1).strip().split())
    return ""


def _duplicate_payment_reference(reference_no: str, current_intake: str) -> str | None:
    if not reference_no:
        return None
    intake = frappe.db.exists(
        "AI Order Intake",
        {"payment_reference": reference_no, "name": ["!=", current_intake]},
    )
    if intake:
        return f"AI Order Intake {intake}"
    payment = frappe.db.exists(
        "Payment Entry",
        {"reference_no": reference_no, "docstatus": ["<", 2]},
    )
    return f"Payment Entry {payment}" if payment else None


def _parse_slip_ocr(raw_text: str, doc, settings) -> dict:
    amount_text = _first_match(
        [
            r"(?:จำนวนเงิน|ยอดเงิน|amount|total)\s*[:：]?\s*(?:฿|THB)?\s*([\d,]+(?:\.\d{1,2})?)",
            r"(?:฿|THB)\s*([\d,]+(?:\.\d{1,2})?)",
            r"([\d,]+\.\d{2})\s*(?:บาท|THB)",
        ],
        raw_text,
    )
    amount = flt(amount_text.replace(",", "")) if amount_text else 0
    reference_no = _first_match(
        [
            r"(?:เลขที่รายการ|รหัสรายการ|transaction\s*(?:id|no\.?)|reference\s*(?:id|no\.?)|ref\.?)\s*[:：#]?\s*([A-Z0-9-]{6,})",
        ],
        raw_text,
    )
    transaction_at = _first_match(
        [
            r"(?:วันที่และเวลา|วันเวลา|date(?:/time)?|transaction\s*time)\s*[:：]?\s*([^\n]{5,50})",
        ],
        raw_text,
    )
    sender = _first_match([r"(?:จาก|ผู้โอน|sender|from)\s*[:：]?\s*([^\n]{2,100})"], raw_text)
    recipient = _first_match([r"(?:ไปยัง|ผู้รับ|recipient|to)\s*[:：]?\s*([^\n]{2,100})"], raw_text)
    expected_amount = flt(doc.total)
    amount_matches = bool(amount_text) and abs(amount - expected_amount) <= 0.01
    expected_recipient = (settings.promptpay_name or "").strip()
    recipient_matches = not expected_recipient or expected_recipient.casefold() in raw_text.casefold()
    duplicate_document = _duplicate_payment_reference(reference_no, doc.name)
    duplicate = bool(duplicate_document)
    flags = []
    if not amount_text:
        flags.append("amount_missing")
    elif not amount_matches:
        flags.append("amount_mismatch")
    if not reference_no:
        flags.append("reference_missing")
    if not recipient_matches:
        flags.append("recipient_mismatch")
    if duplicate:
        flags.append("duplicate_reference")
    confidence = min(0.95, 0.35 + (0.25 if amount_text else 0) + (0.2 if reference_no else 0) + (0.15 if transaction_at else 0))
    if confidence < flt(settings.slip_confidence_threshold or 0.95):
        flags.append("low_confidence")
    return {
        "verified": False,
        "confidence": confidence,
        "amount": amount,
        "reference_no": reference_no,
        "transaction_at": transaction_at,
        "sender": sender,
        "recipient": recipient,
        "amount_matches": amount_matches,
        "recipient_matches": recipient_matches,
        "duplicate_reference": duplicate,
        "duplicate_document": duplicate_document,
        "flags": list(dict.fromkeys(flags)),
        "reason": ", ".join(dict.fromkeys(flags)) or "awaiting_human_review",
        "raw_text": raw_text[:20000],
    }


def _verify_payment_slip(content: bytes, doc) -> dict:
    """Extract a slip with Typhoon OCR. OCR never authorizes a payment."""
    settings = frappe.get_single("NextGen Payment Settings")
    token = settings.get_password("slip_verification_api_key", raise_exception=False) or ""
    if not token:
        return {"verified": False, "confidence": 0, "reason": "typhoon_not_configured", "flags": ["typhoon_not_configured"]}
    from typhoon_ocr import ocr_document

    suffix = ".png" if content.startswith(b"\x89PNG") else ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix) as image:
        image.write(content)
        image.flush()
        raw_text = ocr_document(
            image.name,
            base_url=(settings.slip_verification_url or "https://api.opentyphoon.ai/v1").strip(),
            api_key=token,
            model=(settings.slip_ocr_model or "typhoon-ocr").strip(),
        )
    return _parse_slip_ocr(str(raw_text or ""), doc, settings)


@frappe.whitelist()
def handle_line_payment_slip(
    line_id: str,
    message_id: str,
    event_id: str | None = None,
    content_type: str = "image",
):
    """Download a verified LINE image, attach it to the open invoice, and assess it."""
    _require_service_role()
    if content_type != "image" or not message_id:
        frappe.throw(_("A LINE image message is required"))
    if event_id:
        existing = frappe.db.get_value("LINE Event Receipt", event_id, "result_json")
        if existing:
            result = json.loads(existing)
            result["duplicate"] = True
            return result
    name = frappe.db.get_value(
        "AI Order Intake",
        {"line_ref": line_id, "status": ["in", ["Awaiting Payment", "Payment Review"]]},
        "name",
        order_by="modified desc",
    )
    if not name:
        return {"handled": False, "reason": "no_invoice_awaiting_payment"}
    doc = frappe.get_doc("AI Order Intake", name)
    line_settings = frappe.get_single("LINE Channel Settings")
    access_token = line_settings.get_password("channel_access_token", raise_exception=False) or ""
    if not access_token:
        frappe.throw(_("LINE Channel Access Token is required to download payment slips"))
    from nextgen_erp.line import data_api_base

    response = requests.get(
        f"{data_api_base()}/v2/bot/message/{quote(message_id, safe='')}/content",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    response.raise_for_status()
    content = response.content
    if not content or len(content) > 10 * 1024 * 1024:
        frappe.throw(_("Payment slip must be a non-empty image smaller than 10 MB"))
    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": f"line-slip-{message_id}.jpg",
            "is_private": 1,
            "content": content,
            "attached_to_doctype": "AI Order Intake",
            "attached_to_name": name,
            "attached_to_field": "payment_slip",
        }
    ).insert(ignore_permissions=True)
    doc.payment_slip = file_doc.file_url
    try:
        verification = _verify_payment_slip(content, doc)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "NextGen slip verification failed")
        verification = {"verified": False, "confidence": 0, "reason": "verifier_error"}
    doc.payment_verification_confidence = verification.get("confidence") or 0
    doc.payment_reference = verification.get("reference_no")
    doc.payment_verification_note = verification.get("reason")
    doc.payment_extraction_json = json.dumps(verification, ensure_ascii=False)
    doc.payment_duplicate_reference = int(bool(verification.get("duplicate_reference")))
    doc.payment_verification_status = "Needs Review"
    doc.status = "Payment Review"
    _save_intake(doc)
    _queue_line_notification(doc)
    result = {"handled": True, "name": name, "status": doc.status, "verification": verification}
    if event_id:
        frappe.get_doc(
            {
                "doctype": "LINE Event Receipt",
                "event_id": event_id,
                "line_id": line_id,
                "result_json": json.dumps(result, ensure_ascii=False),
            }
        ).insert()
    return result


@frappe.whitelist()
def approve_payment_slip(name: str, reference_no: str):
    """Human fallback for a low-confidence or unavailable slip verifier."""
    _require_operations_role()
    doc = _lock_intake(name)
    if doc.status != "Payment Review":
        frappe.throw(_("Intake must be in Payment Review"))
    reference_no = (reference_no or doc.payment_reference or "").strip()
    if not reference_no:
        frappe.throw(_("A bank reference is required"))
    duplicate = _duplicate_payment_reference(reference_no, doc.name)
    if duplicate:
        frappe.throw(_("Payment reference {0} is already used by {1}").format(reference_no, duplicate))
    doc.payment_verification_status = "Verified"
    doc.payment_reference = reference_no
    doc.reviewer = frappe.session.user
    doc.payment_reviewed_at = now_datetime()
    doc.payment_reviewed_by = frappe.session.user
    _save_intake(doc)
    return progress_payment(name, reference_no)


@frappe.whitelist()
def reject_payment_slip(name: str, note: str | None = None):
    """Reject one reviewed image without cancelling the order or recording payment."""
    _require_operations_role()
    doc = _lock_intake(name)
    if doc.status != "Payment Review":
        frappe.throw(_("Intake must be in Payment Review"))
    doc.payment_verification_status = "Rejected"
    doc.payment_verification_note = note or _("Slip rejected by staff; customer must send a new slip")
    doc.payment_reviewed_at = now_datetime()
    doc.payment_reviewed_by = frappe.session.user
    doc.reviewer = frappe.session.user
    doc.status = "Awaiting Payment"
    _save_intake(doc)
    _queue_line_notification(doc)
    return {"name": name, "status": doc.status, "payment_verification_status": "Rejected"}


@frappe.whitelist()
def get_catalog(warehouse: str | None = None, price_list: str | None = None, limit_start: int = 0, limit: int = 500):
    """Return a page of sellable catalog data in three database reads."""
    # This is the single catalog read boundary for the external LINE service,
    # Staff Chat and Order Intake. All callers see the same Item, Item Price
    # and Bin data; no assistant owns a separate product catalog.
    _require_operations_role()
    warehouse = (warehouse or "").strip() or None
    if warehouse and not frappe.db.exists(
        "Warehouse", {"name": warehouse, "is_group": 0, "disabled": 0}
    ):
        warehouse = None
    if not warehouse:
        company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
            "Global Defaults", "default_company"
        )
        warehouse = _default_warehouse(company) if company else None
    price_list = (price_list or "").strip() or frappe.db.get_single_value(
        "Selling Settings", "selling_price_list"
    )
    limit = min(max(int(limit or 500), 1), 1000)
    limit_start = max(int(limit_start or 0), 0)
    fields = ["item_code", "item_name", "stock_uom", "standard_rate", "item_group"]
    if frappe.get_meta("Item").has_field("custom_nextgen_aliases"):
        fields.append("custom_nextgen_aliases")
    items = frappe.get_all(
        "Item",
        fields=fields,
        filters={"disabled": 0, "is_sales_item": 1},
        order_by="item_code",
        limit_start=limit_start,
        limit_page_length=limit,
    )
    codes = [row.item_code for row in items]
    bins = frappe.get_all(
        "Bin",
        fields=["item_code", "projected_qty"],
        filters={"warehouse": warehouse, "item_code": ["in", codes]},
        limit_page_length=max(len(codes), 1),
    ) if codes and warehouse else []
    prices = frappe.get_all(
        "Item Price",
        fields=["item_code", "price_list_rate", "valid_from", "valid_upto"],
        filters={"selling": 1, "price_list": price_list, "item_code": ["in", codes]},
        order_by="valid_from desc, modified desc",
        limit_page_length=max(len(codes) * 3, 1),
    ) if codes and price_list else []
    stock_by_item = {}
    for row in bins:
        stock_by_item[row.item_code] = stock_by_item.get(row.item_code, 0) + flt(row.projected_qty)
    price_by_item = {}
    today = getdate(nowdate())
    for row in prices:
        if row.valid_from and getdate(row.valid_from) > today:
            continue
        if row.valid_upto and getdate(row.valid_upto) < today:
            continue
        price_by_item.setdefault(row.item_code, flt(row.price_list_rate))
    routes = {}
    catalog_origin = ""
    if codes and frappe.db.exists("DocType", "Website Item"):
        routes = {
            row.item_code: row.route
            for row in frappe.get_all(
                "Website Item",
                filters={"item_code": ["in", codes], "published": 1},
                fields=["item_code", "route"],
                limit_page_length=max(len(codes), 1),
            )
        }
        try:
            catalog_origin = public_base_url()
        except ValueError:
            catalog_origin = ""
    data = []
    for row in items:
        aliases = _as_list(row.get("custom_nextgen_aliases"))
        if isinstance(row.get("custom_nextgen_aliases"), str) and not aliases:
            aliases = [part.strip() for part in row.custom_nextgen_aliases.split(",") if part.strip()]
        data.append(
            {
                "item_code": row.item_code,
                "item_name": row.item_name,
                "stock_uom": row.stock_uom,
                "item_group": row.item_group,
                "aliases": aliases,
                "price": price_by_item.get(row.item_code, flt(row.standard_rate)),
                # Without a configured warehouse we can still answer public
                # catalog/price questions, but must not claim a stock number.
                "projected_qty": stock_by_item.get(row.item_code, 0) if warehouse else None,
                "warehouse": warehouse or None,
                "route": (
                    f"{catalog_origin}/{routes[row.item_code].lstrip('/')}"
                    if routes.get(row.item_code) and catalog_origin
                    else None
                ),
            }
        )
    return {"data": data, "has_more": len(items) == limit, "next_start": limit_start + len(items)}


@frappe.whitelist()
def get_projected_qty(item_code: str, warehouse: str):
    _require_operations_role()
    return {
        "item_code": item_code,
        "warehouse": warehouse,
        "projected_qty": flt(
            frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "projected_qty")
        ),
    }


@frappe.whitelist()
def get_item_price(item_code: str, customer: str | None = None, price_list: str | None = None):
    _require_operations_role()
    if not customer and not price_list:
        frappe.throw(_("customer or price_list is required"))
    rate = _selling_rate(item_code, customer, price_list) if customer else flt(
        frappe.db.get_value(
            "Item Price",
            {"item_code": item_code, "price_list": price_list, "selling": 1},
            "price_list_rate",
            order_by="valid_from desc, modified desc",
        )
    )
    return {"item_code": item_code, "price_list": price_list, "rate": rate}


def _invoice_token(invoice: str, line_ref: str | None, expires: int) -> str:
    payload = json.dumps(
        {"invoice": invoice, "line_ref": line_ref or "", "expires": expires},
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    key = frappe.get_site_config().encryption_key.encode()
    signature = hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def public_base_url() -> str:
    """Return the canonical externally reachable origin for every customer link."""
    configured = ""
    if frappe.db.exists("DocType", "NextGen Payment Settings"):
        configured = frappe.db.get_single_value("NextGen Payment Settings", "public_base_url") or ""
    value = (configured or get_url() or "").strip().rstrip("/")
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    temporary_host = (
        host.endswith(".ngrok-free.app")
        or host.endswith(".ngrok-free.dev")
        or host.endswith(".ngrok.app")
        or host.endswith(".ngrok.io")
    )
    allow_temporary = str(
        frappe.conf.get("allow_temporary_public_url") or ""
    ).strip().casefold() in {"1", "true", "yes", "on"}
    unsafe = (
        parsed.scheme not in {"http", "https"}
        or not host
        or host in {"localhost", "127.0.0.1", "0.0.0.0"}
        or (temporary_host and not allow_temporary)
    )
    production = not bool(frappe.conf.get("developer_mode"))
    if unsafe and production:
        raise ValueError("A stable public HTTPS base URL is required")
    if production and parsed.scheme != "https":
        raise ValueError("The production public base URL must use HTTPS")
    return value


def make_invoice_download_url(invoice: str, line_ref: str | None = None) -> str:
    days = int(frappe.db.get_single_value("NextGen Automation Settings", "invoice_link_days") or 7)
    token = _invoice_token(invoice, line_ref, int(time.time()) + days * 86400)
    return f"{public_base_url()}/api/method/nextgen_erp.api.download_invoice?token={quote(token)}"


def _safe_invoice_download_url(invoice: str, line_ref: str | None = None) -> str:
    try:
        return make_invoice_download_url(invoice, line_ref)
    except ValueError:
        frappe.log_error(
            "Cannot create invoice URL; configure a stable HTTPS Public Base URL",
            "NextGen public URL unavailable",
        )
        return ""


def _decode_invoice_token(token: str) -> dict:
    encoded, supplied_signature = token.rsplit(".", 1)
    key = frappe.get_site_config().encryption_key.encode()
    expected = hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied_signature):
        raise ValueError("bad signature")
    raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    payload = json.loads(raw)
    if int(payload.get("expires") or 0) < int(time.time()):
        raise ValueError("expired")
    invoice = str(payload.get("invoice") or "")
    if not invoice or not frappe.db.exists("Sales Invoice", invoice):
        raise ValueError("missing invoice")
    return payload


@frappe.whitelist(allow_guest=True)
def download_invoice(token: str):
    """Download an invoice through an expiring, tamper-evident LINE link."""
    try:
        payload = _decode_invoice_token(token)
        invoice = str(payload.get("invoice") or "")
    except (ValueError, TypeError, json.JSONDecodeError):
        frappe.throw(_("This invoice link is invalid or expired"), frappe.PermissionError)
    frappe.local.response.filename = f"{invoice}.pdf"
    from nextgen_erp.print_formats import CUSTOMER_INVOICE_PRINT_FORMAT

    previous_ignore = getattr(frappe.local.flags, "ignore_print_permissions", False)
    try:
        # The signed, expiring token is the authorization boundary for this
        # guest endpoint. Frappe's print renderer otherwise checks the Guest
        # role again and rejects even a valid customer link.
        frappe.local.flags.ignore_print_permissions = True
        frappe.local.response.filecontent = frappe.get_print(
            "Sales Invoice",
            invoice,
            print_format=CUSTOMER_INVOICE_PRINT_FORMAT,
            as_pdf=True,
        )
    finally:
        frappe.local.flags.ignore_print_permissions = previous_ignore
    frappe.local.response.type = "pdf"
    return None


def _emv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def _promptpay_payload(promptpay_id: str, amount: float) -> str:
    proxy = "".join(character for character in (promptpay_id or "") if character.isdigit())
    if len(proxy) == 10:
        proxy_tag = "01"
        proxy = "0066" + proxy[-9:]
    elif len(proxy) == 13:
        proxy_tag = "02"
    elif len(proxy) == 15:
        proxy_tag = "03"
    else:
        frappe.throw(_("PromptPay ID must be a 10-digit phone, 13-digit national/tax ID, or 15-digit e-wallet ID"))
    merchant_account = _emv("00", "A000000677010111") + _emv(proxy_tag, proxy)
    payload = "".join(
        [
            _emv("00", "01"),
            _emv("01", "12"),
            _emv("29", merchant_account),
            _emv("53", "764"),
            _emv("54", f"{flt(amount):.2f}"),
            _emv("58", "TH"),
            "6304",
        ]
    )
    crc = 0xFFFF
    for byte in payload.encode("ascii"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{payload}{crc:04X}"


def make_promptpay_qr_url(invoice: str, line_ref: str | None = None) -> str | None:
    if not frappe.db.get_single_value("NextGen Payment Settings", "promptpay_id"):
        return None
    days = int(frappe.db.get_single_value("NextGen Automation Settings", "invoice_link_days") or 7)
    token = _invoice_token(invoice, line_ref, int(time.time()) + days * 86400)
    return f"{public_base_url()}/api/method/nextgen_erp.api.promptpay_qr?token={quote(token)}"


@frappe.whitelist(allow_guest=True)
def promptpay_qr(token: str):
    """Serve a signed, amount-locked PromptPay QR as a PNG for LINE."""
    try:
        payload = _decode_invoice_token(token)
        invoice = str(payload["invoice"])
        amount = flt(frappe.db.get_value("Sales Invoice", invoice, "grand_total"))
        promptpay_id = frappe.db.get_single_value("NextGen Payment Settings", "promptpay_id") or ""
        value = _promptpay_payload(promptpay_id, amount)
    except (ValueError, TypeError, json.JSONDecodeError):
        frappe.throw(_("This PromptPay QR link is invalid or expired"), frappe.PermissionError)
    import qrcode

    image = qrcode.make(value)
    output = io.BytesIO()
    image.save(output, format="PNG")
    frappe.local.response.filename = f"promptpay-{invoice}.png"
    frappe.local.response.filecontent = output.getvalue()
    frappe.local.response.type = "download"
    return None


def _delivery_note_token(delivery_note: str, expires: int) -> str:
    payload = json.dumps(
        {"delivery_note": delivery_note, "expires": expires}, separators=(",", ":")
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(
        frappe.get_site_config().encryption_key.encode(), encoded.encode(), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}"


def make_delivery_note_download_url(delivery_note: str) -> str:
    token = _delivery_note_token(delivery_note, int(time.time()) + 7 * 86400)
    return f"{public_base_url()}/api/method/nextgen_erp.api.download_delivery_note?token={quote(token)}"


@frappe.whitelist(allow_guest=True)
def download_delivery_note(token: str):
    try:
        encoded, supplied_signature = token.rsplit(".", 1)
        expected = hmac.new(
            frappe.get_site_config().encryption_key.encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, supplied_signature):
            raise ValueError("bad signature")
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if int(payload.get("expires") or 0) < int(time.time()):
            raise ValueError("expired")
        delivery_note = str(payload.get("delivery_note") or "")
        if not delivery_note or not frappe.db.exists("Delivery Note", delivery_note):
            raise ValueError("missing delivery note")
    except (ValueError, TypeError, json.JSONDecodeError):
        frappe.throw(_("This Delivery Note link is invalid or expired"), frappe.PermissionError)
    frappe.local.response.filename = f"{delivery_note}.pdf"
    frappe.local.response.filecontent = frappe.get_print("Delivery Note", delivery_note, as_pdf=True)
    frappe.local.response.type = "pdf"
    return None


@frappe.whitelist()
def quick_intake(text: str, customer: str | None = None):
    """Desk 'New from LINE text': relay to the external extractor, which parses
    the Thai message and pushes an AI Order Intake back into ERPNext.

    Keeps extraction external; returns the created intake name for the UI to open.
    """
    _require_operations_role()
    import frappe.integrations.utils

    if not (text or "").strip():
        frappe.throw(_("Message text is required"))
    settings = frappe.get_single("NextGen Automation Settings")
    base = (settings.external_service_url or "").rstrip("/")
    if not base:
        frappe.throw(_("Set 'External Service URL' in NextGen Automation Settings first"))
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        frappe.throw(_("External Service URL must be a complete HTTP(S) URL"))
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        frappe.throw(_("External Service URL must use HTTPS outside local development"))
    api_key = settings.get_password("external_service_api_key", raise_exception=False) or ""
    if not api_key:
        frappe.throw(_("Set 'External Service API Key' in NextGen Automation Settings first"))

    key = f"desk-{frappe.generate_hash(length=10)}"
    resp = frappe.integrations.utils.make_post_request(
        f"{base}/api/intake/erpnext",
        headers={"Content-Type": "application/json", "X-Prototype-Key": api_key},
        data=json.dumps({"text": text, "customer": customer, "idempotency_key": key}),
    )
    # The external service returns {"erpnext": {"name": ...}, ...}
    erp = (resp or {}).get("erpnext") or {}
    return {"name": erp.get("name"), "status": erp.get("status"), "idempotency_key": key}
