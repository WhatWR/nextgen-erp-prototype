from __future__ import annotations

import base64
import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path

from order_intake.line import verify_line_signature
from order_intake.service import ConflictError, OrderIntakeService


class ServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = OrderIntakeService(root / "test.sqlite3", root / "exports")
        self.service.seed_demo(reset=True)
        self.root = root

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_clean_order_is_structured_and_idempotent(self) -> None:
        payload = dict(
            merchant_id="demo",
            customer_ref="C-001",
            text="พี่เอาน้ำแดง 2 ลัง กะมาม่าต้มยำน้ำข้น 3 แพ๊ค ส่งพรุ่งนี้เช้าน้า",
            idempotency_key="evt-clean-1",
        )
        first = self.service.create_from_message(**payload)
        second = self.service.create_from_message(**payload)

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["status"], "awaiting_customer_confirmation")
        self.assertEqual(first["workflow"]["automation_mode"], "automatic")
        self.assertEqual(first["outbound_messages"][0]["message_type"], "customer_confirmation")
        self.assertEqual([item["sku"] for item in first["items"]], ["DRK-RED-710", "NDL-MAMA-TOM"])
        self.assertEqual(first["total"], "1056.00")

    def test_shortage_requires_explicit_exception_confirmation(self) -> None:
        draft = self.service.create_from_message(
            merchant_id="demo",
            customer_ref="C-001",
            text="ขอ M150 100 ลัง",
            idempotency_key="evt-short-1",
        )
        self.assertEqual(draft["status"], "needs_review")
        self.assertIn("สต็อกไม่พอ", draft["exception_reasons"][0])
        with self.assertRaises(ConflictError):
            self.service.approve_review(draft["id"], reviewer="owner")

        approved = self.service.approve_review(
            draft["id"], reviewer="owner", confirm_exceptions=True, note="ลูกค้ายืนยันรอของ"
        )
        self.assertEqual(approved["status"], "awaiting_customer_confirmation")
        self.assertEqual(approved["workflow"]["automation_mode"], "human_review")

    def test_customer_confirmation_runs_full_dry_run_order_to_cash(self) -> None:
        draft = self.service.create_from_message(
            merchant_id="demo",
            customer_ref="C-002",
            text="น้ำดื่มขวดเล็ก 12 ลัง",
            idempotency_key="evt-approve-1",
        )
        self.assertEqual(draft["status"], "awaiting_customer_confirmation")
        reserved = self.service.customer_confirmation(draft["id"], confirmed=True)
        self.assertEqual(reserved["status"], "reserved_for_pick")
        self.assertTrue(Path(reserved["erpclaw"]["plan"]).exists())
        self.assertTrue(reserved["workflow"]["erpclaw_sales_order_id"].startswith("dry-so-"))

        delivered = self.service.complete_delivery(draft["id"])
        self.assertEqual(delivered["status"], "awaiting_payment")
        self.assertTrue(delivered["workflow"]["erpclaw_sales_invoice_id"].startswith("dry-inv-"))

        paid = self.service.record_payment(draft["id"], reference="PAY-001")
        self.assertEqual(paid["status"], "paid")
        self.assertTrue(paid["workflow"]["erpclaw_payment_id"].startswith("dry-pay-"))
        self.assertEqual(paid["outbound_messages"][-1]["message_type"], "payment_received_invoice")

    def test_catalog_csv_detects_columns(self) -> None:
        csv_data = "รายงานสินค้า,,,\nรหัสสินค้า,ชื่อสินค้า,หน่วยนับ,ราคาขาย,คงเหลือ\nX-1,ปลากระป๋อง,ลัง,850 บาท,15\n".encode("utf-8-sig")
        result = self.service.import_catalog("demo", "catalog.csv", csv_data)
        self.assertEqual(result["product_count"], 1)
        draft = self.service.create_from_message(
            merchant_id="demo",
            customer_ref="C-001",
            text="ขอปลากระป๋อง 2 ลัง",
            idempotency_key="evt-import-1",
        )
        self.assertEqual(draft["items"][0]["sku"], "X-1")

    def test_erpclaw_sync_replaces_demo_inventory_source(self) -> None:
        initial = self.service.catalog("demo")
        self.assertEqual(initial["source"], "demo")
        self.assertIn("DRK-M150", {product["sku"] for product in initial["products"]})

        result = self.service.sync_erpclaw_catalog(
            "demo",
            [
                {
                    "erpclaw_item_id": "erp-item-water",
                    "sku": "ERP-WATER",
                    "name": "น้ำดื่ม ERPClaw",
                    "uom": "ลัง",
                    "price": "110.00",
                    "stock": "25.00",
                    "item_group": "Finished Goods",
                }
            ],
        )
        self.assertEqual(result["source"], "erpclaw")
        synced = self.service.catalog("demo")
        self.assertEqual(synced["source"], "erpclaw")
        self.assertEqual([product["sku"] for product in synced["products"]], ["ERP-WATER"])
        self.assertEqual(synced["products"][0]["stock"], "25.00")

    def test_line_signature_uses_raw_body(self) -> None:
        body = b'{"events":[]}'
        secret = "line-secret"
        signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        self.assertTrue(verify_line_signature(body, signature, secret))
        self.assertFalse(verify_line_signature(body + b" ", signature, secret))


if __name__ == "__main__":
    unittest.main()
