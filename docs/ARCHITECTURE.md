# Architecture overview

**Document status:** Current capability summary with target-architecture link
**Canonical target contract:** [AI/SCM target architecture](AI_HANDOFF/TARGET_ARCHITECTURE.md)

## Current system

    LINE OA / Desk / scheduler
            |
            v
    verified or authenticated request
            |
            v
    bounded AI routing and deterministic domain logic
            |
            v
    whitelisted nextgen_erp methods
            |
            v
    ERPNext documents, stock ledger, and general ledger

ERPNext is the source of truth. The NextGen Frappe app owns intake review,
copilot tools, automation policy, previews, revalidation, channel mappings, and
signed links. The external order-intake service verifies LINE webhooks and
extracts Thai order text. ERPClaw is paused and replaceable.

See [Current state](AI_HANDOFF/CURRENT_STATE.md) for the implemented component
inventory and known debt.

## Invariants

1. Inbound text and model output are untrusted.
2. AI never writes ERP tables or constructs ledger rows directly.
3. Prices, stock, permissions, policy, and quantities are determined by live
   ERP data and versioned code.
4. Write actions use idempotency, preview, approval where required, and live
   revalidation.
5. Companies and warehouses remain explicit security boundaries.
6. No autonomous module generation or unrestricted tool execution is allowed.

## Approved evolution

The next implementation phase introduces a separately deployed Agent Runtime
microservice. Frappe remains the gateway and durable boundary for Agent Run,
Agent Step, Action Proposal, Approval Decision, company policy, permissions,
deterministic tools, and ERP transactions. The runtime never connects directly
to the ERP database.

The phase after that evolves the existing AI Cockpit into a company-isolated
inventory control tower. Implementation order, service APIs, schemas, rollout,
and acceptance criteria are defined in the
[canonical handoff](AI_HANDOFF/README.md).