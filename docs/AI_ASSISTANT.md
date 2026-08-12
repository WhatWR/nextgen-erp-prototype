# LINE AI Assistant (LLM + RAG)

> **Document status:** Current LINE customer-assistant capability. Future agent-platform behavior is defined in the [canonical AI handoff](AI_HANDOFF/README.md).

The assistant answers customer questions on the LINE chat — product/price/stock,
order and payment status, FAQ — and can re-send the invoice with its PromptPay
QR so the customer can pay. It never replaces the order pipeline: messages with
an explicit quantity+unit (order-shaped) always follow the existing intake path,
and confirmations/payment slips are handled exactly as before.

## Architecture

```
LINE webhook → order-intake-api (HMAC verify + idempotency, unchanged)
  → ERPNextLineWorkflow router (order_intake/erpnext_line.py)
       ยืนยัน/ยกเลิก → handle_line_reply     (unchanged)
       slip image    → handle_line_payment_slip (unchanged)
       order-shaped  → AI Order Intake       (unchanged)
       anything else → AI assistant (order_intake/ai/)
  → assistant tool loop → nextgen_erp whitelisted methods only
  → reply via nextgen_erp.ai.send_line_answer (channel token stays in ERPNext)
```

- `order_intake/ai/client.py` — OpenAI-compatible chat/embeddings client
  (any endpoint speaking `/v1/chat/completions`; provider swappable by config).
- `order_intake/ai/tools.py` — the tool registry. Every tool wraps a
  `nextgen_erp` method and is keyed by the **verified** LINE sender id from the
  webhook; model-supplied ids are discarded, so a prompt-injected model cannot
  reach another customer's data or message anyone else.
- `order_intake/ai/rag.py` — retrieval over **NextGen Knowledge Article**
  records (Desk-editable FAQ). Embeddings when a model is configured, with a
  deterministic character-n-gram fallback (works for Thai without tokenisation).
- `order_intake/ai/assistant.py` — the guarded loop: Thai system prompt,
  answer-only-from-tools, bounded tool budget, polite fallback on any failure.
- `apps/nextgen_erp/nextgen_erp/ai.py` — the ERPNext surface:
  `get_ai_config`, `get_knowledge_articles`, `get_customer_context`,
  `resend_payment_request`, `resend_delivery_note`, `send_line_answer`.
  All require the NextGen Order Service role; ownership of an intake is
  re-checked server-side against the verified LINE id.

## Assistant tools

| Tool | Backing method | Notes |
|---|---|---|
| `get_my_orders` | `nextgen_erp.ai.get_customer_context` | sender's own recent intakes only |
| `get_item_info` | `nextgen_erp.api.get_catalog` | live price + stock; prices never invented |
| `search_knowledge` | `nextgen_erp.ai.get_knowledge_articles` | RAG over enabled articles |
| `resend_payment_request` | `nextgen_erp.ai.resend_payment_request` | re-sends invoice link + PromptPay QR (existing signed-link machinery) |
| `resend_delivery_note` | `nextgen_erp.ai.resend_delivery_note` | signed delivery-note link |
| `confirm_order` | `nextgen_erp.api.handle_line_reply` | same status-machine-guarded path as a literal "ยืนยัน" |

## Setup

1. In Desk open **NextGen AI Settings** (Order Agent workspace):
   - Gateway URL of an OpenAI-compatible endpoint and its API key.
   - Chat model name; optionally an embeddings model for semantic FAQ search.
   - Tick **Enable LINE AI Assistant**. The switch takes effect within ~30 s —
     no service restart.
2. Add **NextGen Knowledge Article** records (delivery times, payment methods,
   policies). Only `enabled` articles are served to the assistant.
3. Optional env fallbacks in `.env` / `.env.server` (Desk values win):
   `AI_GATEWAY_URL`, `AI_GATEWAY_API_KEY`, `AI_CHAT_MODEL`,
   `AI_EMBEDDINGS_MODEL`. `AI_ASSISTANT_ENABLED=1` only applies when the
   ERPNext settings method is unreachable.

## Guardrails

- The customer message is treated as untrusted data; the system prompt forbids
  following instructions embedded in it, and — more importantly — the tool
  registry makes cross-customer access impossible regardless of what the model
  emits (`line_id` comes from the verified webhook, never from the model).
- Prices, stock and order status come only from live tool results.
- One answer per LINE webhook event (`LINE Event Receipt`, `ai:` namespace) —
  webhook retries never double-send.
- A global **Daily Answer Cap** bounds LLM-driven outbound volume (0 disables).
- Every failure (gateway down, bad model output, ERP rejection) degrades to a
  polite Thai fallback message; the order pipeline is never blocked.
- Routing is quantity-based, not product-based: a message is an order **only**
  when it contains an explicit quantity + recognised unit (e.g. "2 ลัง").
  "M-150 ราคาเท่าไหร่" is a question even though the product resolves — a
  question can never create an AI Order Intake.
- With the assistant disabled (or unreachable), questions get a polite
  "please order as product + quantity + unit" reply and create **nothing**;
  order-shaped messages behave exactly as before
  (verified by `tests/test_ai_assistant.py::LineRoutingTest`).

## Tests

```bash
cd apps/order-intake-api
NO_PROXY='*' no_proxy='*' python3 -m unittest discover -s tests -v
```

New suites: `tests/test_ai_assistant.py` (loop, fallback, routing, injection),
`tests/test_ai_tools.py` (scoping, soft errors), `tests/test_ai_rag.py`
(chunking, embeddings + fallback ranking). ERPNext-side tests are colocated
with the new DocTypes and run via `bench run-tests --app nextgen_erp`.

The assistant remains a supported channel during the Phase 1 runtime refactor; its public behavior and sender-scoping contract must not change.
