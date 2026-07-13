# Copyright (c) 2026, NextGen and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from nextgen_erp import api


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]



class IntegrationTestNextGenAutomationSettings(IntegrationTestCase):
	def test_confidence_policy_is_read_from_erpnext(self):
		settings = frappe.get_single("NextGen Automation Settings")
		settings.confidence_threshold = 0.97
		settings.auto_confirm = 1
		settings.save()
		result = api.get_automation_settings()
		self.assertEqual(result["confidence_threshold"], 0.97)
		self.assertTrue(result["auto_route_high_confidence"])
