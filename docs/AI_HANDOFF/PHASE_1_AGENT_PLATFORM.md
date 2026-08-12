# Phase 1 — Agent runtime microservice

**Document status:** Decision-complete implementation phase
**Code status:** Implemented and disabled by default; staged rollout not started
**Implementation notes:** [Agent Runtime and the Frappe agent gateway](../AGENT_RUNTIME.md)

## Implementation status

| Deliverable | State |
|---|---|
| `services/agent-runtime` ASGI service, agents, provider adapter, gateway client, runner, redaction, telemetry | Implemented |
| Versioned `v1` contracts with a cross-service fingerprint test on both sides | Implemented |
| Agent Run, Agent Step, Action Proposal, Approval Decision, company Automation Policy DocTypes | Implemented |
| `agent_gateway`, `agent_records`, `action_proposals`, `domain_tools` | Implemented |
| Restricted runtime role, service identity, feature flags, dispatcher, reaper | Implemented |
| Policy conversion and Chat Action backfill patches, both idempotent | Implemented |
| Dual-write, `confirm_action` compatibility, reconciliation report | Implemented |
| Entry prerequisites 1 to 5 below | **Outstanding** — environment work, not code |
| Rollout steps 1 to 8 below | **Not started** — every switch ships disabled |
| Removal of the embedded orchestration path | Deliberately deferred to the end of the transition release |

Two boundaries are narrower than the target contract on purpose and are lifted
in Phase 2, not by reinterpretation:

- An approved proposal executes through its originating chat action, so Phase 1
  keeps exactly one hardened write path. A proposal without one is recorded and
  approvable but not executable.
- No Inventory agent or inventory domain tool is defined. Phase 2 adds them
  together with the deterministic signals they explain.

## Outcome

Extract chat-specific orchestration from Frappe into a reusable, auditable, company-scoped Agent Runtime microservice while preserving all existing user-visible flows and public Frappe methods.

Frappe remains the policy, permission, approval, deterministic-calculation, audit-record, and ERP transaction boundary. The microservice owns orchestration and model interaction only.

## Entry prerequisites

1. Restore the missing ERPClaw proxy checkout by initializing vendor/erpclaw and vendor/erpclaw-web at the commits in UPSTREAM.lock, then apply and verify the tracked patches from the root README.
2. Initialize the uninitialized Frappe submodule, and verify vendor/frappe and vendor/erpnext match UPSTREAM.lock.
3. Resolve Windows SQLite cleanup locks by closing database connections and HTTP servers before temporary directories are removed.
4. Record a green or fully explained baseline for the standalone and Frappe suites.
5. Confirm private service DNS, TLS termination, and deployment-secret storage for service-to-service credentials.

## Target source layout

Create these target areas without moving ERP domain logic out of Frappe:

~~~text
services/agent-runtime/
  pyproject.toml
  src/nextgen_agent_runtime/
    api/
    agents/
    models/
    orchestration/
    redaction/
    telemetry/
  tests/

apps/nextgen_erp/nextgen_erp/
  agent_gateway/
    api.py
    dispatch.py
    contracts.py
    permissions.py
  agent_records/
  action_proposals/
  domain_tools/
    sales/
    procurement/
    inventory/
~~~

The runtime is a Python 3.11-or-newer ASGI service with explicit request/response models and generated OpenAPI. Agent definitions, prompt references, and orchestration tool allowlists are version-controlled in the service. Transaction permissions and the authoritative tool registry remain version-controlled in Frappe; configuration may narrow but never expand them.

## Responsibility split

| Responsibility | Agent Runtime microservice | Frappe / ERPNext |
|---|---|---|
| Model calls and provider retries | Owns | Does not own |
| Agent routing and tool loop | Owns | Supplies approved run context |
| Prompt and agent versions | Owns | Records versions used |
| Company/user authentication | Trusts persisted run context only | Owns and revalidates |
| Domain reads and calculations | Requests through tools | Owns |
| Policies and permission checks | Consumes result | Owns |
| Proposals and approvals | Requests proposal creation | Owns records and decisions |
| ERP document creation | Never | Owns through controllers |
| Durable run and step audit | Sends sanitized callbacks | Owns authoritative records |

The service must not connect to MariaDB, share Frappe Redis, mount site files, call generic DocType APIs, or receive browser/LINE traffic directly.

## Runtime and gateway contracts

Implement the contracts in [Target architecture](TARGET_ARCHITECTURE.md) as versioned JSON APIs.

- Frappe creates the Agent Run before dispatch.
- A dispatcher retries Queued runs with the same run ID and idempotency key.
- The runtime claims a run before executing it; duplicate claims return the current state.
- Each model request, tool call, policy result, proposal, error, and completion appends an ordered sanitized Agent Step through Frappe.
- All runtime tool requests go through execute_tool and identify the run, sequence, tool, company-bound context, and idempotency key.
- Frappe derives requester, execution identity, company, and allowed tools from the run. Client-supplied overrides are rejected.
- The runtime can create a proposal but cannot call human approval endpoints.
- A service outage leaves runs Queued or Failed. After final cutover there is no silent in-process orchestration fallback.

Use a dedicated restricted runtime service user for runtime-to-Frappe calls. Use a separate deployment credential for Frappe-to-runtime dispatch. Both are rotated and stored outside source control.

## Data and migration

1. Add the Agent Run, Agent Step, Action Proposal, and Approval Decision DocTypes defined in [Target architecture](TARGET_ARCHITECTURE.md).
2. Add a company-linked NextGen Automation Policy DocType with a unique company constraint. Include runtime enablement, default mode, allowed warehouses/item groups, expiry, data-quality threshold, value limits, supplier allowlist, item allowlist, and service user.
3. Convert existing global procurement settings into one policy for the current default company. Create policies for other companies in disabled Shadow mode; never copy an active policy across companies automatically.
4. Backfill every existing NextGen Chat Action into one synthetic Agent Run and one Action Proposal. Retain the original record and link both directions where supported.
5. Do not delete Chat Actions or current procurement forecast/recommendation records in this phase.
6. During a feature-flagged compatibility window, current Frappe orchestration remains the production path while the microservice receives shadow runs. Shadow runs may read and compare but cannot create ERP documents.
7. Once results reconcile, route new chat actions through the microservice and dual-write the legacy Chat Action plus generic proposal for one transition release.
8. After cutover, stop new legacy Chat Action creation and remove the normal embedded orchestration route. Keep confirm_action able to resolve either identifier for one release.

Backfill, dispatch, callbacks, shadow comparison, and dual-write operations must be idempotent and safe to rerun.

## Execution and security

- Create a dedicated non-Desk runtime service user. Grant only the gateway and domain-tool methods plus Frappe User Permissions for approved companies and warehouses.
- Scheduled and interactive runs use the runtime service identity for transport, while Frappe separately records and revalidates requested_by.
- Normal autonomous work must not use Administrator or ignore_permissions=True to create business documents.
- Domain tools accept business intent, never raw SQL, arbitrary DocType CRUD, unrestricted Python, or caller-selected identities.
- Persist a step for every model request, tool call, policy evaluation, proposal, revalidation, approval, and result.
- Never persist hidden model reasoning. Store only bounded model metadata and sanitized inputs/outputs needed for audit.
- Redact credentials, LINE tokens, payment secrets, sensitive customer content, and unnecessary PII before cross-service transport, persistence, or logging.
- Generate one correlation ID at channel entry and propagate it across Frappe logs, dispatch, runtime logs, tool calls, proposals, and result documents.
- Frappe revalidates live permissions, company, warehouse, policy, stock, price, supplier, and document versions before every write.
- Network, model, timeout, or partial-callback failures are fail-closed and cannot produce an unrecorded ERP side effect.

## Compatibility requirements

- LINE question, order, confirmation, invoice resend, delivery-note resend, and payment flows remain unchanged.
- Sales Copilot and Procurement Copilot retain current role gates, response shapes, and tool isolation.
- Existing whitelisted Frappe method names remain compatible and become gateway wrappers where orchestration is required.
- Existing pending actions remain confirmable after migration unless expired by their original policy.
- Forecast formulas and procurement recommendation behavior do not change in Phase 1.
- confirm_action remains a compatibility wrapper for one transition release.
- No automatic submission of a Purchase Order, Material Request, Stock Entry, or accounting document is introduced.
- During rollout only, an explicit rollback flag may restore the legacy route. It is removed after the transition release; runtime failure must never trigger it automatically.

## Rollout

1. Add Frappe records, gateway API, restricted identities, and disabled feature flags.
2. Deploy the Agent Runtime service with health, readiness, structured logging, correlation IDs, timeouts, and no enabled agents.
3. Backfill legacy actions and run the reconciliation report.
4. Enable shadow dispatch for internal Sales and Procurement traffic. Compare tool choice, sanitized result, proposal preview hash, company, user, and error classification.
5. Enable microservice execution for internal users while retaining the explicit legacy rollback flag.
6. Enable selected pilot users and scheduled runs; monitor queue age, dispatch attempts, runtime latency, tool errors, token usage, duplicate callbacks, and unmatched actions.
7. Route all supported agent traffic to the service, stop new legacy writes, and retain confirm_action compatibility for one release.
8. Remove the embedded orchestration path and rollback flag after regression and operational acceptance pass.

## Completion criteria

- The runtime is deployed and released independently from Frappe.
- No runtime process has direct database, Redis, site-file, or generic DocType access.
- All new interactive and scheduled agent operations produce complete Frappe Agent Run and Agent Step histories.
- Every proposal has a company, policy snapshot, idempotency key, expiry, current preview hash, and attributable requester and service identity.
- Company policy and current user/service permissions prevent cross-company access.
- Service retries, duplicate delivery, restarts, and callback loss do not duplicate a tool effect, proposal, or ERP result.
- Runtime unavailability fails closed and leaves an observable recoverable run state.
- Existing channel and copilot regression scenarios pass.
- Legacy and generic records reconcile for the transition period.
- The test suite is reproducible on the supported local environment.