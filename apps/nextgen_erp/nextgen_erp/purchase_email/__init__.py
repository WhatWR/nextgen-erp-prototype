"""Purchase-order-from-email intake for the procurement team.

Replaces the Excel validation spreadsheet with:
1. Mailbox connect (Mock / Gmail / Microsoft / IMAP)
2. Deterministic validation rules
3. AI-assisted extraction into a human review queue
"""

from __future__ import annotations

__all__ = ["api", "pipeline", "rules", "extractor", "providers", "fixtures"]
