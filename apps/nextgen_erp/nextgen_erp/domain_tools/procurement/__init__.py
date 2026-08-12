"""Procurement domain tools.

The implementations live in :mod:`nextgen_erp.procurement`. Forecast formulas
and recommendation behaviour are unchanged in Phase 1: ``nextgen-procurement-v1``
still returns identical results for identical inputs.
"""

from __future__ import annotations

AGENT = "procurement"


def tools():
	from nextgen_erp.domain_tools.registry import tools_for_agent

	return tools_for_agent(AGENT)
