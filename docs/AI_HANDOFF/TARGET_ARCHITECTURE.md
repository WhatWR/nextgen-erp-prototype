# Target architecture

**Document status:** Approved target contract; not implemented  
**Implementation order:** Phase 1 before Phase 2

## Component model

~~~mermaid
flowchart TB
    C["Chat · Desk · Scheduler · Webhook · ERP event"] --> G["Frappe agent gateway"]
    G --> RUN["Agent Run queued in Frappe"]
    RUN --> R["NextGen Agent Runtime microservice"]
    R --> M["Model and prompt adapters"]
    R --> API["Versioned Frappe domain-tool API"]
    API --> P["Permission · policy · revalidation boundary"]
    P --> T["Allowlisted deterministic domain tools"]
    T --> Q["Action Proposal in Frappe"]
    Q --> H["Human Approval Decision in Frappe"]
    H --> D["Deterministic ERP domain command"]
    D --> E["ERPNext system of record"]
    E --> O["Queued run · result · domain event"]
    O --> G
~~~

ERPNext remains authoritative for business documents, inventory valuation, accounting, workflow state, and permissions. The NextGen Agent Runtime is a separately deployed microservice that owns orchestration, model calls, prompt versions, agent definitions, and the tool loop. It cannot replace ERPNext validation or write ERP data directly.

Frappe owns the durable audit and action records, company policies, authentication context, approvals, deterministic calculations, and all ERP reads and writes. The runtime accesses those capabilities only through a narrow, versioned service API.

## Deployment and trust boundary

Phase 1 adds **services/agent-runtime** as an independently deployable Python service.

- The runtime has its own container, health checks, release lifecycle, scaling, and failure boundary.
- It does not connect to Frappe MariaDB, share Frappe Redis, mount site files, call generic DocType CRUD, or execute arbitrary Python.
- It authenticates to Frappe as a dedicated restricted service user; Frappe authenticates dispatch over a private TLS-enabled service network.
- Browser, LINE, webhook, and Desk clients never call the runtime directly. Frappe authenticates the requester and establishes company context first.
- A queued NextGen Agent Run is the durable dispatch record. Delivery and callbacks are retryable and idempotent.
- Runtime unavailability leaves the run queued or failed and cannot bypass Frappe through an embedded write path after cutover.
- The runtime has no second business database. Recoverable execution state comes from Frappe Agent Run and Agent Step records.

## Persistent records

### NextGen Agent Run

Required fields:

- `correlation_id`, indexed but **not** unique. It groups rather than identifies: one operation can span several runs, and a workflow's node runs all share their parent's ID. Uniqueness lives on `idempotency_key`. (Corrected in Phase 3; it was specified as unique before workflows existed.)
- `agent_type` and version.
- `trigger_type`: `chat`, `manual`, `schedule`, `webhook`, or `document_event`.
- Optional trigger Dynamic Link.
- `requested_by` and `execution_user`.
- Required `company`.
- Status: Queued, Dispatched, Running, Waiting Approval, Completed, Failed, or Cancelled.
- Runtime version, provider, model, prompt version, start/end timestamps, usage JSON, dispatch attempts, and sanitized error summary.

### NextGen Agent Step

Required fields:

- Parent Agent Run and monotonically increasing sequence.
- Step type: `Model`, `Tool`, `Policy`, `Proposal`, or `System`.
- Tool or operation name, status, timestamps, and latency.
- Globally unique idempotency key.
- Sanitized input and output JSON plus hashes of the complete payloads.
- Optional result Dynamic Link and sanitized error.

Secrets, raw credentials, payment tokens, hidden model reasoning, and unnecessary personal data must never be stored in step payloads.

### NextGen Action Proposal

Required fields:

- Parent Agent Run, required company, action type, target DocType, and risk level.
- Status: `Pending Approval`, `Approved`, `Rejected`, `Executing`, `Completed`, `Expired`, `Failed`, or `Superseded`.
- Unique idempotency key and expiry timestamp.
- Original proposal JSON, current preview JSON, warnings, policy snapshot, resource-version snapshot, and snapshot hash.
- Optional result Dynamic Link and legacy Chat Action reference.

Approved proposals must be revalidated. Material drift creates a superseding proposal rather than silently executing changed data.

### NextGen Approval Decision

Required fields:

- Action Proposal, reviewer, decision, reason, timestamp, and reviewed snapshot hash.
- Decision: `Approve`, `Reject`, or `Request Changes`.

Only an approval against the current proposal snapshot authorizes execution. Approval does not authorize document submission unless a separate domain policy explicitly allows it; Phases 1 and 2 do not.

### NextGen Inventory Exception

Required fields:

- Company, warehouse, item, signal date, horizon, formula version, and deterministic dedupe key.
- Type: `Stockout Risk`, `Excess Stock`, `Transfer Opportunity`, or `Data Quality`.
- Severity: `Low`, `Medium`, or `High`.
- Status: `Open`, `Proposed`, `Resolved`, `Dismissed`, or `Expired`.
- Evidence metrics, warnings, recommended strategy, and optional Action Proposal link.

## Service contracts

All payloads use versioned JSON schemas shared by integration tests. Every mutating request carries correlation_id and idempotency_key.

### Frappe to runtime

~~~text
dispatch_run(run_id, correlation_id, runtime_version) -> accepted
resume_run(run_id, correlation_id) -> accepted
cancel_run(run_id, reason) -> accepted
get_runtime_health() -> health
~~~

Dispatch is asynchronous. A timeout means unknown delivery, not failure; Frappe retries the same run ID and idempotency key.

### Runtime to Frappe trace and tool API

~~~text
claim_run(run_id, runtime_version) -> run_context
record_step(run_id, sequence, step_type, operation, sanitized_input, result, status, latency_ms, idempotency_key) -> step_id
complete_run(run_id, status, usage=None, error=None) -> None
execute_tool(run_id, tool_name, arguments, idempotency_key) -> sanitized_result
~~~

claim_run returns only bounded company, requester, agent, and prompt context. execute_tool rejects unknown tools and rechecks the service identity, requester access, company, warehouse, policy, and resource versions in Frappe.

### Proposal and approval API

~~~text
create_proposal(run_id, action_type, company, preview, policy_snapshot, expires_at, idempotency_key) -> proposal_id
revalidate_proposal(proposal_id) -> live_preview
approve_proposal(proposal_id) -> approval_id
reject_proposal(proposal_id, reason) -> approval_id
execute_proposal(proposal_id, idempotency_key) -> result_reference
~~~

The runtime may create a proposal but cannot approve it. Approval requires an authenticated human Frappe session. Execution remains a Frappe transaction.

### Inventory control-tower API

~~~text
get_inventory_control_tower(company, warehouse=None, horizon_days=30) -> dashboard_payload
refresh_inventory_signals(company, warehouse=None) -> queued_run_id
prepare_replenishment(company, item_code, target_warehouse, horizon_days=30) -> proposal_id
~~~

Control-tower calculations run in deterministic Frappe domain code. The runtime requests and explains results but never calculates authoritative quantities.

The current confirm_action(action_id) method remains a compatibility wrapper for one transition release and delegates to the generic Frappe proposal service.

## End-to-end event flow

1. A channel calls an authenticated Frappe method.
2. Frappe validates requester access and company, creates a Queued Agent Run, and returns the correlation ID.
3. A Frappe dispatcher sends the run ID to the runtime. Repeated delivery is safe.
4. The runtime claims the run and loads its version-controlled agent definition.
5. Model-selected tools are sent to execute_tool; the runtime never executes ERP logic locally.
6. Frappe authorizes each operation and runs the read or deterministic calculation.
7. A write intent becomes an Action Proposal. The runtime cannot create or submit an ERP document directly.
8. A human reviews the live preview in Frappe. Approval and execution revalidate current ERP state and policy.
9. Frappe records the result and the runtime completes the run.

## Domain ownership

| Component | Owns | Must not own |
|---|---|---|
| Agent Runtime microservice | Orchestration, provider adapters, prompt and agent versions, tool loop | ERP data, permissions, policy decisions, approval, authoritative calculations |
| Frappe agent gateway | Authentication context, durable dispatch, run/step persistence, versioned integration API | Model-provider orchestration |
| Frappe policy and proposal services | Company policy, risk, previews, expiry, approval, revalidation, idempotent execution | Free-form model decisions |
| ERP domain modules | Sales, procurement, inventory, warehouse tools and deterministic commands | Channel or model concerns |
| ERPNext | Documents, controllers, permissions, stock and financial posting | Agent orchestration |
| Channels | User experience and correlation propagation | Domain rules or direct runtime access |

## Microservice boundary rule

Phase 1 creates the separate runtime immediately; extraction is no longer a later roadmap step. Distributed consistency is controlled through durable Frappe records, idempotent dispatch and callbacks, explicit state transitions, live revalidation, and fail-closed behavior. No visual workflow engine is introduced in Phase 1 or Phase 2.