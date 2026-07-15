# AgentOps — Visual Workflow Automation inside the Rails App (Modular Monolith)

## Context

Build **AgentOps**, a simplified business-friendly n8n for Next Gen ERP, INSIDE the existing
Rails app (`rails_app/`) as a modular monolith: Rails is source of truth, React/React Flow
handles the canvas, background jobs run workflow execution, OpenRouter is called only from
the backend. Prioritized per spec: Order Intake agent workflow, human review, execution
visibility, Excel/ERP integration, secure OpenRouter execution. NOT an n8n clone.

**Locked decisions (user-confirmed):**
1. **Solid Queue + SQLite** (no Postgres/Redis on this machine; all engine code against
   Active Job so Sidekiq is a later config swap)
2. **Vite Ruby SPA** (vite_rails; React 19 + TS in `app/javascript/agent_ops/`, mounted at
   `/agent_ops`; existing importmap/Stimulus ERB pages untouched)
3. **Business-action nodes write to REAL ERP models** (Order, Product, PaymentSlip,
   TaxDocument) + AuditLog — demo workflow creates orders visible in `/reviews`
4. User HAS an OpenRouter key (entered via encrypted Credentials UI)
5. Workspace == existing `Merchant` (no new Workspace model); Pundit for authorization
6. No "Grill Me" skill exists on this machine → do the critical review manually after build
   (couple: Rails coupling, Sidekiq-path reliability, node isolation, secret leakage,
   version immutability, safe resume, retry duplication, SME usability, service extraction)

All paths relative to `rails_app/`. Ruby via `PATH=/Users/santi/.rbenv/versions/3.4.9/bin:$PATH`.

## 1. Setup

**Gems:** `solid_queue`, `vite_rails`, `pundit`, `faraday`, `faraday-retry`, `rack-attack`,
dev: `foreman`.
- Solid Queue: `bin/rails solid_queue:install`; separate queue DB
  (`storage/development_queue.sqlite3`) in database.yml; adapter `:solid_queue` +
  `connects_to` in environments; worker via `bin/jobs`.
- AR encryption: `bin/rails db:encryption:init` → keys into credentials (master.key exists);
  `Credential encrypts :data`; extend `filter_parameter_logging.rb` with
  `:data, :api_key, :secret, :token, :authorization`.
- Vite: `bundle exec vite install`; **installer caveats**: remove vite tags it injects into
  `layouts/application.html.erb`, delete `app/frontend/`, set `config/vite.json`
  sourceCodeDir to `app/javascript`, entrypoints dir `entrypoints`. Add
  `@vitejs/plugin-react` + `@tailwindcss/vite` to vite.config.ts.
- npm: react@19, react-dom, react-router-dom, @xyflow/react, zustand, zod, tailwindcss (v4),
  clsx, tailwind-merge, lucide-react + TS types.
- Replace `bin/dev` with foreman + `Procfile.dev`: `web: bin/rails server -p 3000`,
  `vite: bin/vite dev`, `jobs: bin/jobs`.

## 2. Data model — JSON-only versioned definitions (no node/edge tables)

Definitions are immutable snapshots; React Flow serializes `{nodes, edges}` natively;
no cross-workflow node queries needed. `Workflow.draft_definition` = editor working copy;
`save_version` snapshots into immutable `WorkflowVersion.definition`; `activate` sets
`active_version_id`.

7 migrations, `agent_ops_` table prefix (models under `app/models/agent_ops/` with
`table_name_prefix`):

| Table | Key columns |
|---|---|
| workflows | merchant/user FK, name, status draft\|active\|archived, active_version_id, `draft_definition json`, webhook_token uniq |
| workflow_versions | workflow FK, version int (uniq per workflow), `definition json`, created_by, notes |
| credentials | merchant FK, name, integration_type, `data` (encrypted), `secret_hint` (••••+last4 stored at write, reads never decrypt), status, last_tested_at |
| executions | workflow+version+merchant FK, trigger_source manual\|test\|webhook\|schedule, status queued\|running\|waiting_review\|succeeded\|failed\|cancelled, input/context json, error, timing |
| node_executions | execution FK, node_key (uniq per execution), node_type, status (incl. skipped/waiting_review), input/output json, error, attempts, timing, `ai_meta json` (model/tokens/latency) |
| human_review_tasks | execution/node_execution/workflow/merchant FK, status pending\|approved\|rejected, payload/resolution json, reviewer, resolved_at |
| workflow_templates | global: name, description, category, definition json, position |

Definition node shape: `{key, type, label, position:{x,y}, config:{..., retry:{max_attempts,
backoff_seconds}, on_error: "fail"|"continue"}}`; edges `{id, source, target, source_handle}`
(condition nodes emit handle "true"/"false").

**Seeder edit:** add drinking-water product to `app/services/demo_seeder.rb` catalog —
`["WATER-DRINK-600-P12", "น้ำดื่มตราคริสตัล 600มล. (แพ็ค 12)", "แพ็ค", 95, 80, 40,
%w[น้ำดื่ม น้ำเปล่า ขวดเล็ก]]` — so the demo Thai message resolves in SKU matching.

## 3. Rails file tree

```
app/models/agent_ops/{workflow,workflow_version,credential,execution,node_execution,
                      human_review_task,workflow_template,application_record}.rb
app/controllers/agent_ops_controller.rb                 # GET /agent_ops → SPA shell
app/controllers/api/agent_ops/{base,workflows,executions,human_review_tasks,credentials,
                               templates,hooks}_controller.rb
app/policies/agent_ops/*.rb                             # Pundit, merchant-scoped
app/jobs/agent_ops/{execute_workflow_job,resume_workflow_job}.rb
app/services/agent_ops/
  demo_seeder.rb  definition_validator.rb  secret_scrubber.rb
  execution_engine/{runner,graph,variable_resolver,node_runner,signals}.rb
  node_handlers/{base,registry}.rb + one file per node type (~27)
  open_router/{client,prompt_template,json_extractor,errors}.rb
app/serializers/agent_ops/*.rb                          # plain POROs, secrets masked
app/views/layouts/agent_ops.html.erb                    # csrf_meta_tags + vite tags ONLY
app/views/agent_ops/show.html.erb
config/initializers/{rack_attack,agent_ops}.rb
db/agent_ops_templates/                                 # template definition builders
storage/agent_ops_exports/.keep
Procfile.dev
```

Routes: `get "agent_ops(/*path)" => "agent_ops#show"` (SPA deep links) + `namespace :api do
namespace :agent_ops` — workflows CRUD + save_version/activate/test_run + meta, executions
index/show, human_review_tasks approve/reject, credentials CRUD + test, templates
index/instantiate, `post "hooks/:workflow_id/:token"` (no session auth; secure_compare +
rack-attack).

## 4. Execution engine

- **Job flow:** controller creates Execution(queued) → `ExecuteWorkflowJob` → claims via
  optimistic transition (`where(status:"queued").update_all(status:"running")==1` else no-op)
  → `Runner.run!`.
- **DAG:** Kahn topo-sort + cycle detection at `save_version` (422) and defensively at run.
  Active-edge traversal: node runs iff ≥1 incoming edge fired; Condition fires only the edge
  matching its output branch; unreached nodes recorded `skipped`.
- **State is DB-backed:** Runner rebuilds completed node outputs from node_executions —
  makes pause/resume deterministic.
- **Variables:** `{{nodes.<key>.output.<dot.path>}}`, `{{trigger.<path>}}`,
  `{{execution.id}}`; full-string expressions resolve to raw values (arrays/objects pass
  through — needed for products[] → SKU matching); embedded ones interpolate to_s;
  unresolvable path = clear config error. Regex-only, no eval.
- **Condition:** operators eq/neq/gt/gte/lt/lte/contains/present/blank; output
  `{result, branch, left, operator, right}`.
- **Pause/resume:** HumanReview handler returns `Signals::Pause` → HumanReviewTask(pending),
  NodeExecution + Execution `waiting_review`, job ends. Approve/reject endpoint: transactional
  optimistic UPDATEs (task pending→resolved, execution waiting_review→queued; 0 rows → 409 =
  double-resume guard) → `ResumeWorkflowJob` re-claims and continues with resolution as the
  node's output. Approval/ManualCorrection reuse the same machinery.
- **Retries:** per-node config, only for handler-flagged retryable errors (429/5xx/timeout),
  inline capped backoff, attempts recorded. `on_error: "continue"` for optional nodes.
  Top-level rescue marks execution failed (nothing stuck in `running`).

## 5. Node handlers

`Base` interface: `validate_configuration!`, `execute` (→ Result(output, ai_meta) | Pause |
Halt), `normalize_output`, `handle_error`, `retryable?`. **Registry** = frozen hash type →
class-name string, lazily constantized; also exposes `Registry.metadata` served at
`GET .../workflows/meta` so server + SPA node catalogs can't drift.

~27 handlers. Real-model actions (+ AuditLog with explicit `merchant:` — no `Current` in
jobs): CreateSalesOrder (real Order+lines; awaiting_review when confidence < 0.85 →
appears in `/reviews`), UpdateInventory (Product stock), ReconcilePayment (`PaymentSlip#match!`
with workflow.user), PrepareETaxDocument (TaxDocument + validate_fields!), SkuMatching
(Product name/aliases lookup), UpdateExcel (append CSV under `storage/agent_ops_exports/` —
CSV stdlib, no gem). Simulated-with-`"demo": true` outputs: Ocr, SendToErp, SendLineReply,
SendEmail. Config-only stubs: ScheduledTrigger, NewOrderTrigger, FileUploadTrigger.
DataTransformation = declarative field mapping (no code eval).

## 6. OpenRouter client (real AI, no fakes)

Faraday → POST `https://openrouter.ai/api/v1/chat/completions`; host hard-pinned (SSRF
guard); key decrypted from Credential inside the call, never logged (`SecretScrubber` strips
from exception messages too). Timeouts 10s/60s; faraday-retry 2× backoff on 429/5xx/timeout.
Structured output: `response_format: json_schema (strict)` + `JsonExtractor` fallback
(strip fences → parse → balanced-brace scan → ONE repair round-trip → fatal JsonParseError —
never invent data). Error taxonomy: ConfigurationError/AuthError (fatal),
RateLimitError/ServerError/TimeoutError (retryable), BadRequestError, JsonParseError.
Token usage + latency + model → `NodeExecution.ai_meta`. Credential test = GET `/models`
(free). PromptTemplate uses the same VariableResolver.

## 7. SPA (`app/javascript/agent_ops/`)

- Router: react-router `basename="/agent_ops"` — pages: WorkflowList, WorkflowEditor,
  ExecutionHistory (`/workflows/:id/executions`), ExecutionDetail, Credentials, Templates,
  Reviews (pending human tasks).
- Zustand editor store: React Flow nodes/edges + handlers, selectedNodeKey, per-node Zod
  validation issues, runtime `{executionId, status, nodeStatus{}}`, actions
  save/saveVersion/activate/testRun. Node `key` generated once (label + nanoid), never changes.
- Nodes: single `AgentOpsNode` driven by `nodeDefinitions.ts` (type → EN/TH label, category,
  icon, Zod config schema, defaults, handles); `ConditionNode` with two labeled source
  handles; status ring: gray unconfigured / blue ready / animated running / green success /
  orange waiting review / red failed / dimmed skipped.
- Config panel: generic `FormRenderer` introspecting Zod schema (input/select/switch/number/
  textarea/credential picker + upstream-variable insert helper); bespoke forms for
  OpenRouterAgent, Condition, DataTransformation.
- API client: fetch wrapper with `X-CSRF-Token` from meta tag, same-origin session cookie,
  401 → `/login`. Polling hook: 1.5s while queued/running, 5s while waiting_review, stop on
  terminal — paints canvas node states and ExecutionDetail.
- Business language, Thai-first labels ("ตรวจสอบโดยพนักงาน / Human Review", "ส่งเข้า ERP").
- **Tailwind isolation is physical:** Tailwind 4 imported only by `entrypoints/agent_ops.tsx`,
  emitted only in `layouts/agent_ops.html.erb`; ERB pages never load it. Dark sidebar
  (#0b1220-ish), light canvas, blue/cyan/teal accents, no glassmorphism.

## 8. Seeds & demo

`AgentOps::DemoSeeder` called from existing `DemoSeeder#run!` (+wipe of 7 tables) so
`/demo/reset` rebuilds everything. Seeds: unconfigured OpenRouter Credential placeholder,
5 templates (LINE Order→Excel, Live Commerce Capture, PromptPay Reconciliation, e-Tax
Preparation, Low-Stock Procurement), and fully-configured demo workflow **"AI Order Intake
Agent"**: line_message_trigger → ai_extract (structured schema: customer/delivery/
products[{name,qty,unit}]/confidence/requires_human_review) → sku_matching →
check_confidence (`{{nodes.ai_extract.output.confidence}} gte 0.85`) → [true]
create_order / [false] human_review → create_order_reviewed → update_excel →
send_line_reply. Preloaded test input: `พี่เอาน้ำดื่มขวดเล็ก 12 ลัง ส่งสาขาบางนาเหมือนเดิมครับ`.
`templates#instantiate` deep-copies definition, rewiring credential placeholders.

## 9. Build order (each step bootable; check ERB pages after each)

1. Gems + Solid Queue (verify trivial job runs via bin/jobs)
2. AR encryption + filter_parameters
3. **Vite + React hello-world at /agent_ops + Tailwind — GATE: /dashboard & /integrations
   pixel-identical** (kills the top integration risk first)
4. Migrations + models + policies + seeder skeleton
5. API: workflows CRUD/save_version/activate + DefinitionValidator (cycle check) + templates
6. SPA: WorkflowList + Editor (canvas, node library w/ search, config panel, TopBar) +
   Templates — round-trip save/load
7. Engine core + basic handlers — **Minitest for topo/branch/variables/skip/pause BEFORE UI wiring**
8. ExecuteWorkflowJob + test_run + executions API + History/Detail pages + polling
9. OpenRouter client + Credentials UI (masked) + AI handlers — **early smoke via
   `bin/rails runner` with the real Thai message**
10. Human review pause/resume + Reviews page + 409 double-resume guard
11. Webhook trigger (token + rack-attack) + remaining connectors
12. Full seeding (5 templates + demo workflow + water product)
13. Hardening: throttles, double-enqueue tests, stuck-execution rescue, audit entries
14. Secret-scrub audit, Brakeman/rubocop, README update
15. E2E verification, then **manual critical review** (the "grill" — coupling, reliability,
    secrets, resume safety, retry duplication, SME usability, extractability)

## 10. Verification (end-to-end)

- `bin/dev` runs web+vite+jobs; existing ERB pages unchanged (no Tailwind bleed)
- `/agent_ops` SPA loads; templates instantiate; editor drag/connect/configure/save/version/
  activate round-trips; validation errors shown per node before activation
- Credentials: real OpenRouter key → Test → connected; responses/logs show only ••••+last4;
  grep dev log for key = zero hits
- Test Run with the Thai message → AI node hits OpenRouter for real (ai_meta shows model +
  token counts + latency); SkuMatching resolves the water SKU via aliases
- Low-confidence path (threshold 0.99) → orange pause at Human Review; second approve → 409;
  approve resumes → real Order created, visible in existing `/reviews`; AuditLog rows exist
- CSV export written under storage/agent_ops_exports/ and opens in Excel
- ExecutionDetail shows every node input/output/error/timing; branch taken visible; skipped
  nodes dimmed; no secrets anywhere
- Webhook curl works; wrong token 401; flood → 429; `/demo/reset` rebuilds all;
  `bin/rails test` green; Brakeman clean

## Critical existing files touched

- `config/routes.rb` (SPA route + API namespace)
- `config/database.yml` (queue database)
- `app/services/demo_seeder.rb` (water product + AgentOps seeder hook)
- `bin/dev` + new `Procfile.dev`
- `config/initializers/filter_parameter_logging.rb`
- `app/views/layouts/application.html.erb` (only to REVERT vite installer's injection)

## Top risks & derisking

1. **Vite/Rails coexistence** → step 3 gate before any feature work; vite tags confined to
   dedicated layout
2. **Engine correctness** (branches, resume, skip) → pure service over persisted state,
   Minitest-first; optimistic single-UPDATE transitions
3. **Structured output reliability** → step-9 runner smoke test with real Thai message;
   json_schema + extraction fallbacks; fail loud, never fake
4. **SQLite concurrency** → separate queue DB file, WAL defaults, single worker in dev
