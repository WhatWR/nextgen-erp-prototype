"""Standalone purchase-email extractor + rules tests (no Frappe site required).

Run from the app package root::

    python -m unittest nextgen_erp.tests.test_purchase_email -v
"""

from __future__ import annotations

import unittest

from nextgen_erp.purchase_email.extractor import extract_order_fields
from nextgen_erp.purchase_email.fixtures import DEFAULT_RULES, FIXTURE_EMAILS
from nextgen_erp.purchase_email.providers import (
	GmailProvider,
	MockProvider,
	ProviderNotConfigured,
	build_provider,
)
from nextgen_erp.purchase_email.rules import RuleSpec, evaluate_rules


class ExtractorTests(unittest.TestCase):
	def test_extracts_siam_hardware_fixture(self):
		email = FIXTURE_EMAILS[0]
		draft = extract_order_fields(email["subject"], email["body"])
		self.assertEqual(draft["vendor_name"], "Siam Hardware Co., Ltd.")
		self.assertEqual(draft["po_reference"], "PO-TH-24081")
		self.assertEqual(draft["currency"], "THB")
		self.assertEqual(len(draft["items"]), 3)
		self.assertAlmostEqual(draft["total_amount"], 730.0)
		self.assertGreaterEqual(draft["confidence"], 0.8)

	def test_extracts_high_value_quote(self):
		email = FIXTURE_EMAILS[1]
		draft = extract_order_fields(email["subject"], email["body"])
		self.assertEqual(draft["vendor_name"], "Bangkok Steel Supply")
		self.assertAlmostEqual(draft["total_amount"], 973000.0)
		self.assertEqual(draft["items"][0]["sku"], "REBAR-12MM")


class RulesEngineTests(unittest.TestCase):
	def setUp(self):
		self.rules = [RuleSpec.from_mapping(row) for row in DEFAULT_RULES]

	def test_allowlisted_low_amount_approves(self):
		draft = extract_order_fields(FIXTURE_EMAILS[0]["subject"], FIXTURE_EMAILS[0]["body"])
		result = evaluate_rules(self.rules, draft)
		self.assertEqual(result.decision, "Approve")

	def test_high_amount_is_flagged(self):
		draft = extract_order_fields(FIXTURE_EMAILS[1]["subject"], FIXTURE_EMAILS[1]["body"])
		result = evaluate_rules(self.rules, draft)
		self.assertEqual(result.decision, "Flag")
		self.assertIn("100,000", result.summary)

	def test_unknown_vendor_is_rejected(self):
		draft = extract_order_fields(FIXTURE_EMAILS[2]["subject"], FIXTURE_EMAILS[2]["body"])
		result = evaluate_rules(self.rules, draft)
		self.assertEqual(result.decision, "Reject")

	def test_required_vendor_field(self):
		rules = [
			RuleSpec(
				name="Need vendor",
				rule_type="Required Field",
				action="Reject",
				field_name="vendor_name",
				message="Vendor required",
			)
		]
		result = evaluate_rules(rules, {"vendor_name": "", "items": []})
		self.assertEqual(result.decision, "Reject")
		self.assertFalse(result.results[0].passed)


class ProviderTests(unittest.TestCase):
	def test_mock_provider_lists_fixtures(self):
		provider = MockProvider()
		messages = provider.list_messages("INBOX/Purchase Orders")
		self.assertEqual(len(messages), 3)
		self.assertEqual(provider.connection_status(), "Connected")

	def test_build_provider_prefers_fixtures_flag(self):
		provider = build_provider({"provider": "Gmail", "use_mock_fixtures": 1})
		self.assertEqual(provider.name, "Mock")

	def test_gmail_without_secrets_is_not_configured(self):
		provider = GmailProvider()
		self.assertEqual(provider.connection_status(), "Not Connected")
		with self.assertRaises(ProviderNotConfigured):
			provider.list_messages("INBOX")

	def test_gmail_auth_url_when_client_present(self):
		provider = GmailProvider(client_id="demo-client", client_secret="demo-secret")
		hint = provider.connect_hint()
		self.assertEqual(hint["status"], "Needs Auth")
		self.assertIn("accounts.google.com", hint["auth_url"] or "")


if __name__ == "__main__":
	unittest.main()
