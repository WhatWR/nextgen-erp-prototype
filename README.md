# NextGen ERP Thailand prototype

A clean-start prototype for Thai wholesale order operations. It uses ERPClaw as
a pinned, replaceable ERP foundation while keeping the differentiated product
layer—LINE intake, Thai catalog matching, exception handling and human review—separate.

## What works

- Thai LINE-like text to structured draft order.
- Flexible product aliases, Thai unit normalization and deterministic matching.
- Stock, unit, customer, ambiguity and confidence exceptions.
- Human approve/edit/reject workflow with audit events.
- Idempotent inbound messages and signature verification for real LINE webhooks.
- CSV/XLSX catalog ingestion with automatic header-row and column detection.
- Controlled CSV write-back plus a non-executed ERPClaw request payload.
- ERPClaw Web review workspace at `/order-intake`.
- Guided LINE OA connection and webhook setup at `/integrations`.
- Confidence-gated order-to-cash automation: 95%+ with no exceptions goes to
  customer confirmation; every lower-confidence or exceptional order requires
  human approval.
- ERPClaw Sales Order, Pick List/reservation, Delivery Note, Sales Invoice and
  payment-allocation adapter with dry-run plans by default.
- LINE confirmation, delivery, payment and invoice-status outbox with optional
  real push delivery using a Channel Access Token.
- Pinned ERPClaw core and UI submodules with their original histories and licences.

## Quick start

```bash
cd "/Users/santi/Documents/1-Projects/Next-Gen ERP/nextgen-erp-prototype"
python3 scripts/dev.py
```

Then open [http://127.0.0.1:5173/order-intake](http://127.0.0.1:5173/order-intake).

To connect a LINE Official Account, open
[http://127.0.0.1:5173/integrations](http://127.0.0.1:5173/integrations), enter the
Messaging API Channel ID and Channel Secret, then copy the webhook URL into
LINE Developers. A public launch URL must use HTTPS.

The demo message is pre-filled. Create a draft, inspect the matched lines and
confidence, then follow the customer-confirmation, delivery and payment buttons.
ERPClaw command plans are created under `apps/order-intake-api/data/exports/`.
They do not execute ERP actions until live execution is explicitly configured.

## ERPClaw execution mode

Dry-run is always the default. To use a prepared ERPClaw company, configure:

```bash
export ERPCLAW_EXECUTE=1
export ERPCLAW_ROOT=/path/to/erpclaw
export ERPCLAW_DB_PATH=/path/to/erpclaw.sqlite3
export ERPCLAW_COMPANY_ID=your-company-id
export ERPCLAW_WAREHOUSE_ID=your-warehouse-id
export ERPCLAW_RECEIVABLE_ACCOUNT=your-receivable-account-id
export ERPCLAW_BANK_ACCOUNT=your-bank-account-id
```

The intake customer references and catalog SKUs must map to ERPClaw customer
and item IDs before enabling this mode. Test in a copied database first.

If the web dependencies have not been installed:

```bash
cd vendor/erpclaw-web
npm ci
```

## Verify

```bash
cd apps/order-intake-api
PYTHONPATH=. python3 -m unittest discover -s tests -v

cd ../../vendor/erpclaw-web
node node_modules/vite/bin/vite.js build
node node_modules/vitest/vitest.mjs run
```

The local environment has a Ruby executable also named `vite`, so direct Node
paths are shown above for deterministic execution.

## Repository map

| Path | Purpose |
|---|---|
| `apps/order-intake-api` | New Thailand-first product service |
| `vendor/erpclaw` | Unmodified pinned ERPClaw core |
| `vendor/erpclaw-web` | Pinned UI with a small `nextgen-thailand` branch |
| `patches/erpclaw-web-nextgen.patch.gz` | Reproducible UI changes against the pinned commit |
| `docs/ARCHITECTURE.md` | Product and integration boundaries |
| `docs/ERPCLAW_BASELINE.md` | Reproduced upstream smoke path |
| `docs/THAILAND_LAUNCH_GATES.md` | Requirements before production |
| `UPSTREAM.lock` | Exact upstream sources and commits |

## Important boundary

This is not yet Thai accounting, tax, e-Tax or payment software. During the
pilot, the customer's existing accounting system remains the legal system of
record. See the launch gates before expanding scope.

For a fresh checkout, initialize the submodules and apply the tracked UI patch:

```bash
git submodule update --init --recursive
gzip -dc patches/erpclaw-web-nextgen.patch.gz > /tmp/erpclaw-web-nextgen.patch
git -C vendor/erpclaw-web apply /tmp/erpclaw-web-nextgen.patch
```
