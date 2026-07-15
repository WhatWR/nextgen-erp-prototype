"""Bridge the Thai extraction pipeline to ERPNext as the system of record.

This is the Phase 2 repoint: instead of persisting an order draft in the local
SQLite mirror, the external service reads its catalog from ERPNext, runs the
existing Thai matcher (:mod:`order_intake.matching`) against real ERPNext Items,
and pushes an ``AI Order Intake`` record through the ``nextgen_erp`` app's
whitelisted method. Review, approval and order-to-cash then happen in ERPNext's
Desk UI — this service holds no business tables.

CLI smoke test::

    ERPNEXT_URL=http://127.0.0.1:8000 \
    ERPNEXT_API_KEY=... ERPNEXT_API_SECRET=... ERPNEXT_WAREHOUSE='Stores - NG' \
    python -m order_intake.erpnext_bridge --customer 'ร้านเจริญพาณิชย์' \
        --key demo-1 'พี่เอาน้ำแดง 2 ลัง กับมาม่าต้มยำ 3 แพ็ก'
"""

from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal

from .erpnext_adapter import ERPNextCatalogSource
from .erpnext_client import ERPNextClient
from .matching import ParsedLine, ProductCandidate, has_quantity_signal, parse_order_lines

CREATE_METHOD = "nextgen_erp.api.create_ai_order_intake"


def _candidates(source: ERPNextCatalogSource) -> list[ProductCandidate]:
    candidates = []
    for p in source.fetch_catalog():
        candidates.append(
            ProductCandidate.from_row(
                {
                    "id": p["erpnext_item_code"],
                    "sku": p["sku"],
                    "name": p["name"],
                    "aliases_json": json.dumps(p.get("aliases") or [], ensure_ascii=False),
                    "uom": p["uom"],
                    "price": p["price"],
                    "stock_qty": p["stock"],
                }
            )
        )
    return candidates


def parse_with_catalog(
    text: str,
    *,
    client: ERPNextClient | None = None,
    warehouse: str | None = None,
    price_list: str | None = None,
) -> list[ParsedLine]:
    """Run the Thai matcher against the live ERPNext catalog."""
    client = client or ERPNextClient()
    source = ERPNextCatalogSource(client, warehouse_id=warehouse, price_list=price_list)
    return parse_order_lines(text, _candidates(source))


def looks_like_question(text: str) -> bool:
    """A message without an explicit quantity + recognised unit is not an order.

    Mentioning a product name is NOT enough — "M-150 ราคาเท่าไหร่" is a price
    question even though the product resolves. Only an explicit quantity+unit
    ("2 ลัง") makes a message order-shaped; order-shaped messages with an
    unknown item keep the Needs Review path so staff can rescue real orders.
    """
    if not (text or "").strip():
        return False
    return not has_quantity_signal(text)


def build_payload(
    text: str,
    parsed: list[ParsedLine],
    *,
    customer: str | None,
    idempotency_key: str,
    line_ref: str | None,
    source_channel: str,
    confidence_threshold: float,
) -> dict:
    exceptions = [line.exception_reason for line in parsed if line.exception_reason]
    line_confidences = [line.confidence for line in parsed]
    customer_confidence = 0.98 if customer else 0.60
    confidence = round(
        (sum(line_confidences) + customer_confidence) / (len(line_confidences) + 1), 4
    ) if line_confidences else customer_confidence

    items = []
    total = Decimal("0")
    for line in parsed:
        price = line.product.price if line.product else Decimal("0")
        amount = price * line.quantity
        total += amount
        items.append(
            {
                "raw_text": line.raw_text,
                "item_code": line.product.sku if line.product else None,
                "qty": float(line.quantity),
                "uom": line.uom,
                "rate": float(price),
                "amount": float(amount),
                "confidence": line.confidence,
                "exception_reason": line.exception_reason,
            }
        )

    auto = bool(customer and not exceptions and confidence >= confidence_threshold)
    return {
        "idempotency_key": idempotency_key,
        "merchant": "demo",
        "line_ref": line_ref,
        "customer": customer,
        "source_channel": source_channel,
        "source_text": text.strip(),
        "confidence": confidence,
        "total": float(total),
        "automation_mode": "automatic" if auto else "human_review",
        "exception_reasons": exceptions,
        "items": items,
    }


def intake_from_message(
    text: str,
    *,
    customer: str | None,
    idempotency_key: str,
    line_ref: str | None = None,
    source_channel: str = "line",
    client: ERPNextClient | None = None,
    warehouse: str | None = None,
    price_list: str | None = None,
    confidence_threshold: float | None = None,
    parsed: list[ParsedLine] | None = None,
) -> dict:
    client = client or ERPNextClient()
    if confidence_threshold is None:
        settings = client.call_method("nextgen_erp.api.get_automation_settings")
        threshold = float(
            settings.get("confidence_threshold", 0.95)
            if isinstance(settings, dict)
            else os.environ.get("ORDER_AUTO_CONFIDENCE", "0.95")
        )
    else:
        threshold = confidence_threshold
    if parsed is None:
        parsed = parse_with_catalog(text, client=client, warehouse=warehouse, price_list=price_list)
    if not parsed:
        raise ValueError("no order lines could be identified")
    payload = build_payload(
        text,
        parsed,
        customer=customer,
        idempotency_key=idempotency_key,
        line_ref=line_ref,
        source_channel=source_channel,
        confidence_threshold=threshold,
    )
    message = client.call_method(CREATE_METHOD, payload=payload)
    return {"payload": payload, "erpnext": message}


def main() -> None:
    parser = argparse.ArgumentParser(description="Push a Thai order message into ERPNext")
    parser.add_argument("text")
    parser.add_argument("--customer", default=None)
    parser.add_argument("--key", required=True, help="idempotency key")
    parser.add_argument("--line-ref", default=None)
    parser.add_argument("--source-channel", default="line")
    parser.add_argument("--warehouse", default=os.environ.get("ERPNEXT_WAREHOUSE"))
    args = parser.parse_args()
    result = intake_from_message(
        args.text,
        customer=args.customer,
        idempotency_key=args.key,
        line_ref=args.line_ref,
        source_channel=args.source_channel,
        warehouse=args.warehouse,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
