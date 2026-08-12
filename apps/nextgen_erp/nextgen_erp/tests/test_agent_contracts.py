"""Boundary contract and redaction tests.

These import no Frappe modules, so they also run standalone:

    python -m unittest nextgen_erp.tests.test_agent_contracts
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from nextgen_erp.agent_gateway import contracts, redaction

RUNTIME_CONTRACTS = (
	Path(__file__).resolve().parents[4]
	/ "services"
	/ "agent-runtime"
	/ "src"
	/ "nextgen_agent_runtime"
	/ "models"
	/ "contracts.py"
)


class ContractSurfaceTests(unittest.TestCase):
	def test_every_boundary_payload_has_a_schema(self):
		expected = {
			"dispatch_run_request",
			"cancel_run_request",
			"accepted",
			"runtime_health",
			"claim_run_request",
			"run_context",
			"record_step_request",
			"step_reference",
			"complete_run_request",
			"execute_tool_request",
			"tool_result",
			"create_proposal_request",
			"proposal_reference",
			"create_node_run_request",
		}
		self.assertEqual(set(contracts.SCHEMAS), expected)

	def test_schemas_are_closed_against_unknown_fields(self):
		for name, schema in contracts.SCHEMAS.items():
			with self.subTest(schema=name):
				self.assertIs(schema["additionalProperties"], False)

	def test_the_runtime_cannot_report_an_approval(self):
		self.assertNotIn("Approved", contracts.RUNTIME_COMPLETION_STATUSES)
		self.assertIn("Approved", contracts.PROPOSAL_STATUSES)

	def test_a_payload_that_smuggles_identity_is_rejected(self):
		with self.assertRaises(contracts.ContractError):
			contracts.validate(
				"execute_tool_request",
				{
					"contract_version": contracts.CONTRACT_VERSION,
					"run_id": "RUN-1",
					"sequence": 1,
					"tool_name": "search_items",
					"arguments": {},
					"idempotency_key": "k",
					"execution_user": "Administrator",
				},
			)

	def test_hashes_ignore_key_order(self):
		self.assertEqual(
			contracts.payload_hash({"a": 1, "b": 2}), contracts.payload_hash({"b": 2, "a": 1})
		)


class CrossServiceContractTests(unittest.TestCase):
	def test_the_runtime_copy_describes_the_same_payloads(self):
		if not RUNTIME_CONTRACTS.exists():
			self.skipTest("Agent Runtime service is not checked out next to the app")
		spec = importlib.util.spec_from_file_location("runtime_contracts", RUNTIME_CONTRACTS)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		self.assertEqual(module.CONTRACT_VERSION, contracts.CONTRACT_VERSION)
		self.assertEqual(module.contract_fingerprint(), contracts.contract_fingerprint())


class RedactionTests(unittest.TestCase):
	def test_secrets_never_reach_a_persisted_step(self):
		result = redaction.redact(
			{
				"gateway_api_key": "sk-live-1234567890abcdef",
				"channel_secret": "abc",
				"items": [{"item_code": "M-150", "rate": 12.5}],
			}
		)
		self.assertEqual(result["gateway_api_key"], redaction.REDACTED)
		self.assertEqual(result["channel_secret"], redaction.REDACTED)
		self.assertEqual(result["items"][0]["item_code"], "M-150")

	def test_hidden_model_reasoning_is_never_stored(self):
		self.assertEqual(
			redaction.redact({"reasoning_content": "..."})["reasoning_content"], redaction.REDACTED
		)

	def test_line_ids_are_masked_in_free_text(self):
		line_id = "U" + "0123abcd" * 4
		self.assertNotIn(line_id, redaction.redact_text(f"from {line_id}"))

	def test_error_summaries_are_one_bounded_line(self):
		summary = redaction.redact_error(ValueError("boom\nnext line"))
		self.assertNotIn("\n", summary)
		self.assertLessEqual(len(summary), 500)


if __name__ == "__main__":
	unittest.main()
