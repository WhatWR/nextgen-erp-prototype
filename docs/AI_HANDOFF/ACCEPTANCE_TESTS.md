# Acceptance tests

**Document status:** Required verification contract for Phase 1 and Phase 2

## Documentation handoff

- Every active Markdown document states Current Capability, Target Contract, Roadmap, or Archived status.
- Root and application READMEs link to `docs/AI_HANDOFF/README.md`.
- All relative Markdown links resolve after archival moves.
- Archived documents remain byte-for-byte unchanged apart from their filesystem location.
- `git diff --name-only` for the documentation-handoff change contains Markdown additions, modifications, and moves only.

## Phase 1 data and migration

- New Agent Run, Step, Proposal, Approval, and company-policy schemas migrate on a populated site.
- Backfill is idempotent and retains original Chat Actions.
- Every legacy action maps to exactly one synthetic run and proposal.
- Dual-write produces matching agent, user, company, status, expiry, preview hash, and result references.
- Pending legacy actions remain confirmable through the compatibility wrapper.
- Expired, rejected, completed, and failed actions cannot execute again.
- No legacy records are deleted at cutover.

## Identity, permission, and company isolation

- Guest access to runtime, proposal, policy, and control-tower methods is rejected.
- A user with access to Company A cannot query, infer, approve, or execute Company B data.
- A warehouse from another company is rejected even if supplied by a model or client request.
- The scheduled service user can act only for companies and warehouses granted through User Permissions.
- Agent tool allowlists reject cross-agent and unknown tools.
- Model arguments cannot override authenticated user, service identity, company, LINE sender, or approval reviewer.
- Persisted steps contain no configured provider key, LINE token, payment secret, or unredacted credential.

## Microservice boundary and resilience

- Browser, LINE, webhook, and Desk clients cannot call the Agent Runtime directly.
- Runtime requests without the restricted service identity are rejected by Frappe.
- The runtime has no Frappe database, shared Redis, site-file, generic DocType CRUD, raw SQL, or arbitrary Python access.
- Contract tests verify the same versioned JSON schemas on both sides of the boundary.
- Dispatch timeout and duplicate delivery reuse the same run and idempotency key.
- Runtime restart resumes or safely fails a persisted run without duplicating steps, proposals, tool effects, or ERP documents.
- Correlation ID, run ID, requester, service identity, company, and runtime version are visible across Frappe and runtime logs.
- Runtime or network failure leaves an observable Queued or Failed run and never triggers an automatic embedded fallback.
- The runtime may create a proposal but cannot approve it or execute an unapproved proposal.
- Frappe revalidates requester access, service access, company, policy, and live resources for every tool call.
## Run and proposal lifecycle

- Each entry channel creates or propagates one correlation ID.
- Tool, model, policy, proposal, approval, execution, and error steps have stable ordering and latency.
- Retrying the same idempotency key does not duplicate a tool effect, proposal, or ERP result.
- An approval for an old preview hash is rejected.
- Proposal expiry blocks execution.
- Material price, stock, demand, supplier, policy, permission, or UOM drift triggers revalidation failure and a fresh proposal.
- An execution failure rolls back the business transaction and leaves an auditable Failed proposal/run.

## Existing-flow regression

- LINE webhook signatures and event idempotency remain valid.
- Thai product aliases and UOM matching behave as before.
- Questions never create orders; order-shaped messages continue through intake.
- Customer confirmation, invoice resend, delivery-note resend, PromptPay, and payment-slip flows remain scoped to the verified sender.
- Sales Copilot cannot call procurement tools and Procurement Copilot cannot call sales write tools.
- Existing Sales and Procurement preview, revise, cancel, expire, and confirm flows preserve their response shapes.
- Forecast `nextgen-procurement-v1` returns identical results for identical inputs.

## Phase 2 signals and replenishment

- Signal generation reads only the selected company and eligible warehouses.
- Repeated daily and on-demand refreshes reuse the deterministic exception key.
- Actual, reserved, incoming, safety-stock, target, shortage, excess, and donor-surplus calculations match fixtures.
- Blocking data-quality warnings prevent draft creation.
- A transfer is proposed only when one eligible same-company donor covers the complete shortage and remains above its floor.
- Donor ranking is stable for equal inputs.
- When no donor covers the complete shortage, purchase is selected for the complete rounded shortage.
- Cross-company, transit, group, disabled, and policy-excluded warehouses are never donors.
- Shadow mode creates no Material Request.
- Approval Required creates a Draft Material Request and never submits it.
- Policy-gated draft automation creates only Draft Material Requests for allowlisted items.
- No test path creates or submits a Stock Entry automatically.

## Cockpit and language

- The cockpit requires an accessible company before returning metrics.
- Company and warehouse filters apply to every KPI, exception, proposal, and activity entry.
- High risk, excess, transfer, purchase, pending proposal, and data-quality KPIs link to supporting records.
- Proposal details show formula version, inputs, source dates, warnings, policy result, and before/after position.
- Thai and English labels render as UTF-8 without mojibake.
- Users without source-document read permission see a safe summary rather than a leaked link or value.

## Phase 3 capability chat and workflows

- A code-executing or permission DocType is refused for Administrator, not merely for a limited role.
- A company policy cannot add a denied DocType or one outside the code allowlist.
- An empty policy allows the assistant to create nothing.
- A preview leaves no row behind, and a controller rejection is returned as a readable error.
- Unknown, read-only and protected fields are refused rather than silently dropped.
- A user without create permission is refused even when the policy allows the DocType.
- Approval creates a draft through the document's controller and never submits it.
- Policy, permission or data drift between approval and execution supersedes the proposal instead of writing.
- A graph with an unknown agent, duplicate node id, dangling edge, self-edge or cycle cannot be saved.
- Node runs share the parent run's correlation ID and carry the previous node's answer forward.
- A node that produces a proposal parks the parent at Waiting Approval and stops later nodes.
- Re-dispatching a workflow run replays its node runs instead of duplicating them.
- Both contract copies agree on the v2 fingerprint, and v1 payloads are still accepted.

## Environment baseline remediation

- `git submodule status` reports initialized pinned checkouts for ERPClaw, ERPClaw Web, Frappe, and ERPNext.
- The ERPClaw proxy test finds the patched proxy file.
- SQLite and HTTP test resources close cleanly on Windows.
- Standalone order-intake and Frappe test suites complete without unexplained environment errors before Phase 1 cutover.
