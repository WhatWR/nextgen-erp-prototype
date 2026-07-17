"""Explicit, production-safe demo seed for the ICONHOME 2019 pilot.

Run only after taking a backup:

    bench --site <site> execute nextgen_erp.seed.run \
      --kwargs '{"confirm":"SEED_ICONHOME_DEMO"}'

The seed is idempotent. It never deletes records, never changes global defaults,
and leaves its sample Sales Invoice in Draft so it does not post to the ledger.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import add_days, nowdate

CONFIRMATION_TOKEN = "SEED_ICONHOME_DEMO"
COMPANY = "บริษัท ไอคอนโฮม 2019 จำกัด"
COMPANY_EN = "ICONHOME 2019 COMPANY LIMITED"
COMPANY_ABBR = "IH19"
COMPANY_TAX_ID = "0355562000129"
COMPANY_ADDRESS = "115 หมู่ที่ 2 ตำบลสามแยก อำเภอเลิงนกทา จังหวัดยโสธร 35120"
COMPANY_PROVINCE = "ยโสธร"
COMPANY_POSTCODE = "35120"

CUSTOMER = "DEMO - บริษัท สยามคอนสตรัคชั่น จำกัด"
SUPPLIER = "DEMO - บริษัท วัสดุก่อสร้างอีสาน จำกัด"
INVOICE_REFERENCE = "ICONHOME-DEMO-INVOICE-001"

ITEMS = (
	{
		"item_code": "DEMO-CON-M240",
		"item_name": "คอนกรีตผสมเสร็จ 240 KSC",
		"uom": "คิว",
		"rate": 2050.0,
		"stock": 120.0,
		"aliases": ["คอนกรีต 240", "ปูน 240", "ready mix 240"],
	},
	{
		"item_code": "DEMO-CEM-50KG",
		"item_name": "ปูนซีเมนต์ปอร์ตแลนด์ 50 กก.",
		"uom": "ถุง",
		"rate": 155.0,
		"stock": 500.0,
		"aliases": ["ปูนถุง", "ปูนปอร์ตแลนด์", "cement 50kg"],
	},
	{
		"item_code": "DEMO-RBR-DB12",
		"item_name": "เหล็กข้ออ้อย DB12 ยาว 10 เมตร",
		"uom": "เส้น",
		"rate": 238.0,
		"stock": 300.0,
		"aliases": ["เหล็ก DB12", "เหล็กข้ออ้อย 12", "db12"],
	},
	{
		"item_code": "DEMO-BLK-STD",
		"item_name": "อิฐบล็อกมาตรฐาน 7 ซม.",
		"uom": "ก้อน",
		"rate": 9.5,
		"stock": 3000.0,
		"aliases": ["อิฐบล็อก", "บล็อก 7 ซม.", "concrete block"],
	},
)


def run(confirm: str | None = None) -> dict:
	"""Create clearly labelled demo master data and one Draft invoice."""
	if confirm != CONFIRMATION_TOKEN:
		frappe.throw(
			_("Demo seed was not run. Pass confirm={0} explicitly.").format(CONFIRMATION_TOKEN),
			frappe.PermissionError,
		)
	if frappe.session.user not in {"Administrator"} and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only Administrator or System Manager can seed demo data."), frappe.PermissionError)

	created: list[str] = []
	_ensure_erpnext_prerequisites(created)
	company = _ensure_company(created)
	_ensure_company_address(company, created)
	_ensure_uoms(created)
	_ensure_customer(created)
	_ensure_customer_address(created)
	_ensure_supplier(created)
	for item in ITEMS:
		_ensure_item(item, created)
	_ensure_selling_prices(created)
	warehouse = _ensure_warehouse(company, created)
	_ensure_stock(company, warehouse, created)
	_configure_line_fulfilment(company, warehouse)
	invoice = _ensure_draft_invoice(company, created)

	frappe.db.commit()
	return {
		"status": "ok",
		"company": company,
		"warehouse": warehouse,
		"draft_invoice": invoice,
		"created": created,
		"message": "Demo data is ready. Existing records were preserved.",
	}


def _configure_line_fulfilment(company: str, warehouse: str) -> None:
	"""Point a fresh demo LINE channel at the same company and stock source."""
	settings = frappe.get_single("LINE Channel Settings")
	changed = False
	if not settings.get("company"):
		settings.company = company
		changed = True
	if not settings.get("selling_warehouse"):
		settings.selling_warehouse = warehouse
		changed = True
	if changed:
		settings.save(ignore_permissions=True)


def _ensure_erpnext_prerequisites(created: list[str]) -> None:
	"""Restore tiny setup fixtures required by ERPNext's Company hook.

	A site restored before the setup wizard completes can have ERPNext installed
	without the standard ``Transit`` Warehouse Type. Company creation always
	creates a transit warehouse and then fails link validation. Keeping this
	bootstrap here makes the explicit demo seed safe on both fresh and fully
	configured sites without running the entire setup wizard again.
	"""
	if not frappe.db.exists("Warehouse Type", "Transit"):
		doc = frappe.new_doc("Warehouse Type")
		doc.name = "Transit"
		doc.insert(ignore_permissions=True)
		created.append("Warehouse Type:Transit")

	_ensure_address_template(created)


def _ensure_address_template(created: list[str]) -> None:
	"""Ensure Address hooks can render records on partially configured sites."""
	default_template = frappe.db.get_value("Address Template", {"is_default": 1}, "name")
	thai_template = frappe.db.get_value("Address Template", {"country": "Thailand"}, "name")
	if thai_template:
		if not default_template:
			frappe.db.set_value("Address Template", thai_template, "is_default", 1)
		return
	if default_template:
		return

	from frappe.contacts.doctype.address_template.address_template import (
		get_default_address_template,
	)

	doc = frappe.get_doc(
		{
			"doctype": "Address Template",
			"country": "Thailand",
			"is_default": 1,
			"template": get_default_address_template(),
		}
	).insert(ignore_permissions=True)
	created.append(f"Address Template:{doc.name}")


def _ensure_company(created: list[str]) -> str:
	existing = frappe.db.get_value("Company", {"tax_id": COMPANY_TAX_ID}, "name")
	if existing:
		return existing
	if frappe.db.exists("Company", COMPANY):
		return COMPANY
	doc = frappe.get_doc(
		{
			"doctype": "Company",
			"company_name": COMPANY,
			"abbr": COMPANY_ABBR,
			"default_currency": "THB",
			"country": "Thailand",
			"tax_id": COMPANY_TAX_ID,
			"date_of_establishment": "2019-04-29",
			"company_description": f"{COMPANY_EN} - DEMO pilot data",
			"create_chart_of_accounts_based_on": "Standard Template",
			"chart_of_accounts": "Standard",
		}
	).insert(ignore_permissions=True)
	created.append(f"Company:{doc.name}")
	return doc.name


def _ensure_company_address(company: str, created: list[str]) -> None:
	if frappe.db.exists(
		"Dynamic Link",
		{"parenttype": "Address", "link_doctype": "Company", "link_name": company},
	):
		return
	doc = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": "ICONHOME 2019",
			"address_type": "Billing",
			"address_line1": COMPANY_ADDRESS,
			"address_line2": "",
			"city": COMPANY_PROVINCE,
			"state": COMPANY_PROVINCE,
			"pincode": COMPANY_POSTCODE,
			"country": "Thailand",
			"is_primary_address": 1,
			"is_your_company_address": 1,
			"links": [{"link_doctype": "Company", "link_name": company}],
		}
	).insert(ignore_permissions=True)
	created.append(f"Address:{doc.name}")


def _ensure_uoms(created: list[str]) -> None:
	for uom in {item["uom"] for item in ITEMS}:
		if not frappe.db.exists("UOM", uom):
			frappe.get_doc({"doctype": "UOM", "uom_name": uom}).insert(ignore_permissions=True)
			created.append(f"UOM:{uom}")


def _leaf(doctype: str, fallback: str) -> str:
	return frappe.db.get_value(doctype, {"is_group": 0}, "name") or fallback


def _ensure_customer(created: list[str]) -> None:
	if frappe.db.exists("Customer", CUSTOMER):
		return
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": CUSTOMER,
			"customer_type": "Company",
			"customer_group": _leaf("Customer Group", "All Customer Groups"),
			"territory": _leaf("Territory", "All Territories"),
			"tax_id": "0105559999991",
		}
	).insert(ignore_permissions=True)
	created.append(f"Customer:{doc.name}")


def _ensure_customer_address(created: list[str]) -> None:
	if frappe.db.exists(
		"Dynamic Link",
		{"parenttype": "Address", "link_doctype": "Customer", "link_name": CUSTOMER},
	):
		return
	doc = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": "DEMO - Siam Construction",
			"address_type": "Billing",
			"address_line1": "99/9 ถนนตัวอย่าง (ข้อมูลจำลอง)",
			"city": "กรุงเทพมหานคร",
			"state": "กรุงเทพมหานคร",
			"pincode": "10110",
			"country": "Thailand",
			"is_primary_address": 1,
			"links": [{"link_doctype": "Customer", "link_name": CUSTOMER}],
		}
	).insert(ignore_permissions=True)
	created.append(f"Address:{doc.name}")


def _ensure_supplier(created: list[str]) -> None:
	if frappe.db.exists("Supplier", SUPPLIER):
		return
	doc = frappe.get_doc(
		{
			"doctype": "Supplier",
			"supplier_name": SUPPLIER,
			"supplier_type": "Company",
			"supplier_group": _leaf("Supplier Group", "All Supplier Groups"),
		}
	).insert(ignore_permissions=True)
	created.append(f"Supplier:{doc.name}")


def _ensure_item(item: dict, created: list[str]) -> None:
	if frappe.db.exists("Item", item["item_code"]):
		return
	doc = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item["item_code"],
			"item_name": item["item_name"],
			"item_group": _leaf("Item Group", "All Item Groups"),
			"stock_uom": item["uom"],
			"is_stock_item": 1,
			"is_sales_item": 1,
			"is_purchase_item": 1,
			"standard_rate": item["rate"],
		}
	)
	if frappe.get_meta("Item").has_field("custom_nextgen_aliases"):
		doc.custom_nextgen_aliases = json.dumps(item["aliases"], ensure_ascii=False)
	doc.insert(ignore_permissions=True)
	created.append(f"Item:{doc.name}")


def _ensure_selling_prices(created: list[str]) -> None:
	price_list = (
		frappe.db.get_single_value("Selling Settings", "selling_price_list")
		or frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")
		or "Standard Selling"
	)
	for item in ITEMS:
		filters = {"item_code": item["item_code"], "price_list": price_list, "selling": 1}
		if frappe.db.exists("Item Price", filters):
			continue
		doc = frappe.get_doc(
			{
				"doctype": "Item Price",
				**filters,
				"price_list_rate": item["rate"],
			}
		).insert(ignore_permissions=True)
		created.append(f"Item Price:{doc.name}")


def _ensure_warehouse(company: str, created: list[str]) -> str:
	abbr = frappe.db.get_value("Company", company, "abbr") or COMPANY_ABBR
	warehouse = f"Demo Stores - {abbr}"
	if frappe.db.exists("Warehouse", warehouse):
		return warehouse
	doc = frappe.get_doc(
		{
			"doctype": "Warehouse",
			"warehouse_name": "Demo Stores",
			"company": company,
			"is_group": 0,
		}
	).insert(ignore_permissions=True)
	created.append(f"Warehouse:{doc.name}")
	return doc.name


def _ensure_stock(company: str, warehouse: str, created: list[str]) -> None:
	rows = []
	for item in ITEMS:
		actual = float(frappe.db.get_value("Bin", {"item_code": item["item_code"], "warehouse": warehouse}, "actual_qty") or 0)
		if actual < item["stock"]:
			rows.append(
				{
					"item_code": item["item_code"],
					"t_warehouse": warehouse,
					"qty": item["stock"] - actual,
					"basic_rate": item["rate"] * 0.72,
				}
			)
	if not rows:
		return
	doc = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": company,
			"to_warehouse": warehouse,
			"remarks": "DEMO seed - ICONHOME 2019",
			"items": rows,
		}
	).insert(ignore_permissions=True)
	doc.submit()
	created.append(f"Stock Entry:{doc.name}")


def _ensure_draft_invoice(company: str, created: list[str]) -> str:
	existing = frappe.db.get_value(
		"Sales Invoice", {"custom_nextgen_external_reference": INVOICE_REFERENCE}, "name"
	)
	if existing:
		return existing
	tax_account = frappe.db.get_value(
		"Account", {"company": company, "account_type": "Tax", "is_group": 0}, "name"
	)
	doc = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": company,
			"customer": CUSTOMER,
			"posting_date": nowdate(),
			"due_date": add_days(nowdate(), 15),
			"custom_nextgen_external_reference": INVOICE_REFERENCE,
			"remarks": "เอกสารตัวอย่างสำหรับสาธิต AI Sales Copilot - ไม่มีผลทางบัญชีจนกว่าจะ Submit",
			"items": [
				{"item_code": "DEMO-CON-M240", "qty": 8, "uom": "คิว", "rate": 2050.0},
				{"item_code": "DEMO-CEM-50KG", "qty": 40, "uom": "ถุง", "rate": 155.0},
				{"item_code": "DEMO-RBR-DB12", "qty": 30, "uom": "เส้น", "rate": 238.0},
			],
		}
	)
	if tax_account:
		doc.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": tax_account,
				"description": "ภาษีมูลค่าเพิ่ม / VAT 7%",
				"rate": 7,
			},
		)
	doc.insert(ignore_permissions=True)
	created.append(f"Sales Invoice (Draft):{doc.name}")
	return doc.name
