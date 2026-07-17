"""Verified LINE event routing with ERPNext as the only system of record."""

from __future__ import annotations

from typing import Any

from .erpnext_bridge import intake_from_message, looks_like_question
from .erpnext_client import ERPNextClient, ERPNextError

# Sent (best effort) when a conversational message arrives and no assistant is
# available. It must never become an order instead.
UNRECOGNIZED_MESSAGE = (
    "ขออภัยค่ะ ระบบยังไม่เข้าใจข้อความนี้ "
    "หากต้องการสั่งซื้อ กรุณาพิมพ์ชื่อสินค้าพร้อมจำนวนและหน่วย เช่น น้ำแดง 2 ลัง"
)


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
        display_name: str | None = None,
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

        is_question = looks_like_question(text)
        mapping = self.client.call_method(
            "nextgen_erp.api.resolve_line_customer",
            line_id=line_id,
            create_if_missing=0 if is_question else 1,
            display_name=display_name,
        )
        customer = mapping.get("customer") if isinstance(mapping, dict) else None
        if is_question:
            # No explicit quantity+unit — a question or chitchat, never an
            # order (a product name alone must not create an intake). Only
            # order-shaped messages continue to the intake path below.
            if self.assistant is not None and self.assistant.enabled():
                return self.assistant.answer(
                    line_id=line_id, text=text, event_id=event_id, customer=customer
                )
            self._push_unrecognized(line_id, event_id)
            return {"kind": "unrecognized", "handled": False}
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

    def _push_unrecognized(self, line_id: str, event_id: str) -> None:
        """Best-effort polite reply when no assistant can answer; never fatal."""
        try:
            self.client.call_method(
                "nextgen_erp.ai.send_line_answer",
                line_id=line_id,
                text=UNRECOGNIZED_MESSAGE,
                event_id=event_id,
            )
        except ERPNextError:
            pass

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
