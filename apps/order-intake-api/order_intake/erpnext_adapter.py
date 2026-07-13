from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from .erpnext_client import ERPNextClient, ERPNextError


class ERPNextAdapter:
    """Translate order-to-cash workflow events into ERPNext documents.

    This is a drop-in replacement for ``ERPClawAdapter``: it exposes the same
    three methods and the same return keys the service reads
    (``sales_order_id`` / ``pick_list_id`` / ``delivery_note_id`` /
    ``sales_invoice_id`` / ``payment_id``).

    Shadow/dry-run is the default. Every stage writes a reviewable plan artifact
    and returns synthetic ids without touching ERPNext. Live execution requires
    ``ERPNEXT_EXECUTE=1`` plus a configured client, company, and warehouse, and
    routes writes through the ``nextgen_erp`` app's whitelisted methods rather
    than generic CRUD so submission and idempotency stay server-side.

    The whitelisted method contract the ``nextgen_erp`` app must provide:

    * ``<ns>.create_sales_order(external_reference, customer, company, currency,
      delivery_date, items, reserve_stock)`` -> ``{sales_order, pick_list}``
    * ``<ns>.deliver_and_invoice(sales_order, pick_list, items)``
      -> ``{delivery_note, sales_invoice}``
    * ``<ns>.record_payment(company, customer, currency, amount, reference_no,
      reference_date, sales_invoice)`` -> ``{payment_entry}``

    ``external_reference`` is the draft id and doubles as the idempotency key so
    a retried stage never creates a duplicate document.
    """

    def __init__(
        self,
        export_dir: str | Path,
        client: ERPNextClient | None = None,
        *,
        company_id: str | None = None,
        warehouse_id: str | None = None,
        execute: bool | None = None,
    ) -> None:
        self.plan_dir = Path(export_dir) / "erpnext_workflows"
        self.plan_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or ERPNextClient()
        self.company_id = company_id or os.environ.get("ERPNEXT_COMPANY", "")
        self.warehouse_id = warehouse_id or os.environ.get("ERPNEXT_WAREHOUSE", "")
        self.method_ns = os.environ.get("ERPNEXT_METHOD_NS", "nextgen_erp.api")
        self.execute = execute if execute is not None else os.environ.get("ERPNEXT_EXECUTE") == "1"

    def create_order_and_reserve(self, draft: dict[str, Any]) -> dict[str, Any]:
        items = [
            {
                "item_code": item.get("erpnext_item_code") or item["sku"],
                "qty": item["quantity"],
                "uom": item.get("uom"),
                "rate": item["unit_price"],
                "warehouse": self.warehouse_id or "WAREHOUSE_REQUIRED",
            }
            for item in draft["items"]
        ]
        args = {
            "external_reference": draft["id"],
            "customer": draft.get("customer_ref") or "CUSTOMER_REQUIRED",
            "company": self.company_id or "COMPANY_REQUIRED",
            "currency": "THB",
            "delivery_date": date.today().isoformat(),
            "items": items,
            "reserve_stock": True,
        }
        commands = [self._method("create_sales_order", args)]
        live_result: dict[str, Any] = {}
        if self.execute:
            self._validate_live()
            message = self._call("create_sales_order", **args)
            live_result = {
                "sales_order_id": message["sales_order"],
                "pick_list_id": message.get("pick_list"),
            }
        result = self._stage(draft, "order_reserved", commands, live_result)
        result.update(
            sales_order_id=result.get("sales_order_id") or f"shadow-so-{draft['id']}",
            pick_list_id=result.get("pick_list_id") or f"shadow-pick-{draft['id']}",
        )
        return result

    def complete_delivery_and_invoice(
        self, draft: dict[str, Any], workflow: dict[str, Any]
    ) -> dict[str, Any]:
        args = {
            "external_reference": draft["id"],
            # Column names carry the ``erpclaw_`` prefix for schema continuity but
            # hold whichever ERP backend produced them.
            "sales_order": workflow.get("erpclaw_sales_order_id"),
            "pick_list": workflow.get("erpclaw_pick_list_id"),
            "items": [
                {
                    "item_code": item.get("erpnext_item_code") or item["sku"],
                    "qty": item["quantity"],
                    "uom": item.get("uom"),
                }
                for item in draft["items"]
            ],
        }
        commands = [self._method("deliver_and_invoice", args)]
        live_result: dict[str, Any] = {}
        if self.execute:
            self._validate_live()
            message = self._call("deliver_and_invoice", **args)
            live_result = {
                "delivery_note_id": message["delivery_note"],
                "sales_invoice_id": message["sales_invoice"],
            }
        result = self._stage(draft, "delivered_invoiced", commands, live_result)
        result.update(
            delivery_note_id=result.get("delivery_note_id") or f"shadow-dn-{draft['id']}",
            sales_invoice_id=result.get("sales_invoice_id") or f"shadow-inv-{draft['id']}",
        )
        return result

    def record_payment(
        self, draft: dict[str, Any], workflow: dict[str, Any], reference: str
    ) -> dict[str, Any]:
        args = {
            "external_reference": f"{draft['id']}:{reference}",
            "company": self.company_id or "COMPANY_REQUIRED",
            "customer": draft.get("customer_ref") or "CUSTOMER_REQUIRED",
            "currency": "THB",
            "amount": draft["total"],
            "reference_no": reference,
            "reference_date": date.today().isoformat(),
            "sales_invoice": workflow.get("erpclaw_sales_invoice_id"),
        }
        commands = [self._method("record_payment", args)]
        live_result: dict[str, Any] = {}
        if self.execute:
            self._validate_live()
            message = self._call("record_payment", **args)
            live_result = {"payment_id": message["payment_entry"]}
        result = self._stage(draft, "payment_allocated", commands, live_result)
        result["payment_id"] = result.get("payment_id") or f"shadow-pay-{draft['id']}"
        return result

    def _method(self, suffix: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"method": f"{self.method_ns}.{suffix}", "args": args}

    def _call(self, suffix: str, **args: Any) -> dict[str, Any]:
        message = self.client.call_method(f"{self.method_ns}.{suffix}", **args)
        if not isinstance(message, dict):
            raise ERPNextError(f"{suffix} returned an unexpected response")
        return message

    def _validate_live(self) -> None:
        missing = []
        if not self.client.configured:
            missing.append("ERPNEXT_URL/ERPNEXT_API_KEY/ERPNEXT_API_SECRET")
        if not self.company_id:
            missing.append("ERPNEXT_COMPANY")
        if not self.warehouse_id:
            missing.append("ERPNEXT_WAREHOUSE")
        if missing:
            raise ERPNextError(f"ERPNext live execution is missing: {', '.join(missing)}")

    def _stage(
        self,
        draft: dict[str, Any],
        stage: str,
        commands: list[dict[str, Any]],
        live_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mode = "live" if self.execute else "shadow"
        payload = {
            "draft_id": draft["id"],
            "backend": "erpnext",
            "mode": mode,
            "stage": stage,
            "commands": commands,
        }
        path = self.plan_dir / f"{draft['id']}-{stage}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"mode": mode, "plan": str(path), "commands": commands, **(live_result or {})}


class ERPNextCatalogSource:
    """Read products, stock and prices from ERPNext (never a separate table).

    Returns the same product shape as ``ERPClawIntegrationStore.fetch_catalog``
    so it is drop-in for ``OrderIntakeService.sync_erpclaw_catalog``. The ERPNext
    ``item_code`` populates the schema's generic ERP-item slot.
    """

    def __init__(
        self,
        client: ERPNextClient | None = None,
        *,
        warehouse_id: str | None = None,
        price_list: str | None = None,
    ) -> None:
        self.client = client or ERPNextClient()
        self.warehouse_id = warehouse_id or os.environ.get("ERPNEXT_WAREHOUSE", "")
        self.price_list = price_list or os.environ.get("ERPNEXT_PRICE_LIST", "")

    def fetch_catalog(self) -> list[dict[str, Any]]:
        if not self.client.configured:
            raise ERPNextError("Configure ERPNEXT_URL, API key and secret before syncing")
        if not self.warehouse_id:
            raise ERPNextError("ERPNEXT_WAREHOUSE is required to read stock")
        items: list[dict[str, Any]] = []
        start = 0
        while True:
            page = self.client.call_method(
                "nextgen_erp.api.get_catalog",
                warehouse=self.warehouse_id,
                price_list=self.price_list or None,
                limit_start=start,
                limit=500,
            )
            if not isinstance(page, dict) or not isinstance(page.get("data"), list):
                raise ERPNextError("nextgen_erp.api.get_catalog returned an unexpected response")
            items.extend(page["data"])
            if not page.get("has_more"):
                break
            next_start = int(page.get("next_start") or 0)
            if next_start <= start:
                raise ERPNextError("ERPNext catalog pagination did not advance")
            start = next_start
        products = []
        for item in items:
            item_code = str(item.get("item_code") or "")
            if not item_code:
                continue
            products.append(
                {
                    "erpnext_item_code": item_code,
                    "erpclaw_item_id": item_code,
                    "sku": item_code,
                    "name": str(item.get("item_name") or item_code),
                    "aliases": list(item.get("aliases") or []),
                    "uom": str(item.get("stock_uom") or "Nos"),
                    "price": str(item.get("price") or "0"),
                    "stock": str(item.get("projected_qty") or "0"),
                    "item_group": str(item.get("item_group") or ""),
                    "price_source": "price_list" if self.price_list else "standard_rate",
                }
            )
        return products

def select_transaction_adapter(export_dir: str | Path):
    """Pick the order-to-cash adapter by ``ORDER_BACKEND`` (default: erpnext).

    Both adapters share the same method surface and return keys, so the service
    is unchanged regardless of which backend is selected.
    """
    if os.environ.get("ORDER_BACKEND", "erpnext").lower() == "erpnext":
        return ERPNextAdapter(export_dir)
    from .workflow import ERPClawAdapter

    return ERPClawAdapter(export_dir)
