# AI Procurement Copilot (NextGen AI)

The bottom-right assistant is now a multi-agent panel called **NextGen AI**
with two agents:

- **AI Sales Copilot** (`sales`) — the existing selling assistant, unchanged.
- **AI Procurement Copilot** (`procurement`) — demand forecasting, stock-risk
  analysis and safe Purchase Order / Material Request preparation.

There is a single floating launcher. The active agent's name, icon and color
are always visible in the panel header; an agent switcher in the header swaps
between agents. Every session, message, action and tool execution is stored
with an explicit `agent_type`, resolved and validated **server-side** — the
LLM never chooses the agent, and a session's agent can never be switched
silently (`nextgen_erp/agents.py`).

Route-based selection when the panel opens:

- Sales / Selling / AI Sales Copilot routes → `sales`
- Buying / Supplier / Purchase / Material Request / AI Procurement Copilot
  routes → `procurement`
- Other routes → the previously used agent, or an in-chat agent chooser.

## Roles

The procurement agent allows **System Manager, Purchase Manager, Purchase
User, Stock Manager**. Normal Frappe document permissions still apply to every
record read or created.

## Deterministic forecast engine

Typhoon never computes forecast numbers. `nextgen_erp/forecast.py` implements
formula **`nextgen-procurement-v1`** (all quantities in stock UOM):

```text
demand_30/60/90      = submitted Sales Order qty in the last 30/60/90 days
average_daily_demand = 0.5*(d30/30) + 0.3*(d60/60) + 0.2*(d90/90)
lead_time_demand     = average_daily_demand * supplier_lead_time_days
safety_stock         = max(Item.safety_stock, average_daily_demand * safety_stock_days)
reorder_point        = lead_time_demand + safety_stock
projected_available  = actual_qty + open-PO incoming - reserved_qty
target_stock         = average_daily_demand * horizon_days + safety_stock
suggested_qty        = max(0, target_stock - projected_available)  → MOQ / order-multiple rounded
```

Every result carries its assumptions, source date range, recent purchase lots
(lot-level cost context and price variance), a **rule-based data-quality
score** (documented penalties in `forecast.QUALITY_PENALTIES` — this is not an
"AI confidence") and warnings for: insufficient history, disabled/EOL items,
missing supplier/price/lead time, negative stock, existing open POs, MOQ/order
multiples, stale data and demand spikes. Horizon, history window, safety-stock
days and default lead time are configurable in **NextGen Procurement
Settings**.

Snapshots persist immutably in **NextGen Procurement Forecast**; proposals in
**NextGen Procurement Recommendation** (statuses: Draft, Suggested, Needs
Review, Approved, Purchase Order Created, Dismissed, Expired).

## Safe purchase actions

`prepare_purchase_order` / `prepare_material_request` only create an expiring
`NextGen Chat Action` preview. On confirmation the server revalidates: item and
supplier state, user permission, UOM conversion, live stock/incoming, open
MR/PO coverage, supplier-item link, live rate + price variance, MOQ/order
multiple, schedule date, maximum PO value, expiry/idempotency, and material
forecast drift. Repeated confirmations return the original result.

## Automation modes (NextGen Procurement Settings)

- **Shadow** (default): forecasts + recommendations only. Even a confirmed
  chat preview records a `Needs Review` recommendation — no buying documents.
- **Approval Required**: confirmed previews create **Draft** Material Requests
  / Purchase Orders (never submitted from chat).
- **Automatic** (off by default): the daily scheduler may create and submit a
  Material Request only when *every* gate passes (item allowlisted, approved
  unambiguous supplier, data quality ≥ threshold, high stockout risk,
  MOQ/multiple respected, price variance within limit, per-PO and daily spend
  limits, no covering open PO, no warnings, unused idempotency key). Any
  failure or exception falls back to a `Needs Review` recommendation. Direct
  automatic PO submission additionally requires the separate
  `allow_direct_po_submission` flag. No payments, receipts, invoices,
  cancellations or deletions are ever automated.

The daily scheduled forecast (`procurement.run_scheduled_forecast`) respects
`enable_procurement_copilot` + `enable_scheduled_forecast`, snapshots
forecasts, maintains recommendations idempotently (dedupe key per
item/warehouse/day/horizon, dismissed items are not resurrected) and logs
structured JSON without secrets. Purchase Manager / System Manager can trigger
it manually via `nextgen_erp.procurement.run_forecast_now`.

## Setup

1. **NextGen AI Settings** → enable Staff Chat, set the Typhoon gateway + key
   (server-side only, never sent to the browser).
2. **NextGen Procurement Settings** → *Enable Procurement Copilot*; optionally
   a dedicated `procurement_model`.
3. `bench --site <site> migrate` (creates DocTypes, workspace, sidebar and the
   `AI Procurement Copilot` tile under the *Next Gen ERP* desk folder).
4. Demo fixture: `bench --site <site> execute nextgen_erp.demo.procurement`
   (supplier terms, 90 days of weekly demand, purchase lots, one open PO).

## Verify

```bash
# Inside the backend container
bench --site <site> run-tests --app nextgen_erp \
  --module nextgen_erp.tests.test_forecast
bench --site <site> run-tests --app nextgen_erp \
  --module nextgen_erp.tests.test_agents
bench --site <site> run-tests --app nextgen_erp \
  --module nextgen_erp.tests.test_procurement
```
