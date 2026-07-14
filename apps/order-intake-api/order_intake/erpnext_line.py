"""Verified LINE event routing with ERPNext as the only system of record."""

from __future__ import annotations

from typing import Any

from .erpnext_bridge import intake_from_message
from .erpnext_client import ERPNextClient, ERPNextError


class ERPNextLineWorkflow:
    def __init__(self, client: ERPNextClient | None = None, *, warehouse: str | None = None):
        self.client = client or ERPNextClient()
        self.warehouse = warehouse

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
        result = intake_from_message(
            text,
            customer=customer,
            idempotency_key=event_id,
            line_ref=line_id,
            source_channel="line",
            client=self.client,
            warehouse=self.warehouse,
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
