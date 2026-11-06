"""Sample purchase-order emails for demo / sandbox sync.

No network or OAuth required. Keep payloads realistic enough that the
extractor and rules engine exercise the happy path, a flag, and a reject.
"""

from __future__ import annotations

from typing import Any


FIXTURE_EMAILS: list[dict[str, Any]] = [
	{
		"message_id": "mock-po-001@nextgen.local",
		"from_email": "orders@siam-hardware.co.th",
		"subject": "Purchase Order Confirm — PO-TH-24081",
		"received_at": "2026-11-05T09:14:00+07:00",
		"body": """เรียน ฝ่ายจัดซื้อ

ยืนยันใบสั่งซื้อตามรายละเอียดด้านล่าง

Vendor: Siam Hardware Co., Ltd.
PO Reference: PO-TH-24081
Currency: THB

Items:
- SKU: BOLT-M8-50 | Hex Bolt M8x50 | Qty 200 | Rate 2.50
- SKU: NUT-M8 | Hex Nut M8 | Qty 200 | Rate 0.80
- SKU: WASHER-M8 | Flat Washer M8 | Qty 400 | Rate 0.35

Total: 730.00 THB

ขอบคุณครับ
ฝ่ายขาย Siam Hardware
""",
	},
	{
		"message_id": "mock-po-002@nextgen.local",
		"from_email": "sales@bangkok-steel.example",
		"subject": "Quotation / PO request — rebar bundle",
		"received_at": "2026-11-05T11:42:00+07:00",
		"body": """Hello Purchase Team,

Please raise a PO for:

Vendor: Bangkok Steel Supply
PO Reference: QS-8891
Currency: THB

- SKU: REBAR-12MM | Deformed Bar 12mm | Qty 50 | Rate 18500
- SKU: WIRE-MESH-A | Wire Mesh Panel A | Qty 20 | Rate 2400

Grand total 973,000 THB

Regards,
Bangkok Steel
""",
	},
	{
		"message_id": "mock-po-003@nextgen.local",
		"from_email": "noreply@unknown-vendor.invalid",
		"subject": "Urgent order confirmation",
		"received_at": "2026-11-05T15:05:00+07:00",
		"body": """Urgent PO

Vendor: Unknown Dropship LLC
PO Reference: DROP-999
Currency: USD

- SKU: FAKE-SKU-99 | Mystery Part | Qty 1 | Rate 99999

Total 99999 USD
""",
	},
]


DEFAULT_RULES: list[dict[str, Any]] = [
	{
		"rule_name": "Vendor Allowlist",
		"priority": 10,
		"rule_type": "Vendor Allowlist",
		"action": "Reject",
		"value": "Siam Hardware Co., Ltd.\nBangkok Steel Supply\nICONHOME Demo Supplier",
		"message": "Vendor is not on the approved purchase list.",
	},
	{
		"rule_name": "Max Amount THB 100000",
		"priority": 20,
		"rule_type": "Amount Max",
		"action": "Flag",
		"value": "100000",
		"message": "Total exceeds the Excel threshold of 100,000 — needs manager review.",
	},
	{
		"rule_name": "Required Vendor",
		"priority": 30,
		"rule_type": "Required Field",
		"action": "Reject",
		"field_name": "vendor_name",
		"value": "",
		"message": "Vendor name is required.",
	},
	{
		"rule_name": "Required PO Reference",
		"priority": 40,
		"rule_type": "Required Field",
		"action": "Flag",
		"field_name": "po_reference",
		"value": "",
		"message": "Missing PO / quote reference.",
	},
	{
		"rule_name": "SKU Present",
		"priority": 50,
		"rule_type": "Required Field",
		"action": "Reject",
		"field_name": "items.sku",
		"value": "",
		"message": "Every line must include a SKU.",
	},
	{
		"rule_name": "Known SKU Catalog",
		"priority": 60,
		"rule_type": "SKU Allowlist",
		"action": "Flag",
		"value": "BOLT-M8-50\nNUT-M8\nWASHER-M8\nREBAR-12MM\nWIRE-MESH-A",
		"message": "SKU is not in the purchase catalog allowlist.",
	},
]


def list_fixture_emails() -> list[dict[str, Any]]:
	return [dict(row) for row in FIXTURE_EMAILS]
