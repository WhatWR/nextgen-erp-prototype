from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .adapters import ControlledWriteback
from .catalog import parse_catalog
from .db import Database
from .matching import ProductCandidate, normalize_thai, parse_order_lines


BANGKOK = ZoneInfo("Asia/Bangkok")
MONEY = Decimal("0.01")


def now_iso() -> str:
    return datetime.now(BANGKOK).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def decimal_text(value: Decimal, money: bool = False) -> str:
    if money:
        return str(value.quantize(MONEY, rounding=ROUND_HALF_UP))
    normalized = value.normalize()
    return format(normalized, "f")


class NotFoundError(ValueError):
    pass


class ConflictError(ValueError):
    pass


class ValidationError(ValueError):
    pass


class OrderIntakeService:
    def __init__(self, db_path: str | Path, export_dir: str | Path):
        self.db = Database(db_path)
        self.db.initialize()
        self.writeback = ControlledWriteback(export_dir)

    def seed_demo(self, reset: bool = False) -> dict[str, Any]:
        created_at = now_iso()
        products = [
            {
                "id": "prod_red_drink",
                "sku": "DRK-RED-710",
                "name": "น้ำแดงเฮลซ์บลูบอย 710 มล.",
                "aliases": ["น้ำแดง", "น้ำหวานแดง", "เฮลซ์บลูบอยแดง"],
                "uom": "ลัง",
                "price": "420.00",
                "stock": "40",
            },
            {
                "id": "prod_mama_tom",
                "sku": "NDL-MAMA-TOM",
                "name": "มาม่าต้มยำน้ำข้น",
                "aliases": ["มาม่าต้มยำ", "มาม่าน้ำข้น", "มาม่าต้มยำน้ำข้น"],
                "uom": "แพ็ก",
                "price": "72.00",
                "stock": "120",
            },
            {
                "id": "prod_m150",
                "sku": "DRK-M150",
                "name": "เครื่องดื่ม M-150",
                "aliases": ["m150", "m-150", "เอ็มร้อยห้าสิบ"],
                "uom": "ลัง",
                "price": "390.00",
                "stock": "12",
            },
            {
                "id": "prod_water_small",
                "sku": "WATER-600",
                "name": "น้ำดื่มขวดเล็ก 600 มล.",
                "aliases": ["น้ำขวดเล็ก", "น้ำดื่มขวดเล็ก", "น้ำเปล่าเล็ก"],
                "uom": "ลัง",
                "price": "105.00",
                "stock": "75",
            },
        ]
        with self.db.transaction() as conn:
            if reset:
                conn.execute("DELETE FROM merchant WHERE id = ?", ("demo",))
            conn.execute(
                """
                INSERT INTO merchant(id, name, currency, timezone, created_at)
                VALUES (?, ?, 'THB', 'Asia/Bangkok', ?)
                ON CONFLICT(id) DO UPDATE SET name = excluded.name
                """,
                ("demo", "สยามโฮลเซล เดโม", created_at),
            )
            conn.execute(
                """
                INSERT INTO customer(id, merchant_id, external_ref, name, aliases_json)
                VALUES (?, 'demo', ?, ?, ?)
                ON CONFLICT(merchant_id, external_ref) DO UPDATE SET
                    name = excluded.name, aliases_json = excluded.aliases_json
                """,
                (
                    "cust_charoen",
                    "C-001",
                    "ร้านเจริญพาณิชย์",
                    json.dumps(["ร้านเจริญ", "พี่เจริญ"], ensure_ascii=False),
                ),
            )
            conn.execute(
                """
                INSERT INTO customer(id, merchant_id, external_ref, name, aliases_json)
                VALUES (?, 'demo', ?, ?, ?)
                ON CONFLICT(merchant_id, external_ref) DO UPDATE SET
                    name = excluded.name, aliases_json = excluded.aliases_json
                """,
                (
                    "cust_bangna",
                    "C-002",
                    "ร้านบางนามาร์ท",
                    json.dumps(["บางนามาร์ท", "สาขาบางนา"], ensure_ascii=False),
                ),
            )
            for product in products:
                conn.execute(
                    """
                    INSERT INTO product(
                        id, merchant_id, sku, name, aliases_json, uom, price, stock_qty, active
                    ) VALUES (?, 'demo', ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(merchant_id, sku) DO UPDATE SET
                        name = excluded.name,
                        aliases_json = excluded.aliases_json,
                        uom = excluded.uom,
                        price = excluded.price,
                        stock_qty = excluded.stock_qty,
                        active = 1
                    """,
                    (
                        product["id"],
                        product["sku"],
                        product["name"],
                        json.dumps(product["aliases"], ensure_ascii=False),
                        product["uom"],
                        product["price"],
                        product["stock"],
                    ),
                )
            self._audit(
                conn,
                merchant_id="demo",
                event_type="demo_seeded",
                actor="system",
                details={"reset": reset, "product_count": len(products)},
            )
        return {"merchant_id": "demo", "products": len(products), "reset": reset}

    def create_from_message(
        self,
        *,
        merchant_id: str,
        text: str,
        idempotency_key: str,
        customer_ref: str | None = None,
        source_channel: str = "simulator",
    ) -> dict[str, Any]:
        if not text or not text.strip():
            raise ValidationError("text is required")
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValidationError("a valid idempotency_key is required")

        with self.db.transaction() as conn:
            merchant = conn.execute(
                "SELECT * FROM merchant WHERE id = ?", (merchant_id,)
            ).fetchone()
            if not merchant:
                raise NotFoundError(f"merchant '{merchant_id}' was not found")

            existing = conn.execute(
                "SELECT id FROM order_draft WHERE merchant_id = ? AND idempotency_key = ?",
                (merchant_id, idempotency_key),
            ).fetchone()
            if existing:
                result = self._serialize_draft(conn, existing["id"])
                result["created"] = False
                return result

            customer, customer_exception = self._resolve_customer(
                conn, merchant_id, customer_ref, text
            )
            product_rows = conn.execute(
                "SELECT * FROM product WHERE merchant_id = ? AND active = 1",
                (merchant_id,),
            ).fetchall()
            products = [ProductCandidate.from_row(dict(row)) for row in product_rows]
            parsed_lines = parse_order_lines(text, products)
            if not parsed_lines:
                raise ValidationError("no order lines could be identified")

            exception_reasons = [line.exception_reason for line in parsed_lines if line.exception_reason]
            if customer_exception:
                exception_reasons.insert(0, customer_exception)
            line_confidences = [line.confidence for line in parsed_lines]
            customer_confidence = 0.98 if customer else 0.60
            confidence = round(
                (sum(line_confidences) + customer_confidence) / (len(line_confidences) + 1), 4
            )
            status = "needs_review" if exception_reasons or confidence < 0.92 else "ready_for_review"
            draft_id = new_id("ord")
            timestamp = now_iso()

            total = Decimal("0")
            item_rows: list[tuple] = []
            for line in parsed_lines:
                price = line.product.price if line.product else Decimal("0")
                line_total = price * line.quantity
                total += line_total
                item_rows.append(
                    (
                        new_id("line"),
                        draft_id,
                        line.raw_text,
                        line.product.id if line.product else None,
                        line.product.sku if line.product else None,
                        line.product.name if line.product else None,
                        decimal_text(line.quantity),
                        line.uom,
                        decimal_text(price, money=True),
                        decimal_text(line_total, money=True),
                        line.confidence,
                        line.exception_reason,
                    )
                )

            conn.execute(
                """
                INSERT INTO order_draft(
                    id, merchant_id, customer_id, customer_ref, source_channel, source_text,
                    status, confidence, total, exception_reasons_json, idempotency_key,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    merchant_id,
                    customer["id"] if customer else None,
                    customer["external_ref"] if customer else customer_ref,
                    source_channel,
                    text.strip(),
                    status,
                    confidence,
                    decimal_text(total, money=True),
                    json.dumps(exception_reasons, ensure_ascii=False),
                    idempotency_key,
                    timestamp,
                    timestamp,
                ),
            )
            conn.executemany(
                """
                INSERT INTO order_item(
                    id, draft_id, raw_text, product_id, sku, product_name, quantity, uom,
                    unit_price, line_total, confidence, exception_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                item_rows,
            )
            self._audit(
                conn,
                merchant_id=merchant_id,
                draft_id=draft_id,
                event_type="draft_created",
                actor=source_channel,
                details={"confidence": confidence, "exceptions": exception_reasons},
            )
            result = self._serialize_draft(conn, draft_id)
            result["created"] = True
            return result

    def import_catalog(self, merchant_id: str, filename: str, data: bytes) -> dict[str, Any]:
        products, metadata = parse_catalog(data, filename)
        if not products:
            raise ValidationError("catalog contains no usable product rows")
        with self.db.transaction() as conn:
            merchant = conn.execute("SELECT id FROM merchant WHERE id = ?", (merchant_id,)).fetchone()
            if not merchant:
                raise NotFoundError(f"merchant '{merchant_id}' was not found")
            for product in products:
                conn.execute(
                    """
                    INSERT INTO product(
                        id, merchant_id, sku, name, aliases_json, uom, price, stock_qty, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(merchant_id, sku) DO UPDATE SET
                        name = excluded.name,
                        aliases_json = excluded.aliases_json,
                        uom = excluded.uom,
                        price = excluded.price,
                        stock_qty = excluded.stock_qty,
                        active = 1
                    """,
                    (
                        new_id("prod"),
                        merchant_id,
                        product["sku"],
                        product["name"],
                        json.dumps(product["aliases"], ensure_ascii=False),
                        product["uom"],
                        product["price"],
                        product["stock"],
                    ),
                )
            self._audit(
                conn,
                merchant_id=merchant_id,
                event_type="catalog_imported",
                actor="admin",
                details={"filename": filename, **metadata},
            )
        return {"merchant_id": merchant_id, "filename": filename, **metadata}

    def list_reviews(
        self, merchant_id: str, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        with self.db.connect() as conn:
            params: list[Any] = [merchant_id]
            sql = "SELECT id FROM order_draft WHERE merchant_id = ?"
            if status:
                sql += " AND status = ?"
                params.append(status)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [self._serialize_draft(conn, row["id"]) for row in rows]

    def get_review(self, draft_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            return self._serialize_draft(conn, draft_id)

    def update_review(
        self,
        draft_id: str,
        *,
        reviewer: str,
        items: list[dict[str, Any]],
        note: str | None = None,
    ) -> dict[str, Any]:
        if not reviewer:
            raise ValidationError("reviewer is required")
        with self.db.transaction() as conn:
            draft = conn.execute("SELECT * FROM order_draft WHERE id = ?", (draft_id,)).fetchone()
            if not draft:
                raise NotFoundError(f"draft '{draft_id}' was not found")
            if draft["status"] in {"approved", "rejected"}:
                raise ConflictError("a completed review cannot be edited")

            for change in items:
                item_id = change.get("id")
                row = conn.execute(
                    "SELECT * FROM order_item WHERE id = ? AND draft_id = ?", (item_id, draft_id)
                ).fetchone()
                if not row:
                    raise NotFoundError(f"order item '{item_id}' was not found")
                quantity = Decimal(str(change.get("quantity", row["quantity"])))
                if quantity <= 0:
                    raise ValidationError("quantity must be greater than zero")
                unit_price = Decimal(str(change.get("unit_price", row["unit_price"])))
                if unit_price < 0:
                    raise ValidationError("unit_price cannot be negative")
                conn.execute(
                    """
                    UPDATE order_item
                    SET quantity = ?, unit_price = ?, line_total = ?, confidence = 1.0,
                        exception_reason = NULL
                    WHERE id = ?
                    """,
                    (
                        decimal_text(quantity),
                        decimal_text(unit_price, money=True),
                        decimal_text(quantity * unit_price, money=True),
                        item_id,
                    ),
                )
            total = sum(
                (Decimal(row["line_total"]) for row in conn.execute(
                    "SELECT line_total FROM order_item WHERE draft_id = ?", (draft_id,)
                )),
                Decimal("0"),
            )
            timestamp = now_iso()
            conn.execute(
                """
                UPDATE order_draft
                SET status = 'ready_for_review', confidence = 1.0, total = ?,
                    exception_reasons_json = '[]', reviewer = ?, decision_note = ?, updated_at = ?
                WHERE id = ?
                """,
                (decimal_text(total, money=True), reviewer, note, timestamp, draft_id),
            )
            self._audit(
                conn,
                merchant_id=draft["merchant_id"],
                draft_id=draft_id,
                event_type="draft_corrected",
                actor=reviewer,
                details={"items_changed": len(items), "note": note},
            )
            return self._serialize_draft(conn, draft_id)

    def approve_review(
        self,
        draft_id: str,
        *,
        reviewer: str,
        note: str | None = None,
        confirm_exceptions: bool = False,
    ) -> dict[str, Any]:
        if not reviewer:
            raise ValidationError("reviewer is required")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM order_draft WHERE id = ?", (draft_id,)).fetchone()
            if not row:
                raise NotFoundError(f"draft '{draft_id}' was not found")
            if row["status"] == "approved":
                result = self._serialize_draft(conn, draft_id)
                result["writeback"] = {"already_approved": True}
                return result
            if row["status"] == "rejected":
                raise ConflictError("a rejected draft cannot be approved")
            exceptions = json.loads(row["exception_reasons_json"] or "[]")
            if exceptions and not confirm_exceptions:
                raise ConflictError("unresolved exceptions require confirm_exceptions=true")

            timestamp = now_iso()
            conn.execute(
                """
                UPDATE order_draft
                SET status = 'approved', reviewer = ?, decision_note = ?, updated_at = ?
                WHERE id = ?
                """,
                (reviewer, note, timestamp, draft_id),
            )
            draft = self._serialize_draft(conn, draft_id)
            writeback = self.writeback.write(draft)
            self._audit(
                conn,
                merchant_id=row["merchant_id"],
                draft_id=draft_id,
                event_type="draft_approved",
                actor=reviewer,
                details={"note": note, "writeback": writeback},
            )
            draft["writeback"] = writeback
            return draft

    def reject_review(
        self, draft_id: str, *, reviewer: str, note: str | None = None
    ) -> dict[str, Any]:
        if not reviewer:
            raise ValidationError("reviewer is required")
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM order_draft WHERE id = ?", (draft_id,)).fetchone()
            if not row:
                raise NotFoundError(f"draft '{draft_id}' was not found")
            if row["status"] == "approved":
                raise ConflictError("an approved draft cannot be rejected")
            timestamp = now_iso()
            conn.execute(
                """
                UPDATE order_draft
                SET status = 'rejected', reviewer = ?, decision_note = ?, updated_at = ?
                WHERE id = ?
                """,
                (reviewer, note, timestamp, draft_id),
            )
            self._audit(
                conn,
                merchant_id=row["merchant_id"],
                draft_id=draft_id,
                event_type="draft_rejected",
                actor=reviewer,
                details={"note": note},
            )
            return self._serialize_draft(conn, draft_id)

    def dashboard(self, merchant_id: str) -> dict[str, Any]:
        with self.db.connect() as conn:
            merchant = conn.execute("SELECT * FROM merchant WHERE id = ?", (merchant_id,)).fetchone()
            if not merchant:
                raise NotFoundError(f"merchant '{merchant_id}' was not found")
            counts = {
                row["status"]: row["count"]
                for row in conn.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM order_draft WHERE merchant_id = ? GROUP BY status
                    """,
                    (merchant_id,),
                )
            }
            summary = conn.execute(
                """
                SELECT COUNT(*) AS total_orders,
                       COALESCE(AVG(confidence), 0) AS avg_confidence,
                       COALESCE(SUM(CAST(total AS NUMERIC)), 0) AS gross_value
                FROM order_draft WHERE merchant_id = ?
                """,
                (merchant_id,),
            ).fetchone()
            queue = counts.get("needs_review", 0) + counts.get("ready_for_review", 0)
            return {
                "merchant": dict(merchant),
                "metrics": {
                    "total_orders": summary["total_orders"],
                    "review_queue": queue,
                    "approved": counts.get("approved", 0),
                    "average_confidence": round(float(summary["avg_confidence"]), 4),
                    "gross_value": decimal_text(Decimal(str(summary["gross_value"])), money=True),
                },
                "status_counts": counts,
            }

    def list_audit(self, merchant_id: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM audit_event WHERE merchant_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (merchant_id, limit),
            ).fetchall()
            return [
                {**dict(row), "details": json.loads(row["details_json"] or "{}")}
                for row in rows
            ]

    def _resolve_customer(self, conn, merchant_id: str, customer_ref: str | None, text: str):
        if customer_ref:
            row = conn.execute(
                "SELECT * FROM customer WHERE merchant_id = ? AND external_ref = ?",
                (merchant_id, customer_ref),
            ).fetchone()
            if row:
                return row, None
            return None, f"ไม่พบลูกค้ารหัส {customer_ref}"

        normalized_text = normalize_thai(text)
        for row in conn.execute("SELECT * FROM customer WHERE merchant_id = ?", (merchant_id,)):
            terms = [row["name"], *json.loads(row["aliases_json"] or "[]")]
            if any(normalize_thai(term) in normalized_text for term in terms):
                return row, None
        return None, "ยังไม่ยืนยันลูกค้า"

    def _serialize_draft(self, conn, draft_id: str) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT d.*, c.name AS customer_name
            FROM order_draft d LEFT JOIN customer c ON c.id = d.customer_id
            WHERE d.id = ?
            """,
            (draft_id,),
        ).fetchone()
        if not row:
            raise NotFoundError(f"draft '{draft_id}' was not found")
        items = [
            dict(item)
            for item in conn.execute(
                "SELECT * FROM order_item WHERE draft_id = ? ORDER BY rowid", (draft_id,)
            ).fetchall()
        ]
        result = dict(row)
        result["exception_reasons"] = json.loads(result.pop("exception_reasons_json") or "[]")
        result["items"] = items
        return result

    def _audit(
        self,
        conn,
        *,
        merchant_id: str,
        event_type: str,
        actor: str,
        details: dict[str, Any],
        draft_id: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO audit_event(id, merchant_id, draft_id, event_type, actor, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("audit"),
                merchant_id,
                draft_id,
                event_type,
                actor,
                json.dumps(details, ensure_ascii=False),
                now_iso(),
            ),
        )
