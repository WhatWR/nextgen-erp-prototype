"""Tool registry for the LINE AI assistant.

Every tool is a thin wrapper over a ``nextgen_erp`` whitelisted method. The
security contract: handlers take the **verified** LINE sender id and webhook
event id from :class:`ToolContext` (set by our code), never from model output,
so a prompt-injected model cannot reach another customer's data or send to a
different recipient. Tool failures are returned as data for the model to
apologise with — they never abort the answer loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable

from ..erpnext_client import ERPNextClient, ERPNextError
from .rag import KnowledgeIndex, _ngrams

CATALOG_PAGE_SIZE = 500
CATALOG_MAX_PAGES = 2
ITEM_RESULT_LIMIT = 8
GENERIC_RECOMMENDATION_LIMIT = 3
CONFIRM_EVENT_SUFFIX = ":ai-confirm"

_CATALOG_QUERY_WORDS = (
    "ราคา",
    "เท่าไหร่",
    "กี่บาท",
    "มีของไหม",
    "มีไหม",
    "สต็อก",
    "stock",
    "สินค้า",
    "ขายอะไร",
    "มีอะไรบ้าง",
    "ครับ",
    "ค่ะ",
    "คะ",
)


@dataclass
class ToolContext:
    client: ERPNextClient
    line_id: str
    event_id: str
    warehouse: str | None = None
    knowledge: KnowledgeIndex | None = None
    knowledge_embedder: Any = None  # Embedder | None; typed loosely to avoid an import cycle
    knowledge_embed_key: str = ""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., dict[str, Any]]


_NO_ARGS = {"type": "object", "properties": {}, "required": []}


def _order_arg(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"order": {"type": "string", "description": description}},
        "required": ["order"],
    }


def _score_item(query: str, row: dict[str, Any]) -> float:
    target = " ".join(
        [str(row.get("item_code") or ""), str(row.get("item_name") or "")]
        + [str(alias) for alias in (row.get("aliases") or [])]
    )
    query_grams = _ngrams(query)
    if not query_grams:
        return 0.0
    return len(query_grams & _ngrams(target)) / len(query_grams)


def _clean_item_query(query: str) -> str:
    """Remove question words so a Thai price question ranks the product itself."""
    cleaned = (query or "").strip().lower()
    for word in _CATALOG_QUERY_WORDS:
        cleaned = cleaned.replace(word, " ")
    return re.sub(r"\s+", " ", cleaned).strip(" ?")


def build_tools(ctx: ToolContext) -> dict[str, ToolSpec]:
    def get_my_orders() -> dict[str, Any]:
        return ctx.client.call_method("nextgen_erp.ai.get_customer_context", line_id=ctx.line_id)

    def get_item_info(query: str = "") -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        start = 0
        for _ in range(CATALOG_MAX_PAGES):
            page = ctx.client.call_method(
                "nextgen_erp.api.get_catalog",
                warehouse=ctx.warehouse or "",
                limit_start=start,
                limit=CATALOG_PAGE_SIZE,
            )
            if not isinstance(page, dict):
                break
            rows.extend(page.get("data") or [])
            if not page.get("has_more"):
                break
            start = int(page.get("next_start") or (start + CATALOG_PAGE_SIZE))
        cleaned_query = _clean_item_query(query)
        if cleaned_query:
            scored = sorted(
                ((_score_item(cleaned_query, row), row) for row in rows),
                key=lambda pair: pair[0],
                reverse=True,
            )
            matches = [row for score, row in scored if score > 0][:ITEM_RESULT_LIMIT]
        else:
            # A generic question such as "มีสินค้าอะไรบ้าง" should list the
            # live catalog, not return an empty result because there is no SKU
            # term to rank. "พร้อมขาย" means the live ERP price is positive
            # and, when a warehouse is configured, projected stock is positive.
            saleable_rows = [
                row
                for row in rows
                if float(row.get("price") or 0) > 0
                and (
                    row.get("projected_qty") is None
                    or float(row.get("projected_qty") or 0) > 0
                )
            ]
            matches = sorted(
                saleable_rows,
                key=lambda row: (
                    row.get("projected_qty") is None,
                    -(float(row.get("projected_qty") or 0)),
                    str(row.get("item_code") or ""),
                ),
            )[:GENERIC_RECOMMENDATION_LIMIT]
        return {
            "items": [
                {
                    "item_code": row.get("item_code"),
                    "item_name": row.get("item_name"),
                    "uom": row.get("stock_uom"),
                    "price": row.get("price"),
                    "available_qty": row.get("projected_qty"),
                    "warehouse": row.get("warehouse") or ctx.warehouse,
                    "route": row.get("route"),
                }
                for row in matches
            ],
            "catalog_count": len(rows) if cleaned_query else len(saleable_rows),
            "query": cleaned_query,
            "source": "ERPNext Item + Item Price + Bin",
        }

    def search_knowledge(question: str = "") -> dict[str, Any]:
        if ctx.knowledge is None:
            return {"results": []}
        return {
            "results": ctx.knowledge.search(
                question,
                embed=ctx.knowledge_embedder,
                embed_key=ctx.knowledge_embed_key,
            )
        }

    def resend_payment_request(order: str = "") -> dict[str, Any]:
        return ctx.client.call_method(
            "nextgen_erp.ai.resend_payment_request", name=order, line_id=ctx.line_id
        )

    def resend_delivery_note(order: str = "") -> dict[str, Any]:
        return ctx.client.call_method(
            "nextgen_erp.ai.resend_delivery_note", name=order, line_id=ctx.line_id
        )

    def confirm_order() -> dict[str, Any]:
        # Routes through the same status-machine-guarded reply handler as a
        # literal "ยืนยัน" message; a namespaced event id keeps receipts distinct.
        return ctx.client.call_method(
            "nextgen_erp.api.handle_line_reply",
            line_id=ctx.line_id,
            text="ยืนยัน",
            event_id=f"{ctx.event_id}{CONFIRM_EVENT_SUFFIX}",
        )

    specs = [
        ToolSpec(
            name="get_my_orders",
            description=(
                "Get this customer's recent orders with status, totals, and linked "
                "invoice/delivery-note numbers. Use before answering any order/payment/delivery "
                "status question."
            ),
            parameters=_NO_ARGS,
            handler=get_my_orders,
        ),
        ToolSpec(
            name="get_item_info",
            description=(
                "Search or list the live product catalog. Use an empty query when the customer "
                "asks what products are available. Returns real selling prices and, when a "
                "warehouse is configured, available stock. Prices MUST come from here."
            ),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "product name to search"}},
                "required": [],
            },
            handler=get_item_info,
        ),
        ToolSpec(
            name="search_knowledge",
            description=(
                "Search the merchant's FAQ/policy knowledge base (delivery times, payment "
                "methods, store policies). Use for general questions not about a specific order."
            ),
            parameters={
                "type": "object",
                "properties": {"question": {"type": "string", "description": "the customer question"}},
                "required": ["question"],
            },
            handler=search_knowledge,
        ),
        ToolSpec(
            name="resend_payment_request",
            description=(
                "Re-send the invoice link and PromptPay QR for one of THIS customer's orders that "
                "is awaiting payment. Use when the customer asks for the invoice or how to pay."
            ),
            parameters=_order_arg("the order/intake name from get_my_orders, e.g. AIO-0001"),
            handler=resend_payment_request,
        ),
        ToolSpec(
            name="resend_delivery_note",
            description=(
                "Send the delivery-note document link for one of THIS customer's delivered orders."
            ),
            parameters=_order_arg("the order/intake name from get_my_orders"),
            handler=resend_delivery_note,
        ),
        ToolSpec(
            name="confirm_order",
            description=(
                "Confirm this customer's order that is currently awaiting their confirmation. "
                "Use ONLY when the customer clearly expresses confirmation of their pending order."
            ),
            parameters=_NO_ARGS,
            handler=confirm_order,
        ),
    ]
    return {spec.name: spec for spec in specs}


def openai_tools(tools: dict[str, ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in tools.values()
    ]


def dispatch(tools: dict[str, ToolSpec], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one tool call; unknown tools and ERP rejections come back as data."""
    spec = tools.get(name)
    if spec is None:
        return {"error": "unknown_tool", "tool": name}
    allowed = set((spec.parameters.get("properties") or {}).keys())
    kwargs = {key: value for key, value in (arguments or {}).items() if key in allowed}
    try:
        result = spec.handler(**kwargs)
    except ERPNextError as exc:
        return {"error": "erp_rejected", "detail": str(exc)[:300]}
    return result if isinstance(result, dict) else {"result": result}
