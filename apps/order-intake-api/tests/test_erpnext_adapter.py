from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from order_intake.erpnext_adapter import (
    ERPNextAdapter,
    ERPNextCatalogSource,
    select_transaction_adapter,
)
from order_intake.erpnext_client import ERPNextClient, ERPNextError
from order_intake.workflow import ERPClawAdapter


class RecordingTransport:
    """Fake HTTP transport: records calls and replies from a URL-substring map."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[dict[str, object]] = []

    def __call__(self, url, method, headers, body):
        self.calls.append(
            {
                "url": url,
                "method": method,
                "headers": headers,
                "body": json.loads(body.decode("utf-8")) if body else None,
            }
        )
        for fragment, message in self.routes.items():
            if fragment in url:
                return 200, json.dumps({"message": message, "data": message}).encode("utf-8")
        raise AssertionError(f"unexpected request to {url}")


DRAFT = {
    "id": "ord_test123",
    "customer_ref": "CUST-1",
    "total": "912.00",
    "items": [
        {"sku": "M150", "quantity": "2", "uom": "ลัง", "unit_price": "390.00"},
        {"sku": "WATER-600", "quantity": "1", "uom": "ลัง", "unit_price": "132.00"},
    ],
}


class ERPNextAdapterShadowTest(unittest.TestCase):
    def test_shadow_mode_writes_plan_and_returns_synthetic_ids_without_http(self) -> None:
        def exploding_transport(*args, **kwargs):  # must never be called in shadow mode
            raise AssertionError("shadow mode must not perform HTTP")

        with tempfile.TemporaryDirectory() as tmp:
            adapter = ERPNextAdapter(
                tmp,
                client=ERPNextClient("https://erp.example", "k", "s", transport=exploding_transport),
                execute=False,
            )
            result = adapter.create_order_and_reserve(DRAFT)
            plan = json.loads(Path(result["plan"]).read_text(encoding="utf-8"))

        self.assertEqual(result["mode"], "shadow")
        self.assertEqual(result["sales_order_id"], "shadow-so-ord_test123")
        self.assertEqual(result["pick_list_id"], "shadow-pick-ord_test123")
        self.assertEqual(plan["backend"], "erpnext")
        self.assertEqual(plan["commands"][0]["method"], "nextgen_erp.api.create_sales_order")
        # The idempotency key is the draft id.
        self.assertEqual(plan["commands"][0]["args"]["external_reference"], "ord_test123")

    def test_full_shadow_lifecycle_returns_all_keys_service_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ERPNextAdapter(tmp, client=ERPNextClient(), execute=False)
            reserve = adapter.create_order_and_reserve(DRAFT)
            workflow = {
                "erpclaw_sales_order_id": reserve["sales_order_id"],
                "erpclaw_pick_list_id": reserve["pick_list_id"],
            }
            delivery = adapter.complete_delivery_and_invoice(DRAFT, workflow)
            workflow["erpclaw_sales_invoice_id"] = delivery["sales_invoice_id"]
            payment = adapter.record_payment(DRAFT, workflow, "PAY-REF-9")

        self.assertEqual(delivery["delivery_note_id"], "shadow-dn-ord_test123")
        self.assertEqual(delivery["sales_invoice_id"], "shadow-inv-ord_test123")
        self.assertEqual(payment["payment_id"], "shadow-pay-ord_test123")


class ERPNextAdapterLiveTest(unittest.TestCase):
    def test_live_mode_calls_whitelisted_methods_and_returns_ids(self) -> None:
        transport = RecordingTransport(
            {"create_sales_order": {"sales_order": "SAL-ORD-001", "pick_list": "PICK-001"}}
        )
        client = ERPNextClient("https://erp.example", "key", "secret", transport=transport)
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ERPNextAdapter(
                tmp, client=client, company_id="NextGen (Thailand)", warehouse_id="Main - NG",
                execute=True,
            )
            result = adapter.create_order_and_reserve(DRAFT)

        self.assertEqual(result["mode"], "live")
        self.assertEqual(result["sales_order_id"], "SAL-ORD-001")
        self.assertEqual(result["pick_list_id"], "PICK-001")
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertIn("/api/method/nextgen_erp.api.create_sales_order", call["url"])
        self.assertEqual(call["headers"]["Authorization"], "token key:secret")
        self.assertEqual(call["body"]["items"][0]["warehouse"], "Main - NG")

    def test_live_mode_without_configuration_raises_before_any_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ERPNextAdapter(tmp, client=ERPNextClient(), execute=True)
            with self.assertRaises(ERPNextError):
                adapter.create_order_and_reserve(DRAFT)


class ERPNextCatalogSourceTest(unittest.TestCase):
    def test_fetch_catalog_uses_batched_erpnext_method(self) -> None:
        transport = RecordingTransport(
            {
                "/api/method/nextgen_erp.api.get_catalog": {
                    "data": [{
                        "item_code": "M150",
                        "item_name": "เครื่องดื่ม M-150",
                        "stock_uom": "ลัง",
                        "price": 375.0,
                        "projected_qty": 12,
                        "item_group": "Drinks",
                        "aliases": ["เอ็มร้อยห้าสิบ"],
                    }],
                    "has_more": False,
                    "next_start": 1,
                },
            }
        )
        client = ERPNextClient("https://erp.example", "k", "s", transport=transport)
        source = ERPNextCatalogSource(client, warehouse_id="Main - NG", price_list="Thailand Selling")

        products = source.fetch_catalog()

        self.assertEqual(len(products), 1)
        product = products[0]
        self.assertEqual(product["sku"], "M150")
        self.assertEqual(product["erpclaw_item_id"], "M150")  # generic ERP-item slot
        self.assertEqual(product["stock"], "12")  # summed across bins
        self.assertEqual(product["price"], "375.0")  # price list wins over standard_rate
        self.assertEqual(product["price_source"], "price_list")
        self.assertEqual(product["aliases"], ["เอ็มร้อยห้าสิบ"])
        self.assertEqual(len(transport.calls), 1)

    def test_fetch_catalog_requires_configuration(self) -> None:
        with self.assertRaises(ERPNextError):
            ERPNextCatalogSource(ERPNextClient(), warehouse_id="Main - NG").fetch_catalog()


class BackendSelectionTest(unittest.TestCase):
    def test_default_backend_is_erpnext(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsInstance(select_transaction_adapter(tmp), ERPNextAdapter)

    def test_erpnext_backend_selected_by_env(self) -> None:
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ORDER_BACKEND": "erpnext"}):
            self.assertIsInstance(select_transaction_adapter(tmp), ERPNextAdapter)

    def test_legacy_erpclaw_backend_requires_explicit_env(self) -> None:
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ORDER_BACKEND": "erpclaw"}):
            self.assertIsInstance(select_transaction_adapter(tmp), ERPClawAdapter)


if __name__ == "__main__":
    unittest.main()
