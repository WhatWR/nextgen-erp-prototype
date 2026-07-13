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



class IntegrationTestLINEChannelSettings(IntegrationTestCase):
	def test_guest_cannot_read_line_signature_secret(self):
		previous = frappe.session.user
		try:
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				api.get_line_config()
		finally:
			frappe.set_user(previous)

	def test_service_config_never_returns_channel_access_token(self):
		previous = frappe.session.user
		try:
			frappe.set_user("Administrator")
			config = api.get_line_config()
			self.assertNotIn("channel_access_token", config)
		finally:
			frappe.set_user(previous)

	def test_tampered_invoice_link_is_rejected(self):
		with self.assertRaises(frappe.PermissionError):
			api.download_invoice("tampered.payload")
