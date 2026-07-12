from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ERPClawIntegrationStore:
    def __init__(self, path: str | Path, default_root: str | Path):
        self.path = Path(path)
        self.default_root = Path(default_root)

    def status(self) -> dict[str, Any]:
        config = self._read()
        root = Path(config.get("erpclaw_root") or os.environ.get("ERPCLAW_ROOT", self.default_root))
        db_path = Path(config.get("db_path") or os.environ.get("ERPCLAW_DB_PATH", ""))
        company_id = str(config.get("company_id") or os.environ.get("ERPCLAW_COMPANY_ID", ""))
        warehouse_id = str(config.get("warehouse_id") or os.environ.get("ERPCLAW_WAREHOUSE_ID", ""))
        configured = bool(root.exists() and db_path.is_file() and company_id and warehouse_id)
        return {
            "configured": configured,
            "erpclaw_root": str(root),
            "db_path": str(db_path) if str(db_path) != "." else "",
            "company_id": company_id,
            "warehouse_id": warehouse_id,
            "price_list_id": str(config.get("price_list_id") or ""),
            "last_synced_at": config.get("last_synced_at"),
            "last_item_count": int(config.get("last_item_count") or 0),
            "source_of_truth": "erpclaw" if configured else "demo",
        }

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = self._read()
        for key in ("erpclaw_root", "db_path", "company_id", "warehouse_id", "price_list_id"):
            if key in payload:
                config[key] = str(payload.get(key) or "").strip()
        root = Path(config.get("erpclaw_root") or self.default_root)
        db_path = Path(config.get("db_path") or "")
        if not root.exists():
            raise ValueError("ERPClaw root directory was not found")
        if not db_path.is_file():
            raise ValueError("ERPClaw database file was not found")
        if not config.get("company_id") or not config.get("warehouse_id"):
            raise ValueError("ERPClaw company and warehouse IDs are required")
        self._write(config)
        return self.status()

    def fetch_catalog(self) -> list[dict[str, Any]]:
        status = self.status()
        if not status["configured"]:
            raise ValueError("Connect an ERPClaw database, company, and warehouse before syncing")
        items_result = self._run(
            "erpclaw-inventory",
            "list-items",
            {
                "company-id": status["company_id"],
                "limit": 200,
            },
        )
        products = []
        for item in items_result.get("items", []):
            if str(item.get("status") or "active") != "active":
                continue
            item_id = str(item["id"])
            stock = self._run(
                "erpclaw-inventory",
                "get-projected-qty",
                {"item-id": item_id, "warehouse-id": status["warehouse_id"]},
            )
            price = str(item.get("standard_rate") or "0")
            price_source = "standard_rate"
            if status["price_list_id"]:
                try:
                    price_result = self._run(
                        "erpclaw-inventory",
                        "get-item-price",
                        {
                            "item-id": item_id,
                            "price-list-id": status["price_list_id"],
                            "qty": 1,
                        },
                    )
                    price = str(price_result.get("rate") or price)
                    price_source = "price_list"
                except RuntimeError:
                    pass
            products.append(
                {
                    "erpclaw_item_id": item_id,
                    "sku": str(item.get("item_code") or item_id),
                    "name": str(item.get("item_name") or item.get("item_code") or item_id),
                    "aliases": [],
                    "uom": str(item.get("stock_uom") or "Nos"),
                    "price": price,
                    "stock": str(stock.get("projected_qty") or "0"),
                    "item_group": str(item.get("item_group_name") or ""),
                    "price_source": price_source,
                }
            )
        config = self._read()
        config["last_synced_at"] = datetime.now(timezone.utc).isoformat()
        config["last_item_count"] = len(products)
        self._write(config)
        return products

    def _run(self, module: str, action: str, flags: dict[str, Any]) -> dict[str, Any]:
        status = self.status()
        script = Path(status["erpclaw_root"]) / "scripts" / module / "db_query.py"
        command = [
            sys.executable, str(script), "--action", action,
            "--db-path", status["db_path"],
        ]
        for name, value in flags.items():
            command.extend([f"--{name}", str(value)])
        env = os.environ.copy()
        env.setdefault("ERPCLAW_HOME", str(Path(status["erpclaw_root"]) / "scripts" / "erpclaw-setup"))
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=30, check=False, env=env
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"ERPClaw {action} failed: {(completed.stderr or completed.stdout).strip()}"
            )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ERPClaw {action} returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeError(f"ERPClaw {action} returned an unexpected response")
        return result

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(self.path)
