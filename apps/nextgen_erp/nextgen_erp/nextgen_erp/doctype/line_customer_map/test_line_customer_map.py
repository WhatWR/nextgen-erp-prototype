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
