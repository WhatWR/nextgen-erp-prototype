# Copyright (c) 2026, NextGen and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from nextgen_erp import api, demo


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Customer",
	"Sales Order",
	"Pick List",
	"Delivery Note",
	"Sales Invoice",
	"Payment Entry",
	"Item",
	"UOM",
]



class IntegrationTestAIOrderIntake(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		demo.run()

	def _payload(self, key):
		return {
			"idempotency_key": key,
			"merchant": "test",
			"customer": demo.CUSTOMER,
			"source_channel": "simulator",
			"source_text": "M-150 2 ลัง",
			"confidence": 0.98,
			"automation_mode": "human_review",
			"items": [
				{
					"raw_text": "M-150 2 ลัง",
					"item_code": "DRK-M150",
					"qty": 2,
					"uom": "ลัง",
					"rate": 1,
					"confidence": 0.99,
				}
			],
		}

	def test_intake_is_idempotent_and_erpnext_controls_price(self):
		key = f"test-{frappe.generate_hash(length=10)}"
		first = api.create_ai_order_intake(self._payload(key))
		second = api.create_ai_order_intake(self._payload(key))
		doc = frappe.get_doc("AI Order Intake", first["name"])
		self.assertTrue(first["created"])
		self.assertFalse(second["created"])
		self.assertEqual(first["name"], second["name"])
		self.assertEqual(doc.items[0].rate, 390)

	def test_direct_status_edit_is_rejected(self):
		created = api.create_ai_order_intake(
			self._payload(f"test-{frappe.generate_hash(length=10)}")
		)
		doc = frappe.get_doc("AI Order Intake", created["name"])
		doc.status = "Paid"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_atomic_order_to_cash_creates_submitted_erpnext_documents(self):
		created = api.create_ai_order_intake(
			self._payload(f"test-{frappe.generate_hash(length=10)}")
		)
		name = created["name"]
		api.approve_ai_order_intake(name)
		reserved = api.record_customer_confirmation(name, 1)
		invoiced = api.progress_delivery(name)
		paid = api.progress_payment(name, f"TEST-{frappe.generate_hash(length=8)}")
		self.assertEqual(frappe.db.get_value("Sales Order", reserved["sales_order"], "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Pick List", reserved["pick_list"], "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Delivery Note", invoiced["delivery_note"], "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Sales Invoice", invoiced["sales_invoice"], "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Payment Entry", paid["payment_entry"], "docstatus"), 1)
