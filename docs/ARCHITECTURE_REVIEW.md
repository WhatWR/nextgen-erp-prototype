# Architecture Review (Phase 1 baseline — July 2026)

> **Document status:** Current capability baseline and historical architecture review. The authoritative target architecture is in the [canonical AI handoff](AI_HANDOFF/README.md).

A snapshot of the system as reviewed before the LINE AI assistant phase, kept
as the shared mental model for future work. Companion docs:
[ERPNEXT_MIGRATION.md](ERPNEXT_MIGRATION.md) (integration/ops),
[LINE_PAYMENT_FLOW.md](LINE_PAYMENT_FLOW.md) (payment-first lifecycle),
[AI_ASSISTANT.md](AI_ASSISTANT.md) (this phase's addition),
[THAILAND_LAUNCH_GATES.md](THAILAND_LAUNCH_GATES.md) (compliance gates).

## What the system is

A Thailand-focused, ERPNext-first order-to-cash prototype. Thai customers order
via LINE OA; a confidence-gated intake pipeline turns messages into real
ERPNext Sales Orders, Pick Lists, Invoices, Payment Entries and Delivery Notes,
with PromptPay QR payment, slip verification and human review below threshold.

```
LINE OA webhook
  → apps/order-intake-api   HMAC verify · Thai matcher (rule-based) · confidence gate
  → nextgen_erp.api.*       role-gated whitelisted methods (the ONLY write surface)
  → ERPNext v16.20          system of record: items, prices, stock, SO/PL/SI/PE/DN, GL
```

The conversational/ERPClaw layer (`vendor/erpclaw`, `vendor/erpclaw-web`,
MCP proxy) is present but **paused**; the AI assistant introduced in
[AI_ASSISTANT.md](AI_ASSISTANT.md) uses the same whitelisted-method boundary,
so ERPClaw can plug back in later without rework.

## Strengths to preserve

1. **Zero-trust toward extractor/AI output** — prices re-derived from Item
   Price, auto/manual decisions re-made server-side, intake status transitions
   locked behind `flags.nextgen_transition`.
2. **Idempotency everywhere** — `LINE Event Receipt` for webhook events,
   `custom_nextgen_external_reference` on SO/DN/SI/PE.
3. **All ERP writes via ERPNext maker functions** — no hand-built stock or GL
   documents.
4. **Secrets stay in ERPNext** — LINE channel token and AI gateway key are
   Password fields; services authenticate with a least-privilege API user
   (`NextGen Order Service` role, provisioned by `nextgen_erp.provision`).
5. **Disciplined vendoring** — submodules pinned by `UPSTREAM.lock`, local
   changes tracked as reviewable patches in `patches/`.
6. **Injectable-transport tests** — both HTTP clients take a fake transport, so
   suites run with no live services.

## Known debt (ranked backlog — tackle incrementally, never big-bang)

1. `apps/nextgen_erp/nextgen_erp/api.py` (~1,200 lines) mixes order-to-cash
   primitives, intake orchestration, LINE, PromptPay and signed links. Future:
   split into domain services with `api.py` as a thin whitelisted façade
   (the AI phase already started this pattern with the separate `ai.py`).
2. Legacy SQLite prototype under `apps/order-intake-api` (`service.py`,
   `db.py`, `workflow.py`, committed `data/*.sqlite3`) — flag-gated
   (`ENABLE_LEGACY_PROTOTYPE`, keep unset) but a confusing surface to remove.
3. Confidence constants duplicated between `erpnext_bridge.py` and
   `api.create_ai_order_intake` (deliberate defense in depth, but thresholds
   should share one definition).
4. `hooks.py` doc_events are stubbed — Delivery Note / Sales Invoice / Payment
   Entry submits do not notify external services yet (planned via ERPNext
   Webhook records).
5. No structured logging or correlation ids across the LINE event → intake →
   Sales Order chain.
6. Submodule patch state lives as staged index changes; the apply step should
   be scripted and verified against `UPSTREAM.lock`.
7. Scalability ceilings acceptable for a prototype: stdlib HTTP server,
   single-host Compose, MariaDB 10.6 local vs 11.8 server drift.
8. ERPClaw's ERPNext backend covers only a fraction of its actions (moot while
   paused).

## Guiding rule for all future work

Every new capability lands as a whitelisted `nextgen_erp` service method
first; AI surfaces (assistant tools today, MCP tools later) only ever call
those methods. AI never touches the database.

This review predates the generic agent-platform decision. Where its future-work language differs from docs/AI_HANDOFF/, the handoff is authoritative.
