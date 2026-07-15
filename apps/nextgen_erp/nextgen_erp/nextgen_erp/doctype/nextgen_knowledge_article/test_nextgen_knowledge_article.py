# Copyright (c) 2026, NextGen and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from nextgen_erp import ai

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestNextGenKnowledgeArticle(IntegrationTestCase):
	def test_disabled_articles_are_never_served_to_the_assistant(self):
		article = frappe.get_doc(
			{
				"doctype": "NextGen Knowledge Article",
				"title": "Test disabled FAQ",
				"content": "ห้ามตอบข้อความนี้",
				"enabled": 0,
			}
		).insert(ignore_permissions=True)
		try:
			previous = frappe.session.user
			try:
				frappe.set_user("Administrator")
				names = [a["name"] for a in ai.get_knowledge_articles()["articles"]]
				self.assertNotIn(article.name, names)
			finally:
				frappe.set_user(previous)
		finally:
			article.delete(ignore_permissions=True)

	def test_intake_ownership_is_enforced_for_resend(self):
		previous = frappe.session.user
		try:
			frappe.set_user("Administrator")
			intake = frappe.get_doc(
				{
					"doctype": "AI Order Intake",
					"merchant": "demo",
					"line_ref": "Uowner",
					"source_channel": "line",
					"source_text": "test",
					"status": "Needs Review",
				}
			)
			intake.flags.nextgen_transition = True
			intake.insert(ignore_permissions=True)
			try:
				with self.assertRaises(frappe.PermissionError):
					ai.resend_payment_request(intake.name, "Uattacker")
				with self.assertRaises(frappe.PermissionError):
					ai.resend_delivery_note(intake.name, "Uattacker")
			finally:
				intake.delete(ignore_permissions=True)
		finally:
			frappe.set_user(previous)
