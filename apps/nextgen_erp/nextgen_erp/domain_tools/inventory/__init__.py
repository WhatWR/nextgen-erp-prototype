"""Inventory domain tools — reserved for Phase 2.

Phase 2 adds deterministic company-scoped inventory signals, donor comparison
and replenishment proposals here, together with the Inventory agent deployed in
the Agent Runtime. Nothing is registered in Phase 1: adding a warehouse agent
before those signals exist would put logistics decisions into the chat-specific
action model, which the locked decisions rule out.
"""

from __future__ import annotations

AGENT = "inventory"

TOOLS: list[dict] = []
