# NextGen Staff Chat (Typhoon AI)

> **Document status:** Current Staff Chat and Sales Copilot capability. The reusable agent-runtime target is defined in [Phase 1](AI_HANDOFF/PHASE_1_AGENT_PLATFORM.md).

NextGen Staff Chat is an authenticated assistant embedded in every ERPNext Desk
page. It reads live ERPNext data through an explicit tool allowlist and can
prepare a Sales Order preview. The model never writes ERP documents directly.

Since the multi-agent update the panel is branded **NextGen AI** and hosts two
agents behind one launcher: the **AI Sales Copilot** described here and the
**AI Procurement Copilot** (see `docs/PROCUREMENT_COPILOT.md`). Agents are
registered in `nextgen_erp/agents.py`; every session, message and action is
pinned to an `agent_type`, the server resolves and validates the agent on every
turn, and each agent only ever sees its own tool allowlist.

## Configure

Open **NextGen AI Settings** as System Manager and set:

- **AI Gateway URL**: `https://api.opentyphoon.ai/v1` (the host-only form is
  accepted too).
- **AI Gateway API Key**: a hosted Typhoon key.
- **Staff Chat Model**: `typhoon-v2.5-30b-a3b-instruct`.
- **Enable Staff Chat**: enabled only after the key and model are ready.

The LINE assistant and Staff Chat share gateway credentials but have separate
enable switches, models and daily caps. The key remains in the ERPNext Password
field and is never returned to the browser.

Run `bench --site <site> migrate`, restart workers, clear the Desk cache and
reload. A purple assistant launcher appears at the bottom-right for System
Manager, Sales Manager, Sales User, Purchase Manager, Purchase User and Stock
Manager accounts (each user only sees the agents allowed by their roles).

If the launcher is missing after a production deployment, verify the server
bootstrap first:

```bash
bench --site <site> execute nextgen_erp.staff_chat.get_status
```

`enabled` must be `true` and `agents` must contain at least one entry. If it is
false, enable **NextGen AI Settings → Enable Staff Chat** and the relevant agent
switch, then run `bench --site <site> migrate`, `bench build --app nextgen_erp`,
`bench --site <site> clear-cache`, restart the web/workers and hard-refresh the
Desk. In Docker, rebuild and recreate the ERP services rather than restarting
containers that still use the old image.

## Safe action flow

1. Typhoon may call read-only tools for customers, items, stock, price, Sales
   Orders, intakes and pipeline counts.
2. `prepare_sales_order` resolves the customer/items and creates an expiring,
   immutable `NextGen Chat Action` preview.
3. The staff member explicitly confirms the preview.
4. ERPNext recalculates price and stock. If every match remains unambiguous,
   no warning exists and confidence meets **NextGen Automation Settings →
   Confidence Threshold**, the existing idempotent `create_sales_order` method
   submits the Sales Order and reserves stock through a submitted Pick List.
5. Any low confidence, warning, stale price, insufficient stock or missing
   master data produces an `AI Order Intake` in **Needs Review** instead.

Confirmation, retry and duplicate clicks reuse the action idempotency key. V1
does not expose delivery, invoice, payment, accounting, cancel or delete tools.

Procurement previews can be revised directly from their card. **แก้ข้อมูล**
opens ERP Link/Date fields for Warehouse, Supplier and Schedule Date. Saving
creates a newly validated immutable proposal and cancels the old proposal for
audit; it does not call Typhoon or consume another chat turn.

## Persistence and operations

- Sessions and messages are private to their owner through the public API;
  System Manager retains DocType-level audit access.
- Pending actions expire after 15 minutes.
- A daily scheduler expires pending actions and removes sessions older than the
  configured retention period (30 days by default).
- Background turns publish `nextgen_staff_chat` events to the authenticated
  user's Frappe realtime channel. Failures appear in ERPNext Error Log without
  API keys.

## Verify

```bash
PYTHONPATH=apps/nextgen_erp python3 -m unittest discover \
  -s apps/nextgen_erp/nextgen_erp/tests -p 'test_typhoon.py' -v

cd apps/order-intake-api
NO_PROXY='*' no_proxy='*' python3 -m unittest discover -s tests -v

bench --site nextgen.localhost set-config allow_tests true
bench --site nextgen.localhost run-tests --app nextgen_erp
bench --site nextgen.localhost set-config allow_tests false
```

The current chat-specific session/action implementation is preserved during Phase 1 compatibility migration. New SCM agents must target the generic run and proposal contracts rather than extending NextGen Chat Action.
