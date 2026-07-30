import frappe
from frappe.tests import IntegrationTestCase

from nextgen_erp.website_theme import THEME_NAME, ensure_website_theme


class IntegrationTestWebsiteTheme(IntegrationTestCase):
	def test_theme_is_idempotent_and_active(self):
		first = ensure_website_theme()
		second = ensure_website_theme()

		self.assertEqual(first["theme"], THEME_NAME)
		self.assertFalse(second["updated"])
		self.assertFalse(second["activated"])
		self.assertEqual(
			frappe.db.count("Website Theme", {"name": THEME_NAME}),
			1,
		)
		self.assertEqual(
			frappe.db.get_single_value("Website Settings", "website_theme"),
			THEME_NAME,
		)

		theme = frappe.get_doc("Website Theme", THEME_NAME)
		self.assertEqual(theme.custom, 1)
		self.assertIn("--ng-midnight: #020617", theme.custom_scss)
		self.assertIn("$primary: #8b5cf6", theme.custom_overrides)
