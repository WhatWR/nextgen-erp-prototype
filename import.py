#!/usr/bin/env python3
"""Resumable CSV importer for ERPNext Item records.

Run this with the Python environment from the Frappe bench. The importer uses
Frappe documents (not raw SQL), commits in small batches, skips existing Items,
and writes row-level failures to a CSV file so a browser session is unnecessary.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

import frappe


EXPECTED_COLUMNS = ("Item Code", "Item Group", "Default Unit of Measure")
ROOT_ITEM_GROUP = "All Item Groups"
FALLBACK_ITEM_GROUP = "Imported Items"


def _clean(value: Any) -> str:
	"""Normalize spreadsheet whitespace without changing Thai or product symbols."""
	return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def _leaf_item_group(source_group: str, *, dry_run: bool) -> tuple[str, bool]:
	"""Return a valid leaf Item Group and whether this run created it."""
	group = _clean(source_group) or ROOT_ITEM_GROUP
	existing = frappe.db.get_value("Item Group", group, "is_group")
	if existing is not None and not int(existing):
		return group, False

	# ERPNext does not allow Items to use a group node. New CSV groups can be
	# created directly as leaves; existing group nodes need a child leaf.
	if existing is None:
		target = group
	elif group == ROOT_ITEM_GROUP:
		target = FALLBACK_ITEM_GROUP
	else:
		target = f"{group} - Items"
	target_is_group = frappe.db.get_value("Item Group", target, "is_group")
	if target_is_group is not None:
		if int(target_is_group):
			raise ValueError(f"Item Group {target!r} exists but is not a leaf")
		return target, False

	if dry_run:
		return target, True

	frappe.get_doc(
		{
			"doctype": "Item Group",
			"item_group_name": target,
			"parent_item_group": group if existing is not None else ROOT_ITEM_GROUP,
			"is_group": 0,
		}
	).insert(ignore_permissions=True)
	return target, True


def _ensure_uom(uom: str, *, dry_run: bool) -> bool:
	"""Ensure a referenced UOM exists; return True when it would be/was created."""
	if frappe.db.exists("UOM", uom):
		return False
	if not dry_run:
		frappe.get_doc(
			{
				"doctype": "UOM",
				"uom_name": uom,
				"enabled": 1,
				"must_be_whole_number": int(uom.casefold() in {"nos", "set"}),
			}
		).insert(ignore_permissions=True)
	return True


def _read_headers(csv_path: Path) -> list[str]:
	with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
		reader = csv.reader(handle)
		return [_clean(value) for value in next(reader, [])]


def _detect_site(sites_path: str) -> str:
	candidates = sorted(
		path.parent.name for path in Path(sites_path).glob("*/site_config.json") if path.is_file()
	)
	if len(candidates) == 1:
		return candidates[0]
	if not candidates:
		raise ValueError(f"No Frappe site found under {sites_path!r}; provide --site")
	raise ValueError(f"Multiple Frappe sites found: {', '.join(candidates)}; provide --site")


def run(
	csv_path: str,
	batch_size: int = 250,
	dry_run: bool = False,
	error_log: str | None = None,
) -> dict[str, Any]:
	"""Import Item rows into the currently connected Frappe site.

	Existing Item codes are skipped, making the operation safe to resume. Duplicate
	CSV rows are also skipped after whitespace normalization. Each failed row is
	rolled back to a savepoint without losing successful rows in the same batch.
	"""
	path = Path(csv_path).expanduser().resolve()
	if not path.is_file():
		raise FileNotFoundError(f"CSV not found: {path}")
	batch_size = max(1, min(int(batch_size), 2000))
	headers = _read_headers(path)
	missing = [column for column in EXPECTED_COLUMNS if column not in headers]
	if missing:
		raise ValueError(f"Missing CSV columns: {', '.join(missing)}; found: {headers}")

	item_meta = frappe.get_meta("Item")
	code_limit = item_meta.get_field("item_code").length or 140
	name_limit = item_meta.get_field("item_name").length or 140
	log_path = (
		Path(error_log).expanduser().resolve()
		if error_log
		else path.with_name(f"{path.stem}_import_errors.csv")
	)

	stats: dict[str, Any] = {
		"site": frappe.local.site,
		"csv": str(path),
		"dry_run": bool(dry_run),
		"rows": 0,
		"unique_rows": 0,
		"created": 0,
		"existing": 0,
		"duplicates": 0,
		"errors": 0,
		"created_item_groups": [],
		"created_uoms": [],
		"error_log": str(log_path),
	}
	seen: set[str] = set()
	existing_codes = set(frappe.get_all("Item", pluck="name", limit_page_length=0))
	group_map: dict[str, str] = {}
	known_uoms: set[str] = set()
	writes_since_commit = 0

	log_path.parent.mkdir(parents=True, exist_ok=True)
	with (
		path.open("r", encoding="utf-8-sig", newline="") as source,
		log_path.open("w", encoding="utf-8-sig", newline="") as failures,
	):
		reader = csv.DictReader(source)
		writer = csv.DictWriter(failures, fieldnames=["line", *EXPECTED_COLUMNS, "error"])
		writer.writeheader()

		for line_number, row in enumerate(reader, start=2):
			stats["rows"] += 1
			code = _clean(row.get("Item Code"))
			source_group = _clean(row.get("Item Group")) or ROOT_ITEM_GROUP
			uom = _clean(row.get("Default Unit of Measure"))

			if code in seen:
				stats["duplicates"] += 1
				continue
			seen.add(code)
			stats["unique_rows"] += 1

			try:
				if not code:
					raise ValueError("Item Code is blank")
				if len(code) > code_limit:
					raise ValueError(f"Item Code is longer than {code_limit} characters")
				if len(code) > name_limit:
					raise ValueError(f"Item Name is longer than {name_limit} characters")
				if not uom:
					raise ValueError("Default Unit of Measure is blank")

				if source_group not in group_map:
					group_map[source_group], created_group = _leaf_item_group(
						source_group, dry_run=dry_run
					)
					if created_group:
						stats["created_item_groups"].append(group_map[source_group])
				if uom not in known_uoms:
					if _ensure_uom(uom, dry_run=dry_run):
						stats["created_uoms"].append(uom)
					known_uoms.add(uom)

				if code in existing_codes:
					stats["existing"] += 1
					continue
				if dry_run:
					stats["created"] += 1
					continue

				savepoint = f"item_import_{line_number}"
				frappe.db.savepoint(savepoint)
				try:
					frappe.get_doc(
						{
							"doctype": "Item",
							"item_code": code,
							"item_name": code,
							"item_group": group_map[source_group],
							"stock_uom": uom,
							"is_stock_item": 1,
							"is_sales_item": 1,
							"is_purchase_item": 1,
						}
					).insert(ignore_permissions=True)
				except Exception:
					frappe.db.rollback(save_point=savepoint)
					raise
				stats["created"] += 1
				existing_codes.add(code)
				writes_since_commit += 1
				if writes_since_commit >= batch_size:
					frappe.db.commit()
					writes_since_commit = 0
			except Exception as exc:
				stats["errors"] += 1
				writer.writerow(
					{
						"line": line_number,
						"Item Code": code,
						"Item Group": source_group,
						"Default Unit of Measure": uom,
						"error": str(exc)[:1000],
					}
				)
				failures.flush()

			if stats["rows"] % 1000 == 0:
				print(
					f"rows={stats['rows']:,} created={stats['created']:,} "
					f"existing={stats['existing']:,} duplicates={stats['duplicates']:,} "
					f"errors={stats['errors']:,}",
					flush=True,
				)

	if not dry_run:
		frappe.db.commit()
	# Avoid pointing users to an empty error file as though failures occurred.
	if not stats["errors"]:
		log_path.unlink(missing_ok=True)
		stats["error_log"] = None
	print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)
	return stats


def main() -> int:
	parser = argparse.ArgumentParser(description="Import Item CSV into ERPNext without an HTTP session")
	parser.add_argument("--site", help="Frappe site name; auto-detected when the bench has one site")
	parser.add_argument("--csv", required=True, dest="csv_path", help="CSV path inside the bench/container")
	parser.add_argument("--sites-path", default="sites", help="Bench sites directory (default: sites)")
	parser.add_argument("--batch-size", type=int, default=250)
	parser.add_argument("--dry-run", action="store_true")
	parser.add_argument("--error-log")
	args = parser.parse_args()

	site = args.site or _detect_site(args.sites_path)
	frappe.init(site=site, sites_path=args.sites_path)
	frappe.connect()
	frappe.set_user("Administrator")
	try:
		run(
			csv_path=args.csv_path,
			batch_size=args.batch_size,
			dry_run=args.dry_run,
			error_log=args.error_log,
		)
		return 0
	except KeyboardInterrupt:
		frappe.db.rollback()
		print("Stopped. Committed batches are preserved; run again to resume.", file=sys.stderr)
		return 130
	except Exception:
		frappe.db.rollback()
		raise
	finally:
		frappe.destroy()


if __name__ == "__main__":
	raise SystemExit(main())
