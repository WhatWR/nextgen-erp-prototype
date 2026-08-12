# Phase 3 — Agent workspace

**Document status:** Implemented; disabled by default
**Code status:** Complete and verified against a live site
**Depends on:** Phase 1 records, gateway and runtime

## Outcome

Two capabilities, sharing one set of records:

1. **A capability chat.** An `assistant` agent that can propose any document the
   signed-in user's role permits — "add an item for me, food" creates an Item —
   without anyone hand-writing a tool per DocType.
2. **A workflow designer.** A visual canvas that arranges registered agents into
   a graph and executes it, with per-node prompts, run history and export/import.

## Part A — generic write intent

`nextgen_erp/domain_tools/documents.py` is the assistant's tool module. Five
layers stand between a model's suggestion and an ERP row, in this order:

| # | Control | Where |
|---|---|---|
| 1 | Hard DocType denylist, unwidenable by policy, role or Administrator | `DENIED_DOCTYPES`, `DENIED_PREFIXES` |
| 2 | Code-level `WRITABLE_DOCTYPES` maximum, narrowed by the company policy's `writable_doctypes` | `writable_doctypes()` |
| 3 | The requesting user's own Frappe permissions | `registry.acting_as()` + `has_permission` |
| 4 | Meta-filtered values: real, writable, non-protected fields only | `clean_values()` |
| 5 | Human approval of an Action Proposal before anything is written | `action_proposals.service` |

Layer 1 is the one that matters most. "Everything the user's role can do" applied
literally to a System Manager includes creating a `Server Script`, which is
remote code execution through chat. That door is closed in code, above every
configuration surface.

### Preview by rollback

The preview is produced by building the real document and letting its own
controller validate it inside a savepoint, then discarding the write:

```text
frappe.db.savepoint(...) → new_doc → update → insert → capture → rollback(save_point=...)
```

Defaults, fetched values, naming and validation all behave exactly as they will
on approval, and a controller rejection becomes a readable error the model can
correct — with no row left behind.

### Execution

`action_proposals.service.EXECUTORS` maps an action type to its one write path.
The copilots keep their original chat-action route; `prepare_document` inserts
through the controller under the requester. The whole boundary is re-asserted
immediately before the write rather than trusted from preview time. Nothing is
ever submitted.

## Part B — workflow designer

| Piece | Where |
|---|---|
| `NextGen Agent Workflow` DocType (graph JSON, company, enabled, version) | `doctype/nextgen_agent_workflow` |
| Graph parsing, validation and topological order | `agent_gateway/graph.py` |
| Workflow CRUD, dispatch, node runs, history | `agent_gateway/workflows.py` |
| Graph executor | `services/agent-runtime/.../orchestration/workflow.py` |
| Vue + `@vue-flow` designer | `public/js/agent_workflow/`, Desk page `agent-workflow` |

A workflow run is one parent Agent Run plus one child run per node, all sharing
the parent's correlation ID. Nodes execute in topological order through the
unchanged single-agent `RunExecutor`, so tool allowlists, company scoping,
proposals, approval and audit are identical to an ordinary run.

Graphs are validated **on save**, not at execution: unknown agents, duplicate
node ids, dangling edges, self-edges and cycles are all refused, so the executor
never has to reason about a malformed definition.

The designer reuses Frappe's own stack — Vue 3, `@vue-flow/core`,
`@vue-flow/background`, esbuild bundles and Desk Pages — mirroring
`frappe/public/js/workflow_builder`. `@vue-flow` resolves through esbuild's
`NODE_PATHS`, which include every installed app's `node_modules`.

## Contract v2

`run_context` gained optional `workflow`, `workflow_node`, `parent_run` and
`graph`, and `create_node_run_request` was added. Every addition is optional and
`v1` stays accepted for one release, because the two services deploy separately.
Both copies are fingerprint-checked by each side's tests.

## Corrections to earlier phases

- `NextGen Agent Run.correlation_id` is no longer unique. It groups records; one
  operation can span several runs and a workflow's nodes share their parent's ID.
- `is_service_identity` no longer treats Administrator as the service identity.
  Administrator implicitly holds every role, so the old check made it impossible
  for Administrator to approve anything.
- An Approval Decision only refuses a reviewer that is genuinely a service
  identity. A human approving their own proposal is the normal flow.
- `create_run` no longer defaults its idempotency key to the correlation ID
  alone, which silently collapsed two runs in one request into the first.

## Enabling it

1. `NextGen AI Settings → Enable Staff Chat`.
2. `NextGen Automation Policy` for the company: `Enabled`, and list the DocTypes
   the assistant may propose in `Assistant Writable DocTypes` (empty means none).
3. For workflows only: `Enable Agent Runtime` plus the policy's `Runtime Enabled`,
   and a deployed runtime.

## Completion criteria

- The denylist refuses code-executing and permission DocTypes for Administrator.
- A policy cannot widen the code allowlist.
- A preview leaves no row behind and a controller rejection is readable.
- Approval creates a draft through the controller; drift supersedes instead.
- A cyclic or unknown-agent graph cannot be saved.
- A workflow parks at Waiting Approval when a node proposes, and never approves.
- Restarting a workflow replays its node runs instead of duplicating them.
