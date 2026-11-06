# LINE simulator page

> **Document status:** Current demo capability. It stands in for the LINE app
> only; the order-to-cash contract it exercises is the one in the
> [canonical acceptance tests](AI_HANDOFF/ACCEPTANCE_TESTS.md).

`/line-sim` is a LINE-style chat page served by the ERPNext site, for
demonstrating the whole order-to-cash flow before a LINE Official Account is
available. It replaces only the two ends LINE itself provides — sending a
customer message, and receiving a pushed reply. Everything in between is the
production path: a correctly signed webhook, `order-intake` verification and
routing, and real ERPNext documents.

[`scripts/sandbox.py`](../scripts/sandbox.py) does the same thing headlessly over
curl and additionally fakes the AI gateway and the slip verifier. Use the
sandbox to rehearse locally; use this page to show someone.

**Demo only.** The page creates genuine Sales Orders, Invoices and Delivery
Notes. Never leave it enabled on a site that a real LINE Official Account points
at — `push_text` would then have two ways to reach a customer.

## How it works

```text
/line-sim  ──send──▶  nextgen_erp.line_simulator.send()
                          │ signs X-Line-Signature with the LINE Channel
                          │ Settings channel secret
                          ▼
                   order-intake  POST /webhooks/line
                          │ verify_line_signature → ERPNextLineWorkflow
                          ▼
                   nextgen_erp.api.*  → Sales Order / Invoice / QR / Delivery Note
                          │
                          ▼
                   nextgen_erp.line.push_text
                          │ recipient starts with "Usim-" ?
                          ▼
                   NextGen LINE Sim Message  ──poll──▶  /line-sim
```

`SIM_PREFIX = "Usim-"` is the only gate. Each logged-in user gets one stable
simulated identity (`Usim-<hash(user)>`), so two presenters never collide, and
`Usim-delivery` backs the delivery-team pane. A recipient without the prefix
behaves exactly as before and still requires an enabled channel and an access
token.

Pushes are captured in a DocType rather than memory because `push_text` runs in a
queue worker — a different process from the page request. Uploaded slips are
stored as private Files and returned by `line.fetch_message_content`, which is
why the demo needs no channel access token.

## Setup

```bash
bench --site <site> set-config nextgen_line_simulator 1
bench --site <site> migrate
```

Then in Desk:

- **LINE Channel Settings**: enable it, set `Channel ID` to anything (e.g. `sim`)
  and a `Channel Secret` to any string. **No access token is needed.**
  `order-intake` reads this record (`LINE_CONFIG_SOURCE=erpnext`), so the
  simulator and the verifier always agree on the secret.
- **NextGen Payment Settings**: PromptPay ID, `Public Base URL` set to the site's
  own URL (the QR image and invoice links are built from it), and
  `Delivery Team LINE User/Group ID` = `Usim-delivery` to feed the delivery pane.
- **NextGen AI Settings**: enable only if the demo should include the assistant
  answering product questions.

Seed the demo catalog and customer if the site is empty:

```bash
bench --site <site> execute nextgen_erp.demo.run
```

Optional site config:

| Key | Default | Purpose |
|---|---|---|
| `nextgen_line_simulator` | unset (off) | Master switch; every entry point refuses without it |
| `nextgen_line_simulator_roles` | `["System Manager"]` | Roles allowed to open the page |
| `nextgen_order_intake_url` | `http://order-intake:8200` | Where the signed webhook is posted |

Turn it off again with:

```bash
bench --site <site> set-config nextgen_line_simulator 0
```

## Demo script

1. **Ask a price** (`M-150 ราคาเท่าไหร่`) — routed as a question, answered by the
   assistant when enabled. It must never become an order.
2. **Order** (`น้ำแดง 2 ลัง กับมาม่าต้มยำ 3 แพ็ก`) — creates AI Order Intake.
   High confidence asks the customer to confirm; low confidence waits in
   **Needs Review** for staff in Desk.
3. **Confirm** (`ยืนยัน`) — Sales Order, Pick List and Sales Invoice are
   submitted, and the invoice link plus an amount-locked PromptPay QR appear in
   the chat.
4. **Attach a slip** with the 📎 button — stored as a private attachment and read
   by Typhoon OCR, then parked in **Payment Review**.
5. **Approve the slip** in Desk (Approve Payment Slip) — this is deliberate:
   OCR never records payment. The Payment Entry and a draft Delivery Note follow,
   and the delivery pane receives the Delivery Note link.
6. **Complete Delivery** in Desk — the customer gets the delivered message.

The ERPNext panel on the right updates at each step and links straight to the
documents, which is the point worth making: nothing here is mocked downstream.

Without a Typhoon OCR key configured, step 4 records a verifier error and still
lands in **Payment Review** — the correct behaviour, since a staff member has to
compare the slip with bank activity either way.

## Tests

```bash
bench --site nextgen.localhost run-tests --module nextgen_erp.tests.test_line_sim
```

Covers the gate (disabled site, Guest, other users' conversations), that a real
LINE recipient is never diverted into the simulator, the signature LINE itself
would produce, and a slip round-tripping through `fetch_message_content`.
`apps/order-intake-api/tests/test_sandbox.py` must keep passing too: the
simulator and the sandbox emit the same envelope for the same verifier.
