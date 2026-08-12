# Product and scope

**Document status:** Authoritative product direction  
**Applies to:** Phase 1 and Phase 2

## Product thesis

NextGen ERP is an AI-native supply-chain execution product built on ERPNext for Thai mid-market distributors. ERPNext supplies governed transactions and ledgers; NextGen supplies intention-based interaction, deterministic operational intelligence, exception management, proposals, explanations, and controlled execution.

The first operational wedge is a multi-company inventory control tower, not a replacement for every SCM system at once.

## Initial customer profile

- Thai mid-market distributor with more than one warehouse.
- One Frappe site may contain several related ERPNext companies.
- Operations include purchasing, stock transfers, sales fulfilment, and LINE-heavy customer workflows.
- Teams currently rely on spreadsheet review and personal experience to detect shortages and decide replenishment.
- Buyers include the owner, COO, Head of Supply Chain, Operations Director, and Inventory or Procurement Manager.

## Jobs to be done

1. Show where inventory shortages, excess, and unreliable data require attention.
2. Explain why an item is at risk using current ERP documents and versioned calculations.
3. Recommend a same-company transfer when an eligible warehouse can safely cover the entire shortage.
4. Otherwise recommend a Purchase Material Request using approved supplier and quantity rules.
5. Produce a reviewable proposal, revalidate it immediately before execution, and retain a complete audit trail.
6. Let users ask questions in Thai or English without allowing the model to invent stock, cost, or document state.

## Pilot outcomes

Measure a baseline before enabling recommendations. The pilot targets are:

- 30% less planner time spent collecting stock and open-order data.
- 15% fewer preventable stockout events for allowlisted items.
- 10% lower excess stock value for items managed by the pilot policy.
- At least 90% of generated proposals accepted or explicitly dismissed with a captured reason.
- 100% of signals and proposals traceable to company-scoped ERP inputs and a formula version.
- Zero cross-company inventory leakage and zero automatically submitted inventory documents.

Business targets are evaluation goals, not hard-coded system thresholds.

## In scope for the next two phases

- Reusable Agent Runtime microservice with Frappe-hosted execution audit and ERP boundaries.
- Versioned Frappe gateway APIs, company-scoped automation policies, and restricted service identity.
- Existing Sales and Procurement copilots migrated without behavior loss.
- Inventory shortage, excess, transfer-opportunity, and data-quality exceptions.
- Daily and on-demand signal generation.
- Purchase versus same-company transfer proposal selection.
- Human approval and policy-gated draft Material Request creation.
- AI Cockpit evolution into a company-scoped inventory control tower.
- English engineering documentation and bilingual Thai/English product text.
- **Phase 3:** an assistant that can propose any document its user's role permits, and a visual designer for multi-agent workflows.

## Explicitly out of scope

- Shared inventory pools or direct transfers across ERPNext companies.
- Automatic submission of Purchase Orders, Material Requests, Stock Entries, or physical movements.
- A general-purpose automation engine. The Phase 3 workflow designer arranges registered agents and nothing else: it cannot define tools, permissions or ERP operations.
- Autonomous schema, agent, or module generation.
- Advanced demand planning, S&OP, multi-echelon optimization, or probabilistic forecasting.
- WMS execution records such as license plates, waves, labour tasks, dock appointments, or offline scanners.
- TMS functions such as load building, carrier tendering, live GPS, freight audit, or proof of delivery.
- Manufacturing planning beyond the existing ERPNext foundation.

## Roadmap after Phase 2

The intended sequence is inventory visibility, warehouse execution, procurement expansion, demand planning, transportation, manufacturing planning, and finally an end-to-end exception coordinator. Each step requires its own decision-complete plan and acceptance tests.
