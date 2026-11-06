"""Workflow graph tests: typed nodes, fan-in, node execution and resumption.

The interesting claims are about authority and continuity, not about wiring:

* a node kind that reads ERP data, a file or an API cannot read anything the
  requester could not have read themselves;
* the runtime names a node and nothing else — its configuration always comes
  from the stored graph;
* a workflow parked for an approval resumes without repeating what it did, and
  a rejected proposal ends it instead.

Positive paths run as Administrator so they do not depend on which ERPNext roles
a given site grants; the restricted users exist for the negative paths, where a
rejection is the assertion.
"""

from __future__ import annotations

import json

import frappe
from frappe.tests import IntegrationTestCase

from nextgen_erp.agent_gateway import api, contracts, graph as graph_utils
from nextgen_erp.agent_gateway import nodes as node_kinds
from nextgen_erp.agent_gateway import permissions, policy, workflows
from nextgen_erp.agent_records import runs

RUNTIME_USER = "workflow-runtime@nextgen.test"
OUTSIDER_USER = "workflow-outsider@nextgen.test"


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


def agent_node(node_id, agent="assistant", x=0):
	return {
		"id": node_id,
		"type": "agent",
		"label": node_id,
		"position": {"x": x, "y": 0},
		"data": {"kind": "agent", "agent": agent, "prompt": ""},
	}


def kind_node(node_id, kind, x=0, **data):
	return {
		"id": node_id,
		"type": "agent",
		"label": node_id,
		"position": {"x": x, "y": 0},
		"data": {"kind": kind, **data},
	}


class WorkflowTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		permissions.ensure_runtime_role()
		_ensure_user(RUNTIME_USER, [permissions.RUNTIME_ROLE])
		_ensure_user(OUTSIDER_USER, ["Sales User"])
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

	def _workflow(self, nodes, edges=None, enabled=1):
		doc = frappe.get_doc(
			{
				"doctype": "NextGen Agent Workflow",
				"title": f"Test workflow {frappe.generate_hash(length=6)}",
				"company": self.company,
				"enabled": enabled,
				"graph_json": json.dumps({"nodes": nodes, "edges": edges or []}),
			}
		).insert(ignore_permissions=True)
		return doc

	def _parent_run(self, workflow):
		return runs.create_run(
			agent_type="assistant",
			company=self.company,
			requested_by="Administrator",
			trigger_type="manual",
			workflow=workflow.name,
			graph_version=workflow.graph_version,
			input_payload={"messages": []},
		)

	def _as_runtime(self, method, **kwargs):
		frappe.set_user(RUNTIME_USER)
		try:
			return method(**kwargs)
		finally:
			frappe.set_user("Administrator")


class IntegrationTestGraphValidation(WorkflowTestCase):
	def test_an_untyped_node_is_still_an_agent_node(self):
		"""Graphs saved before typed nodes existed keep working."""
		node = agent_node("a")
		del node["data"]["kind"]
		doc = self._workflow([node])
		stored = graph_utils.parse(doc.graph_json)["nodes"][0]
		self.assertEqual(graph_utils.node_kind(stored), "agent")
		self.assertTrue(graph_utils.is_agent_node(stored))

	def test_a_query_node_cannot_read_a_denied_doctype(self):
		with self.assertRaises(frappe.ValidationError):
			self._workflow([kind_node("q", "source.query", doctype="User")])

	def test_a_query_node_cannot_read_the_gateways_own_records(self):
		with self.assertRaises(frappe.ValidationError):
			self._workflow([kind_node("q", "source.query", doctype="NextGen Agent Run")])

	def test_an_unknown_node_kind_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._workflow([kind_node("x", "source.shell", command="ls")])

	def test_a_spreadsheet_node_needs_a_file(self):
		with self.assertRaises(frappe.ValidationError):
			self._workflow([kind_node("x", "source.excel")])

	def test_an_api_node_needs_a_connection(self):
		with self.assertRaises(frappe.ValidationError):
			self._workflow([kind_node("x", "source.api", path="/v1/orders")])

	def test_a_cycle_is_still_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._workflow(
				[agent_node("a"), agent_node("b")],
				[{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
			)

	def test_saving_the_graph_bumps_its_version(self):
		doc = self._workflow([agent_node("a")])
		self.assertEqual(doc.graph_version, 1)
		doc.graph_json = json.dumps({"nodes": [agent_node("a"), agent_node("b", x=1)], "edges": []})
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.graph_version, 2)


class IntegrationTestFanIn(WorkflowTestCase):
	def test_a_node_reads_the_nodes_connected_to_it(self):
		graph = graph_utils.parse(
			json.dumps(
				{
					"nodes": [
						kind_node("db", "source.query", x=0, doctype="Item"),
						kind_node("api", "source.query", x=1, doctype="Item"),
						agent_node("merge", x=2),
						agent_node("after", x=3),
					],
					"edges": [
						{"source": "db", "target": "merge"},
						{"source": "api", "target": "merge"},
						{"source": "merge", "target": "after"},
					],
				}
			)
		)
		self.assertEqual(graph_utils.parents(graph, "merge"), ["db", "api"])
		self.assertEqual(graph_utils.parents(graph, "after"), ["merge"])
		# A starting node reads the run's own input, not another node's output.
		self.assertEqual(graph_utils.parents(graph, "db"), [])


class IntegrationTestNodeExecution(WorkflowTestCase):
	def test_a_query_node_returns_rows_as_an_envelope(self):
		workflow = self._workflow(
			[kind_node("q", "source.query", doctype="Item", fields="name", limit=3)]
		)
		parent = self._parent_run(workflow)
		result = self._as_runtime(
			api.execute_node,
			contract_version=contracts.CONTRACT_VERSION,
			parent_run_id=parent.name,
			node_id="q",
			idempotency_key=f"{parent.name}:node:q",
		)
		self.assertEqual(result["status"], "Completed")
		self.assertEqual(result["output"]["node_kind"], "source.query")
		self.assertIsInstance(result["output"]["data"], list)

	def test_executing_a_node_twice_replays_instead_of_repeating_it(self):
		workflow = self._workflow([kind_node("q", "source.query", doctype="Item", limit=1)])
		parent = self._parent_run(workflow)
		payload = {
			"contract_version": contracts.CONTRACT_VERSION,
			"parent_run_id": parent.name,
			"node_id": "q",
			"idempotency_key": f"{parent.name}:node:q",
		}
		first = self._as_runtime(api.execute_node, **payload)
		second = self._as_runtime(api.execute_node, **payload)
		self.assertFalse(first["replayed"])
		self.assertTrue(second["replayed"])
		self.assertEqual(
			frappe.db.count("NextGen Workflow Node Result", {"run": parent.name, "node_id": "q"}), 1
		)

	def test_the_runtime_cannot_run_an_agent_node_through_this_path(self):
		workflow = self._workflow([agent_node("a")])
		parent = self._parent_run(workflow)
		with self.assertRaises(frappe.ValidationError):
			self._as_runtime(
				api.execute_node,
				contract_version=contracts.CONTRACT_VERSION,
				parent_run_id=parent.name,
				node_id="a",
				idempotency_key=f"{parent.name}:node:a",
			)

	def test_the_runtime_cannot_run_a_node_the_graph_does_not_contain(self):
		workflow = self._workflow([kind_node("q", "source.query", doctype="Item")])
		parent = self._parent_run(workflow)
		with self.assertRaises(frappe.ValidationError):
			self._as_runtime(
				api.execute_node,
				contract_version=contracts.CONTRACT_VERSION,
				parent_run_id=parent.name,
				node_id="somebody_elses_node",
				idempotency_key="k",
			)

	def test_a_human_cannot_call_the_runtime_only_node_endpoint(self):
		workflow = self._workflow([kind_node("q", "source.query", doctype="Item")])
		parent = self._parent_run(workflow)
		frappe.set_user(OUTSIDER_USER)
		with self.assertRaises(frappe.PermissionError):
			api.execute_node(
				contract_version=contracts.CONTRACT_VERSION,
				parent_run_id=parent.name,
				node_id="q",
				idempotency_key="k",
			)

	def test_a_node_records_a_step_on_the_parent_runs_trace(self):
		workflow = self._workflow([kind_node("q", "source.query", doctype="Item", limit=1)])
		parent = self._parent_run(workflow)
		self._as_runtime(
			api.execute_node,
			contract_version=contracts.CONTRACT_VERSION,
			parent_run_id=parent.name,
			node_id="q",
			idempotency_key=f"{parent.name}:node:q",
		)
		steps = frappe.get_all(
			"NextGen Agent Step", filters={"run": parent.name}, fields=["operation", "status"]
		)
		self.assertIn(("node.source.query", "Success"), [(s.operation, s.status) for s in steps])

	def test_a_report_node_writes_a_file_attached_to_the_run(self):
		workflow = self._workflow(
			[
				kind_node("q", "source.query", x=0, doctype="Item", fields="name", limit=2),
				kind_node("r", "sink.report", x=1, title="Item list", format="csv"),
			],
			[{"source": "q", "target": "r"}],
		)
		parent = self._parent_run(workflow)
		for node_id in ("q", "r"):
			result = self._as_runtime(
				api.execute_node,
				contract_version=contracts.CONTRACT_VERSION,
				parent_run_id=parent.name,
				node_id=node_id,
				idempotency_key=f"{parent.name}:node:{node_id}",
			)
			self.assertEqual(result["status"], "Completed")
		self.assertTrue(result["output"]["artifacts"])
		self.assertTrue(
			frappe.db.exists(
				"File", {"attached_to_doctype": "NextGen Agent Run", "attached_to_name": parent.name}
			)
		)


class IntegrationTestWorkflowState(WorkflowTestCase):
	def test_state_reports_every_node_and_what_it_produced(self):
		workflow = self._workflow(
			[
				kind_node("q", "source.query", x=0, doctype="Item", limit=1),
				agent_node("a", x=1),
			],
			[{"source": "q", "target": "a"}],
		)
		parent = self._parent_run(workflow)
		self._as_runtime(
			api.execute_node,
			contract_version=contracts.CONTRACT_VERSION,
			parent_run_id=parent.name,
			node_id="q",
			idempotency_key=f"{parent.name}:node:q",
		)
		state = self._as_runtime(
			api.get_workflow_state,
			contract_version=contracts.CONTRACT_VERSION,
			parent_run_id=parent.name,
		)
		by_node = {entry["node_id"]: entry for entry in state["nodes"]}
		self.assertEqual(by_node["q"]["status"], "Completed")
		self.assertEqual(by_node["q"]["output"]["node_kind"], "source.query")
		# The agent node has not run yet, so it has nothing to restore.
		self.assertEqual(by_node["a"]["status"], "Pending")

	def test_state_is_refused_for_a_run_that_orchestrates_nothing(self):
		plain = runs.create_run(
			agent_type="assistant",
			company=self.company,
			requested_by="Administrator",
			trigger_type="chat",
			input_payload={"messages": []},
		)
		with self.assertRaises(frappe.ValidationError):
			self._as_runtime(
				api.get_workflow_state,
				contract_version=contracts.CONTRACT_VERSION,
				parent_run_id=plain.name,
			)


class IntegrationTestResumption(WorkflowTestCase):
	def _parked(self):
		"""A parent parked on an agent node that produced a proposal."""
		workflow = self._workflow(
			[agent_node("a", x=0), agent_node("b", x=1)], [{"source": "a", "target": "b"}]
		)
		parent = self._parent_run(workflow)
		child = runs.create_run(
			agent_type="assistant",
			company=self.company,
			requested_by="Administrator",
			trigger_type="manual",
			workflow=workflow.name,
			workflow_node="a",
			parent_run=parent.name,
			correlation_id=parent.correlation_id,
			input_payload={"messages": []},
		)
		runs.complete(child.name, "Waiting Approval", usage={"final_text": "เตรียมข้อเสนอแล้ว"})
		runs.complete(parent.name, "Waiting Approval")
		return workflow, parent, child

	def test_a_node_waiting_on_a_human_is_not_reported_as_finished(self):
		workflow, parent, child = self._parked()
		proposal = frappe.get_doc(
			{
				"doctype": "NextGen Action Proposal",
				"run": child.name,
				"company": self.company,
				"action_type": "prepare_document",
				"requested_by": "Administrator",
				"status": "Pending Approval",
				"idempotency_key": frappe.generate_hash(),
				"snapshot_hash": "0" * 64,
				"original_proposal": json.dumps({"doctype": "Item", "values": {}}),
			}
		).insert(ignore_permissions=True)

		state = self._as_runtime(
			api.get_workflow_state,
			contract_version=contracts.CONTRACT_VERSION,
			parent_run_id=parent.name,
		)
		by_node = {entry["node_id"]: entry for entry in state["nodes"]}
		self.assertEqual(by_node["a"]["status"], "Waiting Approval")
		self.assertEqual(proposal.status, "Pending Approval")

	def test_a_settled_proposal_makes_its_node_complete_and_reopens_the_run(self):
		workflow, parent, child = self._parked()
		proposal = frappe.get_doc(
			{
				"doctype": "NextGen Action Proposal",
				"run": child.name,
				"company": self.company,
				"action_type": "prepare_document",
				"requested_by": "Administrator",
				"status": "Completed",
				"idempotency_key": frappe.generate_hash(),
				"snapshot_hash": "0" * 64,
				"original_proposal": json.dumps({"doctype": "Item", "values": {}}),
			}
		).insert(ignore_permissions=True)

		workflows.resume_after_proposal(proposal)
		parent.reload()
		self.assertEqual(parent.status, "Queued")

		state = self._as_runtime(
			api.get_workflow_state,
			contract_version=contracts.CONTRACT_VERSION,
			parent_run_id=parent.name,
		)
		by_node = {entry["node_id"]: entry for entry in state["nodes"]}
		# The parked node is finished, so a resumed run skips it instead of
		# asking the model to do the same work again.
		self.assertEqual(by_node["a"]["status"], "Completed")
		self.assertIn("เตรียมข้อเสนอแล้ว", by_node["a"]["output"]["text"])
		self.assertEqual(by_node["b"]["status"], "Pending")

	def test_a_rejected_proposal_ends_the_workflow_instead_of_resuming_it(self):
		workflow, parent, child = self._parked()
		proposal = frappe.get_doc(
			{
				"doctype": "NextGen Action Proposal",
				"run": child.name,
				"company": self.company,
				"action_type": "prepare_document",
				"requested_by": "Administrator",
				"status": "Rejected",
				"idempotency_key": frappe.generate_hash(),
				"snapshot_hash": "0" * 64,
				"original_proposal": json.dumps({"doctype": "Item", "values": {}}),
			}
		).insert(ignore_permissions=True)

		workflows.resume_after_proposal(proposal)
		parent.reload()
		self.assertEqual(parent.status, "Cancelled")

	def test_a_finished_run_is_never_reopened(self):
		workflow, parent, child = self._parked()
		runs.complete(parent.name, "Completed")
		runs.reopen(parent.name)
		parent.reload()
		self.assertEqual(parent.status, "Completed")


class IntegrationTestTriggers(WorkflowTestCase):
	def test_a_schedule_trigger_is_primed_with_a_next_run(self):
		doc = self._workflow([agent_node("a")])
		doc.append(
			"triggers",
			{
				"trigger_type": "Schedule",
				"enabled": 1,
				"run_as": "Administrator",
				"cron": "0 8 * * *",
			},
		)
		doc.save(ignore_permissions=True)
		self.assertTrue(doc.triggers[0].next_run_at)
		# A trigger that has never fired starts at its next occurrence rather
		# than immediately, so enabling one does not surprise anybody.
		self.assertTrue(doc.triggers[0].last_run_at)

	def test_a_webhook_trigger_is_given_a_token_on_save(self):
		doc = self._workflow([agent_node("a")])
		workflows.save_workflow(
			name=doc.name,
			enabled=1,
			triggers=[{"trigger_type": "Webhook", "enabled": 1, "run_as": "Administrator"}],
		)
		doc.reload()
		self.assertTrue(doc.triggers[0].get_password("webhook_token", raise_exception=False))

	def test_a_wrong_webhook_token_is_refused(self):
		doc = self._workflow([agent_node("a")])
		workflows.save_workflow(
			name=doc.name,
			enabled=1,
			triggers=[{"trigger_type": "Webhook", "enabled": 1, "run_as": "Administrator"}],
		)
		with self.assertRaises(frappe.PermissionError):
			workflows.start_from_webhook(doc.name, "not-the-token", {})

	def test_an_unknown_workflow_is_refused_the_same_way_as_a_bad_token(self):
		with self.assertRaises(frappe.PermissionError):
			workflows.start_from_webhook("WF-does-not-exist", "anything", {})

	def test_a_workflow_cannot_be_triggered_by_the_gateways_own_records(self):
		doc = self._workflow([agent_node("a")])
		doc.append(
			"triggers",
			{
				"trigger_type": "Document Event",
				"enabled": 1,
				"run_as": "Administrator",
				"trigger_doctype": "NextGen Agent Run",
				"doc_event": "after_insert",
			},
		)
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_a_trigger_condition_is_compared_not_evaluated(self):
		document = frappe.get_doc({"doctype": "Item", "status": "Active"})
		self.assertTrue(workflows._condition_matches('{"status": "Active"}', document))
		self.assertFalse(workflows._condition_matches('{"status": "Closed"}', document))
		# Anything that is not a JSON object never matches, so a malformed
		# condition fails closed rather than firing on everything.
		self.assertFalse(workflows._condition_matches("1 == 1", document))


class IntegrationTestNodeHelpers(WorkflowTestCase):
	def test_an_order_by_is_rebuilt_from_validated_parts(self):
		meta = frappe.get_meta("Item")
		self.assertEqual(
			node_kinds._safe_order_by(meta, "item_name asc"), "`tabItem`.`item_name` asc"
		)
		# Anything that is not a real field of this DocType falls back rather
		# than reaching the query builder.
		self.assertEqual(
			node_kinds._safe_order_by(meta, "name; drop table"), "`tabItem`.`modified` desc"
		)
		self.assertEqual(node_kinds._safe_order_by(meta, None), "`tabItem`.`modified` desc")

	def test_a_field_name_that_is_not_an_identifier_is_refused(self):
		with self.assertRaises(node_kinds.NodeError):
			node_kinds._field_list("name, (select 1)")
		self.assertEqual(node_kinds._field_list("name, item_name"), ["name", "item_name"])

	def test_an_envelope_is_bounded_and_redacted(self):
		node = kind_node("q", "source.query", doctype="Item")
		result = node_kinds.envelope(
			node, text="ok", data=[{"item": "M-150", "api_key": "sk-live-0123456789abcdef"}]
		)
		self.assertEqual(result["data"][0]["item"], "M-150")
		self.assertNotIn("sk-live", json.dumps(result))
		self.assertEqual(result["row_count"], 1)
		self.assertFalse(result["truncated"])
