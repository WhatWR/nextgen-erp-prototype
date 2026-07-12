# Order Intake API

This is the Thailand-first product layer. It accepts LINE-like Thai order text,
matches it to a merchant catalog, and creates a draft for human review. It does
not post financial entries or modify ERPClaw's database.

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
- Approval writes a UTF-8 CSV and a reviewable ERPClaw **dry-run** JSON payload.
  The adapter never writes directly to ERP tables or runs an ERP command.
- Repeated webhook events are deduplicated by merchant and idempotency key.
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
| POST | `/api/reviews/{id}/approve` | Approve and create write-back artifacts |
| POST | `/api/reviews/{id}/reject` | Reject a draft |
| GET | `/api/audit?merchant_id=demo` | Audit feed |
| POST | `/webhooks/line` | Signature-verified LINE webhook |

LINE webhook processing is disabled unless `LINE_CHANNEL_SECRET` is set. The raw
request body is verified before JSON parsing.

## Tests

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

This is an operational-overlay prototype. The customer's existing accounting
software remains the legal system of record.
