# Order Intake API

This is the Thailand-first product layer. It accepts LINE-like Thai order text,
matches it to a merchant catalog, and routes it through a confidence-gated
order-to-cash workflow. ERPClaw live execution is opt-in and dry-run by default.

## Run

No web-framework package is required. The service uses Python's standard HTTP
server and SQLite. XLSX imports use `openpyxl` when available.

```bash
cd apps/order-intake-api
python3 run.py --port 8200
```

The demo merchant and catalog are seeded automatically. Open
`http://127.0.0.1:8200/health` to verify the service.

## Safety boundary

- Every inbound order is a draft.
- Low-confidence, ambiguous, missing-customer, unit-mismatch, and stock-shortage
  cases are clearly flagged.
- Approval is recorded in an immutable audit feed.
- The default adapter writes reviewable ERPClaw **dry-run** command plans. Live
  mode invokes ERPClaw actions through its CLI and never writes ERP tables directly.
- Repeated webhook events are deduplicated by merchant and idempotency key.
- Automatic progression requires confidence at or above
  `ORDER_AUTO_CONFIDENCE` (default `0.95`), a known customer, and zero
  exceptions. Customer confirmation is still mandatory.
- Stock is reserved at Pick List submission. AI extraction never reduces stock;
  actual movement occurs through the Delivery Note lifecycle.
- The server binds to `127.0.0.1` by default. Set `ORDER_INTAKE_API_KEY` to
  require `X-Prototype-Key` for non-LINE endpoints.

## Key endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/api/dashboard?merchant_id=demo` | Prototype KPIs |
| POST | `/api/demo/seed` | Reset or refresh demo data |
| POST | `/api/catalog/import` | Base64-encoded CSV/XLSX catalog import |
| POST | `/api/intake/messages` | Create a controlled draft |
| GET | `/api/reviews?merchant_id=demo` | Review queue |
| GET/PATCH | `/api/reviews/{id}` | Read or correct a draft |
| POST | `/api/reviews/{id}/approve` | Human approval before customer confirmation |
| POST | `/api/reviews/{id}/reject` | Reject a draft |
| POST | `/api/workflows/{id}/customer-confirm` | Create ERPClaw SO and reservation |
| POST | `/api/workflows/{id}/delivery-complete` | Complete delivery and create invoice |
| POST | `/api/workflows/{id}/payment-received` | Allocate payment and queue invoice status |
| GET | `/api/audit?merchant_id=demo` | Audit feed |
| GET/POST | `/api/integrations/line` | Read or save masked LINE OA configuration |
| POST | `/api/integrations/line/test` | Self-test request-signature verification |
| POST | `/webhooks/line` | Signature-verified LINE webhook |

LINE webhook processing is disabled until the integration is configured and
enabled. The raw request body is verified before JSON parsing. The local
prototype stores the secret and optional access token in a permission-restricted
file; production must use a managed secrets vault. When an access token is
configured, queued workflow messages are pushed through the LINE Messaging API.

## Tests

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

This is an operational-overlay prototype. The customer's existing accounting
software remains the legal system of record.
