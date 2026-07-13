from __future__ import annotations

import json
import unittest

from order_intake.erpnext_bridge import CREATE_METHOD, intake_from_message
from order_intake.erpnext_client import ERPNextClient


class RecordingTransport:
    """Fake ERPNext transport driven by a URL-substring -> payload map."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[dict] = []

    def __call__(self, url, method, headers, body):
        self.calls.append({"url": url, "method": method, "body": json.loads(body.decode()) if body else None})
        for fragment, message in self.routes.items():
            if fragment in url:
                return 200, json.dumps({"message": message, "data": message}).encode("utf-8")
        raise AssertionError(f"unexpected request to {url}")


class ERPNextBridgeTest(unittest.TestCase):
    def _client(self):
        transport = RecordingTransport(
            {
                "/api/method/nextgen_erp.api.get_automation_settings": {"confidence_threshold": 0.95},
                "/api/method/nextgen_erp.api.get_catalog": {"data": [
                    {"item_code": "NDL-MAMA-TOM", "item_name": "มาม่าต้มยำน้ำข้น", "stock_uom": "แพ็ก", "price": 72, "projected_qty": 120, "aliases": [], "item_group": "Products"},
                    {"item_code": "DRK-M150", "item_name": "เครื่องดื่ม M-150", "stock_uom": "ลัง", "price": 390, "projected_qty": 120, "aliases": ["เอ็มร้อยห้าสิบ"], "item_group": "Products"},
                ], "has_more": False, "next_start": 2},
                "/api/method/nextgen_erp.api.create_ai_order_intake": {"name": "AIO-00001", "created": True, "status": "Needs Review"},
            }
        )
        return ERPNextClient("https://erp.example", "k", "s", transport=transport), transport

    def test_matches_item_and_pushes_intake_to_erpnext(self) -> None:
        client, transport = self._client()
        result = intake_from_message(
            "มาม่าต้มยำ 3 แพ็ก",
            customer="ร้านเจริญพาณิชย์",
            idempotency_key="t-1",
            client=client,
            warehouse="Stores - NG",
        )

        self.assertEqual(result["erpnext"]["name"], "AIO-00001")
        # The create method was called with the extracted payload.
        create = [c for c in transport.calls if CREATE_METHOD in c["url"]][0]
        payload = create["body"]["payload"]
        self.assertEqual(payload["customer"], "ร้านเจริญพาณิชย์")
        self.assertEqual(payload["idempotency_key"], "t-1")
        line = payload["items"][0]
        self.assertEqual(line["item_code"], "NDL-MAMA-TOM")
        self.assertEqual(line["qty"], 3.0)
        self.assertEqual(line["rate"], 72.0)

    def test_unmatched_line_flags_exception_and_needs_review(self) -> None:
        client, _ = self._client()
        result = intake_from_message(
            "ขอสินค้าที่ไม่มีในระบบ 5 ลัง",
            customer=None,
            idempotency_key="t-2",
            client=client,
            warehouse="Stores - NG",
        )
        payload = result["payload"]
        self.assertEqual(payload["automation_mode"], "human_review")
        self.assertTrue(payload["exception_reasons"])


if __name__ == "__main__":
    unittest.main()
