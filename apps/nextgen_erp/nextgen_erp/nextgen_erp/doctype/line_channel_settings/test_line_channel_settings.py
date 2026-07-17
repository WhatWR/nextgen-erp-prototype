# Copyright (c) 2026, NextGen and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from nextgen_erp import api


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = ["Company", "Warehouse"]



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

	def test_valid_signed_link_can_render_for_guest_and_restores_permission_flag(self):
		previous_user = frappe.session.user
		previous_flag = getattr(frappe.local.flags, "ignore_print_permissions", False)
		try:
			frappe.set_user("Guest")
			with (
				patch.object(api, "_decode_invoice_token", return_value={"invoice": "SINV-DEMO"}),
				patch.object(frappe, "get_print", return_value=b"%PDF-demo") as get_print,
			):
				api.download_invoice("valid.signed-token")
				self.assertEqual(frappe.local.response.filecontent, b"%PDF-demo")
				self.assertEqual(
					get_print.call_args.kwargs["print_format"], "NextGen Customer Invoice"
				)
				self.assertEqual(
					frappe.local.flags.ignore_print_permissions, previous_flag
				)
		finally:
			frappe.local.flags.ignore_print_permissions = previous_flag
			frappe.set_user(previous_user)
