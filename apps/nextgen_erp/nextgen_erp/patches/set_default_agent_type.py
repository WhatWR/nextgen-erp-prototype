"""Backfill agent_type='sales' on chat records created before the multi-agent
NextGen AI assistant existed."""

import frappe


def execute():
	for doctype in ("NextGen Chat Session", "NextGen Chat Message", "NextGen Chat Action"):
		table = f"tab{doctype}"
		if not frappe.db.table_exists(table):
			continue
		frappe.db.sql(
			f"update `{table}` set agent_type = 'sales' where ifnull(agent_type, '') = ''"
		)
