# Copyright (c) 2026, NextGen and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from nextgen_erp import ai

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestNextGenAISettings(IntegrationTestCase):
	def test_guest_cannot_read_ai_gateway_key(self):
		previous = frappe.session.user
		try:
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				ai.get_ai_config()
		finally:
			frappe.set_user(previous)

	def test_config_defaults_are_safe(self):
		previous = frappe.session.user
		try:
			frappe.set_user("Administrator")
			config = ai.get_ai_config()
			self.assertFalse(config["enabled"] and not config["gateway_url"])
			self.assertGreaterEqual(config["max_tool_calls"], 1)
		finally:
			frappe.set_user(previous)

	def test_answer_requires_recipient_and_text(self):
		previous = frappe.session.user
		try:
			frappe.set_user("Administrator")
			with self.assertRaises(frappe.ValidationError):
				ai.send_line_answer("", "hello")
			with self.assertRaises(frappe.ValidationError):
				ai.send_line_answer("U123", "   ")
		finally:
			frappe.set_user(previous)
