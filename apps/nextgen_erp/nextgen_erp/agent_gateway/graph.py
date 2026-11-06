"""Workflow graph parsing, validation and ordering.

A workflow is a directed acyclic graph of typed nodes stored in the designer's
own format, so the canvas can round-trip it without translation. Frappe owns
validation: a graph is checked on save, not at execution time, so the executor
never has to reason about a malformed or cyclic definition.

Node semantics live in :mod:`nextgen_erp.agent_gateway.nodes`. This module only
knows that a node *has* a kind and that the kind validates itself. An agent node
names an *agent*, never a tool; what that agent may actually do is still derived
at run time from the company policy and the code allowlist, so a workflow
definition can never grant a capability the policy withholds. The same holds for
a source or sink node: it names a DocType, a file or a connection, and the
requester's own permissions decide whether it may read them.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _

from nextgen_erp.agent_gateway import nodes as node_kinds

MAX_NODES = 40
MAX_EDGES = 120


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
	return {
		"nodes": [n for n in nodes if isinstance(n, dict)],
		"edges": [e for e in edges if isinstance(e, dict)],
	}


# Node accessors stay exported here because callers already import them from
# this module; the definitions themselves belong with the node kinds.
node_data = node_kinds.node_data
node_kind = node_kinds.node_kind
node_label = node_kinds.node_label
is_agent_node = node_kinds.is_agent


def node_agent(node: dict[str, Any]) -> str:
	return str(node_kinds.node_data(node).get("agent") or "").strip()


def node_prompt(node: dict[str, Any]) -> str:
	return str(node_kinds.node_data(node).get("prompt") or "").strip()


def find_node(graph: dict[str, list[dict[str, Any]]], node_id: str) -> dict[str, Any] | None:
	return next((n for n in graph["nodes"] if str(n.get("id")) == str(node_id)), None)


def parents(graph: dict[str, list[dict[str, Any]]], node_id: str) -> list[str]:
	"""Ids feeding this node, in the order their sources appear on the canvas.

	This is what makes an edge mean something. A node reads the output of the
	nodes actually connected to it, not simply of whichever node happened to run
	last, so a branch that fans out and merges again behaves as it is drawn.
	"""
	position = {str(n.get("id")): index for index, n in enumerate(ordered_ids(graph))}
	sources = [
		str(edge.get("source"))
		for edge in graph["edges"]
		if str(edge.get("target")) == str(node_id) and str(edge.get("source")) in position
	]
	return sorted(dict.fromkeys(sources), key=lambda source: position[source])


def ordered_ids(graph: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
	return topological_order(graph)


def validate(graph: dict[str, list[dict[str, Any]]], company: str | None = None) -> None:
	"""Reject anything the executor should never have to handle."""
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
		try:
			# Each kind validates its own configuration: the agent it names, the
			# DocType it reads, the connection and path it may call.
			node_kinds.validate_node(node, company)
		except node_kinds.NodeError as exc:
			raise GraphError(str(exc)) from exc

	for edge in edges:
		source = str(edge.get("source") or "").strip()
		target = str(edge.get("target") or "").strip()
		if source not in ids or target not in ids:
			raise GraphError(
				_("Workflow edge references an unknown node: {0} -> {1}").format(source, target)
			)
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
