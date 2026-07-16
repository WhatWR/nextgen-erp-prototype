"""Demo data + a smoke test for the NextGen order-to-cash flow.

    bench --site nextgen.localhost execute nextgen_erp.demo.run
    bench --site nextgen.localhost execute nextgen_erp.demo.smoke
    bench --site nextgen.localhost execute nextgen_erp.demo.procurement
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import add_days, nowdate

CUSTOMER = "ร้านเจริญพาณิชย์"
SUPPLIER = "บจก.โอสถสภา"
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


def procurement():
    """Deterministic procurement fixture: supplier terms, 90 days of demand
    history (closed so it reserves nothing), purchase-price lots and one open
    PO. Idempotent — safe to run repeatedly."""
    run()
    _supplier()
    _supplier_terms()
    _demand_history()
    _purchase_history()
    frappe.db.commit()
    return "ok"


def _supplier():
    if not frappe.db.exists("Supplier", SUPPLIER):
        frappe.get_doc(
            {
                "doctype": "Supplier",
                "supplier_name": SUPPLIER,
                "supplier_type": "Company",
                "supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
                or "All Supplier Groups",
            }
        ).insert()


def _supplier_terms():
    buying_price_list = (
        frappe.db.get_single_value("Buying Settings", "buying_price_list")
        or frappe.db.get_value("Price List", {"buying": 1, "enabled": 1}, "name")
        or "Standard Buying"
    )
    for it, lead, moq, buy_rate in (
        (ITEMS[0], 5, 5, 350.0),
        (ITEMS[1], 7, 10, 62.0),
        (ITEMS[2], 7, 5, 380.0),
    ):
        item = frappe.get_doc("Item", it["item_code"])
        item.lead_time_days = lead
        item.safety_stock = 10
        item.min_order_qty = moq
        item.is_purchase_item = 1
        if not any(row.supplier == SUPPLIER for row in item.supplier_items or []):
            item.append("supplier_items", {"supplier": SUPPLIER})
        item.save(ignore_permissions=True)
        if not frappe.db.exists(
            "Item Price",
            {"item_code": it["item_code"], "price_list": buying_price_list, "buying": 1},
        ):
            frappe.get_doc(
                {
                    "doctype": "Item Price",
                    "item_code": it["item_code"],
                    "price_list": buying_price_list,
                    "buying": 1,
                    "price_list_rate": buy_rate,
                }
            ).insert(ignore_permissions=True)


def _demand_history():
    """Weekly M-150 wholesale orders over the last ~12 weeks. Submitted then
    closed, so history exists without reserving live stock."""
    company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
        "Global Defaults", "default_company"
    )
    for days_ago in range(3, 85, 7):
        date = add_days(nowdate(), -days_ago)
        if frappe.db.exists(
            "Sales Order", {"customer": CUSTOMER, "transaction_date": date, "docstatus": 1}
        ):
            continue
        so = frappe.get_doc(
            {
                "doctype": "Sales Order",
                "customer": CUSTOMER,
                "company": company,
                "transaction_date": date,
                "delivery_date": add_days(date, 2),
                "items": [
                    {"item_code": "DRK-M150", "qty": 70, "uom": "ลัง"},
                    {"item_code": "NDL-MAMA-TOM", "qty": 25, "uom": "แพ็ก"},
                ],
            }
        )
        so.insert(ignore_permissions=True)
        so.submit()
        so.update_status("Closed")


def _purchase_history():
    """Two closed purchase lots with different rates plus one open PO."""
    company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
        "Global Defaults", "default_company"
    )
    warehouse = frappe.db.get_value(
        "Warehouse", {"company": company, "is_group": 0}, "name", order_by="creation asc"
    )
    lots = (
        (add_days(nowdate(), -60), 335.0, 50, "Closed"),
        (add_days(nowdate(), -12), 350.0, 50, "Closed"),
        (add_days(nowdate(), -2), 352.0, 20, None),  # open, awaiting receipt
    )
    for date, rate, qty, close in lots:
        if frappe.db.exists(
            "Purchase Order",
            {"supplier": SUPPLIER, "transaction_date": date, "docstatus": 1},
        ):
            continue
        po = frappe.get_doc(
            {
                "doctype": "Purchase Order",
                "supplier": SUPPLIER,
                "company": company,
                "transaction_date": date,
                "schedule_date": add_days(date, 5),
                "items": [
                    {
                        "item_code": "DRK-M150",
                        "qty": qty,
                        "rate": rate,
                        "uom": "ลัง",
                        "warehouse": warehouse,
                        "schedule_date": add_days(date, 5),
                    }
                ],
            }
        )
        po.insert(ignore_permissions=True)
        po.submit()
        if close:
            po.update_status(close)


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
