# Local Sandbox Simulator

> **Document status:** Current local test and simulation guide. Target acceptance coverage is defined in the [canonical acceptance tests](AI_HANDOFF/ACCEPTANCE_TESTS.md).

`scripts/sandbox.py` is a single stdlib process that stands in for every third
party the stack talks to, so the **entire order-to-cash + AI assistant flow can
be rehearsed locally before deploying** — no LINE OA, no LLM key, no slip
verifier needed. Everything NextGen ERP "writes back" to the customer lands in
an inspectable inbox.

**Local development only.** `scripts/deploy.sh` refuses an `.env.server` that
points at the sandbox.

## What it simulates

| Real service | Sandbox endpoint | Behaviour |
|---|---|---|
| LINE push (`api.line.me`) | `POST /v2/bot/message/push` | records into `GET /inbox` |
| LINE content (`api-data.line.me`) | `GET /v2/bot/message/<id>/content` | serves a PNG (payment slip) |
| AI gateway | `POST /v1/chat/completions`, `/v1/embeddings` | scripted replies via `POST /script/chat`, or a deterministic default; stable embeddings |
| Slip verifier | `POST /verify-slip` | verified/0.99/amount-match by default; override via `POST /config/slip` |
| A LINE customer | `POST /simulate/line` | builds a correctly HMAC-signed webhook and posts it to order-intake |

## Setup (once, local site only)

```bash
# 1. Redirect LINE traffic from ERPNext to the sandbox (NEVER on production):
bench --site nextgen.localhost set-config nextgen_line_api_base http://127.0.0.1:8300

# 2. Desk → NextGen AI Settings: Gateway URL = http://127.0.0.1:8300, any model name, Enable
# 3. Desk → NextGen Payment Settings: Slip Verification URL = http://127.0.0.1:8300/verify-slip
# 4. Desk → LINE Channel Settings: enable, set any channel secret (e.g. sandbox-secret)
#    Desk → LINE Customer Map: map Udemo → a Customer

# 5. Start the stack and the sandbox:
./scripts/start-local.sh
LINE_CHANNEL_SECRET=sandbox-secret python3 scripts/sandbox.py --port 8300
```

Undo the redirect with `bench --site nextgen.localhost set-config nextgen_line_api_base ""`.

## Full rehearsal

```bash
# Customer asks a question → AI assistant answers from the live catalog
curl -s -X POST http://127.0.0.1:8300/simulate/line \
  -H 'Content-Type: application/json' \
  -d '{"line_id": "Udemo", "text": "M-150 ราคาเท่าไหร่"}'

# Customer places an order → intake → (approve in Desk if Needs Review)
curl -s -X POST http://127.0.0.1:8300/simulate/line \
  -H 'Content-Type: application/json' \
  -d '{"line_id": "Udemo", "text": "M-150 2 ลัง"}'

# Customer confirms → Sales Order + Pick List + Invoice; invoice link + QR pushed
curl -s -X POST http://127.0.0.1:8300/simulate/line \
  -H 'Content-Type: application/json' \
  -d '{"line_id": "Udemo", "text": "ยืนยัน"}'

# Customer sends the payment slip image → verified → Payment Entry + draft DN
curl -s -X POST http://127.0.0.1:8300/simulate/line \
  -H 'Content-Type: application/json' \
  -d '{"line_id": "Udemo", "image_message_id": "slip-001"}'

# See everything the "customer" received (confirmation ask, invoice link,
# PromptPay QR image URL, payment result, assistant answers):
curl -s http://127.0.0.1:8300/inbox | python3 -m json.tool
```

To test the failure paths: `POST /config/slip {"verified": false, "confidence": 0.2}`
forces Payment Review; stopping the sandbox while the assistant is enabled
exercises the polite-fallback message.

## Scripting the LLM

The default chat reply is a deterministic echo (no tool calls). To rehearse a
specific assistant behaviour, enqueue OpenAI-shaped messages; each chat request
pops one:

```bash
curl -s -X POST http://127.0.0.1:8300/script/chat -H 'Content-Type: application/json' -d '{
  "messages": [
    {"role": "assistant", "content": null, "tool_calls": [{"id": "c1", "type": "function",
      "function": {"name": "resend_payment_request", "arguments": "{\"order\": \"AIO-0001\"}"}}]},
    {"role": "assistant", "content": "ส่งใบแจ้งหนี้และ QR ให้ใหม่แล้วค่ะ"}
  ]}'
```

## Tests

`apps/order-intake-api/tests/test_sandbox.py` boots the sandbox plus a fake
intake receiver and asserts inbox capture, script/default chat, deterministic
embeddings, slip configuration, PNG content, and that `/simulate/line`
signatures satisfy the same `verify_line_signature` the real webhook uses.
