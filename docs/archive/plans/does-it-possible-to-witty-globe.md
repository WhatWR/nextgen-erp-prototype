# Plan: Make ERPNext the base, migrate order intake into ERPNext UI, ERPClaw as chat-only

## Context

Today the prototype has three separable pieces:

- **`apps/order-intake-api`** (Python stdlib + SQLite) — the NextGen AI layer: Thai
  extraction, confidence scoring, review/approval workflow, order-to-cash orchestration.
- **`vendor/erpclaw-web`** (SvelteKit, branch `nextgen-thailand`) — the review UI
  (`/order-intake`) plus an ERPClaw `ChatPanel` that calls ERPClaw's backend (`:8001`).
- **`vendor/erpnext` + `vendor/frappe`** — full **v16 source, not yet a running bench**
  (no `sites/`, `apps.txt`, MariaDB/Redis/wkhtmltopdf; Node is v26).

The goal: **ERPNext becomes the base — system of record AND UI (Frappe Desk).** The
order-intake review experience moves into ERPNext's native UI. The Python service is
kept (external microservice) but repointed to write ERPNext data through the
`ERPNextAdapter` built this session. ERPClaw is reduced to a chat surface that updates
ERPNext — **designed here but built in a later, deferred phase.**

This is the architecture in the opening brief: ERPNext owns business truth; NextGen AI
reads/writes it through documented methods; ERPClaw chat calls the same adapter.

## Decisions locked (from clarification)

| Fork | Choice |
|---|---|
| ERPNext runtime | **Build from the vendored v16 source** (native bench pointed at `vendor/frappe` + `vendor/erpnext`) |
| Iteration scope | Full end-state **planned**; execution = ERPNext up + order-intake migrated to Desk |
| Thai extraction/automation | **Stays external** in `order-intake-api`, calling ERPNext |
| ERPClaw chat | **Deferred** — designed as the last phase, not built first |

## Target architecture

```
LINE OA ──► order-intake-api (Python, external)
              • Thai extraction + confidence + automation policy  [KEPT]
              • pushes an "AI Order Intake" record into ERPNext (REST)
              • sends LINE replies (driven by ERPNext webhooks)
                     │  REST / whitelisted methods (ERPNextClient)
                     ▼
ERPNext (v16, BASE = system of record + Desk UI)
   nextgen_erp Frappe app:
     • DocTypes: AI Order Intake (+ Item child), LINE Customer Map, Automation Settings
     • Desk review queue (list + form + workspace) + approve/reject/edit
     • Whitelisted order-to-cash methods (already the ERPNextAdapter contract)
     • ERPNext webhooks → notify external service of DN/SI/Payment status
                     ▲
ERPClaw chat (PHASE 3, deferred) — action layer repointed to these same methods
```

Single source of truth: catalog/stock/customers/prices and all order-to-cash documents
live only in ERPNext. The external service holds no business tables — only the LINE
outbound queue and transient extraction state.

## Reuse — already built this session (the migration seam)

- `apps/order-intake-api/order_intake/erpnext_client.py` — stdlib ERPNext REST client
  (token auth, `get_list`/`get_doc`/`call_method`, injectable transport).
- `apps/order-intake-api/order_intake/erpnext_adapter.py` — `ERPNextAdapter` (the three
  order-to-cash methods, shadow default) and `ERPNextCatalogSource` (Item/Bin/Item Price
  reads). The **whitelisted-method contract in its docstring is what the Frappe app must
  implement**:
  - `nextgen_erp.api.create_sales_order(external_reference, customer, company, currency, delivery_date, items, reserve_stock)` → `{sales_order, pick_list}`
  - `nextgen_erp.api.deliver_and_invoice(external_reference, sales_order, pick_list, items)` → `{delivery_note, sales_invoice}`
  - `nextgen_erp.api.record_payment(external_reference, company, customer, currency, amount, reference_no, reference_date, sales_invoice)` → `{payment_entry}`
- `order_intake/service.py`, `matching.py`, `catalog.py` — extraction/confidence logic to keep.
- `select_transaction_adapter()` already lets `ORDER_BACKEND=erpnext` flip the service to ERPNext.

## Phase 0 — Stand up ERPNext v16 Desk from vendored source

Heaviest/riskiest phase (infra). Native bench on macOS.

1. Runtime deps: `brew install mariadb@10.6 redis wkhtmltopdf`; start MariaDB + Redis;
   set MariaDB `innodb` + `utf8mb4` config Frappe requires. Use **nvm to install Node 20**
   (Frappe's build breaks on Node 26) and `pip install frappe-bench`.
2. `bench init nextgen-bench --frappe-path <abs>/vendor/frappe --python python3.12`
   (uses the vendored Frappe checkout instead of cloning).
3. `bench get-app <abs>/vendor/erpnext` to register the vendored ERPNext.
4. `bench new-site nextgen.localhost --admin-password ...`; `bench --site nextgen.localhost install-app erpnext`.
5. `bench start` → Desk at `http://nextgen.localhost:8000`. Log in, confirm ERPNext loads.

**Risk / fallback:** if the v16-source native build fails on Node/MariaDB/asset compile,
fall back to `frappe_docker` stable v15 images (Docker is installed; daemon just needs
starting). Keep this documented as the escape hatch — do not silently pivot.

## Phase 1 — Scaffold the `nextgen_erp` Frappe app

`bench new-app nextgen_erp` living under the prototype repo (e.g. `apps/nextgen_erp`,
symlinked into the bench); install onto the site.

DocTypes (mirror the SQLite schema in `order_intake/db.py`):

- **AI Order Intake** — `merchant`, `line_ref`, `customer` (Link Customer), `source_channel`,
  `source_text` (Thai), `status` (workflow field), `confidence` (Float), `total`,
  `exception_reasons` (Small Text/JSON), `idempotency_key` (unique), `automation_mode`,
  and Link fields for the resulting `Sales Order` / `Delivery Note` / `Sales Invoice` /
  `Payment Entry`. Approval history via Frappe's built-in timeline/comments (replaces `audit_event`).
- **AI Order Intake Item** (child) — `raw_text`, `item` (Link Item), `qty`, `uom`, `rate`,
  `line_total`, `confidence`, `exception_reason`.
- **LINE Customer Map** — `line_id` (unique), `customer` (Link), `display_name` (replaces the `customer` table's alias mapping).
- **NextGen Automation Settings** (Single) — `confidence_threshold`, `auto_confirm`.

Code:

- `nextgen_erp/api.py` — the three whitelisted order-to-cash methods above (real ERPNext
  document creation + submit, keyed idempotently on `external_reference`), plus
  `create_ai_order_intake(payload)`, `approve_ai_order_intake(name, reviewer)`,
  `record_customer_confirmation(name, confirmed)`.
- `hooks.py` — doc events wiring approval/confirmation to `create_sales_order`; register
  ERPNext **Webhook** records (DN/SI/Payment submit) that POST status back to the external
  service for LINE replies.
- A Desk **Workspace** + list view (filtered review queue by `status`) + form with
  approve/reject/edit buttons.

## Phase 2 — Migrate order intake into ERPNext Desk

Repoint the external service; retire the SvelteKit review UI.

- `order_intake/service.py`: replace SQLite draft persistence
  (`create_from_message` → `order_draft`/`order_item`/`order_workflow`) with a REST call to
  `nextgen_erp.api.create_ai_order_intake` via `ERPNextClient`. Idempotency moves to the
  DocType's unique `idempotency_key`. Extraction/confidence/policy logic in
  `matching.py` + the scoring in `service.py` stays.
- Catalog: use `ERPNextCatalogSource` (already built) for reads; drop the local `product`
  mirror and `sync_erpclaw_catalog`.
- Order-to-cash orchestration moves server-side: **approval happens in Desk**, so
  `approve`/`confirm`/`deliver`/`pay` are driven by the app's methods, not the Python
  service. The service keeps only the LINE webhook (`server.py` `/webhooks/line`),
  extraction, the outbound LINE queue, and reply-sending triggered by ERPNext webhooks.
- Flip `ERPNEXT_EXECUTE=1` so `ERPNextAdapter` writes for real; keep shadow mode as the
  pre-cutover dry run.
- Retire `vendor/erpclaw-web` `/order-intake` pages (review now lives in Desk). Keep the
  `ChatPanel` for Phase 3.

## Phase 3 — Repoint ERPClaw chat (DEFERRED — design only now)

Design doc + interface; build later. ERPClaw's chat backend (`:8001`, `/api/chat/stream`)
has a data/action layer over its own SQLite; swap that layer to the ERPNext REST +
whitelisted methods (the same `nextgen_erp.api.*` contract), so chat actions
(`list-items`, `add-sales-order`, `create-delivery-note`, `add-payment`) mutate ERPNext.
ERPClaw becomes chat-only. Deliverable this iteration: an ADR mapping each ERPClaw chat
action → ERPNext method/DocType, plus the auth/permission boundary.

## Verification

- **Phase 0:** `bench start`; open Desk in the browser, log in, confirm the ERPNext
  workspace and standard DocTypes (Item, Customer, Sales Order) load.
- **Phase 1:** `bench migrate` clean; AI Order Intake list/form render in Desk; call each
  whitelisted method via `bench execute nextgen_erp.api.create_sales_order` (and REST) and
  confirm return shape + a submitted Sales Order in Desk. Existing adapter unit tests
  (`tests/test_erpnext_adapter.py`) stay green against the real contract.
- **Phase 2 (end-to-end):** POST a Thai order to `order-intake-api` `/webhooks/line` (or
  `/api/intake/messages`) → an **AI Order Intake appears in the Desk review queue** →
  approve in Desk → **Sales Order created & submitted in ERPNext**, stock reserved (Bin
  `projected_qty` drops) → progress delivery/invoice/payment → statuses + linked docs
  reflected on the intake record, and a LINE reply is queued via the webhook. Run first in
  shadow mode, then `ERPNEXT_EXECUTE=1`.
- **Phase 3:** reviewable ADR; no runtime change.

## Out of scope / flags

- Thai accounting, tax, e-Tax, PDPA gates (`docs/THAILAND_LAUNCH_GATES.md`) remain
  unmet — this migration is UX/architecture, not legal system-of-record readiness.
- GPL boundary: ERPNext/Frappe (GPL-3.0) run as a separate service reached over REST;
  `nextgen_erp` is a Frappe app (GPL). Keep the external Python service and any proprietary
  logic behind the documented API boundary; counsel review before commercial launch.
- v16 is Frappe/ERPNext develop; if native build is unstable, stable v15 via `frappe_docker`
  is the documented fallback.

---

# Follow-up — Move order intake + integrations fully into the ERPNext UI

## Context

Phases 0–2 already put the **review/ops** experience into ERPNext Desk (AI Order
Intake DocType, form buttons, NextGen Orders workspace). Two legacy SvelteKit
pages remain on `:5173`:

- `/order-intake` — dashboard tiles + a "paste a Thai order" box + review queue +
  workflow actions. Now duplicated by Desk; only the quick-intake box and the
  metric tiles aren't in Desk yet.
- `/integrations` — (1) **LINE OA config** and (2) an **ERPClaw connection** panel.
  The ERPClaw panel is obsolete (ERPNext is the base); the LINE panel's config
  lives in the external service's `line_integration.py`.

Decisions: **retire both SvelteKit pages**; move **LINE config into a Desk DocType**
(the webhook receiver stays in the external service and reads config from ERPNext,
per "extraction stays external"); **add the quick Thai-text intake to Desk**; drop
the obsolete ERPClaw config panel entirely.

## A. `nextgen_erp` app (ERPNext side)

- **New DocType `LINE Channel Settings`** (Single) via the existing pattern in
  [`setup_doctypes.py`](../nextgen-bench/apps/nextgen_erp/nextgen_erp/setup_doctypes.py):
  `channel_id` (Data), `channel_secret` (Password), `channel_access_token`
  (Password), `webhook_url` (Data), `enabled` (Check), `merchant` (Data).
- **`api.py` additions** (reuse `apps/nextgen_erp/nextgen_erp/api.py`):
  - `get_line_config()` — returns the decrypted LINE settings for the authenticated
    external service (scoped API user; localhost).
  - `quick_intake(text, customer=None)` — server-side POST to
    `NextGen Automation Settings.external_service_url` + `/api/intake/erpnext`;
    returns the created AI Order Intake name (relay so extraction stays external).
- **List action** `ai_order_intake_list.js` — a "New from LINE text" button →
  `frappe.prompt(text, customer)` → `frappe.call("nextgen_erp.api.quick_intake")`
  → route to the new intake. Mirrors the button pattern already in
  [`ai_order_intake.js`](../nextgen-bench/apps/nextgen_erp/nextgen_erp/nextgen_erp/doctype/ai_order_intake/ai_order_intake.js).
- **Metric tiles**: Number Cards on `AI Order Intake` by `status` (Needs Review /
  Awaiting Customer / Reserved / Paid), added to the NextGen Orders workspace.

## B. External service (`apps/order-intake-api`)

- **New endpoint** in [`server.py`](../apps/order-intake-api/order_intake/server.py):
  `POST /api/intake/erpnext` → `erpnext_bridge.intake_from_message(...)` (already
  built) → returns `{name, ...}`. This is what `quick_intake` and future callers hit.
- **`erpnext_line_config.py`** (new): fetch `channel_secret` / `access_token` /
  `enabled` from ERPNext via `get_line_config` (through `ERPNextClient`), short-cached.
  Wire the LINE webhook path (`_handle_line_webhook`, `_deliver_line_outbound`) to
  use it when `LINE_CONFIG_SOURCE=erpnext`, falling back to the local store.
- **Retire config endpoints/stores**: remove `/api/integrations/line`,
  `/api/integrations/erpclaw`, `/api/integrations/erpclaw/sync` and the
  `ERPClawIntegrationStore` usage (config now lives in ERPNext). Keep the
  ERPClaw *catalog* read path only if still needed; otherwise drop it in favour of
  `ERPNextCatalogSource`.

## C. SvelteKit app (`vendor/erpclaw-web`, branch `nextgen-thailand`)

- Delete `src/routes/order-intake/` and `src/routes/integrations/`.
- Remove their entries in `src/lib/components/SidebarNav.svelte` and the label map
  in `src/routes/+layout.svelte`. Leave `ChatPanel` and other pages intact (Phase 3).
- Regenerate `patches/erpclaw-web-nextgen.patch.gz`; note the change in
  `UPSTREAM.lock`. Update `scripts/dev.py` / `README.md` references away from the
  removed pages.

## Verification

- **LINE config**: set `LINE Channel Settings` in Desk; `bench execute
  nextgen_erp.api.get_line_config` returns the values; external service verifies a
  signed test webhook using the ERPNext-sourced secret.
- **Quick intake**: in Desk, AI Order Intake list → "New from LINE text" → paste a
  Thai order → an AI Order Intake is created and opens (round-trips through the
  external extractor).
- **Retirement**: `http://127.0.0.1:5173/order-intake` and `/integrations` return
  404; `ChatPanel` and remaining pages still load.
- **Tests**: add unit tests (fake transport) for `POST /api/intake/erpnext` and the
  ERPNext LINE-config fetch; run `PYTHONPATH=. python3 -m unittest discover -s tests`
  (should stay green, currently 20/20).
