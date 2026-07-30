"""Idempotent provisioning of the public /store landing page (Page Builder).

Creates a Frappe ``Web Page`` with ``content_type = "Page Builder"`` composed of
the webshop web-template blocks (Hero Slider, Product Category Cards, Item Card
Group), mirroring ERPNext's documented store landing page:
https://docs.frappe.io/erpnext/store-landing-page

The page is public and funnels visitors to the LINE Official Account for
ordering. The gated ``/nextgen-catalog`` and the LINE-only checkout boundary are
untouched.

Design notes:
- The webshop web templates ship only inside the running container (webshop is
  not vendored), so their exact field names are discovered at runtime from
  ``Web Template Field`` instead of hard-coded. Category/featured cards are filled
  by matching Link fields to their target doctype (Item Group / Website Item), and
  hero copy is written to whatever ``slide_1_*`` fields the installed version has.
- Create-if-missing: once the page exists, migrations never overwrite it, so
  store-owner edits in Desk survive. Delete the Web Page to regenerate.

Run standalone with:

    bench --site <site> execute nextgen_erp.store_landing.ensure_store_landing_page
"""

from __future__ import annotations

import frappe

STORE_ROUTE = "store"
PAGE_TITLE = "ร้านค้า NextGen · NextGen Store"
HERO_IMAGE = "/assets/nextgen_erp/images/nextgen-logo.svg"
FEATURED_SECTION_ID = "featured"
# Documented placeholder used until LINE Channel Settings.oa_add_friend_url is set.
DEFAULT_OA_URL = "https://line.me/R/ti/p/@nextgen"

HERO_TEMPLATE = "Hero Slider"
CATEGORY_TEMPLATE = "Product Category Cards"
ITEM_CARD_TEMPLATE = "Item Card Group"

MAX_CATEGORIES = 8
MAX_FEATURED = 12


def _template_fields(template: str) -> list[frappe._dict]:
	"""Field metadata for a Web Template, or [] when the template is absent."""
	if not frappe.db.exists("Web Template", template):
		return []
	return frappe.get_all(
		"Web Template Field",
		filters={"parent": template, "parenttype": "Web Template"},
		fields=["fieldname", "fieldtype", "options"],
		order_by="idx asc",
	)


def _oa_url() -> str:
	if frappe.db.exists("DocType", "LINE Channel Settings"):
		url = frappe.db.get_single_value("LINE Channel Settings", "oa_add_friend_url")
		if url:
			return url
	return DEFAULT_OA_URL


def _hero_values(fields: list[frappe._dict], oa_url: str) -> dict:
	"""Bilingual single-slide hero, writing only fields the template defines."""
	names = {field.fieldname for field in fields}
	values: dict = {}

	def put(key: str, value) -> None:
		if key in names:
			values[key] = value

	put("slide_1_image", HERO_IMAGE)
	put("slide_1_title", "NextGen ERP Thailand")
	subtitle = (
		"สั่งซื้อง่าย ๆ ผ่าน LINE — ราคาและสต็อกเรียลไทม์จากระบบ ERP\n"
		"Order easily on LINE — live prices and stock straight from our ERP"
	)
	# Different webshop versions name the body field _subtitle or _content.
	put("slide_1_subtitle", subtitle)
	put("slide_1_content", subtitle)
	put("slide_1_theme", "Light")
	put("slide_1_primary_action_label", "สั่งซื้อผ่าน LINE · Order on LINE")
	put("slide_1_primary_action", oa_url)
	put("slide_1_secondary_action_label", "ดูสินค้าแนะนำ · Featured products")
	put("slide_1_secondary_action", f"/{STORE_ROUTE}#{FEATURED_SECTION_ID}")
	return values


def _link_fields(fields: list[frappe._dict], target_doctype: str) -> list[str]:
	"""Ordered Link fieldnames pointing at ``target_doctype`` (e.g. card_1..card_n)."""
	return [
		field.fieldname
		for field in fields
		if field.fieldtype == "Link" and (field.options or "") == target_doctype
	]


def _title_field(fields: list[frappe._dict]) -> str | None:
	for field in fields:
		if field.fieldname == "title" and field.fieldtype in {"Data", "Small Text", "Text"}:
			return field.fieldname
	return None


def _featured_item_groups() -> list[str]:
	"""Up to MAX_CATEGORIES Item Groups that have published Website Items."""
	if not frappe.db.exists("DocType", "Website Item"):
		return []
	rows = frappe.get_all(
		"Website Item",
		filters={"published": 1},
		fields=["item_group"],
		order_by="creation asc",
		limit_page_length=0,
	)
	groups: list[str] = []
	for row in rows:
		group = row.get("item_group")
		if group and group not in groups and frappe.db.exists("Item Group", group):
			groups.append(group)
		if len(groups) >= MAX_CATEGORIES:
			break
	return groups


def _ensure_group_on_website(group: str) -> bool:
	"""Publish an Item Group to the website so category cards render/link. Returns kept-True."""
	doc = frappe.get_doc("Item Group", group)
	if getattr(doc, "show_in_website", 0) and getattr(doc, "route", None):
		return True
	doc.show_in_website = 1
	doc.save(ignore_permissions=True)
	# Frappe generates `route` on save when show_in_website is enabled; fall back
	# to a scrubbed slug on the rare version that leaves it blank.
	if not doc.route:
		doc.route = frappe.scrub(group).replace("_", "-")
		doc.save(ignore_permissions=True)
	return True


def _featured_website_items() -> list[str]:
	if not frappe.db.exists("DocType", "Website Item"):
		return []
	return frappe.get_all(
		"Website Item",
		filters={"published": 1},
		pluck="name",
		order_by="creation asc",
		limit_page_length=MAX_FEATURED,
	)


def _card_values(link_fields: list[str], names: list[str]) -> dict:
	return {field: name for field, name in zip(link_fields, names)}


def _block(web_template: str, values: dict, **extra) -> dict:
	row = {
		"web_template": web_template,
		"web_template_values": frappe.as_json(values),
	}
	row.update(extra)
	return row


def ensure_store_landing_page() -> dict:
	"""Create the /store Page Builder Web Page once. No-op if it already exists.

	Returns a small summary dict. Safe to call on every migrate: it never edits an
	existing page and quietly no-ops when the Web Page doctype or the webshop web
	templates are unavailable (e.g. webshop not installed yet).
	"""
	if not frappe.db.exists("DocType", "Web Page"):
		return {"created": False, "reason": "Web Page doctype missing"}
	if frappe.db.exists("Web Page", {"route": STORE_ROUTE}):
		return {"created": False, "reason": "already exists", "route": STORE_ROUTE}

	hero_fields = _template_fields(HERO_TEMPLATE)
	category_fields = _template_fields(CATEGORY_TEMPLATE)
	item_card_fields = _template_fields(ITEM_CARD_TEMPLATE)
	if not (hero_fields or category_fields or item_card_fields):
		return {"created": False, "reason": "webshop web templates not installed"}

	blocks: list[dict] = []
	summary = {"categories": 0, "featured": 0}

	# 1. Hero (the funnel). Full-width, no container.
	if hero_fields:
		blocks.append(
			_block(
				HERO_TEMPLATE,
				_hero_values(hero_fields, _oa_url()),
				add_container=0,
				add_top_padding=0,
			)
		)

	# 2. Product category cards, sourced from published Website Items' groups.
	if category_fields:
		category_links = _link_fields(category_fields, "Item Group")
		groups = _featured_item_groups()[: len(category_links)] if category_links else []
		published_groups = [group for group in groups if _ensure_group_on_website(group)]
		if published_groups:
			values = _card_values(category_links, published_groups)
			title_field = _title_field(category_fields)
			if title_field:
				values[title_field] = "หมวดหมู่สินค้า · Shop by category"
			blocks.append(_block(CATEGORY_TEMPLATE, values, add_container=1))
			summary["categories"] = len(published_groups)

	# 3. Featured item cards.
	if item_card_fields:
		card_links = _link_fields(item_card_fields, "Website Item")
		items = _featured_website_items()[: len(card_links)] if card_links else []
		if items:
			values = _card_values(card_links, items)
			title_field = _title_field(item_card_fields)
			if title_field:
				values[title_field] = "สินค้าแนะนำ · Featured products"
			blocks.append(
				_block(
					ITEM_CARD_TEMPLATE,
					values,
					add_container=1,
					section_id=FEATURED_SECTION_ID,
				)
			)
			summary["featured"] = len(items)

	if not blocks:
		return {"created": False, "reason": "no usable web templates"}

	page = frappe.get_doc(
		{
			"doctype": "Web Page",
			"title": PAGE_TITLE,
			"route": STORE_ROUTE,
			"published": 1,
			"content_type": "Page Builder",
			"page_blocks": blocks,
		}
	)
	page.insert(ignore_permissions=True)
	summary.update({"created": True, "route": STORE_ROUTE, "name": page.name})
	return summary
