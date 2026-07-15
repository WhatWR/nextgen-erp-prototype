"""Verified LINE event routing with ERPNext as the only system of record."""

from __future__ import annotations

from typing import Any

from .erpnext_bridge import intake_from_message, looks_like_question, parse_with_catalog
from .erpnext_client import ERPNextClient, ERPNextError


class ERPNextLineWorkflow:
    def __init__(
        self,
        client: ERPNextClient | None = None,
        *,
        warehouse: str | None = None,
        assistant=None,
    ):
        self.client = client or ERPNextClient()
        self.warehouse = warehouse
        # Optional order_intake.ai.AIAssistant; answers messages the Thai order
        # parser cannot read. When absent or disabled, behaviour is unchanged.
        self.assistant = assistant

    def handle_event(
        self,
        *,
        line_id: str,
        text: str,
        event_id: str,
    ) -> dict[str, Any]:
        if not self.client.configured:
            raise ERPNextError("ERPNext credentials are required for LINE order intake")

        reply = self.client.call_method(
            "nextgen_erp.api.handle_line_reply",
            line_id=line_id,
            text=text,
            event_id=event_id,
        )
        if isinstance(reply, dict) and reply.get("handled"):
            return {"kind": "customer_reply", **reply}

        mapping = self.client.call_method(
            "nextgen_erp.api.resolve_line_customer", line_id=line_id
        )
        customer = mapping.get("customer") if isinstance(mapping, dict) else None
        parsed = None
        if self.assistant is not None and self.assistant.enabled():
            parsed = parse_with_catalog(text, client=self.client, warehouse=self.warehouse)
            if looks_like_question(text, parsed):
                # No product resolved and no qty+unit signal — a question or
                # chitchat, not an order. Order-shaped messages (even with an
                # unknown item) keep the existing Needs Review intake path.
                return self.assistant.answer(
                    line_id=line_id, text=text, event_id=event_id, customer=customer
                )
        result = intake_from_message(
            text,
            customer=customer,
            idempotency_key=event_id,
            line_ref=line_id,
            source_channel="line",
            client=self.client,
            warehouse=self.warehouse,
            parsed=parsed,
        )
        erpnext = result.get("erpnext") or {}
        return {
            "kind": "order_intake",
            "name": erpnext.get("name"),
            "status": erpnext.get("status"),
            "created": erpnext.get("created"),
            "customer": customer,
        }

    def handle_attachment(
        self,
        *,
        line_id: str,
        message_id: str,
        event_id: str,
        content_type: str,
    ) -> dict[str, Any]:
        """Forward a LINE attachment reference; ERPNext owns token and file storage."""
        if not self.client.configured:
            raise ERPNextError("ERPNext credentials are required for LINE payment slips")
        result = self.client.call_method(
            "nextgen_erp.api.handle_line_payment_slip",
            line_id=line_id,
            message_id=message_id,
            event_id=event_id,
            content_type=content_type,
        )
        return {"kind": "payment_slip", **(result if isinstance(result, dict) else {})}
