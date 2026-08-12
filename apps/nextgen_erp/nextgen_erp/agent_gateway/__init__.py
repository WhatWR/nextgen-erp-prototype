"""The Frappe side of the agent boundary.

Frappe owns authentication context, durable dispatch, run and step
persistence, company policy, proposals, approvals, deterministic calculations
and every ERP read and write. The Agent Runtime microservice reaches those
capabilities only through the versioned, allowlisted methods in
:mod:`nextgen_erp.agent_gateway.api`.
"""

from __future__ import annotations

from .contracts import CONTRACT_VERSION

__all__ = ["CONTRACT_VERSION"]
