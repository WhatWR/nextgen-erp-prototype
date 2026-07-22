import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, flt, now_datetime, nowdate

from nextgen_erp import demo, forecast, procurement, staff_chat


class IntegrationTestProcurementCopilot(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		demo.procurement()

	def setUp(self):
		frappe.set_user("Administrator")
		demo.run()
		frappe.db.set_single_value("NextGen Procurement Settings", "enable_procurement_copilot", 1)
		frappe.db.set_single_value("NextGen Procurement Settings", "automation_mode", "Approval Required")
		frappe.db.set_single_value("NextGen Procurement Settings", "maximum_po_value", 1000000)
		frappe.db.set_single_value("NextGen Procurement Settings", "maximum_price_variance_percent", 10)
		frappe.db.set_single_value("NextGen Procurement Settings", "default_buying_warehouse", "")
		frappe.db.set_single_value("NextGen Procurement Settings", "allowed_warehouses", "")

	def _session(self):
		return frappe.get_doc(
			{
				"doctype": "NextGen Chat Session",
				"user": "Administrator",
				"agent_type": "procurement",
				"title": "Procurement chat test",
				"status": "Open",
				"last_activity_at": now_datetime(),
			}
		).insert(ignore_permissions=True)

	def _prepare(self, qty=10):
		session = self._session()
		return procurement._prepare_purchase_order(
			{"supplier": demo.SUPPLIER, "items": [{"item": "M-150", "qty": qty, "uom": "ลัง"}]},
			user="Administrator",
			session_id=session.name,
		)

	# ------------------------------------------------------------------
	# Forecast over live demo data
	# ------------------------------------------------------------------

	def test_forecast_from_demo_data(self):
		result = forecast.forecast_item("DRK-M150")
		self.assertGreater(result["demand_90"], 0)
		self.assertGreater(result["average_daily_demand"], 0)
		self.assertGreater(result["incoming_qty"], 0)  # open demo PO
		self.assertEqual(result["lead_time_days"], 5)
		self.assertIn(demo.SUPPLIER, result["suppliers"])
		self.assertGreater(result["last_purchase_rate"], 0)
		self.assertEqual(result["formula_version"], forecast.FORMULA_VERSION)
		json.dumps(result, ensure_ascii=False, default=str)

	def test_forecast_snapshot_is_stored_and_immutable(self):
		result = forecast.forecast_item("DRK-M150", save_snapshot=True)
		snapshot = frappe.get_doc("NextGen Procurement Forecast", result["snapshot"])
		self.assertEqual(snapshot.item, "DRK-M150")
		snapshot.suggested_qty = 999
		with self.assertRaises(frappe.ValidationError):
			snapshot.save(ignore_permissions=True)

	def test_manual_forecast_button_creates_snapshot_only(self):
		po_before = frappe.db.count("Purchase Order")
		mr_before = frappe.db.count("Material Request")
		result = forecast.generate_forecasts(item_codes=["DRK-M150"], horizon_days=30, limit=1)
		self.assertEqual(result["generated"], 1)
		self.assertEqual(result["failed"], 0)
		self.assertTrue(
			frappe.db.exists("NextGen Procurement Forecast", result["forecasts"][0]["snapshot"])
		)
		self.assertEqual(frappe.db.count("Purchase Order"), po_before)
		self.assertEqual(frappe.db.count("Material Request"), mr_before)

	def test_risk_summary_exposes_bounded_forecast_cards_for_chat(self):
		result = procurement._summarize_risk(limit=10, horizon_days=30)
		self.assertLessEqual(len(result["forecast_cards"]), 5)
		if result["items"]:
			self.assertEqual(result["forecast_cards"][0]["formula_version"], forecast.FORMULA_VERSION)

	# ------------------------------------------------------------------
	# Purchase actions
	# ------------------------------------------------------------------

	def test_preview_creates_no_purchase_order(self):
		before = frappe.db.count("Purchase Order")
		result = self._prepare()
		self.assertTrue(result["action_id"])
		self.assertEqual(result["status"], "Pending")
		self.assertEqual(frappe.db.count("Purchase Order"), before)
		preview = result["preview"]
		self.assertEqual(preview["supplier"], demo.SUPPLIER)
		self.assertEqual(preview["document_type"], "Purchase Order")
		self.assertTrue(preview["items"][0]["forecast"]["formula_version"])
		json.dumps(result, ensure_ascii=False, default=str)

	def test_confirmation_creates_one_draft_po_and_is_idempotent(self):
		result = self._prepare()
		first = staff_chat.confirm_action(result["action_id"])
		second = staff_chat.confirm_action(result["action_id"])
		self.assertEqual(first["document_type"], "Purchase Order")
		self.assertEqual(first["document_name"], second["document_name"])
		self.assertTrue(second["already"])
		# Draft only: chat confirmation never submits a Purchase Order.
		self.assertEqual(frappe.db.get_value("Purchase Order", first["document_name"], "docstatus"), 0)

	def test_expired_action_is_rejected(self):
		result = self._prepare()
		frappe.db.set_value(
			"NextGen Chat Action",
			result["action_id"],
			"expires_at",
			add_to_date(now_datetime(), minutes=-1),
		)
		with self.assertRaises(frappe.ValidationError):
			staff_chat.confirm_action(result["action_id"])

	def test_pending_preview_can_select_warehouse_without_new_ai_turn(self):
		company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
			"Global Defaults", "default_company"
		)
		abbr = frappe.db.get_value("Company", company, "abbr")
		warehouse = f"Procurement Revision - {abbr}"
		if not frappe.db.exists("Warehouse", warehouse):
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": "Procurement Revision",
					"company": company,
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
		result = self._prepare()
		original = frappe.get_doc("NextGen Chat Action", result["action_id"])
		original_payload = original.proposal_payload
		revised = staff_chat.revise_action(
			result["action_id"],
			{"warehouse": warehouse, "schedule_date": add_days(nowdate(), 7)},
		)
		self.assertNotEqual(revised["action_id"], result["action_id"])
		self.assertEqual(revised["preview"]["warehouse"], warehouse)
		self.assertTrue(all(row["warehouse"] == warehouse for row in revised["preview"]["items"]))
		original.reload()
		self.assertEqual(original.status, "Cancelled")
		self.assertEqual(original.proposal_payload, original_payload)

	def test_default_buying_warehouse_never_uses_transit(self):
		company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
			"Global Defaults", "default_company"
		)
		transit = frappe.db.get_value("Company", company, "default_in_transit_warehouse")
		self.assertNotEqual(forecast.default_buying_warehouse(company), transit)

	def test_shadow_mode_never_creates_buying_documents(self):
		frappe.db.set_single_value("NextGen Procurement Settings", "automation_mode", "Shadow")
		result = self._prepare()
		before = frappe.db.count("Purchase Order")
		outcome = staff_chat.confirm_action(result["action_id"])
		self.assertEqual(outcome["document_type"], "NextGen Procurement Recommendation")
		self.assertEqual(frappe.db.count("Purchase Order"), before)
		self.assertEqual(
			frappe.db.get_value(
				"NextGen Procurement Recommendation", outcome["document_name"], "status"
			),
			"Needs Review",
		)

	def test_price_change_triggers_revalidation(self):
		result = self._prepare()
		# A new, far more expensive submitted PO changes the live rate by >10%.
		company = frappe.defaults.get_user_default("company") or frappe.db.get_single_value(
			"Global Defaults", "default_company"
		)
		warehouse = frappe.db.get_value(
			"Warehouse", {"company": company, "is_group": 0}, "name", order_by="creation asc"
		)
		po = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"supplier": demo.SUPPLIER,
				"company": company,
				"transaction_date": nowdate(),
				"schedule_date": add_days(nowdate(), 5),
				"items": [
					{
						"item_code": "DRK-M150",
						"qty": 5,
						"rate": 600,
						"uom": "ลัง",
						"warehouse": warehouse,
						"schedule_date": add_days(nowdate(), 5),
					}
				],
			}
		)
		po.insert(ignore_permissions=True)
		po.submit()
		try:
			outcome = staff_chat.confirm_action(result["action_id"])
			self.assertEqual(outcome["document_type"], "NextGen Procurement Recommendation")
			self.assertTrue(outcome["result"]["issues"])
		finally:
			po.reload()
			po.cancel()

	def test_ambiguous_or_unknown_supplier_is_flagged(self):
		session = self._session()
		result = procurement._prepare_purchase_order(
			{"supplier": "ไม่มีจริง จำกัด", "items": [{"item": "M-150", "qty": 5}]},
			user="Administrator",
			session_id=session.name,
		)
		self.assertTrue(any("supplier" in w or "Supplier" in w for w in result["preview"]["warnings"]))
		outcome = staff_chat.confirm_action(result["action_id"])
		self.assertEqual(outcome["document_type"], "NextGen Procurement Recommendation")

	def test_material_request_preview_and_confirmation(self):
		session = self._session()
		result = procurement._prepare_material_request(
			{"items": [{"item": "M-150", "qty": 8, "uom": "ลัง"}]},
			user="Administrator",
			session_id=session.name,
		)
		self.assertEqual(result["preview"]["document_type"], "Material Request")
		outcome = staff_chat.confirm_action(result["action_id"])
		self.assertEqual(outcome["document_type"], "Material Request")
		self.assertEqual(
			frappe.db.get_value("Material Request", outcome["document_name"], "docstatus"), 0
		)

	# ------------------------------------------------------------------
	# Automation modes
	# ------------------------------------------------------------------

	def test_automation_defaults_are_safe(self):
		frappe.db.set_single_value("NextGen Procurement Settings", "automation_mode", "")
		settings = forecast.get_settings()
		self.assertEqual(settings["automation_mode"], "Shadow")
		self.assertFalse(settings["allow_direct_po_submission"])

	def test_scheduled_forecast_in_shadow_mode_only_records(self):
		frappe.db.set_single_value("NextGen Procurement Settings", "automation_mode", "Shadow")
		frappe.db.set_single_value("NextGen Procurement Settings", "enable_scheduled_forecast", 1)
		po_before = frappe.db.count("Purchase Order")
		mr_before = frappe.db.count("Material Request")
		stats = procurement.run_scheduled_forecast(force=True)
		self.assertGreaterEqual(stats["analyzed"], 1)
		self.assertEqual(stats["auto_created"], 0)
		self.assertEqual(frappe.db.count("Purchase Order"), po_before)
		self.assertEqual(frappe.db.count("Material Request"), mr_before)

	def test_scheduled_forecast_is_idempotent_per_day(self):
		frappe.db.set_single_value("NextGen Procurement Settings", "automation_mode", "Shadow")
		procurement.run_scheduled_forecast(force=True)
		count_after_first = frappe.db.count("NextGen Procurement Recommendation")
		procurement.run_scheduled_forecast(force=True)
		self.assertEqual(frappe.db.count("NextGen Procurement Recommendation"), count_after_first)

	def test_automatic_mode_fails_closed_without_allowlist(self):
		frappe.db.set_single_value("NextGen Procurement Settings", "automation_mode", "Automatic")
		frappe.db.set_single_value("NextGen Procurement Settings", "auto_item_allowlist", "")
		po_before = frappe.db.count("Purchase Order")
		mr_before = frappe.db.count("Material Request")
		stats = procurement.run_scheduled_forecast(force=True)
		self.assertEqual(stats["auto_created"], 0)
		self.assertEqual(frappe.db.count("Purchase Order"), po_before)
		self.assertEqual(frappe.db.count("Material Request"), mr_before)

	def test_automatic_gates_require_every_condition(self):
		settings = forecast.get_settings()
		result = forecast.forecast_item("DRK-M150", None, None, settings)
		failures = procurement._automatic_gates(result, settings, 1000)
		self.assertIn("item not in auto allowlist", failures)

	def test_direct_po_submission_disabled_by_default(self):
		self.assertFalse(forecast.get_settings()["allow_direct_po_submission"])

	# ------------------------------------------------------------------
	# Permissions
	# ------------------------------------------------------------------

	def test_cross_user_action_access_is_rejected(self):
		from nextgen_erp.tests.test_agents import PURCHASE_ONLY_USER, _ensure_user

		_ensure_user(PURCHASE_ONLY_USER, ["Purchase User"])
		result = self._prepare()
		frappe.set_user(PURCHASE_ONLY_USER)
		try:
			with self.assertRaises(frappe.DoesNotExistError):
				staff_chat.confirm_action(result["action_id"])
		finally:
			frappe.set_user("Administrator")

	def test_sales_only_user_cannot_confirm_procurement_action(self):
		from nextgen_erp.tests.test_agents import SALES_ONLY_USER, _ensure_user

		_ensure_user(SALES_ONLY_USER, ["Sales User"])
		result = self._prepare()
		frappe.db.set_value("NextGen Chat Action", result["action_id"], "user", SALES_ONLY_USER)
		frappe.set_user(SALES_ONLY_USER)
		try:
			with self.assertRaises(frappe.PermissionError):
				staff_chat.confirm_action(result["action_id"])
		finally:
			frappe.set_user("Administrator")

	def test_run_forecast_now_requires_purchase_manager(self):
		from nextgen_erp.tests.test_agents import SALES_ONLY_USER, _ensure_user

		_ensure_user(SALES_ONLY_USER, ["Sales User"])
		frappe.set_user(SALES_ONLY_USER)
		try:
			with self.assertRaises(frappe.PermissionError):
				procurement.run_forecast_now()
		finally:
			frappe.set_user("Administrator")

	# ------------------------------------------------------------------
	# Vendor comparison + priority-aware ordering
	# ------------------------------------------------------------------

	def test_compare_suppliers_ranks_by_priority(self):
		urgent = procurement._compare_suppliers("DRK-M150", "urgent")
		best_price = procurement._compare_suppliers("DRK-M150", "best_price")
		names = {row["supplier"] for row in urgent["suppliers"]}
		self.assertIn(demo.SUPPLIER, names)
		self.assertIn(demo.SUPPLIER2, names)
		# โอสถสภา receives in ~2 days; สยามซัพพลาย is cheaper but takes 6-7 days.
		self.assertEqual(urgent["recommended"]["supplier"], demo.SUPPLIER)
		self.assertEqual(best_price["recommended"]["supplier"], demo.SUPPLIER2)
		self.assertTrue(urgent["recommended"]["reason"])
		self.assertTrue(urgent["comparison_card"])
		fast = next(row for row in urgent["suppliers"] if row["supplier"] == demo.SUPPLIER)
		slow = next(row for row in urgent["suppliers"] if row["supplier"] == demo.SUPPLIER2)
		self.assertLess(fast["effective_lead_days"], slow["effective_lead_days"])
		self.assertLess(slow["last_rate"], fast["last_rate"])
		json.dumps(urgent, ensure_ascii=False, default=str)

	def test_compare_suppliers_is_deterministic(self):
		first = procurement._compare_suppliers("DRK-M150", "urgent")
		second = procurement._compare_suppliers("DRK-M150", "urgent")
		self.assertEqual(first, second)

	def test_urgent_priority_auto_selects_fastest_supplier(self):
		session = self._session()
		result = procurement._prepare_purchase_order(
			{"items": [{"item": "M-150", "qty": 10, "uom": "ลัง"}], "priority": "urgent"},
			user="Administrator",
			session_id=session.name,
		)
		preview = result["preview"]
		self.assertEqual(preview["supplier"], demo.SUPPLIER)
		self.assertTrue(preview["supplier_auto_selected"])
		self.assertEqual(preview["priority"], "urgent")
		self.assertTrue(preview["priority_note"])
		# Urgent uses the supplier's measured lead (~2 days), not the item's 5.
		self.assertEqual(preview["schedule_date"], str(add_days(nowdate(), 2)))

	def test_best_price_priority_selects_cheapest_supplier(self):
		session = self._session()
		result = procurement._prepare_purchase_order(
			{"items": [{"item": "M-150", "qty": 10, "uom": "ลัง"}], "priority": "best_price"},
			user="Administrator",
			session_id=session.name,
		)
		preview = result["preview"]
		self.assertEqual(preview["supplier"], demo.SUPPLIER2)
		self.assertTrue(preview["supplier_auto_selected"])

	def test_priority_change_reranks_auto_selected_supplier(self):
		session = self._session()
		result = procurement._prepare_purchase_order(
			{"items": [{"item": "M-150", "qty": 10, "uom": "ลัง"}], "priority": "urgent"},
			user="Administrator",
			session_id=session.name,
		)
		revised = staff_chat.revise_action(result["action_id"], {"priority": "best_price"})
		self.assertEqual(revised["preview"]["supplier"], demo.SUPPLIER2)
		self.assertEqual(revised["preview"]["priority"], "best_price")
		self.assertEqual(
			frappe.db.get_value("NextGen Chat Action", result["action_id"], "status"), "Cancelled"
		)

	def test_llm_supplied_rate_override_is_stripped(self):
		session = self._session()
		result = procurement.dispatch_tool(
			"prepare_purchase_order",
			{
				"supplier": demo.SUPPLIER,
				"items": [
					{"item": "M-150", "qty": 5, "uom": "ลัง", "rate": 1, "rate_overridden": True}
				],
			},
			user="Administrator",
			session_id=session.name,
		)
		line = result["preview"]["items"][0]
		self.assertFalse(line["rate_overridden"])
		self.assertGreater(line["rate"], 1)

	# ------------------------------------------------------------------
	# In-chat editing (qty / rate / priority) without a new AI turn
	# ------------------------------------------------------------------

	def test_revise_action_updates_quantity(self):
		result = self._prepare(qty=10)
		revised = staff_chat.revise_action(
			result["action_id"],
			{"items": [{"item_code": "DRK-M150", "qty": 25}]},
		)
		line = revised["preview"]["items"][0]
		self.assertEqual(line["qty"], 25)
		self.assertAlmostEqual(revised["preview"]["total"], 25 * line["rate"], places=2)
		self.assertEqual(
			frappe.db.get_value("NextGen Chat Action", result["action_id"], "status"), "Cancelled"
		)

	def test_revise_action_rejects_bad_items(self):
		result = self._prepare()
		with self.assertRaises(frappe.ValidationError):
			staff_chat.revise_action(
				result["action_id"], {"items": [{"item_code": "DRK-M150", "qty": 0}]}
			)
		result = self._prepare()
		with self.assertRaises(frappe.ValidationError):
			staff_chat.revise_action(
				result["action_id"], {"items": [{"item_code": "NO-SUCH-ITEM", "qty": 5}]}
			)

	def test_rate_override_is_audited_and_creates_draft_po(self):
		result = self._prepare(qty=10)
		revised = staff_chat.revise_action(
			result["action_id"],
			{"items": [{"item_code": "DRK-M150", "qty": 10, "rate": 500}]},
		)
		line = revised["preview"]["items"][0]
		self.assertTrue(line["rate_overridden"])
		self.assertEqual(line["rate"], 500)
		self.assertEqual(line["rate_override_by"], "Administrator")
		self.assertTrue(
			any("กำหนดเองโดย Administrator" in warning for warning in revised["preview"]["warnings"])
		)
		# 500 is >10% off the system rate, but the override is the buyer's audited
		# decision: confirmation must not be blocked by the variance guardrail.
		outcome = staff_chat.confirm_action(revised["action_id"])
		self.assertEqual(outcome["document_type"], "Purchase Order")
		po_rate = frappe.db.get_value(
			"Purchase Order Item", {"parent": outcome["document_name"]}, "rate"
		)
		self.assertEqual(flt(po_rate), 500)

	def test_rate_override_still_respects_budget_guardrail(self):
		frappe.db.set_single_value("NextGen Procurement Settings", "maximum_po_value", 1000)
		result = self._prepare(qty=10)
		revised = staff_chat.revise_action(
			result["action_id"],
			{"items": [{"item_code": "DRK-M150", "qty": 10, "rate": 500}]},
		)
		outcome = staff_chat.confirm_action(revised["action_id"])
		self.assertEqual(outcome["document_type"], "NextGen Procurement Recommendation")
		self.assertEqual(
			frappe.db.get_value(
				"NextGen Procurement Recommendation", outcome["document_name"], "status"
			),
			"Needs Review",
		)
