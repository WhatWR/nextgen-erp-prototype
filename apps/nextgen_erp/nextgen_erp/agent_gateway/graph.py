"""Workflow graph parsing, validation and ordering.

A workflow is a directed acyclic graph of agent nodes stored in the designer's
own format, so the canvas can round-trip it without translation. Frappe owns
validation: a graph is checked on save, not at execution time, so the executor
never has to reason about a malformed or cyclic definition.

A node names an *agent*, never a tool. What that agent may actually do is still
derived at run time from the company policy and the code allowlist, so a
workflow definition can never grant a capability the policy withholds.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _

MAX_NODES = 40
MAX_EDGES = 120
NODE_TYPE = "agent"


class GraphError(ValueError):
	"""The graph is malformed, cyclic, or references something unknown."""


def parse(graph_json: Any) -> dict[str, list[dict[str, Any]]]:
	"""Normalise a stored or posted graph into ``{"nodes": [...], "edges": [...]}``."""
	if isinstance(graph_json, str):
		try:
			graph_json = json.loads(graph_json or "{}")
		except (TypeError, ValueError) as exc:
			raise GraphError(_("Workflow graph is not valid JSON")) from exc
	if not isinstance(graph_json, dict):
		raise GraphError(_("Workflow graph must be an object"))
	nodes = graph_json.get("nodes") or []
	edges = graph_json.get("edges") or []
	if not isinstance(nodes, list) or not isinstance(edges, list):
		raise GraphError(_("Workflow graph nodes and edges must be lists"))
	return {"nodes": [n for n in nodes if isinstance(n, dict)], "edges": [e for e in edges if isinstance(e, dict)]}


def node_agent(node: dict[str, Any]) -> str:
	data = node.get("data") if isinstance(node.get("data"), dict) else {}
	return str(data.get("agent") or "").strip()


def node_prompt(node: dict[str, Any]) -> str:
	data = node.get("data") if isinstance(node.get("data"), dict) else {}
	return str(data.get("prompt") or "").strip()


def node_label(node: dict[str, Any]) -> str:
	data = node.get("data") if isinstance(node.get("data"), dict) else {}
	return str(node.get("label") or data.get("label") or node.get("id") or "").strip()


def validate(graph: dict[str, list[dict[str, Any]]], company: str | None = None) -> None:
	"""Reject anything the executor should never have to handle."""
	from nextgen_erp import agents
	from nextgen_erp.agent_gateway import policy
	from nextgen_erp.domain_tools import allowed_tool_names

	nodes, edges = graph["nodes"], graph["edges"]
	if not nodes:
		raise GraphError(_("A workflow needs at least one node"))
	if len(nodes) > MAX_NODES:
		raise GraphError(_("A workflow may not exceed {0} nodes").format(MAX_NODES))
	if len(edges) > MAX_EDGES:
		raise GraphError(_("A workflow may not exceed {0} edges").format(MAX_EDGES))

	ids: set[str] = set()
	for node in nodes:
		node_id = str(node.get("id") or "").strip()
		if not node_id:
			raise GraphError(_("Every workflow node needs an id"))
		if node_id in ids:
			raise GraphError(_("Duplicate workflow node id: {0}").format(node_id))
		ids.add(node_id)

		agent_key = node_agent(node)
		if not agent_key:
			raise GraphError(_("Node {0} does not name an agent").format(node_label(node) or node_id))
		if agent_key not in agents.AGENTS:
			raise GraphError(_("Node {0} references an unknown agent: {1}").format(node_id, agent_key))
		if company and not allowed_tool_names(agent_key, company):
			# The agent exists but this company's policy leaves it with nothing
			# it may call, so the node could never do useful work.
			raise GraphError(
				_("Agent {0} has no tools allowed by the policy for {1}").format(agent_key, company)
			)

	for edge in edges:
		source = str(edge.get("source") or "").strip()
		target = str(edge.get("target") or "").strip()
		if source not in ids or target not in ids:
			raise GraphError(_("Workflow edge references an unknown node: {0} -> {1}").format(source, target))
		if source == target:
			raise GraphError(_("A workflow node cannot connect to itself: {0}").format(source))

	topological_order(graph)


def topological_order(graph: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
	"""Kahn's algorithm. Raises when the graph contains a cycle.

	Ties are broken by the node's position on the canvas and then by id, so the
	same graph always executes in the same order.
	"""
	nodes = {str(node["id"]): node for node in graph["nodes"] if node.get("id")}
	incoming: dict[str, int] = {node_id: 0 for node_id in nodes}
	outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
	for edge in graph["edges"]:
		source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
		if source in nodes and target in nodes:
			outgoing[source].append(target)
			incoming[target] += 1

	def sort_key(node_id: str) -> tuple:
		position = nodes[node_id].get("position") or {}
		return (float(position.get("x") or 0), float(position.get("y") or 0), node_id)

	ready = sorted([node_id for node_id, count in incoming.items() if count == 0], key=sort_key)
	ordered: list[dict[str, Any]] = []
	while ready:
		node_id = ready.pop(0)
		ordered.append(nodes[node_id])
		for target in outgoing[node_id]:
			incoming[target] -= 1
			if incoming[target] == 0:
				ready.append(target)
		ready.sort(key=sort_key)

	if len(ordered) != len(nodes):
		unresolved = sorted(set(nodes) - {str(node["id"]) for node in ordered})
		raise GraphError(_("Workflow graph contains a cycle: {0}").format(", ".join(unresolved)))
	return ordered


def throw_on_error(graph_json: Any, company: str | None = None) -> dict[str, list[dict[str, Any]]]:
	"""Parse and validate, converting a GraphError into a Frappe validation error."""
	try:
		graph = parse(graph_json)
		validate(graph, company)
	except GraphError as exc:
		frappe.throw(str(exc), frappe.ValidationError)
	return graph
