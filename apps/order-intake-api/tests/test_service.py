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
        self.assertEqual(first["status"], "ready_for_review")
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
        self.assertEqual(approved["status"], "approved")
        self.assertFalse(approved["writeback"]["erpclaw_executed"])

    def test_approval_creates_csv_and_dry_run_payload(self) -> None:
        draft = self.service.create_from_message(
            merchant_id="demo",
            customer_ref="C-002",
            text="น้ำดื่มขวดเล็ก 12 ลัง",
            idempotency_key="evt-approve-1",
        )
        approved = self.service.approve_review(draft["id"], reviewer="ops@example.com")
        self.assertTrue(Path(approved["writeback"]["csv"]).exists())
        self.assertTrue(Path(approved["writeback"]["erpclaw_dry_run"]).exists())

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

    def test_line_signature_uses_raw_body(self) -> None:
        body = b'{"events":[]}'
        secret = "line-secret"
        signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        self.assertTrue(verify_line_signature(body, signature, secret))
        self.assertFalse(verify_line_signature(body + b" ", signature, secret))


if __name__ == "__main__":
    unittest.main()
