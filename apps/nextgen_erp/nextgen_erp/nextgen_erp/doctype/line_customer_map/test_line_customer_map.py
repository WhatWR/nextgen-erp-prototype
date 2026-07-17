# Copyright (c) 2026, NextGen and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from nextgen_erp import api, demo


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = ["Customer"]



class IntegrationTestLINECustomerMap(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		demo.run()

	def test_verified_line_id_resolves_to_erpnext_customer(self):
		line_id = f"U{frappe.generate_hash(length=12)}"
		frappe.get_doc(
			{"doctype": "LINE Customer Map", "line_id": line_id, "customer": demo.CUSTOMER}
		).insert()
		result = api.resolve_line_customer(line_id)
		self.assertEqual(result["customer"], demo.CUSTOMER)

	def test_new_line_user_is_onboarded_once(self):
		line_id = f"U{frappe.generate_hash(length=32)}"
		first = api.resolve_line_customer(line_id, create_if_missing=1, display_name="คุณสมชาย")
		second = api.resolve_line_customer(line_id, create_if_missing=1, display_name="ชื่อใหม่")

		self.assertTrue(first["created"])
		self.assertFalse(second["created"])
		self.assertEqual(first["customer"], second["customer"])
		self.assertEqual(
			frappe.db.get_value("LINE Customer Map", line_id, "customer"),
			first["customer"],
		)
		self.assertEqual(frappe.db.get_value("LINE Customer Map", line_id, "display_name"), "คุณสมชาย")

	def test_group_id_is_not_onboarded_as_one_customer(self):
		with self.assertRaises(frappe.ValidationError):
			api.resolve_line_customer(
				f"C{frappe.generate_hash(length=32)}",
				create_if_missing=1,
			)
