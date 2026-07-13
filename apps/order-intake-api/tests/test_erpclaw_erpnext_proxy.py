from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "erpnext_proxy", ROOT / "vendor" / "erpclaw" / "mcp" / "erpnext_proxy.py"
)
proxy = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(proxy)


class ERPClawERPNextProxyTest(unittest.TestCase):
    def test_list_items_reads_erpnext_catalog(self):
        with patch.object(
            proxy,
            "_call",
            return_value={"data": [{"item_code": "M150"}], "has_more": False, "next_start": 1},
        ) as call:
            result = proxy.dispatch("list-items", {"warehouse_id": "Stores - NG"})
        self.assertEqual(result["backend"], "erpnext")
        self.assertEqual(result["data"][0]["item_code"], "M150")
        self.assertEqual(call.call_args.kwargs["warehouse"], "Stores - NG")

    def test_sales_order_write_requires_stable_idempotency_key(self):
        with self.assertRaises(proxy.ERPNextProxyError):
            proxy.dispatch(
                "add-sales-order",
                {"customer_id": "CUST-1", "company_id": "NextGen", "items": []},
            )

    def test_sales_order_write_maps_to_secured_erpnext_method(self):
        with patch.object(
            proxy,
            "_call",
            return_value={"sales_order": "SO-1", "pick_list": "PL-1"},
        ) as call:
            result = proxy.dispatch(
                "add-sales-order",
                {
                    "idempotency_key": "chat-1",
                    "customer_id": "CUST-1",
                    "company_id": "NextGen",
                    "items": [{"item_code": "M150", "qty": 2}],
                },
            )
        self.assertEqual(result["sales_order_id"], "SO-1")
        self.assertEqual(call.call_args.args[0], "nextgen_erp.api.create_sales_order")
        self.assertEqual(call.call_args.kwargs["external_reference"], "chat-1")


if __name__ == "__main__":
    unittest.main()
