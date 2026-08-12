# Current state

**Document status:** Current capability snapshot  
**Snapshot date:** 2026-08-10

## Implemented system

NextGen ERP is an ERPNext-first Thailand order-to-cash and procurement prototype.

```text
LINE OA / Desk / scheduler
        |
        v
verified intake or authenticated staff request
        |
        v
rule-based routing + bounded LLM tool loop
        |
        v
whitelisted nextgen_erp domain methods
        |
        v
ERPNext documents, stock ledger, and general ledger
```

Implemented components include:

- ERPNext v16.20 as system of record for items, prices, stock, customers, suppliers, Sales Orders, Pick Lists, Delivery Notes, invoices, payments, Purchase Orders, Material Requests, and ledgers.
- Frappe CRM and HRMS in the deployed product composition.
- LINE webhook verification, durable event idempotency, Thai item matching, customer mapping, payment flow, and signed invoice or delivery-note links.
- Customer-facing LINE AI assistant with live catalog/order tools and FAQ retrieval.
- Authenticated Staff Chat with isolated Sales and Procurement agents.
- Preview, expiry, confirmation, and live revalidation before creating ERP documents.
- Deterministic `nextgen-procurement-v1` forecast with 30/60/90-day demand, stock position, open purchasing, MOQ, order multiple, warnings, and data-quality score.
- Daily procurement forecast scheduler with Shadow, Approval Required, and tightly gated Automatic modes.
- AI Cockpit showing sales, procurement, stock-risk, attention, and recent activity.
- Separately deployed Agent Runtime microservice with the Frappe agent gateway, generic Agent Run, Agent Step, Action Proposal, Approval Decision, and company-scoped Automation Policy records. Every switch ships disabled; see [the runtime guide](../AGENT_RUNTIME.md).
- An `assistant` agent that proposes any document the user's role permits, behind a code-level denylist, a policy allowlist, a rolled-back controller preview and human approval.
- A visual agent workflow designer (Desk page `agent-workflow`) with a graph executor in the runtime. See [Phase 3](PHASE_3_AGENT_WORKSPACE.md).

## Safeguards to preserve

1. Extractor and LLM output is untrusted. The server re-derives prices, quantities, permissions, stock, and policy decisions.
2. AI tools call narrow Frappe methods and never write ERP tables directly.
3. LINE IDs come from verified webhooks, not model arguments.
4. Agent selection, role access, and tool allowlists are enforced by Python code.
5. Write actions create an expiring preview and are revalidated before execution.
6. Idempotency protects external events and ERP document creation.
7. Purchase confirmation creates a Draft Purchase Order; it does not submit it.
8. Forecast quantities are calculated by deterministic code, not the LLM.

## Architectural limitations

- The production chat path still runs embedded orchestration. The Agent Runtime is implemented but disabled, and its staged rollout has not started, so the two paths coexist during the transition release by design.
- `NextGen Chat Action` remains the executing record. Generic proposals are dual-written and reconciled, but a proposal without a chat action cannot execute until Phase 2 adds its own executor.
- `api.py`, `procurement.py`, and `staff_chat.py` still combine orchestration and domain concerns. The gateway now fronts them, but the modules have not been split.
- The global `NextGen Procurement Settings` Single DocType still drives the forecast and the scheduled run; the per-company `NextGen Automation Policy` governs the gateway only.
- Scheduled Material Request creation still uses `ignore_permissions=True` instead of the restricted service identity.
- The forecast is a transparent MVP and does not model seasonality, promotions, service levels, or lead-time variability.
- No custom warehouse-task, handling-unit, scanner, dock, carrier, or proof-of-delivery model exists.
- Correlation IDs now span Frappe and the runtime, but a transactional event outbox is still absent.
- Legacy SQLite code remains behind a feature flag and creates an avoidable second mental model.

## Test baseline

### Agent Runtime service — 2026-08-10

**62 tests, all passing**, run on Windows with the bundled Python runtime:

```bash
cd services/agent-runtime && PYTHONPATH=src:tests python -m unittest discover -s tests -t tests
```

They cover the boundary contracts, redaction, agent allowlist narrowing, the
tool loop, fail-closed behaviour, dispatch authentication and the gateway
client's retry and rejection handling. The suite has no third-party
dependencies.

### Phase 3 — 2026-08-12

Run against the live podman site (`nextgen.localhost`), not only locally:

| Module | Result |
|---|---|
| `test_document_tool` | 18 passing |
| `test_agent_gateway` | 32 passing |
| `test_agents` | 14 passing |
| `test_staff_chat` | 10 passing |
| `test_procurement` | 32 passing |
| Agent Runtime suite (local) | 82 passing |

Running against a real site found four defects that local checks could not: a
unique constraint on `correlation_id`, Administrator being treated as the service
identity, an idempotency key that collapsed two runs into one, and child-table
row names making every preview look like drift. All four are fixed and covered.

### Frappe agent gateway — 2026-08-10

`nextgen_erp/tests/test_agent_contracts.py` (**10 tests, all passing**) needs no
site and proves the Frappe and runtime copies of the `v1` contracts share one
fingerprint in both directions.

`nextgen_erp/tests/test_agent_gateway.py` covers identity, company isolation,
tool authorisation, step idempotency, proposal lifecycle and the migration
patches. **It has not been executed yet**: this machine has no bench, no site
and no Frappe installation, so the app suite could not be run. Run it before
starting the Phase 1 rollout.

### Order intake — 2026-08-09

The standalone order-intake suite was run on Windows with the bundled Python runtime on 2026-08-09:

**72 discovered, 63 passing, nine environment/setup errors, with no assertion failures.**

Detailed counts:

- 72 tests discovered.
- 63 tests passed.
- 9 tests ended in environment/setup errors.
- No assertion failures were reported.

The errors came from:

- Missing ERPClaw proxy checkout: `vendor/erpclaw/mcp/erpnext_proxy.py` is unavailable because the ERPClaw checkout is absent.
- Uninitialized Frappe submodule: `vendor/frappe` has not been initialized.
- Windows SQLite cleanup locks: temporary database files remain open during test cleanup.

Before the Phase 1 rollout, initialize all submodules, apply the pinned local ERPClaw patches described in the root README, and make the SQLite-backed test servers close database and HTTP resources deterministically on Windows. These remain the outstanding Phase 1 entry prerequisites; they are environment work rather than code.

## Documentation authority

Feature and operations documents under `docs/` describe current capabilities. This folder owns target architecture and implementation order. Files under `docs/archive/` are historical and not active requirements.
