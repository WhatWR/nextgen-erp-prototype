from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from order_intake.erpclaw_integration import ERPClawIntegrationStore


class ERPClawIntegrationTest(unittest.TestCase):
    def test_fetch_catalog_uses_erpclaw_item_and_projected_stock_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "data.sqlite"
            db_path.touch()
            store = ERPClawIntegrationStore(root / "connection.json", root)
            store.save(
                {
                    "erpclaw_root": str(root),
                    "db_path": str(db_path),
                    "company_id": "COMP-1",
                    "warehouse_id": "WH-1",
                }
            )
            with patch.object(
                store,
                "_run",
                side_effect=[
                    {
                        "items": [
                            {
                                "id": "ITEM-1",
                                "item_code": "M150",
                                "item_name": "เครื่องดื่ม M-150",
                                "stock_uom": "ลัง",
                                "standard_rate": "390.00",
                                "item_group_name": "Drinks",
                                "status": "active",
                            }
                        ]
                    },
                    {"projected_qty": "12.00"},
                ],
            ) as run:
                products = store.fetch_catalog()

            self.assertEqual(products[0]["erpclaw_item_id"], "ITEM-1")
            self.assertEqual(products[0]["stock"], "12.00")
            self.assertEqual(run.call_args_list[0].args[1], "list-items")
            self.assertEqual(run.call_args_list[1].args[1], "get-projected-qty")


if __name__ == "__main__":
    unittest.main()
