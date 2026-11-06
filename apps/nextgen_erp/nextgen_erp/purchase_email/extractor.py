"""Extract purchase-order fields from an email body.

The MVP ships a deterministic regex/heuristic extractor so demos work without
an LLM key. When a live model is configured later, swap in ``extract_with_llm``
behind the same return shape — the pipeline never trusts model output for
prices or permissions without a human confirm.
"""

from __future__ import annotations

import json
import re
from typing import Any


_VENDOR_RE = re.compile(
	r"(?im)^\s*(?:vendor|supplier|ผู้ขาย|บริษัท)\s*[:：\-]\s*(.+?)\s*$"
)
_PO_RE = re.compile(
	r"(?im)^\s*(?:po(?:\s*reference)?|quote|ใบสั่งซื้อ|อ้างอิง)\s*[:：\-]\s*(.+?)\s*$"
)
_CURRENCY_RE = re.compile(r"(?im)^\s*currency\s*[:：\-]\s*([A-Z]{3})\s*$")
_TOTAL_RE = re.compile(
	r"(?im)(?:total|grand\s*total|มูลค่ารวม|รวม)\s*[:：]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([A-Z]{3})?"
)
_LINE_RE = re.compile(
	r"(?im)^\s*[-*•]?\s*(?:SKU\s*[:：]\s*)?([A-Z0-9][A-Z0-9\-_/]+)\s*[|–\-]\s*(.+?)\s*[|–\-]\s*"
	r"(?:Qty|จำนวน)\s*([0-9]+(?:\.[0-9]+)?)\s*[|–\-]\s*(?:Rate|ราคา)\s*([0-9]+(?:\.[0-9]+)?)\s*$"
)


def _money(raw: str | None) -> float:
	if not raw:
		return 0.0
	return float(str(raw).replace(",", "").strip() or 0)


def extract_order_fields(subject: str, body: str) -> dict[str, Any]:
	"""Return a draft purchase-order payload from email text."""
	text = body or ""
	vendor = ""
	po_reference = ""
	currency = "THB"
	items: list[dict[str, Any]] = []

	match = _VENDOR_RE.search(text)
	if match:
		vendor = match.group(1).strip()

	match = _PO_RE.search(text)
	if match:
		po_reference = match.group(1).strip()

	match = _CURRENCY_RE.search(text)
	if match:
		currency = match.group(1).upper()

	for line in _LINE_RE.finditer(text):
		sku, name, qty_raw, rate_raw = line.groups()
		qty = float(qty_raw)
		rate = float(rate_raw)
		items.append(
			{
				"sku": sku.strip(),
				"item_name": name.strip(),
				"qty": qty,
				"uom": "Nos",
				"rate": rate,
				"amount": round(qty * rate, 2),
			}
		)

	total = 0.0
	total_match = _TOTAL_RE.search(text)
	if total_match:
		total = _money(total_match.group(1))
		if total_match.group(2):
			currency = total_match.group(2).upper()
	elif items:
		total = round(sum(float(row["amount"]) for row in items), 2)

	confidence = 0.35
	if vendor:
		confidence += 0.2
	if po_reference:
		confidence += 0.15
	if items:
		confidence += 0.25
	if total > 0:
		confidence += 0.05
	confidence = min(round(confidence, 2), 0.99)

	draft = {
		"vendor_name": vendor,
		"po_reference": po_reference,
		"currency": currency,
		"total_amount": total,
		"items": items,
		"confidence": confidence,
		"subject": subject or "",
		"extractor": "heuristic-v1",
	}
	draft["extracted_json"] = json.dumps(draft, ensure_ascii=False, indent=2)
	return draft
