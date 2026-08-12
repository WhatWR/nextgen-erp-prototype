# Agent Runtime and the Frappe agent gateway

> **Document status:** Current capability of the Phase 1 implementation. The approved architecture it implements is owned by [docs/AI_HANDOFF](AI_HANDOFF/README.md); the phase contract is [Phase 1](AI_HANDOFF/PHASE_1_AGENT_PLATFORM.md).

Agent orchestration runs in a separately deployed microservice
([services/agent-runtime](../services/agent-runtime/README.md)). ERPNext stays
the transaction, inventory and accounting source of truth, and Frappe keeps the
authentication context, durable audit, company policy, approvals and every ERP
read and write.

## Where each responsibility lives

| Concern | Owner |
|---|---|
| Model calls, provider retries, prompt and agent versions, the tool loop | `services/agent-runtime` |
| Authentication context, durable dispatch, run and step records, versioned API | `nextgen_erp.agent_gateway` |
| Company policy, proposals, approvals, revalidation, idempotent execution | `nextgen_erp.action_proposals` |
| Allowlisted deterministic tools and domain commands | `nextgen_erp.domain_tools` |
| Documents, controllers, permissions, stock and financial posting | ERPNext |

The runtime has no MariaDB connection, no shared Redis, no site files, no
generic DocType CRUD, no raw SQL and no arbitrary Python. It reaches ERP only
through the whitelisted methods in `nextgen_erp/agent_gateway/api.py`.

## Records

| DocType | Holds |
|---|---|
| `NextGen Agent Run` | Correlation ID, company, agent, trigger, requester, execution identity, status, runtime/model/prompt versions, dispatch attempts, sanitized error |
| `NextGen Agent Step` | Ordered sanitized trace: one step per model request, tool call, policy result, proposal, error and completion |
| `NextGen Action Proposal` | Immutable original proposal, live preview, warnings, policy snapshot, resource versions, snapshot hash, expiry, result |
| `NextGen Approval Decision` | Reviewer, decision, reason, and the exact snapshot hash they reviewed |
| `NextGen Automation Policy` | One per company: enablement, mode, warehouses, item groups, allowlists, limits, service user, and the DocTypes the assistant may propose |
| `NextGen Agent Workflow` | Phase 3: a validated graph of agent nodes, versioned and company-scoped |

## Boundary contract

Both services carry an identical copy of the `v2` schemas
(`nextgen_erp/agent_gateway/contracts.py` and
`nextgen_agent_runtime/models/contracts.py`). Each side's test suite loads the
other copy and compares `contract_fingerprint()`, so the two cannot drift while
staying independently deployable.

```text
Frappe  -> runtime   dispatch_run · resume_run · cancel_run · get_runtime_health
runtime -> Frappe    claim_run · record_step · execute_tool · create_proposal
                     create_node_run · complete_run
human   -> Frappe    start_agent_run · get_run · get_proposal · revalidate_proposal
                     approve_proposal · reject_proposal · execute_proposal
                     list_workflows · get_workflow · save_workflow · delete_workflow
                     start_workflow_run · get_workflow_run · get_workflow_history
```

The runtime can create a proposal. It cannot approve one, and no gateway method
exists that would let it try: every decision endpoint calls `require_human()`,
which rejects the service identity.

## Invariants enforced in code

1. Every run, proposal and decision has an explicit company, and a warehouse
   from another company is rejected even when a model asks for it.
2. The requester, execution identity, company and tool allowlist are read from
   the persisted run. Payload overrides are rejected, not merged.
3. Configuration narrows the tool allowlist and never widens it: the effective
   set is the release's agent definition intersected with the run's list.
4. Tools run under the requester's own ERP permissions, not the service
   identity's.
5. Idempotency keys are enforced at dispatch, run, step, tool, proposal and
   document boundaries. A retry replays; it never duplicates.
6. Approval applies to one snapshot hash and expires with the proposal.
   Material drift supersedes the proposal instead of executing changed data.
7. Secrets, hidden model reasoning and unnecessary personal data are redacted on
   both sides; the complete payload's hash is kept so audit stays verifiable.
8. Failures are fail-closed. There is no automatic fallback to embedded
   orchestration; runtime failure leaves an observable Queued or Failed run.

## Configuration

**Frappe — NextGen AI Settings → Agent Runtime Microservice**

| Field | Meaning |
|---|---|
| Enable Agent Runtime | Deployment switch |
| Agent Runtime URL | Private base URL; never reachable from a browser |
| Agent Runtime Service Token | Dispatch credential presented to the runtime |
| Runtime Service User | Restricted, non-Desk user holding the `NextGen Agent Runtime` role |
| Legacy Orchestration Rollback | Transition-only operator switch; a failure never sets it |

**Frappe — NextGen Automation Policy (one per company)** enables the runtime
per company, sets the mode (`Shadow`, `Approval Required`, `Automatic Draft`),
the allowed warehouses, item groups and allowlists, the proposal expiry and the
value limits. A company without a policy is treated as disabled Shadow.

**Runtime** reads its configuration from the environment only; see the
[service README](../services/agent-runtime/README.md).

## Migration and compatibility

- `create_company_automation_policies` converts the global procurement settings
  into a policy for the current default company. Every other company gets a
  disabled Shadow policy; an active policy is never copied automatically.
- `backfill_agent_records` maps each `NextGen Chat Action` to one synthetic
  Agent Run and one Action Proposal, keeping the original and linking both ways.
  Both patches are safe to rerun.
- New chat previews dual-write the legacy Chat Action and the generic proposal,
  and the two are kept reconciled as the action is confirmed, cancelled,
  superseded or expired.
- `confirm_action` accepts either identifier for one transition release.
- `get_reconciliation_report` compares agent, company, requester, chosen write
  tool, preview hash and error classification between the two records.

**Phase 1 execution boundary.** An approved proposal executes through its
originating chat action, so there is exactly one hardened write path with its
own locking, revalidation and idempotency. Proposals without a chat action are
recorded and approvable but not executable until Phase 2 adds its own
deterministic executor. No path submits a Purchase Order, Material Request,
Stock Entry or accounting document.

## Operating it

- `nextgen_erp.agent_gateway.dispatch.maintenance` runs every five minutes: it
  redelivers unacknowledged runs, expires due proposals and fails runs that were
  claimed but never reported back.
- A run exhausts delivery after five attempts and is failed visibly.
- `get_runtime_health` reports the runtime's version, contract version, enabled
  agents and in-flight count, and whether the contract version matches Frappe's.

## Tests

```bash
cd services/agent-runtime && PYTHONPATH=src:tests python -m unittest discover -s tests -t tests
```

```bash
bench --site <site> run-tests --app nextgen_erp --module nextgen_erp.tests.test_agent_gateway
```

`nextgen_erp/tests/test_agent_contracts.py` needs no site and can be run with
plain `python -m unittest` from `apps/nextgen_erp`.
