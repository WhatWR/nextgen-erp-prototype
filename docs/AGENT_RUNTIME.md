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
| `NextGen Agent Workflow` | A validated graph of typed nodes, versioned and company-scoped, plus its triggers |
| `NextGen Workflow Node Result` | One row per deterministic node per run: status, sanitized output envelope, artifact |
| `NextGen Agent Connection` | An outbound HTTP target a `source.api` node may use: base URL, path allowlist, encrypted credential |

## Boundary contract

Both services carry an identical copy of the `v2` schemas
(`nextgen_erp/agent_gateway/contracts.py` and
`nextgen_agent_runtime/models/contracts.py`). Each side's test suite loads the
other copy and compares `contract_fingerprint()`, so the two cannot drift while
staying independently deployable.

```text
Frappe  -> runtime   dispatch_run · resume_run · cancel_run · get_runtime_health
runtime -> Frappe    claim_run · record_step · execute_tool · create_proposal
                     create_node_run · execute_node · get_workflow_state · complete_run
human   -> Frappe    start_agent_run · get_run · get_proposal · revalidate_proposal
                     approve_proposal · reject_proposal · execute_proposal
                     list_workflows · get_workflow · save_workflow · delete_workflow
                     start_workflow_run · get_workflow_run · get_workflow_history
                     reveal_webhook_token
external -> Frappe   workflow_webhook (token-authenticated, runs as a named user)
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
9. A workflow cannot approve its own work. A node that produces a proposal parks
   the parent run at `Waiting Approval` and later nodes do not run. Executing
   the proposal resumes the graph; rejecting it cancels the run.
10. The runtime never executes a source, sink or API call itself. It names a
   node; Frappe reads that node from the stored graph, rebuilds its inputs from
   the outputs it already persisted, and runs it under the requester's own
   permissions. The runtime has no file, database or outbound-network access.

## Workflow node kinds

A workflow is a DAG of typed nodes. An **agent node** runs a registered agent as
its own child run — same allowlist, proposals, approval and audit as a
single-agent run. Every other kind is deterministic and executes inside Frappe.

| Kind | Does | Bounded by |
|---|---|---|
| `agent` | Runs one registered agent | Company policy and the code tool allowlist |
| `source.query` | Reads ERP rows | `frappe.get_list` as the requester; denylisted DocTypes refused; scoped to the run's company |
| `source.excel` | Parses an attached `.xlsx`/`.csv`/`.tsv` | The requester's read permission on the File; 500 rows |
| `source.api` | Calls an HTTP endpoint | A `NextGen Agent Connection`: its base URL, its path allowlist, its response size cap. The graph holds no URL or credential |
| `sink.report` | Writes an HTML/CSV/JSON file | Attached privately to the run |

Nodes pass an **envelope** — text, rows, documents, artifacts — and a node reads
the envelopes of the nodes actually connected to it, so a graph that fans out and
merges behaves as drawn. Envelopes are redacted and row-bounded before storage,
and a prompt gets a sample rather than the whole result set.

A graph saved before typed nodes existed carries no `kind` and is read as a graph
of agent nodes, so it keeps running unchanged.

## Triggers

Manual execution from the designer always works and needs no configuration.
Beyond that a workflow may carry `NextGen Agent Workflow Trigger` rows:

| Trigger | Fires | Notes |
|---|---|---|
| Schedule | Cron, evaluated every five minutes against the trigger's own last run | A missed tick starts the workflow late rather than not at all |
| Webhook | `POST /api/method/nextgen_erp.agent_gateway.api.workflow_webhook` | Token in `X-NextGen-Workflow-Token`; compared with `hmac.compare_digest` |
| Document Event | `after_insert` / `on_update` / `on_submit` / `on_cancel` | Optional condition is a JSON object the server *compares*, never evaluates |

Every trigger names a `run_as` user: an unattended run still acts as somebody,
under their own permissions, and never as Administrator. A document a workflow
proposed does not retrigger that workflow.

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

```bash
bench --site <site> run-tests --app nextgen_erp --module nextgen_erp.tests.test_agent_workflows
```

`nextgen_erp/tests/test_agent_contracts.py` needs no site and can be run with
plain `python -m unittest` from `apps/nextgen_erp`.
