from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from nextgen_agent_runtime.models import contracts

FRAPPE_CONTRACTS = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "nextgen_erp"
    / "nextgen_erp"
    / "agent_gateway"
    / "contracts.py"
)


class ContractValidationTests(unittest.TestCase):
    def test_accepts_a_well_formed_payload(self):
        payload = contracts.validate(
            "dispatch_run_request",
            {
                "contract_version": "v1",
                "run_id": "RUN-0001",
                "correlation_id": "corr-1",
                "idempotency_key": "RUN-0001:dispatch",
            },
        )
        self.assertEqual(payload["run_id"], "RUN-0001")

    def test_rejects_unknown_fields(self):
        with self.assertRaises(contracts.ContractError):
            contracts.validate(
                "dispatch_run_request",
                {
                    "contract_version": "v1",
                    "run_id": "RUN-0001",
                    "correlation_id": "corr-1",
                    "idempotency_key": "key",
                    "company": "smuggled",
                },
            )

    def test_rejects_a_missing_required_field(self):
        with self.assertRaises(contracts.ContractError):
            contracts.validate("complete_run_request", {"contract_version": "v1", "run_id": "RUN-1"})

    def test_rejects_a_status_outside_the_enum(self):
        with self.assertRaises(contracts.ContractError):
            contracts.validate(
                "complete_run_request",
                {"contract_version": "v1", "run_id": "RUN-1", "status": "Approved"},
            )

    def test_runtime_cannot_report_a_run_as_approved(self):
        # Approval is a human decision in Frappe; the runtime has no status for it.
        self.assertNotIn("Approved", contracts.RUNTIME_COMPLETION_STATUSES)

    def test_rejects_an_unsupported_contract_version(self):
        with self.assertRaises(contracts.ContractError):
            contracts.validate(
                "claim_run_request",
                {"contract_version": "v99", "run_id": "RUN-1", "runtime_version": "1"},
            )

    def test_the_previous_version_stays_accepted_for_one_release(self):
        # The services deploy independently: a runtime still on v1 must keep
        # working against a v2 gateway.
        for version in contracts.SUPPORTED_CONTRACT_VERSIONS:
            with self.subTest(version=version):
                contracts.validate(
                    "claim_run_request",
                    {"contract_version": version, "run_id": "RUN-1", "runtime_version": "1"},
                )

    def test_v2_additions_are_optional_so_a_v1_runtime_can_ignore_them(self):
        required = contracts.SCHEMAS["run_context"]["required"]
        for field in ("workflow", "workflow_node", "parent_run", "graph"):
            with self.subTest(field=field):
                self.assertIn(field, contracts.SCHEMAS["run_context"]["properties"])
                self.assertNotIn(field, required)

    def test_numeric_bounds_are_enforced(self):
        with self.assertRaises(contracts.ContractError):
            contracts.validate(
                "record_step_request",
                {
                    "contract_version": "v1",
                    "run_id": "RUN-1",
                    "sequence": 0,
                    "step_type": "Model",
                    "operation": "model.completion",
                    "status": "Success",
                    "idempotency_key": "k",
                },
            )

    def test_hashes_are_stable_across_key_order(self):
        self.assertEqual(
            contracts.payload_hash({"a": 1, "b": [2, 3]}),
            contracts.payload_hash({"b": [2, 3], "a": 1}),
        )


class CrossServiceContractTests(unittest.TestCase):
    """Both sides of the boundary must describe the same versioned payloads."""

    def test_frappe_gateway_copy_matches_this_release(self):
        if not FRAPPE_CONTRACTS.exists():
            self.skipTest("Frappe app is not checked out next to the runtime")
        spec = importlib.util.spec_from_file_location("frappe_contracts", FRAPPE_CONTRACTS)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.CONTRACT_VERSION, contracts.CONTRACT_VERSION)
        self.assertEqual(module.contract_fingerprint(), contracts.contract_fingerprint())


if __name__ == "__main__":
    unittest.main()
