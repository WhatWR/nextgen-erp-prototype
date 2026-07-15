from __future__ import annotations

import unittest

from order_intake.ai.tools import ToolContext, build_tools, dispatch
from order_intake.erpnext_client import ERPNextError


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
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def make_ctx(client, **overrides):
    defaults = dict(client=client, line_id="Uowner", event_id="evt-9", warehouse="Stores - NG")
    defaults.update(overrides)
    return ToolContext(**defaults)


class ToolScopingTest(unittest.TestCase):
    def test_model_cannot_override_the_verified_line_id(self):
        client = FakeClient({"nextgen_erp.ai.resend_payment_request": [{"sent": True}]})
        tools = build_tools(make_ctx(client))
        # A prompt-injected model tries to smuggle another customer's id.
        result = dispatch(
            tools,
            "resend_payment_request",
            {"order": "AIO-1", "line_id": "Uattacker", "recipient": "Uattacker"},
        )
        self.assertEqual(result, {"sent": True})
        method, kwargs = client.calls[0]
        self.assertEqual(method, "nextgen_erp.ai.resend_payment_request")
        self.assertEqual(kwargs["line_id"], "Uowner")

    def test_erp_rejection_comes_back_as_tool_data_not_an_exception(self):
        client = FakeClient(
            {
                "nextgen_erp.ai.resend_payment_request": [
                    ERPNextError("HTTP 403: does not belong to this LINE customer")
                ]
            }
        )
        tools = build_tools(make_ctx(client))
        result = dispatch(tools, "resend_payment_request", {"order": "AIO-999"})
        self.assertEqual(result["error"], "erp_rejected")

    def test_unknown_tool_is_reported_as_data(self):
        tools = build_tools(make_ctx(FakeClient({})))
        self.assertEqual(
            dispatch(tools, "drop_database", {})["error"], "unknown_tool"
        )

    def test_confirm_order_uses_the_guarded_reply_handler_with_namespaced_event(self):
        client = FakeClient(
            {"nextgen_erp.api.handle_line_reply": [{"handled": True, "name": "AIO-1"}]}
        )
        tools = build_tools(make_ctx(client))
        result = dispatch(tools, "confirm_order", {})
        self.assertTrue(result["handled"])
        method, kwargs = client.calls[0]
        self.assertEqual(method, "nextgen_erp.api.handle_line_reply")
        self.assertEqual(kwargs["line_id"], "Uowner")
        self.assertEqual(kwargs["text"], "ยืนยัน")
        self.assertEqual(kwargs["event_id"], "evt-9:ai-confirm")


class CatalogToolTest(unittest.TestCase):
    def test_item_search_returns_live_price_and_stock(self):
        client = FakeClient(
            {
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
                            },
                            {
                                "item_code": "NDL-TY",
                                "item_name": "มาม่าต้มยำ",
                                "stock_uom": "แพ็ก",
                                "price": 55,
                                "projected_qty": 12,
                                "aliases": [],
                            },
                        ],
                        "has_more": False,
                        "next_start": 2,
                    }
                ]
            }
        )
        tools = build_tools(make_ctx(client))
        result = dispatch(tools, "get_item_info", {"query": "มาม่าต้มยำ"})
        self.assertEqual(result["items"][0]["item_code"], "NDL-TY")
        self.assertEqual(result["items"][0]["price"], 55)

    def test_missing_warehouse_is_a_soft_error(self):
        tools = build_tools(make_ctx(FakeClient({}), warehouse=None))
        result = dispatch(tools, "get_item_info", {"query": "มาม่า"})
        self.assertEqual(result["error"], "catalog_unavailable")


if __name__ == "__main__":
    unittest.main()
