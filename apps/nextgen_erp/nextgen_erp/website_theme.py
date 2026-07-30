"""Provision the native NextGen Midnight website theme."""

from __future__ import annotations

from pathlib import Path

import frappe

THEME_NAME = "NextGen Midnight"
THEME_SOURCE = Path(__file__).parent / "public" / "scss" / "nextgen_midnight.scss"

CUSTOM_OVERRIDES = """
$primary: #8b5cf6;
$body-bg: #020617;
$body-color: #f8fafc;
$border-color: rgba(148, 163, 184, 0.16);
$card-bg: #0b1120;
$input-bg: #0f172a;
$input-color: #f8fafc;
$input-border-color: rgba(148, 163, 184, 0.16);
$link-color: #a78bfa;
$link-hover-color: #c4b5fd;
$enable-shadows: true;
$enable-gradients: false;
$enable-rounded: true;
""".strip()


def _theme_values() -> dict:
	return {
		"theme": THEME_NAME,
		"module": "Website",
		"custom": 1,
		"font_size": "16px",
		"button_rounded_corners": 1,
		"button_shadows": 1,
		"button_gradients": 0,
		"custom_overrides": CUSTOM_OVERRIDES,
		"custom_scss": THEME_SOURCE.read_text(encoding="utf-8"),
		"js": "",
	}


def ensure_website_theme() -> dict:
	"""Create/update and activate the website theme.

	Safe to run after every install or migration. The Website Theme document is
	only saved when its source changes, while Website Settings is only saved when
	the active theme differs.
	"""
	if not frappe.db.exists("DocType", "Website Theme"):
		return {"updated": False, "activated": False, "reason": "Website Theme doctype missing"}

	values = _theme_values()
	exists = bool(frappe.db.exists("Website Theme", THEME_NAME))
	doc = frappe.get_doc("Website Theme", THEME_NAME) if exists else frappe.get_doc(
		{"doctype": "Website Theme", **values}
	)
	changed = not exists

	if exists:
		for fieldname, value in values.items():
			if doc.get(fieldname) != value:
				doc.set(fieldname, value)
				changed = True

	if exists and changed:
		doc.save(ignore_permissions=True)
	elif not exists:
		doc.insert(ignore_permissions=True)

	settings = frappe.get_single("Website Settings")
	activated = settings.website_theme != THEME_NAME
	if activated:
		settings.website_theme = THEME_NAME
		settings.save(ignore_permissions=True)

	return {
		"updated": changed,
		"activated": activated,
		"theme": THEME_NAME,
	}
