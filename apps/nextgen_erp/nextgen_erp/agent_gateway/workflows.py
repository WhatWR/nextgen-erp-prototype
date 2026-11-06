"""Workflow definitions, dispatch, node execution and resumption.

A workflow orchestrates *typed nodes*: registered agents, plus the
deterministic sources and sinks in :mod:`nextgen_erp.agent_gateway.nodes`. It is
still not a general automation engine. An agent node names an agent, and what
that agent may do is decided by the company policy and the code allowlist at run
time; a source or sink node runs as the human who requested the run, under their
own permissions. Saving a workflow can never grant a capability the policy or
the permission withholds.

A workflow run is one parent Agent Run plus one child run per agent node, and
one Workflow Node Result per deterministic node. They share the parent's
correlation ID, so the whole execution reads as a single operation across both
services while each node keeps its own ordered trace.

Two properties are load-bearing:

* **A workflow cannot approve its own work.** When a node produces a proposal
  the parent parks at ``Waiting Approval`` and later nodes do not run.
* **An approval resumes it, and a rejection ends it.** Because the resumed
  workflow executes in a *different* process, everything earlier nodes produced
  is read back from Frappe by :func:`workflow_state` rather than remembered.
"""

from __future__ import annotations

import hmac
import json
import secrets
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from nextgen_erp.agent_gateway import contracts
from nextgen_erp.agent_gateway import graph as graph_utils
from nextgen_erp.agent_gateway import nodes as node_kinds
from nextgen_erp.agent_gateway import permissions, policy
from nextgen_erp.agent_gateway.redaction import redact, redact_error
from nextgen_erp.agent_records import runs, steps

TRIGGER_DOCTYPE = "NextGen Agent Workflow Trigger"
WORKFLOW_DOCTYPE = "NextGen Agent Workflow"
NODE_RESULT_DOCTYPE = "NextGen Workflow Node Result"
CONNECTION_DOCTYPE = "NextGen Agent Connection"
PROPOSAL_DOCTYPE = "NextGen Action Proposal"

#: Proposal states that still block a parked workflow from continuing.
UNSETTLED_PROPOSAL_STATUSES = ("Pending Approval", "Approved", "Executing")
#: Proposal states that end a workflow rather than resume it. A human said no,
#: or the preview went stale; either way the graph does not carry on by itself.
REFUSED_PROPOSAL_STATUSES = ("Rejected", "Expired", "Superseded", "Failed")

DOCUMENT_EVENTS = ("after_insert", "on_update", "on_submit", "on_cancel")
DOCUMENT_TRIGGER_CACHE_KEY = "nextgen_workflow_document_triggers"
NODE_SAVEPOINT = "nextgen_workflow_node"


def _accessible(name: str, user: str):
	doc = frappe.get_doc(WORKFLOW_DOCTYPE, name)
	policy.assert_company_access(doc.company, user)
	return doc


# ---------------------------------------------------------------------------
# definitions
# ---------------------------------------------------------------------------


def list_workflows(company: str | None = None) -> list[dict[str, Any]]:
	user = permissions.require_human()
	filters: dict[str, Any] = {}
	if company:
		filters["company"] = policy.assert_company_access(company, user)
	rows = frappe.get_list(
		WORKFLOW_DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"title",
			"description",
			"company",
			"enabled",
			"graph_version",
			"total_runs",
			"modified",
		],
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
		# The designer offers only what this user and company can actually run.
		"available_agents": available_agents(doc.company, user),
		"node_kinds": node_kinds.describe_kinds(),
		"connections": available_connections(doc.company),
		"triggers": [
			{
				"trigger_type": row.trigger_type,
				"enabled": bool(cint(row.enabled)),
				"run_as": row.run_as,
				"cron": row.cron,
				"trigger_doctype": row.trigger_doctype,
				"doc_event": row.doc_event,
				"condition": row.condition,
				"last_run_at": str(row.last_run_at or ""),
				"next_run_at": str(row.next_run_at or ""),
			}
			for row in doc.get("triggers") or []
		],
		"webhook_url": f"/api/method/nextgen_erp.agent_gateway.api.workflow_webhook?workflow={doc.name}",
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


def available_connections(company: str) -> list[dict[str, Any]]:
	"""Connection names only. A base URL or credential never reaches the client."""
	return frappe.get_all(
		CONNECTION_DOCTYPE,
		filters={"company": company, "enabled": 1},
		fields=["name", "description"],
		order_by="name asc",
		limit_page_length=100,
	)


def save_workflow(
	name: str | None = None,
	title: str | None = None,
	company: str | None = None,
	description: str | None = None,
	graph: Any = None,
	enabled: int | bool = 0,
	triggers: Any = None,
) -> dict[str, Any]:
	"""Create or update a definition. The graph is validated before it is stored."""
	user = permissions.require_human()
	if isinstance(graph, str):
		try:
			graph = json.loads(graph or "{}")
		except (TypeError, ValueError):
			frappe.throw(_("Workflow graph is not valid JSON"), frappe.ValidationError)
	if isinstance(triggers, str):
		try:
			triggers = json.loads(triggers or "null")
		except (TypeError, ValueError):
			frappe.throw(_("Workflow triggers are not valid JSON"), frappe.ValidationError)

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
	if triggers is not None:
		_apply_triggers(doc, triggers, user)
	doc.enabled = cint(enabled)
	# The controller validates the graph: known kinds, agents the policy leaves
	# able to work, no duplicate ids, no dangling edges, no cycles.
	doc.save()
	return get_workflow(doc.name)


def _apply_triggers(doc, rows: Any, user: str) -> None:
	"""Replace the trigger table, keeping any token already issued.

	``run_as`` is the identity an unattended run acts under, so it is checked
	here rather than trusted: a trigger may not lend a workflow more access than
	the person configuring it already has.
	"""
	if not isinstance(rows, list):
		frappe.throw(_("Workflow triggers must be a list"), frappe.ValidationError)
	existing = {
		(row.trigger_type, row.trigger_doctype or "", row.doc_event or "", row.cron or ""): row
		for row in doc.get("triggers") or []
	}
	doc.set("triggers", [])
	for row in rows:
		if not isinstance(row, dict):
			continue
		trigger_type = str(row.get("trigger_type") or "").strip()
		if trigger_type not in ("Schedule", "Webhook", "Document Event"):
			frappe.throw(_("Unknown trigger type: {0}").format(trigger_type), frappe.ValidationError)
		run_as = str(row.get("run_as") or "").strip() or user
		if run_as != user and "System Manager" not in frappe.get_roles(user):
			# Only an administrator may point a trigger at somebody else's
			# access; otherwise a trigger becomes a way to borrow it.
			frappe.throw(
				_("You may only create triggers that run as yourself"), frappe.PermissionError
			)
		key = (
			trigger_type,
			str(row.get("trigger_doctype") or ""),
			str(row.get("doc_event") or ""),
			str(row.get("cron") or ""),
		)
		previous = existing.get(key)
		child = doc.append(
			"triggers",
			{
				"trigger_type": trigger_type,
				"enabled": cint(row.get("enabled")),
				"run_as": run_as,
				"cron": (row.get("cron") or "").strip() or None,
				"trigger_doctype": (row.get("trigger_doctype") or "").strip() or None,
				"doc_event": (row.get("doc_event") or "").strip() or None,
				"condition": (row.get("condition") or "").strip() or None,
				"last_run_at": previous.last_run_at if previous else None,
			},
		)
		if trigger_type == "Webhook":
			token = (
				previous.get_password("webhook_token", raise_exception=False)
				if previous
				else None
			) or secrets.token_urlsafe(32)
			child.webhook_token = token


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


# ---------------------------------------------------------------------------
# starting a run
# ---------------------------------------------------------------------------


def start_workflow_run(workflow: str, message: str | None = None) -> dict[str, Any]:
	"""Queue the parent run and dispatch it. Nodes are created by the runtime."""
	user = permissions.require_human()
	doc = _accessible(workflow, user)
	return _start(doc, requested_by=user, trigger_type="manual", message=message)


def _start(
	doc,
	*,
	requested_by: str,
	trigger_type: str,
	message: str | None = None,
	payload: dict[str, Any] | None = None,
	trigger_doctype: str | None = None,
	trigger_name: str | None = None,
) -> dict[str, Any]:
	from nextgen_erp.agent_gateway import dispatch

	if not cint(doc.enabled):
		frappe.throw(_("This workflow is disabled"), frappe.ValidationError)
	if not policy.runtime_enabled(doc.company):
		frappe.throw(
			_("The Agent Runtime is not enabled for company {0}").format(doc.company),
			frappe.ValidationError,
		)
	graph = graph_utils.parse(doc.graph_json)
	first = graph_utils.topological_order(graph)[0]

	context: dict[str, Any] = {"workflow": doc.name, "workflow_title": doc.title}
	if payload:
		# Whatever started the run — a webhook body, a document — is available
		# to the graph as context, redacted like any other run input.
		context["trigger_payload"] = redact(payload)

	run = runs.create_run(
		# The parent run is bookkeeping for the graph, not an agent turn. It is
		# labelled with the first node's agent so existing run views stay
		# meaningful; the runtime routes it to the graph executor regardless.
		agent_type=graph_utils.node_agent(first) or _first_agent(graph),
		company=doc.company,
		requested_by=requested_by,
		trigger_type=trigger_type,
		trigger_doctype=trigger_doctype,
		trigger_name=trigger_name,
		workflow=doc.name,
		graph_version=cint(doc.graph_version),
		input_payload={
			"messages": [{"role": "user", "content": str(message or "").strip()}] if message else [],
			"context": context,
		},
	)
	doc.db_set("last_run", run.name, update_modified=False)
	doc.db_set("total_runs", cint(doc.total_runs) + 1, update_modified=False)
	dispatch.enqueue_dispatch(run)
	return {"run_id": run.name, "correlation_id": run.correlation_id, "status": run.status}


def _first_agent(graph: dict[str, Any]) -> str:
	"""The agent that labels the parent run of a graph starting with a source.

	The parent run never takes an agent turn — the runtime routes it to the
	graph executor — so this is a label for run lists and nothing more. A graph
	made only of sources and sinks is a legitimate workflow and falls back to
	the registry's default rather than being refused.
	"""
	from nextgen_erp import agents

	for node in graph_utils.topological_order(graph):
		agent = graph_utils.node_agent(node)
		if agent:
			return agent
	return agents.DEFAULT_AGENT


# ---------------------------------------------------------------------------
# node execution
# ---------------------------------------------------------------------------


def _parent_for_node_work(parent_run_id: str):
	"""The parent run of a workflow, verified for the calling service identity."""
	service = permissions.require_service_identity()
	parent = runs.get_run(parent_run_id)
	permissions.assert_run_execution_identity(parent, service)
	if not parent.workflow or parent.workflow_node:
		frappe.throw(_("This run does not orchestrate a workflow"), frappe.ValidationError)
	return parent


def _graph_of(parent) -> dict[str, Any]:
	workflow = frappe.get_doc(WORKFLOW_DOCTYPE, parent.workflow)
	if workflow.company != parent.company:
		frappe.throw(_("Workflow and run belong to different companies"), frappe.PermissionError)
	return graph_utils.parse(workflow.graph_json)


def create_node_run(
	parent_run_id: str,
	node_id: str,
	agent_type: str,
	input_payload: dict[str, Any],
	idempotency_key: str,
	node_prompt: str | None = None,
) -> dict[str, Any]:
	"""Create one child run for an agent node and return its claim context.

	The runtime supplies the node. Everything that matters — company, requester,
	execution identity and the tool allowlist — is derived from the parent run,
	and the node's agent must appear in the parent's stored graph.
	"""
	parent = _parent_for_node_work(parent_run_id)
	graph = _graph_of(parent)
	node = graph_utils.find_node(graph, node_id)
	if not node:
		frappe.throw(_("Unknown workflow node: {0}").format(node_id), frappe.ValidationError)
	if not graph_utils.is_agent_node(node):
		frappe.throw(
			_("Node {0} is not an agent node").format(node_id),
			frappe.ValidationError,
		)
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


def execute_node(parent_run_id: str, node_id: str, idempotency_key: str) -> dict[str, Any]:
	"""Execute one deterministic node in Frappe and persist its envelope.

	The runtime names the node and nothing else. The node's configuration comes
	from the stored graph, its inputs from the outputs Frappe already recorded,
	and its authority from the human on the parent run.
	"""
	parent = _parent_for_node_work(parent_run_id)
	graph = _graph_of(parent)
	node = graph_utils.find_node(graph, node_id)
	if not node:
		frappe.throw(_("Unknown workflow node: {0}").format(node_id), frappe.ValidationError)
	if graph_utils.is_agent_node(node):
		frappe.throw(
			_("Node {0} is an agent node and runs as its own child run").format(node_id),
			frappe.ValidationError,
		)

	existing = _node_result(parent.name, node_id)
	if existing and existing.status == "Completed":
		# A resumed or redelivered workflow replays the node instead of
		# repeating work that may have written a file or called an API.
		return {
			"node_id": str(node_id),
			"status": "Completed",
			"output": existing.envelope(),
			"replayed": True,
		}

	if existing:
		# A previous attempt failed or was interrupted. Reuse the row so the
		# node keeps one identity across retries.
		record = existing
		record.db_set({"status": "Running", "started_at": now_datetime(), "error": None})
	else:
		record = frappe.get_doc(
			{
				"doctype": NODE_RESULT_DOCTYPE,
				"run": parent.name,
				"node_id": str(node_id),
				"node_kind": graph_utils.node_kind(node),
				"idempotency_key": idempotency_key,
				"correlation_id": parent.correlation_id,
				"status": "Running",
				"started_at": now_datetime(),
			}
		).insert(ignore_permissions=True)

	inputs = node_inputs(parent, graph, node_id)
	# A node runs inside a savepoint so a refusal — a permission, a controller,
	# a failed statement — cannot leave the surrounding transaction poisoned and
	# take the run's own bookkeeping down with it.
	frappe.db.savepoint(NODE_SAVEPOINT)
	try:
		output = node_kinds.execute_node(node, parent, inputs)
	except Exception as exc:
		# Every refusal is recorded and fails the node. Nothing here is retried:
		# Frappe is the authority, and a rejection is a decision, not an outage.
		frappe.db.rollback(save_point=NODE_SAVEPOINT)
		return _fail_node(parent, node, record, exc)

	record.db_set(
		{
			"status": "Completed",
			"ended_at": now_datetime(),
			"row_count": cint(output.get("row_count")),
			"output_json": json.dumps(output, ensure_ascii=False, default=str),
			"artifact": _artifact_name(output),
			"error": None,
		}
	)
	_record_node_step(parent, node, "Success", output=output)
	return {"node_id": str(node_id), "status": "Completed", "output": output, "replayed": False}


def _fail_node(parent, node, record, exc: BaseException) -> dict[str, Any]:
	message = redact_error(exc)
	record.db_set({"status": "Failed", "ended_at": now_datetime(), "error": message})
	_record_node_step(parent, node, "Error", error=message)
	return {
		"node_id": str(node.get("id")),
		"status": "Failed",
		"output": None,
		"error": message,
		"replayed": False,
	}


def _record_node_step(parent, node, status: str, output=None, error: str | None = None) -> None:
	"""Append the node to the parent run's ordered trace.

	A deterministic node is as auditable as a tool call: same sanitisation, same
	hashes, same append-only sequence.
	"""
	node_id = str(node.get("id"))
	steps.append(
		parent.name,
		sequence=steps.next_sequence(parent.name),
		step_type="Tool",
		operation=f"node.{graph_utils.node_kind(node)}",
		status=status,
		# The status is part of the key so a node that failed and then succeeded
		# leaves both outcomes in the trace instead of replaying the first.
		idempotency_key=f"{parent.name}:nodestep:{node_id}:{status}"[:140],
		sanitized_input={"node": node_id, "label": graph_utils.node_label(node)},
		result=output,
		error=error,
		correlation_id=parent.correlation_id,
	)


def _artifact_name(output: dict[str, Any]) -> str | None:
	for url in output.get("artifacts") or []:
		name = frappe.db.get_value("File", {"file_url": url}, "name")
		if name:
			return name
	return None


def node_inputs(parent, graph: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
	"""The envelopes of the nodes connected into this one.

	A node with no incoming edge is a starting point and reads the run's own
	input instead. This is what makes an edge mean something: fan-in reads every
	parent, in canvas order, rather than whatever ran last.
	"""
	state = {entry["node_id"]: entry for entry in _node_states(parent, graph)}
	inputs = []
	for source in graph_utils.parents(graph, node_id):
		output = (state.get(source) or {}).get("output")
		if output:
			inputs.append(output)
	return inputs


# ---------------------------------------------------------------------------
# workflow state
# ---------------------------------------------------------------------------


def workflow_state(parent_run_id: str) -> dict[str, Any]:
	"""What the graph has already achieved, read back from Frappe.

	A workflow that parked for an approval resumes in a different process, so
	the runtime cannot remember what earlier nodes produced. It asks.
	"""
	parent = _parent_for_node_work(parent_run_id)
	graph = _graph_of(parent)
	return {
		"contract_version": contracts.CONTRACT_VERSION,
		"run_id": parent.name,
		"status": parent.status,
		"nodes": _node_states(parent, graph),
	}


def _node_states(parent, graph: dict[str, Any]) -> list[dict[str, Any]]:
	child_runs = {
		row.workflow_node: row
		for row in frappe.get_all(
			"NextGen Agent Run",
			filters={"parent_run": parent.name},
			fields=["name", "workflow_node", "status", "usage_json", "error_summary"],
		)
		if row.workflow_node
	}
	results = {
		row.node_id: row
		for row in frappe.get_all(
			NODE_RESULT_DOCTYPE,
			filters={"run": parent.name},
			fields=["name", "node_id", "status", "output_json", "error"],
		)
	}

	states = []
	for node in graph["nodes"]:
		node_id = str(node.get("id") or "")
		if graph_utils.is_agent_node(node):
			states.append(_agent_node_state(node, node_id, child_runs.get(node_id)))
		else:
			states.append(_result_node_state(node_id, results.get(node_id)))
	return states


def _agent_node_state(node: dict[str, Any], node_id: str, child) -> dict[str, Any]:
	"""An agent node is finished only once its proposals are settled too."""
	if not child:
		return {"node_id": node_id, "status": "Pending"}
	status = child.status
	if status in ("Queued", "Dispatched", "Running"):
		return {"node_id": node_id, "status": "Running"}
	if status == "Failed":
		return {"node_id": node_id, "status": "Failed", "error": child.error_summary}
	if status == "Cancelled":
		return {"node_id": node_id, "status": "Cancelled", "error": child.error_summary}

	proposals = frappe.get_all(
		PROPOSAL_DOCTYPE,
		filters={"run": child.name},
		fields=["name", "status", "result_doctype", "result_name"],
	)
	if any(p.status in REFUSED_PROPOSAL_STATUSES for p in proposals):
		# A human declined, or the preview went stale. The graph stops here
		# rather than proceeding on an assumption nobody agreed to.
		return {
			"node_id": node_id,
			"status": "Cancelled",
			"error": _("A proposal from this node was not approved"),
		}
	if any(p.status in UNSETTLED_PROPOSAL_STATUSES for p in proposals):
		return {"node_id": node_id, "status": "Waiting Approval"}

	documents = [
		{"doctype": p.result_doctype, "name": p.result_name}
		for p in proposals
		if p.result_doctype and p.result_name
	]
	return {
		"node_id": node_id,
		"status": "Completed",
		"output": node_kinds.envelope(
			node,
			text=_final_text(child.usage_json),
			documents=documents,
		),
	}


def _result_node_state(node_id: str, record) -> dict[str, Any]:
	if not record:
		return {"node_id": node_id, "status": "Pending"}
	state: dict[str, Any] = {"node_id": node_id, "status": record.status}
	if record.error:
		state["error"] = record.error
	if record.status == "Completed":
		try:
			state["output"] = json.loads(record.output_json or "{}")
		except (TypeError, ValueError):
			state["output"] = None
	return state


def _final_text(usage_json: Any) -> str:
	try:
		usage = json.loads(usage_json or "{}")
	except (TypeError, ValueError):
		return ""
	return str(usage.get("final_text") or "") if isinstance(usage, dict) else ""


# ---------------------------------------------------------------------------
# resumption
# ---------------------------------------------------------------------------


def resume_after_proposal(proposal) -> None:
	"""Continue or end a workflow that parked on this proposal.

	Called after a proposal is executed or rejected. A workflow never resumes
	itself and never resumes on approval alone: the document has to exist first,
	which is exactly what "execute" means.
	"""
	parent = _parked_parent(proposal)
	if not parent:
		return
	settled = frappe.get_all(
		PROPOSAL_DOCTYPE,
		filters={"run": ["in", _run_family(parent)]},
		fields=["status"],
	)
	if any(row.status in REFUSED_PROPOSAL_STATUSES for row in settled):
		runs.complete(
			parent.name, "Cancelled", error=_("A proposal in this workflow was not approved")
		)
		return
	if any(row.status in UNSETTLED_PROPOSAL_STATUSES for row in settled):
		# Another proposal from the same node is still waiting on a human.
		return

	from nextgen_erp.agent_gateway import dispatch

	runs.reopen(parent.name)
	parent.reload()
	if parent.status == "Queued":
		dispatch.enqueue_dispatch(parent)


def _parked_parent(proposal):
	"""The parent workflow run waiting on this proposal, if there is one."""
	if not proposal.get("run") or not frappe.db.exists("NextGen Agent Run", proposal.run):
		return None
	child = frappe.get_doc("NextGen Agent Run", proposal.run)
	parent_id = child.parent_run or (child.name if child.workflow and not child.workflow_node else None)
	if not parent_id:
		return None
	parent = frappe.get_doc("NextGen Agent Run", parent_id)
	return parent if parent.status == "Waiting Approval" else None


def _run_family(parent) -> list[str]:
	children = frappe.get_all(
		"NextGen Agent Run", filters={"parent_run": parent.name}, pluck="name"
	)
	return [parent.name, *children]


# ---------------------------------------------------------------------------
# triggers
# ---------------------------------------------------------------------------


def run_due_schedules(limit: int = 20) -> list[str]:
	"""Scheduler task: start workflows whose cron came due.

	Cron is evaluated against ``last_run_at`` rather than wall-clock ticks, so a
	missed scheduler run starts the workflow late instead of not at all — and a
	long outage still starts it exactly once.
	"""
	started: list[str] = []
	rows = frappe.get_all(
		TRIGGER_DOCTYPE,
		filters={"trigger_type": "Schedule", "enabled": 1, "parenttype": WORKFLOW_DOCTYPE},
		fields=["name", "parent", "cron", "run_as", "last_run_at"],
		limit_page_length=200,
	)
	now = now_datetime()
	for row in rows:
		if len(started) >= limit:
			break
		if not _is_due(row.cron, row.last_run_at, now):
			continue
		try:
			doc = frappe.get_doc(WORKFLOW_DOCTYPE, row.parent)
			if not cint(doc.enabled) or not policy.runtime_enabled(doc.company):
				continue
			result = _start(doc, requested_by=row.run_as, trigger_type="schedule")
		except Exception as exc:
			frappe.log_error(
				title=f"NextGen workflow schedule {row.parent}", message=redact_error(exc)
			)
			continue
		frappe.db.set_value(
			TRIGGER_DOCTYPE,
			row.name,
			{"last_run_at": now, "next_run_at": _next_run(row.cron, now)},
			update_modified=False,
		)
		started.append(result["run_id"])
	return started


def _croniter():
	try:
		from croniter import croniter
	except ImportError:  # pragma: no cover - croniter ships with Frappe
		return None
	return croniter


def _is_due(cron: str | None, last_run_at: Any, now) -> bool:
	croniter = _croniter()
	if not cron or not croniter or not croniter.is_valid(cron):
		return False
	if not last_run_at:
		# A trigger that has never fired starts at its next occurrence rather
		# than immediately, so enabling one does not surprise anybody.
		return False
	return croniter(cron, get_datetime(last_run_at)).get_next(type(now)) <= now


def _next_run(cron: str | None, after) -> Any:
	croniter = _croniter()
	if not cron or not croniter or not croniter.is_valid(cron):
		return None
	return croniter(cron, after).get_next(type(after))


def prime_schedule(doc) -> None:
	"""Give each schedule row a starting point, on save."""
	now = now_datetime()
	for row in doc.get("triggers") or []:
		if row.trigger_type != "Schedule" or not cint(row.enabled):
			row.next_run_at = None
			continue
		if not row.last_run_at:
			row.last_run_at = now
		row.next_run_at = _next_run(row.cron, now)


def start_from_webhook(workflow: str, token: str, payload: dict[str, Any] | None = None):
	"""Start a run from an external caller holding this workflow's token."""
	if not workflow or not frappe.db.exists(WORKFLOW_DOCTYPE, workflow):
		# The same refusal whichever part is wrong, so the endpoint does not
		# confirm which workflows exist.
		frappe.throw(_("Unknown workflow or token"), frappe.PermissionError)
	doc = frappe.get_doc(WORKFLOW_DOCTYPE, workflow)
	trigger = next(
		(
			row
			for row in doc.get("triggers") or []
			if row.trigger_type == "Webhook"
			and cint(row.enabled)
			and hmac.compare_digest(
				str(row.get_password("webhook_token", raise_exception=False) or ""), str(token or "")
			)
		),
		None,
	)
	if not trigger:
		frappe.throw(_("Unknown workflow or token"), frappe.PermissionError)
	# The run acts as the configured human, never as Guest and never as the
	# caller: the token authenticates the *trigger*, not a person.
	frappe.set_user(trigger.run_as)
	return _start(doc, requested_by=trigger.run_as, trigger_type="webhook", payload=payload)


def reveal_webhook_token(workflow: str) -> dict[str, Any]:
	"""Return a webhook trigger's token to a System Manager, on request.

	Deliberately its own call rather than part of the designer payload: the
	token is a credential, so reading it is an explicit act by a named person
	and not a side effect of opening a page.
	"""
	user = permissions.require_human()
	if "System Manager" not in frappe.get_roles(user):
		frappe.throw(_("Only a System Manager can read a webhook token"), frappe.PermissionError)
	doc = _accessible(workflow, user)
	for index, row in enumerate(doc.get("triggers") or []):
		if row.trigger_type != "Webhook":
			continue
		return {
			"workflow": doc.name,
			"index": index,
			"token": row.get_password("webhook_token", raise_exception=False) or "",
		}
	frappe.throw(_("This workflow has no webhook trigger"), frappe.ValidationError)


def document_triggers() -> dict[str, list[dict[str, Any]]]:
	"""Cached ``{"DocType:event": [trigger, ...]}`` index.

	Document events fire on every write in the site, so the common case has to
	be a cache read rather than a query.
	"""
	cached = frappe.cache().get_value(DOCUMENT_TRIGGER_CACHE_KEY)
	if cached is not None:
		return cached
	if not frappe.db.table_exists(TRIGGER_DOCTYPE):
		# A fresh install or an interrupted migration reaches here before the
		# trigger table is created. Report "no triggers" without caching it, so
		# the index is built for real once the table exists.
		return {}
	index: dict[str, list[dict[str, Any]]] = {}
	for row in frappe.get_all(
		TRIGGER_DOCTYPE,
		filters={"trigger_type": "Document Event", "enabled": 1, "parenttype": WORKFLOW_DOCTYPE},
		fields=["parent", "trigger_doctype", "doc_event", "condition", "run_as"],
		limit_page_length=200,
	):
		if not row.trigger_doctype or row.doc_event not in DOCUMENT_EVENTS:
			continue
		index.setdefault(f"{row.trigger_doctype}:{row.doc_event}", []).append(dict(row))
	frappe.cache().set_value(DOCUMENT_TRIGGER_CACHE_KEY, index)
	return index


def clear_trigger_cache() -> None:
	frappe.cache().delete_value(DOCUMENT_TRIGGER_CACHE_KEY)


def on_document_event(doc, method: str | None = None) -> None:
	"""``doc_events`` entry point. Frappe passes the event name as ``method``."""
	handle_document_event(doc, str(method or ""))


def handle_document_event(doc, event: str) -> None:
	"""Start any workflow subscribed to this document event.

	Fails soft: a broken trigger logs and is skipped, because a workflow must
	never be able to block the ERP write that mentioned it.
	"""
	if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
		# Schema sync, app install and patches are not business events. They also
		# write DocType rows before the tables a trigger reads exist, so running
		# a lookup here can abort a migration outright.
		return
	doctype = getattr(doc, "doctype", "")
	if not doctype or doctype.startswith(("NextGen ", "Version", "Comment", "Activity Log")):
		# The gateway's own records never trigger workflows; that is how a
		# workflow would end up triggering itself.
		return
	if frappe.flags.get("in_nextgen_proposal_execution"):
		# A document a workflow itself proposed must not retrigger that
		# workflow: the graph's own output is not a new external event.
		return
	matches = document_triggers().get(f"{doctype}:{event}")
	if not matches:
		return
	for trigger in matches:
		try:
			if not _condition_matches(trigger.get("condition"), doc):
				continue
			workflow = frappe.get_doc(WORKFLOW_DOCTYPE, trigger["parent"])
			if not cint(workflow.enabled) or not policy.runtime_enabled(workflow.company):
				continue
			_start(
				workflow,
				requested_by=trigger["run_as"],
				trigger_type="document_event",
				trigger_doctype=doctype,
				trigger_name=doc.name,
				payload={"doctype": doctype, "name": doc.name, "event": event},
			)
		except Exception as exc:
			frappe.log_error(
				title=f"NextGen workflow document trigger {trigger.get('parent')}",
				message=redact_error(exc),
			)


def _condition_matches(condition: Any, doc) -> bool:
	"""Compare a JSON object of field values against the document.

	Deliberately not an expression language: a trigger condition is data the
	server compares, never something it evaluates.
	"""
	if not condition:
		return True
	try:
		expected = json.loads(condition)
	except (TypeError, ValueError):
		return False
	if not isinstance(expected, dict):
		return False
	return all(str(doc.get(field) or "") == str(value or "") for field, value in expected.items())


# ---------------------------------------------------------------------------
# reading a run
# ---------------------------------------------------------------------------


def get_workflow_run(run_id: str) -> dict[str, Any]:
	"""Parent run, its node runs, node results and any proposals they produced."""
	user = permissions.current_user()
	parent = runs.get_run(run_id)
	policy.assert_company_access(parent.company, user)
	nodes = frappe.get_all(
		"NextGen Agent Run",
		filters={"parent_run": parent.name},
		fields=[
			"name",
			"workflow_node",
			"agent_type",
			"status",
			"started_at",
			"ended_at",
			"error_summary",
		],
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
			"trigger_type": parent.trigger_type,
			"started_at": str(parent.started_at or ""),
			"ended_at": str(parent.ended_at or ""),
			"error_summary": parent.error_summary,
		},
		"nodes": nodes,
		"node_results": frappe.get_all(
			NODE_RESULT_DOCTYPE,
			filters={"run": parent.name},
			fields=["node_id", "node_kind", "status", "row_count", "artifact", "error"],
			order_by="creation asc",
		),
		"proposals": frappe.get_all(
			PROPOSAL_DOCTYPE,
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
		fields=["name", "status", "trigger_type", "started_at", "ended_at", "graph_version", "error_summary"],
		order_by="creation desc",
		limit=max(1, min(cint(limit) or 20, 100)),
	)


def _node_result(run_id: str, node_id: str):
	name = frappe.db.get_value(NODE_RESULT_DOCTYPE, {"run": run_id, "node_id": str(node_id)}, "name")
	return frappe.get_doc(NODE_RESULT_DOCTYPE, name) if name else None
