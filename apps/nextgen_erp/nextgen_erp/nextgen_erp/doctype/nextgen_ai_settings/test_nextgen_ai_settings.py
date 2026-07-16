# Copyright (c) 2026, NextGen and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from nextgen_erp import ai
from nextgen_erp import staff_chat

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

	def test_staff_chat_is_disabled_by_default_and_guest_is_rejected(self):
		previous = frappe.session.user
		try:
			frappe.set_user("Administrator")
			settings = frappe.get_single("NextGen AI Settings")
			self.assertIn(settings.get("enable_staff_chat"), (0, None))
			self.assertGreaterEqual(settings.get("chat_history_retention_days") or 30, 1)
			frappe.set_user("Guest")
			with self.assertRaises(frappe.AuthenticationError):
				staff_chat.list_sessions()
		finally:
			frappe.set_user(previous)
