"""Gateway boundary tests: identity, company isolation, idempotency, lifecycle.

Positive paths run as Administrator so they do not depend on which ERPNext
roles a given site grants; the restricted users exist for the negative paths,
where a rejection is the assertion.
"""

from __future__ import annotations

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from nextgen_erp.action_proposals import service as proposals
from nextgen_erp.agent_gateway import api, contracts, permissions, policy
from nextgen_erp.agent_records import runs, steps

RUNTIME_USER = "agent-runtime@nextgen.test"
BUYER_USER = "gateway-buyer@nextgen.test"

SAMPLE_PREVIEW = {
	"customer": "GATEWAY-TEST-CUSTOMER",
	"company": None,
	"warehouse": None,
	"currency": "THB",
	"items": [{"item_code": "GATEWAY-TEST-ITEM", "qty": 2, "uom": "Nos", "rate": 10.0, "amount": 20.0}],
	"total": 20.0,
}


def _ensure_user(email: str, roles: list[str]):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"roles": [{"role": role} for role in roles],
			}
		).insert(ignore_permissions=True)
		return
	user = frappe.get_doc("User", email)
	user.roles = []
	for role in roles:
		user.append("roles", {"role": role})
	user.save(ignore_permissions=True)


def _payload(**fields):
	return {"contract_version": contracts.CONTRACT_VERSION, **fields}


class AgentGatewayTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		permissions.ensure_runtime_role()
		_ensure_user(RUNTIME_USER, [permissions.RUNTIME_ROLE])
		_ensure_user(BUYER_USER, ["Sales User"])
		cls.company = frappe.db.get_value("Company", {}, "name", order_by="creation asc")
		if cls.company:
			doc = policy.ensure_policy(cls.company)
			doc.enabled = 1
			doc.runtime_enabled = 1
			doc.default_mode = policy.APPROVAL_REQUIRED
			doc.save(ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		if not self.company:
			self.skipTest("no Company exists on this site")

	def tearDown(self):
		frappe.set_user("Administrator")

	# -- helpers -----------------------------------------------------------

	def _run(self, agent_type: str = "sales", **overrides):
		return runs.create_run(
			agent_type=agent_type,
			company=self.company,
			requested_by="Administrator",
			trigger_type="chat",
			input_payload={"messages": [{"role": "user", "content": "ทดสอบ"}]},
			**overrides,
		)

	def _claim(self, run):
		frappe.set_user(RUNTIME_USER)
		try:
			return api.claim_run(
				contract_version=contracts.CONTRACT_VERSION,
				run_id=run.name,
				runtime_version="test-runtime",
			)
		finally:
			frappe.set_user("Administrator")

	def _as_runtime(self, method, **kwargs):
		frappe.set_user(RUNTIME_USER)
		try:
			return method(**kwargs)
		finally:
			frappe.set_user("Administrator")


class IntegrationTestGatewayIdentity(AgentGatewayTestCase):
	def test_guest_cannot_reach_the_gateway(self):
		run = self._run()
		frappe.set_user("Guest")
		with self.assertRaises(frappe.AuthenticationError):
			api.claim_run(
				contract_version=contracts.CONTRACT_VERSION,
				run_id=run.name,
				runtime_version="test-runtime",
			)

	def test_a_human_session_cannot_claim_a_run(self):
		run = self._run()
		frappe.set_user(BUYER_USER)
		with self.assertRaises(frappe.PermissionError):
			api.claim_run(
				contract_version=contracts.CONTRACT_VERSION,
				run_id=run.name,
				runtime_version="test-runtime",
			)

	def test_the_runtime_identity_cannot_approve_a_proposal(self):
		run = self._run()
		proposal, _ = proposals.create_proposal(
			run,
			action_type="prepare_sales_order",
			company=self.company,
			preview={**SAMPLE_PREVIEW, "company": self.company},
			idempotency_key=f"test-approve-{run.name}",
		)
		frappe.set_user(RUNTIME_USER)
		with self.assertRaises(frappe.PermissionError):
			api.approve_proposal(proposal.name)

	def test_no_human_decision_endpoint_accepts_the_service_identity(self):
		run = self._run()
		proposal, _ = proposals.create_proposal(
			run,
			action_type="prepare_sales_order",
			company=self.company,
			preview={**SAMPLE_PREVIEW, "company": self.company},
			idempotency_key=f"test-decisions-{run.name}",
		)
		frappe.set_user(RUNTIME_USER)
		for method in (api.approve_proposal, api.reject_proposal, api.execute_proposal):
			with self.subTest(method=method.__name__), self.assertRaises(frappe.PermissionError):
				method(proposal.name)

	def test_a_contract_violation_is_rejected(self):
		run = self._run()
		frappe.set_user(RUNTIME_USER)
		with self.assertRaises(frappe.ValidationError):
			api.claim_run(contract_version="v0", run_id=run.name, runtime_version="test-runtime")


class IntegrationTestRunAuthority(AgentGatewayTestCase):
	def test_a_run_derives_its_company_and_tool_allowlist(self):
		run = self._run()
		self.assertEqual(run.company, self.company)
		self.assertEqual(run.status, "Queued")
		allowed = run.allowed_tool_names()
		self.assertIn("search_items", allowed)
		self.assertIn("prepare_sales_order", allowed)
		self.assertNotIn("summarize_procurement_risk", allowed)

	def test_a_disabled_policy_removes_write_tools(self):
		doc = policy.get_policy_doc(self.company)
		doc.enabled = 0
		doc.save(ignore_permissions=True)
		try:
			run = self._run()
			allowed = run.allowed_tool_names()
			self.assertIn("search_items", allowed)
			self.assertNotIn("prepare_sales_order", allowed)
		finally:
			doc.enabled = 1
			doc.save(ignore_permissions=True)

	def test_the_same_intent_reuses_one_run(self):
		first = self._run(idempotency_key="test-duplicate-intent")
		second = self._run(idempotency_key="test-duplicate-intent")
		self.assertEqual(first.name, second.name)

	def test_claiming_returns_a_bounded_context(self):
		run = self._run()
		context = self._claim(run)
		contracts.validate("run_context", context)
		self.assertEqual(context["company"], self.company)
		self.assertEqual(context["requested_by"], "Administrator")
		self.assertEqual(context["next_sequence"], 1)
		serialized = json.dumps(context, default=str).casefold()
		for secret in ("api_key", "password", "token", "secret"):
			self.assertNotIn(secret, serialized)

	def test_claiming_twice_returns_the_current_state(self):
		run = self._run()
		first = self._claim(run)
		second = self._claim(run)
		self.assertEqual(first["run_id"], second["run_id"])
		self.assertEqual(second["status"], "Running")

	def test_a_finished_run_is_never_reopened(self):
		run = self._run()
		self._claim(run)
		self._as_runtime(
			api.complete_run,
			**_payload(run_id=run.name, status="Completed"),
		)
		self._as_runtime(
			api.complete_run,
			**_payload(run_id=run.name, status="Failed", error="late callback"),
		)
		self.assertEqual(frappe.db.get_value("NextGen Agent Run", run.name, "status"), "Completed")


class IntegrationTestStepTrace(AgentGatewayTestCase):
	def test_steps_are_ordered_and_idempotent(self):
		run = self._run()
		self._claim(run)
		first = self._as_runtime(
			api.record_step,
			**_payload(
				run_id=run.name,
				sequence=1,
				step_type="System",
				operation="run.claimed",
				status="Success",
				idempotency_key=f"{run.name}:1:run.claimed",
			),
		)
		replay = self._as_runtime(
			api.record_step,
			**_payload(
				run_id=run.name,
				sequence=1,
				step_type="System",
				operation="run.claimed",
				status="Success",
				idempotency_key=f"{run.name}:1:run.claimed",
			),
		)
		self.assertFalse(first["replayed"])
		self.assertTrue(replay["replayed"])
		self.assertEqual(first["step_id"], replay["step_id"])
		self.assertEqual(frappe.db.count("NextGen Agent Step", {"run": run.name}), 1)

	def test_a_step_payload_is_redacted_before_persistence(self):
		run = self._run()
		self._claim(run)
		self._as_runtime(
			api.record_step,
			**_payload(
				run_id=run.name,
				sequence=1,
				step_type="Model",
				operation="model.completion",
				status="Success",
				idempotency_key=f"{run.name}:1:model.completion",
				sanitized_input={"api_key": "sk-live-abcdef1234567890"},
				result={"reasoning": "hidden"},
			),
		)
		step = frappe.get_doc("NextGen Agent Step", {"run": run.name, "sequence": 1})
		self.assertNotIn("sk-live", step.sanitized_input or "")
		self.assertIn("[redacted]", step.sanitized_input or "")
		self.assertIn("[redacted]", step.sanitized_output or "")
		# The complete payload stays verifiable through its hash.
		self.assertTrue(step.input_hash)


class IntegrationTestToolAuthorisation(AgentGatewayTestCase):
	def _execute(self, run, tool_name, arguments, sequence=2, key=None):
		return self._as_runtime(
			api.execute_tool,
			**_payload(
				run_id=run.name,
				sequence=sequence,
				tool_name=tool_name,
				arguments=arguments,
				idempotency_key=key or f"{run.name}:{sequence}:{tool_name}",
			),
		)

	def test_a_read_tool_runs_and_is_recorded(self):
		run = self._run()
		self._claim(run)
		result = self._execute(run, "search_items", {"query": "nextgen-gateway-no-such-item"})
		contracts.validate("tool_result", result)
		self.assertEqual(result["status"], "ok")
		step = frappe.get_doc("NextGen Agent Step", {"run": run.name, "operation": "search_items"})
		self.assertEqual(step.step_type, "Tool")
		self.assertEqual(step.status, "Success")

	def test_the_same_idempotency_key_replays_instead_of_rerunning(self):
		run = self._run()
		self._claim(run)
		key = f"{run.name}:2:search_items"
		self._execute(run, "search_items", {"query": "abc"}, key=key)
		replay = self._execute(run, "search_items", {"query": "abc"}, key=key)
		self.assertTrue(replay["replayed"])
		self.assertEqual(frappe.db.count("NextGen Agent Step", {"run": run.name, "operation": "search_items"}), 1)

	def test_a_tool_belonging_to_another_agent_is_rejected(self):
		run = self._run("sales")
		self._claim(run)
		result = self._execute(run, "summarize_procurement_risk", {})
		self.assertEqual(result["status"], "rejected")
		step = frappe.get_doc(
			"NextGen Agent Step", {"run": run.name, "operation": "summarize_procurement_risk"}
		)
		self.assertEqual(step.status, "Rejected")

	def test_an_unknown_tool_is_rejected(self):
		run = self._run()
		self._claim(run)
		self.assertEqual(self._execute(run, "drop_database", {})["status"], "rejected")

	def test_arguments_cannot_override_identity_or_company(self):
		run = self._run()
		self._claim(run)
		for smuggled in ({"user": "Administrator"}, {"company": "Other Co"}, {"ignore_permissions": True}):
			with self.subTest(smuggled=smuggled):
				result = self._execute(
					run, "search_items", {"query": "a", **smuggled}, key=f"{run.name}:x:{list(smuggled)[0]}"
				)
				self.assertEqual(result["status"], "rejected")

	def test_a_warehouse_from_another_company_is_rejected(self):
		other = frappe.db.get_value(
			"Warehouse", {"company": ["!=", self.company], "is_group": 0}, "name"
		)
		if not other:
			self.skipTest("site has warehouses for one company only")
		run = self._run()
		self._claim(run)
		result = self._execute(run, "get_item_price_and_stock", {"item_code": "X", "warehouse": other})
		self.assertEqual(result["status"], "rejected")

	def test_a_tool_call_on_a_finished_run_is_refused(self):
		run = self._run()
		self._claim(run)
		self._as_runtime(api.complete_run, **_payload(run_id=run.name, status="Completed"))
		with self.assertRaises(frappe.ValidationError):
			self._execute(run, "search_items", {"query": "a"})


class IntegrationTestProposalLifecycle(AgentGatewayTestCase):
	def _proposal(self, run, **overrides):
		values = {
			"action_type": "prepare_sales_order",
			"company": self.company,
			"preview": {**SAMPLE_PREVIEW, "company": self.company},
			"idempotency_key": f"test-proposal-{run.name}",
		}
		values.update(overrides)
		return proposals.create_proposal(run, **values)

	def test_a_proposal_carries_company_expiry_and_snapshot_hash(self):
		run = self._run()
		proposal, replayed = self._proposal(run)
		self.assertFalse(replayed)
		self.assertEqual(proposal.company, self.company)
		self.assertEqual(proposal.status, "Pending Approval")
		self.assertTrue(proposal.snapshot_hash)
		self.assertTrue(proposal.expires_at)
		self.assertEqual(proposal.target_doctype, "Sales Order")

	def test_the_same_key_returns_the_same_proposal(self):
		run = self._run()
		first, _ = self._proposal(run)
		second, replayed = self._proposal(run)
		self.assertEqual(first.name, second.name)
		self.assertTrue(replayed)

	def test_a_proposal_cannot_leave_the_company_of_its_run(self):
		run = self._run()
		with self.assertRaises(frappe.PermissionError):
			self._proposal(run, company="Some Other Company", idempotency_key=f"x-{run.name}")

	def test_an_expired_proposal_cannot_be_approved(self):
		run = self._run()
		proposal, _ = self._proposal(run, idempotency_key=f"expired-{run.name}")
		proposal.db_set("expires_at", add_to_date(now_datetime(), minutes=-1))
		with self.assertRaises(frappe.ValidationError):
			api.approve_proposal(proposal.name)
		self.assertEqual(
			frappe.db.get_value("NextGen Action Proposal", proposal.name, "status"), "Expired"
		)

	def test_an_approval_for_an_older_snapshot_is_rejected(self):
		run = self._run()
		proposal, _ = self._proposal(run, idempotency_key=f"stale-{run.name}")
		with self.assertRaises(frappe.ValidationError):
			api.approve_proposal(proposal.name, snapshot_hash="0" * 64)

	def test_rejecting_records_a_decision_against_the_reviewed_hash(self):
		run = self._run()
		proposal, _ = self._proposal(run, idempotency_key=f"reject-{run.name}")
		result = api.reject_proposal(proposal.name, "not needed")
		self.assertEqual(result["status"], "Rejected")
		decision = frappe.get_doc("NextGen Approval Decision", result["approval_id"])
		self.assertEqual(decision.decision, "Reject")
		self.assertEqual(decision.reviewed_snapshot_hash, proposal.snapshot_hash)
		self.assertEqual(decision.company, self.company)

	def test_an_unapproved_proposal_cannot_be_executed(self):
		run = self._run()
		proposal, _ = self._proposal(run, idempotency_key=f"unapproved-{run.name}")
		with self.assertRaises(frappe.ValidationError):
			api.execute_proposal(proposal.name)

	def test_expiry_sweeps_pending_proposals(self):
		run = self._run()
		proposal, _ = self._proposal(run, idempotency_key=f"sweep-{run.name}")
		proposal.db_set("expires_at", add_to_date(now_datetime(), minutes=-5))
		proposals.expire_due()
		self.assertEqual(
			frappe.db.get_value("NextGen Action Proposal", proposal.name, "status"), "Expired"
		)


class IntegrationTestMigrationAndMaintenance(AgentGatewayTestCase):
	def test_backfill_is_idempotent_and_keeps_the_original(self):
		from nextgen_erp.patches import backfill_agent_records

		before = frappe.db.count("NextGen Chat Action")
		backfill_agent_records.execute()
		first = frappe.db.count("NextGen Action Proposal")
		backfill_agent_records.execute()
		self.assertEqual(frappe.db.count("NextGen Action Proposal"), first)
		self.assertEqual(frappe.db.count("NextGen Chat Action"), before)
		report = backfill_agent_records.reconciliation_report()
		self.assertTrue(report["reconciled"], report["unmatched_chat_actions"])

	def test_every_company_has_a_policy_and_new_ones_start_disabled(self):
		from nextgen_erp.patches import create_company_automation_policies

		create_company_automation_policies.execute()
		for company in frappe.get_all("Company", pluck="name"):
			self.assertTrue(policy.get_policy_doc(company), company)

	def test_an_abandoned_run_is_failed_rather_than_left_running(self):
		run = self._run()
		self._claim(run)
		frappe.db.set_value(
			"NextGen Agent Run", run.name, "modified", add_to_date(now_datetime(), minutes=-120),
			update_modified=False,
		)
		runs.reap_stale_runs(minutes=30)
		self.assertEqual(frappe.db.get_value("NextGen Agent Run", run.name, "status"), "Failed")

	def test_the_trace_is_readable_through_the_gateway(self):
		run = self._run()
		self._claim(run)
		steps.append(
			run.name,
			sequence=1,
			step_type="System",
			operation="run.claimed",
			status="Success",
			idempotency_key=f"trace-{run.name}",
		)
		trace = api.get_run(run.name)
		self.assertEqual(trace["run"]["company"], self.company)
		self.assertEqual(trace["steps"][0]["operation"], "run.claimed")
