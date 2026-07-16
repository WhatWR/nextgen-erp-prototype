import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime, nowdate

from nextgen_erp import demo, staff_chat


class IntegrationTestStaffChatActions(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		demo.run()

	def setUp(self):
		frappe.set_user("Administrator")
		# Other order-to-cash integration tests reserve demo stock. Replenish the
		# deterministic fixture before every action test so suite order is irrelevant.
		demo.run()

	def _session(self):
		return frappe.get_doc(
			{
				"doctype": "NextGen Chat Session",
				"user": "Administrator",
				"title": "Staff chat test",
				"status": "Open",
				"last_activity_at": now_datetime(),
			}
		).insert(ignore_permissions=True)

	def _action(self, *, confidence=0.99, warnings=None, expires_at=None):
		session = self._session()
		snapshot = staff_chat._item_snapshot("DRK-M150", demo.CUSTOMER)
		company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
			"Global Defaults", "default_company"
		)
		preview = {
			"customer": demo.CUSTOMER,
			"customer_name": demo.CUSTOMER,
			"company": company,
			"warehouse": snapshot["warehouse"],
			"delivery_date": nowdate(),
			"currency": "THB",
			"confidence": confidence,
			"warnings": warnings or [],
			"items": [
				{
					"requested_text": "M-150",
					"item_code": "DRK-M150",
					"item_name": snapshot["item_name"],
					"qty": 1,
					"uom": snapshot["uom"],
					"rate": snapshot["rate"],
					"amount": snapshot["rate"],
					"projected_qty": snapshot["projected_qty"],
					"warehouse": snapshot["warehouse"],
					"confidence": confidence,
				}
			],
			"total": snapshot["rate"],
		}
		return frappe.get_doc(
			{
				"doctype": "NextGen Chat Action",
				"session": session.name,
				"user": "Administrator",
				"action_type": "prepare_sales_order",
				"status": "Pending",
				"confidence": confidence,
				"expires_at": expires_at or add_to_date(now_datetime(), minutes=15),
				"idempotency_key": f"test-chat-{frappe.generate_hash(length=12)}",
				"proposal_payload": json.dumps({"customer": demo.CUSTOMER}),
				"preview": json.dumps(preview),
				"warnings": json.dumps(warnings or []),
			}
		).insert(ignore_permissions=True)

	def test_high_confidence_confirmation_submits_and_reserves_once(self):
		action = self._action()
		first = staff_chat.confirm_action(action.name)
		second = staff_chat.confirm_action(action.name)
		self.assertTrue(first["high_confidence"])
		self.assertEqual(first["document_type"], "Sales Order")
		self.assertEqual(first["document_name"], second["document_name"])
		self.assertTrue(second["already"])
		self.assertEqual(frappe.db.get_value("Sales Order", first["document_name"], "docstatus"), 1)

	def test_low_confidence_confirmation_creates_review_intake(self):
		action = self._action(confidence=0.5, warnings=["สินค้ากำกวม"])
		result = staff_chat.confirm_action(action.name)
		self.assertFalse(result["high_confidence"])
		self.assertEqual(result["document_type"], "AI Order Intake")
		self.assertEqual(
			frappe.db.get_value("AI Order Intake", result["document_name"], "status"), "Needs Review"
		)

	def test_expired_action_cannot_execute(self):
		action = self._action(expires_at=add_to_date(now_datetime(), minutes=-1))
		with self.assertRaises(frappe.ValidationError):
			staff_chat.confirm_action(action.name)

	def test_tool_registry_has_no_accounting_or_delete_actions(self):
		names = {tool["function"]["name"] for tool in staff_chat.TOOLS}
		self.assertIn("prepare_sales_order", names)
		self.assertFalse(any("delete" in name or "payment" in name or "invoice" in name for name in names))
		self.assertIn("ห้ามแต่งเลขเอกสาร", staff_chat.SYSTEM_PROMPT)
		self.assertIn("ยังไม่ได้สร้าง Sales Order", staff_chat.ACTION_PREVIEW_TEXT)

	def test_item_snapshot_without_customer_uses_selling_price_list(self):
		snapshot = staff_chat._item_snapshot("DRK-M150")
		self.assertNotIn("error", snapshot)
		self.assertEqual(snapshot["price_list"], "Standard Selling")
		self.assertGreater(snapshot["rate"], 0)

	def test_prepare_sales_order_result_is_json_serializable(self):
		session = self._session()
		result = staff_chat._prepare_sales_order(
			{"customer": demo.CUSTOMER, "items": [{"item": "M-150", "qty": 1}]},
			user="Administrator",
			session_id=session.name,
		)
		self.assertTrue(result["action_id"])
		self.assertIsInstance(result["expires_at"], str)
		json.dumps(result, ensure_ascii=False)

	def test_json_content_tool_call_is_normalized(self):
		calls = staff_chat._extract_tool_calls(
			{
				"content": json.dumps(
					{
						"name": "prepare_sales_order",
						"arguments": {"customer": demo.CUSTOMER, "items": []},
					},
					ensure_ascii=False,
				)
			}
		)
		self.assertEqual(calls[0]["function"]["name"], "prepare_sales_order")
		self.assertIn(demo.CUSTOMER, calls[0]["function"]["arguments"])
