# NextGen ERP AI/SCM handoff

**Document status:** Authoritative current-state and target-state index  
**Last verified:** 2026-08-10  
**Current implementation phase:** [Phase 1 — Agent runtime microservice](PHASE_1_AGENT_PLATFORM.md) — code implemented, staged rollout not yet started  
**Next implementation phase:** [Phase 2 — Inventory control tower](PHASE_2_INVENTORY_CONTROL_TOWER.md), after the Phase 1 rollout and completion criteria pass

This folder is the canonical starting point for humans and AI agents working on NextGen ERP. It separates what exists today from the architecture that is approved but not yet implemented.

## Required reading order

1. [Product and scope](PRODUCT_AND_SCOPE.md) — customer, problem, outcomes, and boundaries.
2. [Current state](CURRENT_STATE.md) — running capabilities, safeguards, debt, and test baseline.
3. [Decisions and guardrails](DECISIONS_AND_GUARDRAILS.md) — constraints that implementations must not reinterpret.
4. [Target architecture](TARGET_ARCHITECTURE.md) — future components, records, states, and service contracts.
5. [Phase 1 — Agent runtime microservice](PHASE_1_AGENT_PLATFORM.md) — the next decision-complete implementation phase.
6. [Phase 2 — Inventory control tower](PHASE_2_INVENTORY_CONTROL_TOWER.md) — the first SCM vertical slice after Phase 1.
7. [Phase 3 — Agent workspace](PHASE_3_AGENT_WORKSPACE.md) — the capability chat and workflow designer.
8. [Acceptance tests](ACCEPTANCE_TESTS.md) — regression, security, migration, and product scenarios.

Historical plans are evidence, not instructions. They are indexed under [the documentation archive](../archive/README.md).

## Current phase map

| Area | State | Source of truth |
|---|---|---|
| ERPNext-first LINE order-to-cash | Implemented prototype | [Current state](CURRENT_STATE.md) |
| Sales and Procurement copilots | Implemented prototype | [Current state](CURRENT_STATE.md) |
| Deterministic procurement forecast | Implemented prototype | [Current state](CURRENT_STATE.md) |
| Agent Runtime microservice and generic run/step/proposal records | Implemented, disabled by default, rollout pending | [Phase 1](PHASE_1_AGENT_PLATFORM.md) and [the runtime guide](../AGENT_RUNTIME.md) |
| Multi-company inventory control tower | Approved, not implemented | [Phase 2](PHASE_2_INVENTORY_CONTROL_TOWER.md) |
| Capability chat and agent workflow designer | Implemented, disabled by default | [Phase 3](PHASE_3_AGENT_WORKSPACE.md) |
| Advanced WMS, manufacturing, and TMS | Roadmap only | [Product scope](PRODUCT_AND_SCOPE.md) |

## Repository map

| Path | Role |
|---|---|
| `apps/nextgen_erp` | Frappe app, ERPNext domain methods, copilots, settings, and Desk UI |
| `apps/order-intake-api` | Verified LINE webhook receiver and Thai order extraction service |
| `services/agent-runtime` | Separately deployed orchestration microservice; owns model calls, prompts and the tool loop |
| `vendor/frappe`, `vendor/erpnext` | Pinned upstream application foundations |
| `vendor/erpclaw*` | Paused optional conversational substrate; not the target runtime |
| `docs/AI_HANDOFF` | Canonical product, architecture, and implementation contract |
| `docs/archive` | Superseded and historical documentation |

## Working rules for another AI

1. Check `git status` and read this folder before proposing or changing code.
2. Treat ERPNext as the transaction, inventory, and accounting source of truth.
3. Keep agent orchestration in the separate runtime microservice; it may access ERP only through versioned Frappe gateway APIs and never through direct database access.
4. Never let an LLM write ERP tables, calculate authoritative quantities, choose its own permissions, or bypass document controllers.
5. Put every ERP write behind a narrow version-controlled domain command with permission, policy, idempotency, and live revalidation.
6. Keep companies isolated. A warehouse, policy, signal, proposal, or execution without an explicit company is invalid.
7. Preserve LINE, Sales Copilot, Procurement Copilot, and current public methods while Phase 1 migrates their internals.
8. Implement Phase 1 before Phase 2. Do not add warehouse or logistics agents to the current chat-specific action model.
9. Do not implement advanced WMS or logistics from roadmap descriptions without a separately approved detailed plan.
10. Update this handoff when a phase is completed or a locked decision changes.

## Documentation labels

- **Current capability** means the behavior exists in the repository now.
- **Target contract** means the design is approved but code does not exist yet.
- **Roadmap** means direction only and is not implementation authorization.
- **Archived** means historical context that must not override this folder.
