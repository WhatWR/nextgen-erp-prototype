import frappe
from frappe.contacts.doctype.address.address import render_address
from frappe.tests import IntegrationTestCase

from nextgen_erp import seed


class IntegrationTestSeedPrerequisites(IntegrationTestCase):
	def test_missing_address_templates_are_restored_before_address_creation(self):
		frappe.db.delete("Address Template")
		created = []

		seed._ensure_erpnext_prerequisites(created)

		self.assertEqual(
			frappe.db.get_value("Address Template", "Thailand", "is_default"),
			1,
		)
		self.assertIn("Address Template:Thailand", created)
		rendered = render_address(
			{
				"address_line1": "115 หมู่ที่ 2",
				"city": "ยโสธร",
				"country": "Thailand",
			}
		)
		self.assertIn("115 หมู่ที่ 2", rendered)
