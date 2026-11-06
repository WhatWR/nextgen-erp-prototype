"""Scheduled mailbox poll for PO email intake."""

from __future__ import annotations

import frappe


def poll_mailbox() -> None:
	"""Periodic sync. Soft-fails so a mailbox outage never blocks the scheduler."""
	try:
		if not frappe.db.exists("DocType", "NextGen PO Email Settings"):
			return
		enabled = frappe.db.get_single_value("NextGen PO Email Settings", "enabled")
		if not enabled:
			return
		from nextgen_erp.purchase_email import pipeline

		pipeline.sync_inbox(limit=20)
	except Exception:
		frappe.log_error(title="PO email mailbox poll", message=frappe.get_traceback())
