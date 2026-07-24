# Copyright (c) 2026, NextGen and Contributors
# See license.txt

from unittest.mock import patch

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

	def test_line_intake_auto_creates_and_reuses_customer(self):
		line_id = f"U{frappe.generate_hash(length=32)}"
		payload = self._payload(f"test-{frappe.generate_hash(length=10)}")
		payload.update({"customer": None, "source_channel": "line", "line_ref": line_id})

		created = api.create_ai_order_intake(payload)
		doc = frappe.get_doc("AI Order Intake", created["name"])

		self.assertTrue(doc.customer)
		self.assertEqual(doc.customer, frappe.db.get_value("LINE Customer Map", line_id, "customer"))
		self.assertEqual(doc.items[0].rate, 390)
		self.assertEqual(api.approve_ai_order_intake(doc.name)["status"], "Awaiting Customer")

	def test_approval_repairs_existing_line_intake_without_customer(self):
		line_id = f"U{frappe.generate_hash(length=32)}"
		payload = self._payload(f"test-{frappe.generate_hash(length=10)}")
		payload.update({"customer": None, "source_channel": "simulator"})
		created = api.create_ai_order_intake(payload)
		frappe.db.set_value(
			"AI Order Intake",
			created["name"],
			{"source_channel": "line", "line_ref": line_id},
			update_modified=False,
		)

		result = api.approve_ai_order_intake(created["name"])
		doc = frappe.get_doc("AI Order Intake", created["name"])

		self.assertEqual(result["status"], "Awaiting Customer")
		self.assertEqual(doc.customer, frappe.db.get_value("LINE Customer Map", line_id, "customer"))
		self.assertEqual(doc.items[0].rate, 390)

	def test_line_message_never_says_none_for_unresolved_items(self):
		payload = self._payload(f"test-{frappe.generate_hash(length=10)}")
		payload["items"] = [
			{
				"raw_text": "สินค้าที่ไม่รู้จัก 2 ลัง",
				"item_code": None,
				"qty": 2,
				"uom": "ลัง",
				"rate": 0,
				"confidence": 0.1,
				"exception_reason": "ไม่พบสินค้าที่ตรงกับข้อความ",
			}
		]
		payload["exception_reasons"] = ["ไม่พบสินค้าที่ตรงกับข้อความ"]
		created = api.create_ai_order_intake(payload)
		doc = frappe.get_doc("AI Order Intake", created["name"])
		message = api._line_message(doc)
		self.assertNotIn("None", message)
		self.assertIn("สินค้าที่ไม่รู้จัก", message)

	def test_promptpay_message_is_truthful_when_promptpay_is_missing(self):
		settings = frappe.get_single("NextGen Payment Settings")
		previous = settings.promptpay_id
		settings.promptpay_id = ""
		settings.save(ignore_permissions=True)
		try:
			doc = frappe._dict(
				name="AIO-TEST",
				status="Awaiting Payment",
				sales_invoice="",
				line_ref="U-test",
				total=100,
				items=[],
			)
			message = api._line_message(doc)
			self.assertNotIn("กรุณาชำระผ่าน QR", message)
			self.assertIn("ยังไม่ได้ตั้งค่า PromptPay", message)
		finally:
			settings.promptpay_id = previous
			settings.save(ignore_permissions=True)

	def test_slip_ocr_is_extraction_only_and_flags_mismatches(self):
		doc = frappe._dict(name="AIO-TEST", total=15500)
		settings = frappe._dict(
			promptpay_name="บริษัท เน็กซ์เจน จำกัด",
			slip_confidence_threshold=0.95,
		)
		raw = """
		จำนวนเงิน: 15,400.00 บาท
		เลขที่รายการ: TXNABC123456
		วันที่และเวลา: 23/07/2026 12:10
		จาก: นายทดสอบ
		ไปยัง: ร้านอื่น
		"""
		with patch.object(frappe.db, "exists", return_value=False):
			result = api._parse_slip_ocr(raw, doc, settings)
		self.assertFalse(result["verified"])
		self.assertFalse(result["amount_matches"])
		self.assertFalse(result["recipient_matches"])
		self.assertIn("amount_mismatch", result["flags"])
		self.assertIn("recipient_mismatch", result["flags"])

	def test_promptpay_payload_contains_locked_amount_and_valid_crc(self):
		payload = api._promptpay_payload("0812345678", 15500)
		self.assertIn("540815500.00", payload)
		body, supplied_crc = payload[:-4], payload[-4:]
		crc = 0xFFFF
		for byte in body.encode("ascii"):
			crc ^= byte << 8
			for _ in range(8):
				crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
		self.assertEqual(supplied_crc, f"{crc:04X}")

	def test_invoice_outstanding_is_the_customer_payment_source_of_truth(self):
		with patch.object(
			frappe.db,
			"get_value",
			return_value=frappe._dict(
				outstanding_amount=10,
				rounded_total=10,
				grand_total=10.17,
			),
		):
			self.assertEqual(api._invoice_payable_amount("ACC-SINV-TEST"), 10)

	def test_atomic_order_to_cash_creates_submitted_erpnext_documents(self):
		created = api.create_ai_order_intake(
			self._payload(f"test-{frappe.generate_hash(length=10)}")
		)
		name = created["name"]
		api.approve_ai_order_intake(name)
		invoiced = api.record_customer_confirmation(name, 1)
		invoiced_retry = api.record_customer_confirmation(name, 1)
		paid = api.progress_payment(name, f"TEST-{frappe.generate_hash(length=8)}")
		paid_retry = api.progress_payment(name, "DUPLICATE-MUST-NOT-CREATE")
		self.assertEqual(frappe.db.get_value("Delivery Note", paid["delivery_note"], "docstatus"), 0)
		delivered = api.complete_delivery(name)
		delivered_retry = api.complete_delivery(name)
		self.assertTrue(invoiced_retry["already"])
		self.assertEqual(invoiced_retry["sales_order"], invoiced["sales_order"])
		self.assertEqual(invoiced_retry["sales_invoice"], invoiced["sales_invoice"])
		self.assertTrue(paid_retry["already"])
		self.assertEqual(paid_retry["payment_entry"], paid["payment_entry"])
		self.assertTrue(delivered_retry["already"])
		self.assertEqual(delivered_retry["delivery_note"], delivered["delivery_note"])
		self.assertEqual(frappe.db.get_value("Sales Order", invoiced["sales_order"], "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Pick List", invoiced["pick_list"], "docstatus"), 1)
		self.assertGreater(
			frappe.db.count(
				"Stock Reservation Entry",
				{
					"from_voucher_type": "Pick List",
					"from_voucher_no": invoiced["pick_list"],
					"docstatus": 1,
				},
			),
			0,
		)
		self.assertEqual(frappe.db.get_value("Sales Invoice", invoiced["sales_invoice"], "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Payment Entry", paid["payment_entry"], "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Delivery Note", delivered["delivery_note"], "docstatus"), 1)

	def test_line_confirmation_creates_invoice_message_from_configured_stock_warehouse(self):
		company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
			"Global Defaults", "default_company"
		)
		payload = self._payload(f"test-{frappe.generate_hash(length=10)}")
		warehouse = api._select_sales_warehouse(company, payload["items"])
		settings = frappe.get_single("LINE Channel Settings")
		settings.company = company
		settings.selling_warehouse = warehouse
		settings.save(ignore_permissions=True)

		payload.update(
			{
				"source_channel": "line",
				"line_ref": f"U{frappe.generate_hash(length=32)}",
			}
		)
		created = api.create_ai_order_intake(payload)
		api.approve_ai_order_intake(created["name"])
		confirmed = api.record_customer_confirmation(created["name"], 1)
		doc = frappe.get_doc("AI Order Intake", created["name"])

		self.assertEqual(confirmed["status"], "Awaiting Payment")
		self.assertEqual(frappe.db.get_value("Sales Order", doc.sales_order, "company"), company)
		self.assertEqual(
			frappe.db.get_value("Sales Order", doc.sales_order, "set_warehouse"),
			warehouse,
		)
		self.assertEqual(frappe.db.get_value("Sales Invoice", doc.sales_invoice, "docstatus"), 1)
		self.assertEqual(
			doc.total,
			frappe.db.get_value("Sales Invoice", doc.sales_invoice, "outstanding_amount"),
		)
		message = api._line_message(doc)
		self.assertIn(doc.sales_invoice, message)
		self.assertIn(f"ยอด {doc.total:,.2f} บาท", message)
		self.assertIn("/api/method/nextgen_erp.api.download_invoice", message)

		with patch.object(api, "_queue_line_notification") as resend:
			retry = api.handle_line_reply(
				doc.line_ref,
				"ยืนยัน",
				f"retry-{frappe.generate_hash(length=12)}",
			)
		self.assertTrue(retry["handled"])
		self.assertTrue(retry["already"])
		self.assertTrue(retry["resent"])
		self.assertEqual(retry["sales_invoice"], doc.sales_invoice)
		resend.assert_called_once()

	def _zero_stock_item(self):
		"""A sellable stock item with a price but no reservable stock anywhere."""
		code = f"ZS-{frappe.generate_hash(length=8)}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": f"เหล็กสั่งทำ {code}",
				"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name")
				or "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": 1,
				"is_sales_item": 1,
				"standard_rate": 100,
			}
		).insert(ignore_permissions=True)
		return code

	def _backorder_payload(self, key, item_code):
		return {
			"idempotency_key": key,
			"merchant": "test",
			"customer": demo.CUSTOMER,
			"source_channel": "simulator",
			"source_text": "เหล็กข้ออ้อย DB12 5 เส้น",
			"confidence": 0.98,
			"automation_mode": "human_review",
			"items": [
				{
					"raw_text": "เหล็กข้ออ้อย DB12 5 เส้น",
					"item_code": item_code,
					"qty": 5,
					"uom": "Nos",
					"rate": 1,
					"confidence": 0.99,
				}
			],
		}

	def test_backorder_invoicing_on_invoices_without_reservation(self):
		frappe.db.set_single_value("NextGen Automation Settings", "allow_backorder_invoicing", 1)
		item = self._zero_stock_item()
		created = api.create_ai_order_intake(
			self._backorder_payload(f"test-{frappe.generate_hash(length=10)}", item)
		)
		api.approve_ai_order_intake(created["name"])
		result = api.record_customer_confirmation(created["name"], 1)
		doc = frappe.get_doc("AI Order Intake", created["name"])

		self.assertEqual(result["status"], "Awaiting Payment")
		self.assertIsNone(doc.pick_list)
		self.assertEqual(frappe.db.get_value("Sales Order", doc.sales_order, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Sales Invoice", doc.sales_invoice, "docstatus"), 1)
		self.assertEqual(
			frappe.db.count("Stock Reservation Entry", {"voucher_no": doc.sales_order}), 0
		)
		self.assertIn(api.BACKORDER_NOTE, api._as_list(doc.exception_reasons))
		self.assertIn(doc.sales_invoice, api._line_message(doc))

	def test_backorder_confirmation_is_idempotent(self):
		frappe.db.set_single_value("NextGen Automation Settings", "allow_backorder_invoicing", 1)
		item = self._zero_stock_item()
		created = api.create_ai_order_intake(
			self._backorder_payload(f"test-{frappe.generate_hash(length=10)}", item)
		)
		api.approve_ai_order_intake(created["name"])
		first = api.record_customer_confirmation(created["name"], 1)
		second = api.record_customer_confirmation(created["name"], 1)
		self.assertTrue(second["already"])
		self.assertEqual(first["sales_invoice"], second["sales_invoice"])
		# A repeated confirmation must not create a second Sales Order.
		self.assertEqual(frappe.db.count("Sales Order", {"po_no": created["name"]}), 1)

	def test_backorder_invoicing_off_blocks_with_itemized_error(self):
		frappe.db.set_single_value("NextGen Automation Settings", "allow_backorder_invoicing", 0)
		item = self._zero_stock_item()
		created = api.create_ai_order_intake(
			self._backorder_payload(f"test-{frappe.generate_hash(length=10)}", item)
		)
		api.approve_ai_order_intake(created["name"])
		with self.assertRaises(frappe.ValidationError) as ctx:
			api.record_customer_confirmation(created["name"], 1)
		# The clearer message names the shortfall instead of the raw pick-list error.
		self.assertIn("จองสต๊อก", str(ctx.exception))
		self.assertNotIn("Pick List", str(ctx.exception))
		doc = frappe.get_doc("AI Order Intake", created["name"])
		self.assertEqual(doc.status, "Awaiting Customer")
		self.assertFalse(doc.sales_order)
