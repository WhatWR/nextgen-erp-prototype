from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any


class AutomationPolicy:
    def __init__(self, confidence_threshold: float | None = None):
        configured = confidence_threshold or float(os.environ.get("ORDER_AUTO_CONFIDENCE", "0.95"))
        self.confidence_threshold = max(0.5, min(configured, 1.0))

    def qualifies(self, confidence: float, exceptions: list[str], customer_id: str | None) -> bool:
        return bool(
            customer_id
            and not exceptions
            and confidence >= self.confidence_threshold
        )


class ERPClawAdapter:
    """Translate workflow events into ERPClaw actions.

    Dry-run is the default. Live execution requires ERPCLAW_EXECUTE=1 plus the
    ERPClaw database, company, warehouse, and payment account configuration.
    """

    def __init__(self, export_dir: str | Path):
        self.plan_dir = Path(export_dir) / "erpclaw_workflows"
        self.plan_dir.mkdir(parents=True, exist_ok=True)
        self.execute = os.environ.get("ERPCLAW_EXECUTE") == "1"
        prototype_root = Path(__file__).resolve().parents[3]
        self.erpclaw_root = Path(
            os.environ.get("ERPCLAW_ROOT", prototype_root / "vendor" / "erpclaw")
        )
        self.db_path = os.environ.get("ERPCLAW_DB_PATH", "")
        self.company_id = os.environ.get("ERPCLAW_COMPANY_ID", "")
        self.warehouse_id = os.environ.get("ERPCLAW_WAREHOUSE_ID", "")

    def create_order_and_reserve(self, draft: dict[str, Any]) -> dict[str, Any]:
        items = [
            {
                "item_id": item.get("erpclaw_item_id") or item["sku"],
                "qty": item["quantity"],
                "uom": item.get("uom"),
                "rate": item["unit_price"],
                "warehouse_id": self.warehouse_id or "WAREHOUSE_REQUIRED",
            }
            for item in draft["items"]
        ]
        commands = [
            self._command(
                "add-sales-order",
                **{
                    "customer-id": draft.get("customer_ref") or "CUSTOMER_REQUIRED",
                    "posting-date": date.today().isoformat(),
                    "company-id": self.company_id or "COMPANY_REQUIRED",
                    "items": json.dumps(items, ensure_ascii=False),
                },
            ),
            {"action": "submit-sales-order", "uses": "sales_order_id from step 1"},
            {"action": "create-pick-list", "uses": "sales_order_id from step 1"},
            {"action": "submit-pick-list", "uses": "pick_list_id from step 3"},
        ]
        live_result: dict[str, Any] = {}
        if self.execute:
            self._validate_live_configuration()
            sales_order = self._run("add-sales-order", commands[0]["flags"])
            sales_order_id = sales_order["sales_order_id"]
            self._run("submit-sales-order", {"sales-order-id": sales_order_id})
            pick_list = self._run("create-pick-list", {"from-sales-order": sales_order_id})
            pick_list_id = pick_list["pick_list_id"]
            self._run("submit-pick-list", {"id": pick_list_id})
            live_result = {"sales_order_id": sales_order_id, "pick_list_id": pick_list_id}
        result = self._stage(draft, "order_reserved", commands, live_result)
        result.update(
            sales_order_id=result.get("sales_order_id") or f"dry-so-{draft['id']}",
            pick_list_id=result.get("pick_list_id") or f"dry-pick-{draft['id']}",
        )
        return result

    def complete_delivery_and_invoice(self, draft: dict[str, Any], workflow: dict[str, Any]) -> dict[str, Any]:
        pick_commands = [
            self._command(
                "mark-picked",
                **{
                    "pick-list": workflow["erpclaw_pick_list_id"],
                    "item": item.get("erpclaw_item_id") or item["sku"],
                    "picked-qty": item["quantity"],
                },
            )
            for item in draft["items"]
        ]
        commands = [
            *pick_commands,
            self._command("complete-pick-list", id=workflow["erpclaw_pick_list_id"]),
            {"action": "submit-delivery-note", "uses": "delivery_note_id from complete-pick-list"},
            {"action": "create-sales-invoice", "uses": "delivery_note_id from complete-pick-list"},
            {"action": "submit-sales-invoice", "uses": "sales_invoice_id from create-sales-invoice"},
        ]
        live_result: dict[str, Any] = {}
        if self.execute:
            self._validate_live_configuration()
            for item in draft["items"]:
                self._run(
                    "mark-picked",
                    {
                        "pick-list": workflow["erpclaw_pick_list_id"],
                        "item": item.get("erpclaw_item_id") or item["sku"],
                        "picked-qty": item["quantity"],
                    },
                )
            delivery = self._run("complete-pick-list", {"id": workflow["erpclaw_pick_list_id"]})
            delivery_note_id = delivery["delivery_note_id"]
            self._run("submit-delivery-note", {"delivery-note-id": delivery_note_id})
            invoice = self._run("create-sales-invoice", {"delivery-note-id": delivery_note_id})
            sales_invoice_id = invoice["sales_invoice_id"]
            self._run("submit-sales-invoice", {"sales-invoice-id": sales_invoice_id})
            live_result = {
                "delivery_note_id": delivery_note_id,
                "sales_invoice_id": sales_invoice_id,
            }
        result = self._stage(draft, "delivered_invoiced", commands, live_result)
        result.update(
            delivery_note_id=result.get("delivery_note_id") or f"dry-dn-{draft['id']}",
            sales_invoice_id=result.get("sales_invoice_id") or f"dry-inv-{draft['id']}",
        )
        return result

    def record_payment(self, draft: dict[str, Any], workflow: dict[str, Any], reference: str) -> dict[str, Any]:
        commands = [
            self._command(
                "add-payment",
                **{
                    "company-id": self.company_id or "COMPANY_REQUIRED",
                    "payment-type": "receive",
                    "posting-date": date.today().isoformat(),
                    "party-type": "customer",
                    "party-id": draft.get("customer_ref") or "CUSTOMER_REQUIRED",
                    "paid-amount": draft["total"],
                    "payment-currency": "THB",
                    "reference-number": reference,
                    "reference-date": date.today().isoformat(),
                    "paid-from-account": os.environ.get("ERPCLAW_RECEIVABLE_ACCOUNT", "RECEIVABLE_REQUIRED"),
                    "paid-to-account": os.environ.get("ERPCLAW_BANK_ACCOUNT", "BANK_REQUIRED"),
                },
            ),
            {"action": "submit-payment", "uses": "payment_entry_id from step 1"},
            {
                "action": "allocate-payment",
                "uses": "payment_entry_id from step 1",
                "voucher_type": "sales_invoice",
                "voucher_id": workflow["erpclaw_sales_invoice_id"],
                "allocated_amount": draft["total"],
            },
        ]
        live_result: dict[str, Any] = {}
        if self.execute:
            self._validate_live_configuration()
            payment = self._run("add-payment", commands[0]["flags"])
            payment_id = payment["payment_entry_id"]
            self._run("submit-payment", {"payment-entry-id": payment_id})
            self._run(
                "allocate-payment",
                {
                    "payment-entry-id": payment_id,
                    "voucher-type": "sales_invoice",
                    "voucher-id": workflow["erpclaw_sales_invoice_id"],
                    "allocated-amount": draft["total"],
                },
            )
            live_result = {"payment_id": payment_id}
        result = self._stage(draft, "payment_allocated", commands, live_result)
        result["payment_id"] = result.get("payment_id") or f"dry-pay-{draft['id']}"
        return result

    def _stage(
        self,
        draft: dict[str, Any],
        stage: str,
        commands: list[dict[str, Any]],
        live_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "draft_id": draft["id"],
            "mode": "live" if self.execute else "dry_run",
            "stage": stage,
            "commands": commands,
        }
        path = self.plan_dir / f"{draft['id']}-{stage}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "mode": "live" if self.execute else "dry_run",
            "plan": str(path),
            "commands": commands,
            **(live_result or {}),
        }

    def _command(self, action: str, **flags: Any) -> dict[str, Any]:
        return {"action": action, "flags": flags}

    def _validate_live_configuration(self) -> None:
        missing = []
        if not self.erpclaw_root.exists():
            missing.append("ERPCLAW_ROOT")
        if not self.db_path:
            missing.append("ERPCLAW_DB_PATH")
        if not self.company_id:
            missing.append("ERPCLAW_COMPANY_ID")
        if not self.warehouse_id:
            missing.append("ERPCLAW_WAREHOUSE_ID")
        if missing:
            raise RuntimeError(f"ERPClaw live execution is missing: {', '.join(missing)}")

    def _run(self, action: str, flags: dict[str, Any]) -> dict[str, Any]:
        selling_actions = {
            "add-sales-order", "submit-sales-order", "submit-delivery-note",
            "create-sales-invoice", "submit-sales-invoice",
        }
        inventory_actions = {
            "create-pick-list", "submit-pick-list", "mark-picked", "complete-pick-list",
        }
        module = (
            "erpclaw-selling" if action in selling_actions
            else "erpclaw-inventory" if action in inventory_actions
            else "erpclaw-payments"
        )
        script = self.erpclaw_root / "scripts" / module / "db_query.py"
        if not script.exists():
            raise RuntimeError(f"ERPClaw action module is missing: {script}")
        command = [
            sys.executable,
            str(script),
            "--action",
            action,
            "--db-path",
            self.db_path,
        ]
        for name, value in flags.items():
            if value is None:
                continue
            command.extend([f"--{name}", str(value)])
        env = os.environ.copy()
        env.setdefault("ERPCLAW_HOME", str(self.erpclaw_root / "scripts" / "erpclaw-setup"))
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
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
