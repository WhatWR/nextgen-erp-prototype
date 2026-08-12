"""Allowlisted deterministic domain tools, grouped by business area.

``sales`` and ``procurement`` are implemented in Phase 1 and delegate to the
existing copilot tool modules through :mod:`nextgen_erp.domain_tools.registry`.
``inventory`` is the Phase 2 slot and deliberately registers nothing yet: no
warehouse or logistics tool exists until its deterministic Frappe signals do.
"""

from __future__ import annotations

from .registry import (
	WRITE_TOOLS,
	DomainTool,
	ToolRejected,
	allowed_tool_names,
	execute,
	schemas_for,
	tool_registry,
	tools_for_agent,
)

__all__ = [
	"WRITE_TOOLS",
	"DomainTool",
	"ToolRejected",
	"allowed_tool_names",
	"execute",
	"schemas_for",
	"tool_registry",
	"tools_for_agent",
]
