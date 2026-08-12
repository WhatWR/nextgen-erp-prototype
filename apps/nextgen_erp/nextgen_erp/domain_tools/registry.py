"""The authoritative domain-tool registry.

Tools accept business intent. They never accept raw SQL, arbitrary DocType
CRUD, unrestricted Python or a caller-selected identity, and they never receive
a company, warehouse or user chosen by the model: those come from the persisted
run and are revalidated here on every call.

The registry is built from :mod:`nextgen_erp.agents`, which already owns the
per-agent tool allowlist used by the in-process copilots. Phase 1 therefore
exposes exactly the tools that exist today, through one authorised entry point.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import frappe
from frappe import _

from nextgen_erp import agents
from nextgen_erp.agent_gateway import permissions, policy

# Tools whose result is a preview that must become an Action Proposal. They
# never write an ERP document themselves.
WRITE_TOOLS = frozenset(
	{
		"prepare_sales_order",
		"prepare_purchase_order",
		"prepare_material_request",
		"prepare_document",
	}
)

# Arguments that name a warehouse. Whatever the model supplied is re-checked
# against the run's company and the company policy before the tool runs.
WAREHOUSE_ARGUMENTS = ("warehouse", "target_warehouse", "from_warehouse")

# Arguments a caller must never be able to set. Identity and company come from
# the persisted run; a payload override is rejected, not merged.
FORBIDDEN_ARGUMENTS = frozenset(
	{"user", "owner", "company", "requested_by", "execution_user", "session_id", "ignore_permissions"}
)


class ToolRejected(Exception):
	"""The tool call is not permitted. Recorded as a Rejected step."""


@dataclass(frozen=True)
class DomainTool:
	name: str
	agent: str
	schema: dict[str, Any]
	writes: bool


def tool_registry() -> dict[str, DomainTool]:
	"""Every tool of every agent, keyed ``agent:tool``."""
	tools: dict[str, DomainTool] = {}
	for agent in agents.AGENTS.values():
		for schema in agent.tools:
			name = schema["function"]["name"]
			tools[f"{agent.key}:{name}"] = DomainTool(
				name=name, agent=agent.key, schema=schema, writes=name in WRITE_TOOLS
			)
	return tools


def tools_for_agent(agent_key: str) -> list[DomainTool]:
	agent = agents.get_agent(agent_key)
	return [
		DomainTool(
			name=schema["function"]["name"],
			agent=agent.key,
			schema=schema,
			writes=schema["function"]["name"] in WRITE_TOOLS,
		)
		for schema in agent.tools
	]


def allowed_tool_names(agent_key: str, company: str) -> list[str]:
	"""Code allowlist narrowed by company policy. Configuration never widens it."""
	snapshot = policy.snapshot(company)
	names = [tool.name for tool in tools_for_agent(agent_key)]
	if not snapshot.get("enabled"):
		# Automation disabled for this company: reads stay available, previews
		# that could become ERP documents do not.
		return [name for name in names if name not in WRITE_TOOLS]
	return names


def schemas_for(agent_key: str, names: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
	permitted = set(names or ())
	return [tool.schema for tool in tools_for_agent(agent_key) if tool.name in permitted]


@contextmanager
def acting_as(user: str) -> Iterator[None]:
	"""Run a tool with the requester's own ERP permissions.

	The gateway authenticates as the restricted service identity, but the work
	is authorised by the human on the run. Executing the tool under that user
	means ERPNext's own permission and User Permission checks apply.
	"""
	original = frappe.session.user
	frappe.set_user(user)
	try:
		yield
	finally:
		frappe.set_user(original)


def _validate_arguments(tool: DomainTool, arguments: dict[str, Any], run) -> dict[str, Any]:
	if not isinstance(arguments, dict):
		raise ToolRejected(_("Tool arguments must be an object"))
	smuggled = sorted(FORBIDDEN_ARGUMENTS.intersection(arguments))
	if smuggled:
		raise ToolRejected(
			_("Tool arguments may not set {0}; identity and company come from the run").format(
				", ".join(smuggled)
			)
		)
	cleaned = dict(arguments)
	for field in WAREHOUSE_ARGUMENTS:
		warehouse = str(cleaned.get(field) or "").strip()
		if warehouse:
			policy.assert_warehouse_in_company(warehouse, run.company, user=run.requested_by)
	return cleaned


def resolve(run, tool_name: str) -> DomainTool:
	"""Resolve a tool against the code allowlist and the run's persisted list."""
	name = str(tool_name or "").strip()
	agent_tools = {tool.name: tool for tool in tools_for_agent(run.agent_type)}
	tool = agent_tools.get(name)
	if not tool:
		raise ToolRejected(
			_("Tool {0} is not available to agent {1}").format(name or "(empty)", run.agent_type)
		)
	allowed = run.allowed_tool_names()
	if name not in allowed:
		raise ToolRejected(_("Tool {0} is not allowed for this run").format(name))
	return tool


def execute(run, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
	"""Authorise and run one domain tool for a persisted run.

	Returns the sanitized tool payload. Raises :class:`ToolRejected` when the
	call is refused; the caller records that as a Rejected step.
	"""
	tool = resolve(run, tool_name)
	requester = run.requested_by
	permissions.assert_agent_access(run.agent_type, requester)
	policy.assert_company_access(run.company, requester)
	cleaned = _validate_arguments(tool, arguments, run)

	if tool.writes and not policy.snapshot(run.company).get("enabled"):
		raise ToolRejected(
			_("Automation is disabled for company {0}").format(run.company)
		)

	agent = agents.get_agent(run.agent_type)
	with acting_as(requester):
		result = agent.dispatch(
			tool.name, cleaned, user=requester, session_id=run.chat_session or None, run=run
		)
	if not isinstance(result, dict):
		result = {"result": result}
	if result.get("error"):
		raise ToolRejected(str(result["error"]))

	if tool.writes:
		from nextgen_erp.action_proposals import service as proposals

		result = proposals.from_tool_result(run, tool.name, cleaned, result)
	return result
