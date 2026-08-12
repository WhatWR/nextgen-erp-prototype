# Phase 2 — Inventory control tower

**Document status:** Decision-complete phase after Phase 1  
**Code status:** Not started  
**Dependency:** Phase 1 completion criteria must pass

## Outcome

Evolve the existing AI Cockpit into a multi-company inventory control tower that detects actionable inventory exceptions and prepares safe replenishment proposals for Thai multi-warehouse distributors.

## Company and warehouse boundary

- Every request requires a company the user can read.
- The optional warehouse must be a non-group, enabled warehouse belonging to that company and permitted by the company policy.
- Signals, donor selection, policies, proposals, and documents never cross company boundaries.
- Transit, disabled, group, and policy-excluded warehouses are never donors or receiving warehouses.
- Intercompany availability may not be displayed as usable stock in Phase 2.

## Deterministic inventory signals

Reuse the versioned `nextgen-procurement-v1` demand inputs for the first release. For each company, item, receiving warehouse, and horizon:

```text
projected_available = actual_qty + open_purchase_incoming - reserved_qty
target_stock        = average_daily_demand * horizon_days + safety_stock
shortage_qty        = max(0, target_stock - projected_available)
donor_floor         = max(reorder_point, target_stock)
donor_surplus       = max(0, donor_projected_available - donor_floor)
excess_qty          = max(0, projected_available - target_stock)
```

The calculation remains in stock UOM and carries source dates, formula version, data-quality score, and warnings. Disabled/EOL items, negative stock, missing UOM conversion, missing supplier, unresolved warehouse, and stale history create Data Quality exceptions and block draft creation.

Signal rules:

- High Stockout Risk: projected available reaches zero before lead-time demand is covered.
- Medium Stockout Risk: projected available is below reorder point but remains positive.
- Excess Stock: projected available exceeds target stock after policy buffer.
- Transfer Opportunity: one eligible donor warehouse can cover the complete shortage and remain at or above its donor floor.
- Data Quality: inputs fail the policy threshold or contain a blocking warning.

Use a deterministic key of company, target warehouse, item, signal type, forecast date, horizon, and formula version. Reruns update or reuse the current signal and never create duplicate open exceptions.

## Replenishment decision

For a shortage, the server chooses exactly one strategy:

1. Prefer a same-company transfer only when one eligible donor warehouse can cover the complete shortage. Rank donors by largest safe surplus, then shortest configured warehouse priority, then warehouse name for a stable tie-break.
2. Otherwise propose a Purchase Material Request for the complete shortage, rounded by existing MOQ and order-multiple rules.
3. Do not split one shortage between transfer and purchase in the first release.
4. Do not let the LLM choose quantities, donor warehouses, suppliers, rates, or strategy.

The transfer proposal targets a Draft Material Request of type Material Transfer. The purchase proposal targets a Draft Material Request of type Purchase. Phase 2 does not create a Stock Entry or submit either request.

## Agent and services

Add a Python-defined Inventory Agent deployed in the separate Agent Runtime microservice, with read tools for company inventory signals, exception details, stock position, donor comparison, and open proposals. Its only write tool prepares a replenishment Action Proposal.

Implement the target control-tower and replenishment service contracts from [Target architecture](TARGET_ARCHITECTURE.md). Manual refresh enqueues a company-scoped Agent Run; the daily scheduler creates one run per enabled company and records item failures without aborting other companies.

## Cockpit experience

Reuse `/app/ai-cockpit` and its current page assets. Add:

- Mandatory accessible-company selector and optional allowed-warehouse selector.
- Horizon selector with 7, 30, 60, and 90 days.
- KPIs for high-risk items, excess stock value, transfer opportunities, purchase needs, pending proposals, and data-quality blocks.
- Exception table with severity, evidence, proposed strategy, warnings, and owner/status filters.
- Proposal drawer showing formula inputs, source documents, before/after stock position, policy checks, expiry, and approval controls.
- Agent activity backed by Agent Runs and Steps rather than chat summaries.

All labels and explanations support Thai and English. Identifiers and engineering telemetry remain English.

## Authority rollout

1. Shadow: signals and proposals only; no ERP documents.
2. Approval Required: approval may create a Draft Material Request after live revalidation.
3. Policy-gated draft automation: after pilot acceptance, allowlisted items may create Draft Material Requests automatically; users must still review and submit them.

There is no Phase 2 mode that automatically submits Material Requests or creates/submits Stock Entries.

## Completion criteria

- Company and warehouse isolation tests pass for every query, signal, proposal, and result.
- Repeated daily/manual runs are idempotent.
- Transfer is selected only when one eligible same-company donor covers the complete shortage safely.
- Purchase is selected otherwise and obeys MOQ/order-multiple rules.
- Approval after material stock, demand, supplier, price, or policy drift is blocked and replaced by a fresh proposal.
- Shadow mode creates no ERP documents; draft modes never submit documents.
- The cockpit is usable in Thai and English and links every decision to live ERP evidence and a complete agent trace.
