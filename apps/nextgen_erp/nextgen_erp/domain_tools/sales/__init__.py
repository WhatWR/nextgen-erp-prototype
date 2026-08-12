"""Sales domain tools.

The implementations live in :mod:`nextgen_erp.staff_chat`, which is also the
sales copilot's tool module today. This package names the sales surface of the
authoritative registry so Phase 1 can migrate the internals without moving ERP
domain logic out of Frappe or changing the copilot's behaviour.
"""

from __future__ import annotations

AGENT = "sales"


def tools():
	from nextgen_erp.domain_tools.registry import tools_for_agent

	return tools_for_agent(AGENT)
