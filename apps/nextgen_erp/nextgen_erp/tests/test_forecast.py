import unittest

from frappe.utils import add_days, nowdate

from nextgen_erp import forecast

SETTINGS = {
	"enabled": True,
	"model": "",
	"default_buying_warehouse": "",
	"horizon_days": 30,
	"history_window_days": 90,
	"safety_stock_days": 7,
	"minimum_data_days": 14,
	"default_lead_time_days": 7,
	"automation_mode": "Shadow",
	"enable_scheduled_forecast": False,
	"maximum_po_value": 50000.0,
	"maximum_price_variance_percent": 10.0,
	"minimum_data_quality_score": 0.8,
	"daily_auto_spend_limit": 0.0,
	"allowed_item_groups": [],
	"allowed_warehouses": [],
	"auto_item_allowlist": [],
	"approved_suppliers": [],
	"allow_direct_po_submission": False,
}


def make_inputs(**overrides):
	"""Synthetic collect_inputs() payload: healthy 90-day weekly demand."""
	inputs = {
		"item_code": "TEST-ITEM",
		"item_name": "Test Item",
		"stock_uom": "Nos",
		"item_group": "All Item Groups",
		"disabled": 0,
		"is_purchase_item": 1,
		"end_of_life": None,
		"order_multiple": 0.0,
		"last_movement_date": nowdate(),
		"demand": {
			"daily": [
				{"day": add_days(nowdate(), -offset), "qty": 70.0} for offset in range(3, 85, 7)
			],
			"windows": {"30": 280.0, "60": 630.0, "90": 840.0},
			"order_days": 12,
			"max_daily_qty": 70.0,
			"first_demand_date": add_days(nowdate(), -80),
			"history_coverage_days": 80,
			"source": "Sales Order (docstatus=1)",
			"from_date": add_days(nowdate(), -90),
			"to_date": nowdate(),
		},
		"stock": {
			"warehouse": "Stores - NG",
			"actual_qty": 100.0,
			"reserved_qty": 0.0,
			"ordered_qty": 0.0,
			"indented_qty": 0.0,
			"projected_qty": 100.0,
		},
		"open_purchase_orders": [],
		"prices": {
			"lots": [
				{"purchase_order": "PO-2", "supplier": "S", "date": add_days(nowdate(), -12), "rate": 350.0, "qty": 50, "uom": "Nos"},
				{"purchase_order": "PO-1", "supplier": "S", "date": add_days(nowdate(), -60), "rate": 335.0, "qty": 50, "uom": "Nos"},
			],
			"last_purchase_rate": 350.0,
			"previous_purchase_rate": 335.0,
			"price_variance_percent": 4.48,
			"buying_price_list_rate": 350.0,
		},
		"supplier": {
			"default_supplier": "S",
			"suppliers": ["S"],
			"lead_time_days": 5,
			"lead_time_source": "Item.lead_time_days",
			"effective_lead_time_days": 5,
			"item_safety_stock": 10.0,
			"min_order_qty": 0.0,
		},
		"as_of": nowdate(),
	}
	for key, value in overrides.items():
		if isinstance(inputs.get(key), dict) and isinstance(value, dict):
			inputs[key] = {**inputs[key], **value}
		else:
			inputs[key] = value
	return inputs


class TestForecastFormulaV1(unittest.TestCase):
	def test_normal_history_produces_expected_metrics(self):
		result = forecast.compute_forecast(make_inputs(), SETTINGS)
		# 0.5*(280/30) + 0.3*(630/60) + 0.2*(840/90)
		self.assertAlmostEqual(result["average_daily_demand"], 9.6833, places=3)
		self.assertEqual(result["lead_time_days"], 5)
		self.assertAlmostEqual(result["lead_time_demand"], 9.6833 * 5, places=2)
		# safety = max(10, add*7)
		self.assertAlmostEqual(result["safety_stock"], round(9.6833 * 7, 4), places=2)
		self.assertAlmostEqual(
			result["reorder_point"], result["lead_time_demand"] + result["safety_stock"], places=4
		)
		self.assertEqual(result["projected_available"], 100.0)
		expected_suggested = round(9.6833 * 30 + 9.6833 * 7 - 100, 1)
		self.assertAlmostEqual(result["suggested_qty"], expected_suggested, delta=0.5)
		self.assertEqual(result["formula_version"], forecast.FORMULA_VERSION)
		self.assertEqual(result["movement_class"], "fast-moving")
		self.assertTrue(result["assumptions"]["history_from"])

	def test_deterministic_for_identical_inputs(self):
		first = forecast.compute_forecast(make_inputs(), SETTINGS)
		second = forecast.compute_forecast(make_inputs(), SETTINGS)
		self.assertEqual(first, second)

	def test_insufficient_history_warns_and_lowers_quality(self):
		inputs = make_inputs(
			demand={
				"windows": {"30": 20.0, "60": 20.0, "90": 20.0},
				"history_coverage_days": 5,
				"order_days": 2,
				"max_daily_qty": 10.0,
			}
		)
		result = forecast.compute_forecast(inputs, SETTINGS)
		self.assertIn("short_history", result["data_quality_penalties"])
		self.assertLess(result["data_quality_score"], 1.0)

	def test_no_history_flags_no_history(self):
		inputs = make_inputs(
			demand={
				"daily": [],
				"windows": {"30": 0.0, "60": 0.0, "90": 0.0},
				"order_days": 0,
				"max_daily_qty": 0.0,
				"history_coverage_days": 0,
				"first_demand_date": None,
			}
		)
		result = forecast.compute_forecast(inputs, SETTINGS)
		self.assertIn("no_history", result["data_quality_penalties"])
		self.assertEqual(result["average_daily_demand"], 0)
		self.assertEqual(result["movement_class"], "no-movement")

	def test_no_supplier_warns(self):
		inputs = make_inputs(
			supplier={"default_supplier": None, "suppliers": [], "lead_time_days": 5, "effective_lead_time_days": 5}
		)
		result = forecast.compute_forecast(inputs, SETTINGS)
		self.assertIn("no_supplier", result["data_quality_penalties"])

	def test_missing_lead_time_uses_default_and_warns(self):
		inputs = make_inputs(
			supplier={
				"default_supplier": "S",
				"suppliers": ["S"],
				"lead_time_days": None,
				"effective_lead_time_days": SETTINGS["default_lead_time_days"],
				"item_safety_stock": 10.0,
				"min_order_qty": 0.0,
			}
		)
		result = forecast.compute_forecast(inputs, SETTINGS)
		self.assertIn("no_lead_time", result["data_quality_penalties"])
		self.assertEqual(result["lead_time_days"], SETTINGS["default_lead_time_days"])

	def test_open_po_reduces_suggested_qty(self):
		without_po = forecast.compute_forecast(make_inputs(), SETTINGS)
		with_po = forecast.compute_forecast(
			make_inputs(
				open_purchase_orders=[
					{"purchase_order": "PO-X", "supplier": "S", "status": "To Receive and Bill",
					 "transaction_date": nowdate(), "schedule_date": nowdate(), "pending_qty": 50.0,
					 "rate": 350.0, "uom": "Nos"}
				]
			),
			SETTINGS,
		)
		self.assertEqual(with_po["incoming_qty"], 50.0)
		self.assertAlmostEqual(with_po["suggested_qty"], without_po["suggested_qty"] - 50.0, places=2)

	def test_reserved_stock_increases_need(self):
		base = forecast.compute_forecast(make_inputs(), SETTINGS)
		reserved = forecast.compute_forecast(
			make_inputs(stock={"reserved_qty": 40.0}), SETTINGS
		)
		self.assertAlmostEqual(reserved["suggested_qty"], base["suggested_qty"] + 40.0, places=2)
		self.assertLess(reserved["projected_available"], base["projected_available"])

	def test_moq_and_order_multiple_rounding(self):
		# Big stock so raw suggestion is small but positive.
		inputs = make_inputs(stock={"actual_qty": 320.0, "projected_qty": 320.0})
		raw = forecast.compute_forecast(inputs, SETTINGS)
		self.assertGreater(raw["suggested_qty"], 0)
		moq_inputs = make_inputs(
			stock={"actual_qty": 320.0, "projected_qty": 320.0},
			supplier={
				"default_supplier": "S", "suppliers": ["S"], "lead_time_days": 5,
				"effective_lead_time_days": 5, "item_safety_stock": 10.0, "min_order_qty": 100.0,
			},
		)
		with_moq = forecast.compute_forecast(moq_inputs, SETTINGS)
		self.assertEqual(with_moq["suggested_qty"], 100.0)
		multiple_inputs = make_inputs(
			stock={"actual_qty": 320.0, "projected_qty": 320.0}, order_multiple=25.0
		)
		with_multiple = forecast.compute_forecast(multiple_inputs, SETTINGS)
		self.assertEqual(with_multiple["suggested_qty"] % 25.0, 0)

	def test_lead_time_changes_reorder_point(self):
		short = forecast.compute_forecast(make_inputs(), SETTINGS)
		long_inputs = make_inputs(
			supplier={
				"default_supplier": "S", "suppliers": ["S"], "lead_time_days": 15,
				"effective_lead_time_days": 15, "item_safety_stock": 10.0, "min_order_qty": 0.0,
			}
		)
		longer = forecast.compute_forecast(long_inputs, SETTINGS)
		self.assertGreater(longer["reorder_point"], short["reorder_point"])

	def test_slow_moving_item_suggests_nothing(self):
		inputs = make_inputs(
			demand={
				"windows": {"30": 2.0, "60": 4.0, "90": 6.0},
				"order_days": 6,
				"max_daily_qty": 1.0,
				"history_coverage_days": 85,
			},
			stock={"actual_qty": 500.0, "projected_qty": 500.0},
		)
		result = forecast.compute_forecast(inputs, SETTINGS)
		self.assertEqual(result["movement_class"], "slow-moving")
		self.assertEqual(result["suggested_qty"], 0)

	def test_negative_stock_and_spike_warn(self):
		inputs = make_inputs(
			stock={"actual_qty": -5.0, "projected_qty": -5.0},
			demand={"max_daily_qty": 400.0},
		)
		result = forecast.compute_forecast(inputs, SETTINGS)
		self.assertIn("negative_stock", result["data_quality_penalties"])
		self.assertIn("demand_spike", result["data_quality_penalties"])

	def test_price_variance_is_reported_and_flagged(self):
		inputs = make_inputs(
			prices={
				"lots": [],
				"last_purchase_rate": 460.0,
				"previous_purchase_rate": 400.0,
				"price_variance_percent": 15.0,
				"buying_price_list_rate": 460.0,
			}
		)
		result = forecast.compute_forecast(inputs, SETTINGS)
		self.assertEqual(result["price_variance_percent"], 15.0)
		self.assertTrue(any("15" in warning for warning in result["warnings"]))

	def test_disabled_item_suggests_zero(self):
		result = forecast.compute_forecast(make_inputs(disabled=1), SETTINGS)
		self.assertEqual(result["suggested_qty"], 0)
		self.assertIn("item_disabled", result["data_quality_penalties"])

	def test_no_purchase_price_penalised(self):
		inputs = make_inputs(
			prices={
				"lots": [],
				"last_purchase_rate": 0.0,
				"previous_purchase_rate": 0.0,
				"price_variance_percent": 0.0,
				"buying_price_list_rate": 0.0,
			}
		)
		result = forecast.compute_forecast(inputs, SETTINGS)
		self.assertIn("no_purchase_price", result["data_quality_penalties"])
