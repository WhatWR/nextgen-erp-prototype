# ERPNext integration and operations

## Runtime flow

```text
LINE OA webhook
  -> order-intake-api (signature verification + Thai extraction)
  -> AI Order Intake in ERPNext
       high confidence/no exception -> Awaiting Customer
       otherwise                    -> Needs Review
  -> customer confirms
  -> submitted Sales Order + submitted Pick List + stock reservation
  -> submitted Delivery Note + submitted Sales Invoice
  -> submitted Payment Entry allocated to the invoice
  -> LINE status messages + expiring signed invoice PDF URL
```

ERPNext owns all business masters, prices, stock and accounting documents. The
extractor never accepts the model's price as authoritative: it resolves the Item,
selling price and availability from ERPNext. A LINE event ID and external
reference are unique, making retries idempotent. Lifecycle mutations lock the
intake row and run in one Frappe transaction; failed reservations roll back.

## Components

- `nextgen_erp.api` exposes role-protected orchestration methods. The service
  user needs **NextGen Order Service** plus the provisioned operational roles.
- `LINE Event Receipt` durably records webhook results.
- `LINE Channel Settings` stores secrets in ERPNext. The read API never returns
  the Channel Access Token; queued Frappe workers send outbound messages.
- `NextGen Automation Settings` controls the confidence threshold, automatic
  routing, external extractor API key and signed-link lifetime.
- `vendor/erpclaw/mcp/erpnext_proxy.py` maps ERPClaw read/write tools to these
  methods. Every write requires a stable external reference.

## Fresh-site setup

Install the pinned Frappe and ERPNext v16.20 sources, then install this app:

```bash
bench get-app /absolute/path/to/apps/nextgen_erp
bench --site your-site install-app nextgen_erp
bench --site your-site migrate
```

The install hooks create required custom fields, the service role and the local
service-user record. In ERPNext, open that User and generate an API key/secret.
Store the values only in the external service's environment. Do not paste them
into source control and do not use Administrator credentials.

For local demonstration data only:

```bash
bench --site nextgen.localhost execute nextgen_erp.demo.run
```

The demo creates ERPNext Items with Thai aliases and real stock through a
Material Receipt, so phrases such as `เครื่องดื่ม M-150` resolve only when that
Item exists in ERPNext.

## Required environment

Copy `.env.example` to `.env`. Important defaults are:

```text
ORDER_BACKEND=erpnext
LINE_CONFIG_SOURCE=erpnext
LINE_WORKFLOW_BACKEND=erpnext
ERPCLAW_BACKEND=erpnext
```

`ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET`, and `ORDER_INTAKE_API_KEY` are required
secrets. The same order-intake key must be stored in **NextGen Automation
Settings** for Desk quick intake. Set `ERPNEXT_WAREHOUSE` to the exact ERPNext
warehouse name.

Legacy SQLite endpoints are retained only for historical tests and must remain
disabled in deployment. Never set `ENABLE_LEGACY_PROTOTYPE=1` in a live system.

## LINE setup

1. Set the Messaging API Channel Secret and Channel Access Token in **LINE
   Channel Settings**.
2. Expose `/webhooks/line` through a public HTTPS endpoint and register it in
   LINE Developers.
3. Create a **LINE Customer Map** from each LINE user ID to an ERPNext Customer.
4. Configure the production ERPNext hostname so generated invoice links use the
   public HTTPS origin.

The webhook verifies `X-Line-Signature`. Text replies such as confirmation or
rejection are applied to the existing intake first, so they cannot accidentally
be interpreted as a new order.

## Verification

The automated suites cover webhook routing and idempotency, confidence routing,
ERPNext-derived pricing, direct-state-edit rejection, scoped secret access,
signed invoice tamper rejection and a real submitted order-to-cash lifecycle.

```bash
cd apps/order-intake-api
NO_PROXY='*' no_proxy='*' python3 -m unittest discover -s tests -v

cd ~/nextgen-bench
bench --site nextgen.localhost set-config allow_tests true
bench --site nextgen.localhost run-tests --app nextgen_erp
bench --site nextgen.localhost set-config allow_tests false
```

Keep test mode disabled outside the test run.

## Production gates

Before launch, add managed MariaDB/Redis, backups and restore drills, HTTPS,
worker/process supervision, monitoring, secret rotation, rate limiting and PDPA
retention controls. Thai VAT/e-Tax invoice correctness and payment webhook trust
must be reviewed by qualified local accounting and legal specialists.
