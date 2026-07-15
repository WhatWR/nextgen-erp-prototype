from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Iterable


THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
UNIT_ALIASES = {
    "แพ๊ค": "แพ็ก",
    "แพค": "แพ็ก",
    "pack": "แพ็ก",
    "ลัง": "ลัง",
    "กล่อง": "กล่อง",
    "ขวด": "ขวด",
    "ชิ้น": "ชิ้น",
    "ถุง": "ถุง",
    "กระสอบ": "กระสอบ",
    "โหล": "โหล",
}
FILLER_WORDS = (
    "พี่เอา",
    "ขอ",
    "เอา",
    "เพิ่ม",
    "ครับ",
    "ค่ะ",
    "คะ",
    "นะ",
    "น้า",
    "หน่อย",
    "ส่ง",
    "พรุ่งนี้",
    "วันนี้",
    "เหมือนเดิม",
)
NUMBER_UNIT_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<uom>ลัง|แพ็ก|แพ๊ค|แพค|pack|กล่อง|ขวด|ชิ้น|ถุง|กระสอบ|โหล)?",
    re.IGNORECASE,
)


def normalize_thai(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").translate(THAI_DIGITS).lower()
    text = text.replace("\u200b", "").replace("\ufeff", "")
    for source, target in UNIT_ALIASES.items():
        text = text.replace(source, target)
    text = re.sub(r"[^0-9a-zก-๙.\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class ProductCandidate:
    id: str
    sku: str
    name: str
    aliases: tuple[str, ...]
    uom: str
    price: Decimal
    stock_qty: Decimal

    @classmethod
    def from_row(cls, row: dict) -> "ProductCandidate":
        return cls(
            id=row["id"],
            sku=row["sku"],
            name=row["name"],
            aliases=tuple(json.loads(row.get("aliases_json") or "[]")),
            uom=row["uom"],
            price=Decimal(str(row["price"])),
            stock_qty=Decimal(str(row["stock_qty"])),
        )

    @property
    def terms(self) -> tuple[str, ...]:
        unique = {normalize_thai(self.name), normalize_thai(self.sku)}
        unique.update(normalize_thai(alias) for alias in self.aliases)
        return tuple(sorted((term for term in unique if term), key=len, reverse=True))


@dataclass(frozen=True)
class ParsedLine:
    raw_text: str
    product: ProductCandidate | None
    quantity: Decimal
    uom: str | None
    confidence: float
    exception_reason: str | None


def _candidate_score(segment: str, product: ProductCandidate) -> tuple[float, str]:
    normalized = normalize_thai(segment)
    best_score = 0.0
    best_term = product.name
    for term in product.terms:
        if term in normalized:
            score = min(0.99, 0.93 + min(len(term), 12) / 200)
        else:
            reduced = normalized
            for filler in FILLER_WORDS:
                reduced = reduced.replace(filler, " ")
            reduced = NUMBER_UNIT_RE.sub(" ", reduced)
            reduced = re.sub(r"\s+", " ", reduced).strip()
            score = SequenceMatcher(None, reduced, term).ratio()
        if score > best_score:
            best_score, best_term = score, term
    return best_score, best_term


def has_quantity_signal(text: str) -> bool:
    """True when the message contains an explicit quantity+unit (order-shaped)."""
    normalized = normalize_thai(text)
    return any(match.group("uom") for match in NUMBER_UNIT_RE.finditer(normalized))


def _segments(text: str) -> list[str]:
    normalized = normalize_thai(text)
    boundaries = [0]
    for match in NUMBER_UNIT_RE.finditer(normalized):
        if match.group("uom"):
            boundaries.append(match.end())
    if len(boundaries) == 1:
        return [normalized]
    result: list[str] = []
    start = 0
    for end in boundaries[1:]:
        segment = normalized[start:end].strip(" ,")
        if segment:
            result.append(segment)
        start = end
    tail = normalized[start:].strip(" ,")
    if tail and not all(word in tail for word in ("ส่ง",)):
        result.append(tail)
    return result


def parse_order_lines(text: str, products: Iterable[ProductCandidate]) -> list[ParsedLine]:
    catalog = list(products)
    parsed: list[ParsedLine] = []
    used_products: set[str] = set()

    for segment in _segments(text):
        quantity_match = None
        for match in NUMBER_UNIT_RE.finditer(segment):
            if match.group("uom"):
                quantity_match = match
        quantity = Decimal(quantity_match.group("qty")) if quantity_match else Decimal("1")
        raw_uom = quantity_match.group("uom") if quantity_match else None
        uom = UNIT_ALIASES.get(raw_uom or "", raw_uom)

        ranked = sorted(
            ((_candidate_score(segment, product)[0], product) for product in catalog),
            key=lambda item: item[0],
            reverse=True,
        )
        best_score, best = ranked[0] if ranked else (0.0, None)
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        exception = None

        if best is None or best_score < 0.58:
            best = None
            exception = "ไม่พบสินค้าที่ตรงกับข้อความ"
        elif best.id in used_products:
            exception = "พบสินค้าซ้ำในข้อความ โปรดตรวจจำนวน"
        elif best_score - runner_up < 0.06:
            exception = "ชื่อสินค้าใกล้เคียงกันหลายรายการ"
        elif quantity_match is None:
            exception = "ไม่พบจำนวนหรือหน่วย จึงตั้งค่าเริ่มต้นเป็น 1"
            best_score = min(best_score, 0.70)
        elif uom and normalize_thai(uom) != normalize_thai(best.uom):
            exception = f"หน่วยที่สั่ง ({uom}) ไม่ตรงกับหน่วยสินค้า ({best.uom})"
            best_score = min(best_score, 0.78)
        elif quantity > best.stock_qty:
            exception = f"สต็อกไม่พอ: มี {best.stock_qty.normalize()} {best.uom}"
            best_score = min(best_score, 0.80)

        if best:
            used_products.add(best.id)
        parsed.append(
            ParsedLine(
                raw_text=segment,
                product=best,
                quantity=quantity,
                uom=uom or (best.uom if best else None),
                confidence=round(float(best_score), 4),
                exception_reason=exception,
            )
        )

    return parsed
