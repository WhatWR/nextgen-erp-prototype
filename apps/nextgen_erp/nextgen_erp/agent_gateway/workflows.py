"""Workflow definitions, dispatch and node runs.

A workflow orchestrates *registered agents*. It is not a general automation
engine: a node names an agent, and what that agent may do is still decided by
the company policy and the code allowlist at run time. Saving a workflow can
never grant a capability the policy withholds.

A workflow run is one parent Agent Run plus one child run per node. They share
the parent's correlation ID, so the whole execution reads as a single operation
across both services while each node keeps its own ordered step trace.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint

from nextgen_erp.agent_gateway import graph as graph_utils
from nextgen_erp.agent_gateway import permissions, policy
from nextgen_erp.agent_records import runs

WORKFLOW_DOCTYPE = "NextGen Agent Workflow"


def _accessible(name: str, user: str):
	doc = frappe.get_doc(WORKFLOW_DOCTYPE, name)
	policy.assert_company_access(doc.company, user)
	return doc


def list_workflows(company: str | None = None) -> list[dict[str, Any]]:
	user = permissions.require_human()
	filters: dict[str, Any] = {}
	if company:
		filters["company"] = policy.assert_company_access(company, user)
	rows = frappe.get_list(
		WORKFLOW_DOCTYPE,
		filters=filters,
		fields=["name", "title", "description", "company", "enabled", "graph_version", "total_runs", "modified"],
		order_by="modified desc",
		limit_page_length=100,
	)
	for row in rows:
		row["node_count"] = len(
			graph_utils.parse(frappe.db.get_value(WORKFLOW_DOCTYPE, row["name"], "graph_json"))["nodes"]
		)
	return rows


def get_workflow(name: str) -> dict[str, Any]:
	user = permissions.require_human()
	doc = _accessible(name, user)
	return {
		"name": doc.name,
		"title": doc.title,
		"description": doc.description,
		"company": doc.company,
		"enabled": bool(cint(doc.enabled)),
		"graph_version": cint(doc.graph_version),
		"total_runs": cint(doc.total_runs),
		"graph": graph_utils.parse(doc.graph_json),
		# The designer offers only agents this user and company can actually run.
		"available_agents": available_agents(doc.company, user),
	}


def available_agents(company: str, user: str | None = None) -> list[dict[str, Any]]:
	from nextgen_erp import agents
	from nextgen_erp.domain_tools import allowed_tool_names

	user = user or frappe.session.user
	options = []
	for agent in agents.AGENTS.values():
		if not agent.has_access(user):
			continue
		tools = allowed_tool_names(agent.key, company)
		if not tools:
			continue
		options.append({"key": agent.key, "title": agent.title, "tools": tools})
	return options


def save_workflow(
	name: str | None = None,
	title: str | None = None,
	company: str | None = None,
	description: str | None = None,
	graph: Any = None,
	enabled: int | bool = 0,
) -> dict[str, Any]:
	"""Create or update a definition. The graph is validated before it is stored."""
	user = permissions.require_human()
	if isinstance(graph, str):
		try:
			graph = json.loads(graph or "{}")
		except (TypeError, ValueError):
			frappe.throw(_("Workflow graph is not valid JSON"), frappe.ValidationError)

	if name and frappe.db.exists(WORKFLOW_DOCTYPE, name):
		doc = _accessible(name, user)
	else:
		company = policy.assert_company_access(company or policy.default_company(user), user)
		doc = frappe.new_doc(WORKFLOW_DOCTYPE)
		doc.company = company
	if title:
		doc.title = title
	if description is not None:
		doc.description = description
	if graph is not None:
		doc.graph_json = json.dumps(graph, ensure_ascii=False, default=str)
	doc.enabled = cint(enabled)
	# The controller validates the graph: known agents, no duplicate ids, no
	# dangling edges, no cycles, and nothing the policy leaves toolless.
	doc.save()
	return get_workflow(doc.name)


def delete_workflow(name: str) -> dict[str, Any]:
	user = permissions.require_human()
	doc = _accessible(name, user)
	if frappe.db.count("NextGen Agent Run", {"workflow": doc.name}):
		# Runs are the audit trail of what actually happened; disabling keeps
		# them readable instead of orphaning them.
		frappe.throw(
			_("This workflow has run history. Disable it instead of deleting it."),
			frappe.ValidationError,
		)
	frappe.delete_doc(WORKFLOW_DOCTYPE, doc.name)
	return {"deleted": True, "workflow": name}


def start_workflow_run(workflow: str, message: str | None = None) -> dict[str, Any]:
	"""Queue the parent run and dispatch it. Nodes are created by the runtime."""
	from nextgen_erp.agent_gateway import dispatch

	user = permissions.require_human()
	doc = _accessible(workflow, user)
	if not cint(doc.enabled):
		frappe.throw(_("This workflow is disabled"), frappe.ValidationError)
	if not policy.runtime_enabled(doc.company):
		frappe.throw(
			_("The Agent Runtime is not enabled for company {0}").format(doc.company),
			frappe.ValidationError,
		)
	nodes = graph_utils.topological_order(graph_utils.parse(doc.graph_json))
	first = nodes[0]

	run = runs.create_run(
		agent_type=graph_utils.node_agent(first),
		company=doc.company,
		requested_by=user,
		trigger_type="manual",
		workflow=doc.name,
		graph_version=cint(doc.graph_version),
		input_payload={
			"messages": [{"role": "user", "content": str(message or "").strip()}] if message else [],
			"context": {"workflow": doc.name, "workflow_title": doc.title},
		},
	)
	doc.db_set("last_run", run.name, update_modified=False)
	doc.db_set("total_runs", cint(doc.total_runs) + 1, update_modified=False)
	dispatch.enqueue_dispatch(run)
	return {"run_id": run.name, "correlation_id": run.correlation_id, "status": run.status}


def create_node_run(
	parent_run_id: str,
	node_id: str,
	agent_type: str,
	input_payload: dict[str, Any],
	idempotency_key: str,
	node_prompt: str | None = None,
) -> dict[str, Any]:
	"""Create one child run for a workflow node and return its claim context.

	The runtime supplies the node. Everything that matters — company, requester,
	execution identity and the tool allowlist — is derived from the parent run,
	and the node's agent must appear in the parent's stored graph.
	"""
	service = permissions.require_service_identity()
	parent = runs.get_run(parent_run_id)
	permissions.assert_run_execution_identity(parent, service)
	if not parent.workflow:
		frappe.throw(_("This run does not orchestrate a workflow"), frappe.ValidationError)

	workflow = frappe.get_doc(WORKFLOW_DOCTYPE, parent.workflow)
	graph = graph_utils.parse(workflow.graph_json)
	node = next((n for n in graph["nodes"] if str(n.get("id")) == str(node_id)), None)
	if not node:
		frappe.throw(_("Unknown workflow node: {0}").format(node_id), frappe.ValidationError)
	if graph_utils.node_agent(node) != agent_type:
		# The runtime may not substitute a different agent for a node.
		frappe.throw(
			_("Node {0} is bound to agent {1}").format(node_id, graph_utils.node_agent(node)),
			frappe.PermissionError,
		)

	payload = dict(input_payload or {})
	prompt = node_prompt or graph_utils.node_prompt(node)
	if prompt:
		payload.setdefault("context", {})["node_prompt"] = prompt
	child = runs.create_run(
		agent_type=agent_type,
		company=parent.company,
		requested_by=parent.requested_by,
		execution_user=parent.execution_user,
		trigger_type=parent.trigger_type,
		correlation_id=parent.correlation_id,
		idempotency_key=idempotency_key,
		workflow=parent.workflow,
		workflow_node=str(node_id),
		parent_run=parent.name,
		graph_version=cint(parent.graph_version),
		input_payload=payload,
	)
	return runs.claim(child.name, parent.runtime_version or "workflow")


def get_workflow_run(run_id: str) -> dict[str, Any]:
	"""Parent run, its node runs and any proposals they produced."""
	user = permissions.current_user()
	parent = runs.get_run(run_id)
	policy.assert_company_access(parent.company, user)
	nodes = frappe.get_all(
		"NextGen Agent Run",
		filters={"parent_run": parent.name},
		fields=["name", "workflow_node", "agent_type", "status", "started_at", "ended_at", "error_summary"],
		order_by="creation asc",
	)
	return {
		"run": {
			"name": parent.name,
			"workflow": parent.workflow,
			"company": parent.company,
			"status": parent.status,
			"correlation_id": parent.correlation_id,
			"graph_version": cint(parent.graph_version),
			"started_at": str(parent.started_at or ""),
			"ended_at": str(parent.ended_at or ""),
			"error_summary": parent.error_summary,
		},
		"nodes": nodes,
		"proposals": frappe.get_all(
			"NextGen Action Proposal",
			filters={"run": ["in", [parent.name, *[n["name"] for n in nodes]]]},
			fields=["name", "action_type", "target_doctype", "status", "expires_at"],
			order_by="creation asc",
		),
	}


def run_history(workflow: str, limit: int = 20) -> list[dict[str, Any]]:
	user = permissions.require_human()
	doc = _accessible(workflow, user)
	return frappe.get_all(
		"NextGen Agent Run",
		filters={"workflow": doc.name, "parent_run": ["is", "not set"]},
		fields=["name", "status", "started_at", "ended_at", "graph_version", "error_summary"],
		order_by="creation desc",
		limit=max(1, min(cint(limit) or 20, 100)),
	)
