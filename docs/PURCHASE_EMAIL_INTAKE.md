# Purchase Email Intake

> Desk automation that turns purchase-order emails into reviewed Draft Purchase Orders.

## Overview

Procurement teams previously validated vendor POs in Excel. This MVP watches a mailbox (or mock fixtures), extracts order fields, runs Excel-parity rules, and parks drafts in a human review queue before any ERP write.

Entry point: Desk page **PO Email Intake** (`/app/po-email-intake`), also linked from **AI Procurement Copilot**.

## Components

| Piece | DocType / module |
|---|---|
| Mailbox settings | `NextGen PO Email Settings` (Single) |
| Validation rules | `NextGen PO Email Rule` |
| Review queue | `NextGen PO Email Intake` (+ item / rule-result children) |
| Service | `nextgen_erp.purchase_email` |
| Scheduler | `*/15` cron → `purchase_email.tasks.poll_mailbox` |

Providers: **Mock** (fixtures), **Gmail**, **Microsoft**, **IMAP**. Live list APIs are stubbed until OAuth/IMAP secrets are configured; Sync falls back to fixtures so demos stay end-to-end.

## Demo without secrets

1. `bench --site <site> migrate`
2. Open `/app/po-email-intake`
3. Keep **Use mock fixtures** enabled → **Sync Inbox**
4. Approve / reject / flag rows, then **Export Draft PO** when Supplier + Items exist

## Tests

```bash
cd apps/nextgen_erp
PYTHONPATH=. python -m unittest nextgen_erp.tests.test_purchase_email -v
```

## Guardrails

- Extracted fields are untrusted until a human confirms.
- Export creates a **Draft** Purchase Order only (never submits), matching existing procurement policy.
