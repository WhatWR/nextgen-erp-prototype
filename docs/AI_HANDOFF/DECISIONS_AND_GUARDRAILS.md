# Decisions and guardrails

**Document status:** Locked decisions for Phases 1 to 3  
**Amended:** 2026-08-12 — the workflow-UI and generic-write locks were lifted for Phase 3 with explicit product owner approval. The amendment narrows rather than removes the safety model: generic write intent is admitted only as a draft proposal a human approves, and the workflow designer orchestrates registered agents rather than defining capabilities. Invariants 14 to 17 were added as part of the same decision.

Changes to this document require explicit product and architecture approval. An implementation agent must not silently reinterpret these decisions.

## Locked decisions

| Topic | Decision |
|---|---|
| Market | Thai mid-market multi-warehouse distributors |
| ERP foundation | ERPNext is the transaction, inventory, and accounting source of truth |
| Runtime | A separately deployed Agent Runtime microservice is introduced in Phase 1 |
| Runtime storage | Frappe owns authoritative Agent Run, Step, Proposal, Approval, policy, and ERP records; the runtime has no separate business database |
| Integration | The runtime accesses ERP capabilities only through versioned, allowlisted Frappe service APIs |
| Workflow UI | No visual workflow engine in Phases 1 or 2. Phase 3 adds an agent workflow designer that orchestrates registered agents only; it is not a general automation engine and cannot define tools, permissions or ERP operations |
| Tenancy | One site may contain related companies; policies and inventory remain company-isolated |
| First SCM wedge | Inventory exceptions and replenishment |
| Replenishment | Same-company full-coverage transfer first; otherwise purchase |
| Authority | Shadow first, then draft creation; no automatic submission or physical movement |
| Calculations | Versioned deterministic Frappe code; the LLM explains but does not calculate authoritative values |
| Language | English engineering documentation; bilingual Thai/English product UI |
| Migration | Incremental shadowing, compatibility, and backfill; no big-bang replacement or historical deletion |

## Non-negotiable security invariants

1. Every run, signal, policy, proposal, and result has an explicit company.
2. User and service identities must have Frappe permissions and company User Permissions for every record they read or create.
3. Browser, LINE, webhook, and Desk clients authenticate through Frappe and never call the runtime directly.
4. The runtime has no direct MariaDB, shared Redis, site-file, generic DocType CRUD, arbitrary Python, or raw SQL access.
5. Tools accept business intent or an explicitly allowlisted document write, never model-selected identities or unrestricted ERP operations.
6. Frappe derives the requester, service identity, company, and tool allowlist from the persisted run and rejects payload overrides.
7. Live ERP data, permissions, and policy are revalidated in Frappe immediately before any write.
8. Approval applies to one immutable snapshot hash and expires with the proposal.
9. Idempotency is enforced at channel, dispatch, run, step, proposal, tool, callback, and ERP document boundaries.
10. Secrets, hidden model reasoning, and unnecessary personal data do not enter prompts, persisted traces, logs, or model-visible tool results.
11. ERP documents are built through their controllers and normal validations; AI never constructs stock or ledger rows directly.
12. Failures are fail-closed. A service or model outage cannot trigger an embedded bypass or weaken a domain rule.
13. No Phase 1 or Phase 2 path automatically submits a Purchase Order, Material Request, Stock Entry, or accounting document.
14. A hard, code-level DocType denylist governs generic document access and can never be widened by a policy, a role, or Administrator. It covers everything that executes code or grants permission — `Server Script`, `Client Script`, `Webhook`, `User`, `Role`, `Custom Field`, `Property Setter`, `System Settings` — and the gateway's own records. Without it, "everything the user's role can do" would mean remote code execution through chat.
15. A generic document write is proposed, never performed: the preview is produced by inserting inside a database savepoint and rolling it back, and the real insert happens only after a human approves, through the document's own controller, under the requesting user's permissions, always as a draft.
16. A workflow can never approve or execute its own proposal. A node that produces one parks the parent run at Waiting Approval, and the remaining nodes do not run until a human decides.
17. A workflow node names a registered agent. What that agent may do is still derived at run time from the company policy and the code allowlist, so a workflow definition can never grant a capability the policy withholds.

## Rejected alternatives

- Frappe-embedded agent orchestration as the target: rejected because runtime scaling, model-provider isolation, release cadence, and future SCM agents require an independent failure and deployment boundary.
- Runtime access to Frappe MariaDB or generic REST DocType APIs: rejected because it would bypass controllers, permission context, and the auditable domain boundary.
- A second agent business database as source of truth: rejected because proposals, approvals, company policy, and execution audit must remain transactionally governed in Frappe.
- Automatic fallback to embedded orchestration when the runtime is unavailable: rejected because it creates two production paths and can bypass the audited boundary. Rollback is explicit and transition-only.
- Flowise, Langflow, or Dify as the primary ERP runtime: rejected because critical authorization and transaction logic must be code-owned and versioned. The Phase 3 designer does not change this: it arranges agents that are already defined in code, and defines no tool, permission or ERP operation of its own.
- Letting the assistant call generic DocType CRUD directly under the user's permissions: rejected. Frappe permissions alone would still allow a System Manager to create a `Server Script` by asking, so writes go through the denylist, the policy allowlist, a rolled-back preview and a human approval instead.
- Shared inventory across companies: rejected because ERPNext warehouse ownership and accounting boundaries must remain intact.
- Direct intercompany transfer: rejected; future intercompany movement requires paired commercial/accounting documents and a separate plan.
- WMS-first implementation: rejected until generic agent execution and inventory signals are safe and observable.
- Configurable tool permissions only in DocTypes: rejected; configuration may narrow but never expand code allowlists.
- LLM-computed forecast or replenishment quantity: rejected because outputs must be reproducible and testable.
- Adding more agents to NextGen Chat Action: rejected because non-chat schedules and events require a generic run and proposal model.
- Deleting legacy actions during migration: rejected because existing approvals and audit history must remain recoverable.