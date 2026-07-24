from __future__ import annotations

from collections import defaultdict

import frappe
from frappe.utils import flt, getdate, nowdate
from frappe.utils.data import strip_html

from nextgen_erp.webshop import _current_shop_session

no_cache = 1


def _catalog_products() -> list[dict]:
	if not frappe.db.exists("DocType", "Website Item"):
		return []

	website_meta = frappe.get_meta("Website Item")
	website_fields = [
		field
		for field in (
			"item_code",
			"web_item_name",
			"item_name",
			"website_image",
			"route",
			"website_warehouse",
			"short_description",
			"short_website_description",
		)
		if website_meta.has_field(field)
	]
	website_items = frappe.get_all(
		"Website Item",
		filters={"published": 1},
		fields=website_fields,
		order_by="creation asc",
		limit_page_length=500,
	)
	codes = [row.item_code for row in website_items if row.get("item_code")]
	if not codes:
		return []

	item_rows = frappe.get_all(
		"Item",
		filters={"item_code": ["in", codes], "disabled": 0, "is_sales_item": 1},
		fields=["item_code", "item_name", "item_group", "stock_uom", "description", "image"],
		limit_page_length=len(codes),
	)
	item_by_code = {row.item_code: row for row in item_rows}

	price_list = frappe.db.get_single_value("Webshop Settings", "price_list")
	if not price_list:
		price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
	price_rows = (
		frappe.get_all(
			"Item Price",
			filters={
				"item_code": ["in", codes],
				"price_list": price_list,
				"selling": 1,
				"price_list_rate": [">", 0],
			},
			fields=["item_code", "price_list_rate", "valid_from", "valid_upto"],
			order_by="valid_from desc, modified desc",
			limit_page_length=max(len(codes) * 10, 1),
		)
		if price_list
		else []
	)
	today = getdate(nowdate())
	prices = {}
	for row in price_rows:
		if row.valid_from and getdate(row.valid_from) > today:
			continue
		if row.valid_upto and getdate(row.valid_upto) < today:
			continue
		prices.setdefault(row.item_code, flt(row.price_list_rate))

	warehouses = {
		row.website_warehouse
		for row in website_items
		if row.get("website_warehouse")
	}
	bin_rows = (
		frappe.get_all(
			"Bin",
			filters={
				"item_code": ["in", codes],
				"warehouse": ["in", list(warehouses)],
			},
			fields=["item_code", "warehouse", "projected_qty"],
			limit_page_length=max(len(codes) * max(len(warehouses), 1), 1),
		)
		if warehouses
		else []
	)
	stock = defaultdict(float)
	for row in bin_rows:
		stock[(row.item_code, row.warehouse)] += flt(row.projected_qty)

	products = []
	for web_item in website_items:
		item = item_by_code.get(web_item.item_code)
		price = prices.get(web_item.item_code)
		if not item or not price:
			continue
		warehouse = web_item.get("website_warehouse")
		description = (
			web_item.get("short_description")
			or web_item.get("short_website_description")
			or item.description
			or ""
		)
		products.append(
			{
				"item_code": item.item_code,
				"item_name": (
					web_item.get("web_item_name")
					or web_item.get("item_name")
					or item.item_name
				),
				"item_group": item.item_group,
				"uom": item.stock_uom,
				"price": price,
				"image": web_item.get("website_image") or item.image,
				"description": strip_html(description)[:180],
				"warehouse": warehouse,
				"stock": stock.get((item.item_code, warehouse)) if warehouse else None,
			}
		)
	return products


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw("กรุณาเปิดแคตตาล็อกจากลิงก์ใน LINE", frappe.PermissionError)
	_current_shop_session(frappe.session.user)
	context.no_cache = 1
	context.title = "แคตตาล็อกสินค้า"
	context.products = _catalog_products()
	context.initial_query = (frappe.form_dict.get("q") or "").strip()
	context.groups = sorted({row["item_group"] for row in context.products if row["item_group"]})
	return context
