from __future__ import annotations

import unittest

from order_intake.erpnext_line import ERPNextLineWorkflow


class FakeClient:
    configured = True

    def __init__(self, replies):
        self.replies = {name: list(values) for name, values in replies.items()}
        self.calls = []

    def call_method(self, method, **kwargs):
        self.calls.append((method, kwargs))
        values = self.replies.get(method)
        if not values:
            raise AssertionError(f"unexpected call: {method}")
        return values.pop(0)


class ERPNextLineWorkflowTest(unittest.TestCase):
    def test_image_is_forwarded_as_payment_slip(self):
        client = FakeClient(
            {
                "nextgen_erp.api.handle_line_payment_slip": [
                    {"handled": True, "name": "AIO-3", "status": "Payment Review"}
                ]
            }
        )
        result = ERPNextLineWorkflow(client).handle_attachment(
            line_id="U123",
            message_id="image-1",
            event_id="evt-image-1",
            content_type="image",
        )
        self.assertEqual(result["kind"], "payment_slip")
        self.assertEqual(result["name"], "AIO-3")
        self.assertEqual(client.calls[0][1]["message_id"], "image-1")

    def test_customer_confirmation_reply_never_becomes_a_new_order(self):
        client = FakeClient(
            {"nextgen_erp.api.handle_line_reply": [{"handled": True, "name": "AIO-1", "status": "Reserved"}]}
        )
        result = ERPNextLineWorkflow(client, warehouse="Stores - NG").handle_event(
            line_id="U123", text="ยืนยัน", event_id="evt-1"
        )
        self.assertEqual(result["kind"], "customer_reply")
        self.assertEqual(result["name"], "AIO-1")
        self.assertEqual(len(client.calls), 1)

    def test_new_line_order_resolves_customer_and_creates_erpnext_intake(self):
        client = FakeClient(
            {
                "nextgen_erp.api.handle_line_reply": [{"handled": False}],
                "nextgen_erp.api.resolve_line_customer": [{"customer": "ร้านเจริญพาณิชย์"}],
                "nextgen_erp.api.get_automation_settings": [{"confidence_threshold": 0.95}],
                "nextgen_erp.api.get_catalog": [
                    {
                        "data": [
                            {
                                "item_code": "DRK-M150",
                                "item_name": "เครื่องดื่ม M-150",
                                "stock_uom": "ลัง",
                                "price": 390,
                                "projected_qty": 20,
                                "aliases": ["เอ็มร้อยห้าสิบ"],
                                "item_group": "Drinks",
                            }
                        ],
                        "has_more": False,
                        "next_start": 1,
                    }
                ],
                "nextgen_erp.api.create_ai_order_intake": [
                    {"name": "AIO-2", "created": True, "status": "Awaiting Customer"}
                ],
            }
        )
        result = ERPNextLineWorkflow(client, warehouse="Stores - NG").handle_event(
            line_id="U123", text="M-150 2 ลัง", event_id="evt-2"
        )
        self.assertEqual(result["kind"], "order_intake")
        self.assertEqual(result["name"], "AIO-2")
        self.assertEqual(result["customer"], "ร้านเจริญพาณิชย์")
        resolve = [call for call in client.calls if call[0] == "nextgen_erp.api.resolve_line_customer"][0]
        self.assertEqual(resolve[1]["create_if_missing"], 1)
        create = [call for call in client.calls if call[0] == "nextgen_erp.api.create_ai_order_intake"][0]
        self.assertEqual(create[1]["payload"]["idempotency_key"], "evt-2")
        self.assertEqual(create[1]["payload"]["line_ref"], "U123")


if __name__ == "__main__":
    unittest.main()
