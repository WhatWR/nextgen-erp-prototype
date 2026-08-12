"""Generic proposals, approvals and execution.

Approval requires an authenticated human Frappe session and applies to one
immutable snapshot hash. Execution remains a Frappe transaction and never
submits a Purchase Order, Material Request, Stock Entry or accounting document.
"""

from __future__ import annotations

from . import service

__all__ = ["service"]
