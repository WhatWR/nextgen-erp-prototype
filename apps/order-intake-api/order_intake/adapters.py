from __future__ import annotations

import csv
import json
from pathlib import Path


class ControlledWriteback:
    """Prototype adapters that create reviewable artifacts and never touch ERP tables."""

    def __init__(self, export_dir: str | Path):
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def write(self, draft: dict) -> dict:
        csv_path = self.export_dir / "approved_orders.csv"
        payload_dir = self.export_dir / "erpclaw_payloads"
        payload_dir.mkdir(parents=True, exist_ok=True)

        new_file = not csv_path.exists()
        with csv_path.open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "draft_id",
                    "merchant_id",
                    "customer_ref",
                    "sku",
                    "product_name",
                    "quantity",
                    "uom",
                    "unit_price",
                    "line_total",
                    "approved_at",
                ],
            )
            if new_file:
                writer.writeheader()
            for item in draft["items"]:
                writer.writerow(
                    {
                        "draft_id": draft["id"],
                        "merchant_id": draft["merchant_id"],
                        "customer_ref": draft.get("customer_ref") or "",
                        "sku": item.get("sku") or "",
                        "product_name": item.get("product_name") or "",
                        "quantity": item["quantity"],
                        "uom": item.get("uom") or "",
                        "unit_price": item["unit_price"],
                        "line_total": item["line_total"],
                        "approved_at": draft["updated_at"],
                    }
                )

        erpclaw_payload = {
            "mode": "dry_run",
            "executed": False,
            "reason": "Prototype safety boundary: a human-approved payload for a future ERPClaw adapter.",
            "target": "erpclaw-selling",
            "action": "add-sales-order",
            "request": {
                "external_reference": draft["id"],
                "customer_reference": draft.get("customer_ref"),
                "currency": "THB",
                "items": [
                    {
                        "sku": item.get("sku"),
                        "quantity": item["quantity"],
                        "uom": item.get("uom"),
                        "unit_price": item["unit_price"],
                    }
                    for item in draft["items"]
                ],
            },
        }
        payload_path = payload_dir / f"{draft['id']}.json"
        payload_path.write_text(
            json.dumps(erpclaw_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "csv": str(csv_path),
            "erpclaw_dry_run": str(payload_path),
            "erpclaw_executed": False,
        }
