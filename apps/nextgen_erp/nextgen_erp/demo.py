"""Demo data + a smoke test for the NextGen order-to-cash flow.

    bench --site nextgen.localhost execute nextgen_erp.demo.run
    bench --site nextgen.localhost execute nextgen_erp.demo.smoke
"""

from __future__ import annotations

import json

import frappe

CUSTOMER = "ร้านเจริญพาณิชย์"
ITEMS = [
    {"item_code": "DRK-M150", "item_name": "เครื่องดื่ม M-150", "uom": "ลัง", "rate": 390, "aliases": ["M-150", "เอ็มร้อยห้าสิบ", "เอ็ม150"]},
    {"item_code": "NDL-MAMA-TOM", "item_name": "มาม่าต้มยำน้ำข้น", "uom": "แพ็ก", "rate": 72, "aliases": ["มาม่าต้มยำ", "มาม่า"]},
    {"item_code": "DRK-RED-710", "item_name": "น้ำแดงเฮลซ์บลูบอย 710 มล.", "uom": "ลัง", "rate": 420, "aliases": ["น้ำแดง", "เฮลซ์บลูบอย"]},
]


def run():
    _uoms()
    _customer()
    for it in ITEMS:
        _item(it)
    _stock()
    _stock_settings()
    frappe.db.commit()
    return "ok"


def _stock_settings():
    ss = frappe.get_single("Stock Settings")
    ss.allow_negative_stock = 0
    ss.enable_stock_reservation = 1
    ss.allow_partial_reservation = 0
    ss.save()


def _uoms():
    for u in ("ลัง", "แพ็ก"):
        if not frappe.db.exists("UOM", u):
            frappe.get_doc({"doctype": "UOM", "uom_name": u}).insert()


def _customer():
    if not frappe.db.exists("Customer", CUSTOMER):
        frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": CUSTOMER,
                "customer_type": "Company",
                "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or "All Customer Groups",
                "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories",
            }
        ).insert()


def _item(it):
    if frappe.db.exists("Item", it["item_code"]):
        if frappe.get_meta("Item").has_field("custom_nextgen_aliases"):
            frappe.db.set_value("Item", it["item_code"], "custom_nextgen_aliases", json.dumps(it["aliases"], ensure_ascii=False))
        return
    doc = frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": it["item_code"],
            "item_name": it["item_name"],
            "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups",
            "stock_uom": it["uom"],
            "is_stock_item": 1,
            "is_sales_item": 1,
            "standard_rate": it["rate"],
        }
    )
    if frappe.get_meta("Item").has_field("custom_nextgen_aliases"):
        doc.custom_nextgen_aliases = json.dumps(it["aliases"], ensure_ascii=False)
    doc.insert()


def _stock():
    from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
        get_available_qty_to_reserve,
    )

    company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
        "Global Defaults", "default_company"
    )
    warehouse = frappe.db.get_value(
        "Warehouse", {"company": company, "is_group": 0}, "name", order_by="creation asc"
    )
    rows = []
    for item in ITEMS:
        available = get_available_qty_to_reserve(item["item_code"], warehouse)
        if available < 100:
            rows.append(
                {
                    "item_code": item["item_code"],
                    "t_warehouse": warehouse,
                    "qty": 100 - available,
                    "basic_rate": item["rate"],
                }
            )
    if rows:
        entry = frappe.get_doc(
            {
                "doctype": "Stock Entry",
                "stock_entry_type": "Material Receipt",
                "company": company,
                "to_warehouse": warehouse,
                "items": rows,
            }
        )
        entry.insert()
        entry.submit()


def smoke():
    """Drive one intake all the way to Paid; returns the trace."""
    from nextgen_erp import api

    run()
    key = f"smoke-{frappe.generate_hash(length=8)}"
    intake = api.create_ai_order_intake(
        {
            "idempotency_key": key,
            "merchant": "demo",
            "customer": CUSTOMER,
            "source_channel": "simulator",
            "source_text": "เอามาม่าต้มยำ 3 แพ็ก กับ M-150 2 ลัง",
            "confidence": 0.98,
            "automation_mode": "human_review",
            "items": [
                {"raw_text": "มาม่าต้มยำ 3 แพ็ก", "item_code": "NDL-MAMA-TOM", "qty": 3, "uom": "แพ็ก", "rate": 72, "confidence": 0.97},
                {"raw_text": "M-150 2 ลัง", "item_code": "DRK-M150", "qty": 2, "uom": "ลัง", "rate": 390, "confidence": 0.99},
            ],
        }
    )
    name = intake["name"]
    trace = {"intake": name, "created": intake}
    trace["approve"] = api.approve_ai_order_intake(name, reviewer="staff@nextgen")
    trace["confirm"] = api.record_customer_confirmation(name, confirmed=1)
    trace["deliver"] = api.progress_delivery(name)
    trace["pay"] = api.progress_payment(name, reference_no="BANK-XFER-001")
    frappe.db.commit()
    return json.dumps(trace, ensure_ascii=False, default=str)
