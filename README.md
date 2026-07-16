# NextGen ERP Thailand

An ERPNext-first prototype for receiving Thai orders from LINE OA and running
them through a confidence-gated order-to-cash workflow.

## Current architecture

- **ERPNext v16.20** is the system of record for items, aliases, prices, stock,
  customers, Sales Orders, Pick Lists, Delivery Notes, invoices and payments.
- **NextGen ERP Frappe app** owns the intake review record, automation policy,
  LINE customer mapping, durable webhook idempotency and signed invoice links.
- **Order Intake API** verifies LINE webhooks, extracts Thai order text and writes
  the result to ERPNext. Its SQLite prototype workflow is legacy-only.
- **ERPClaw/OpenClaw** is the conversational action surface. With
  `ERPCLAW_BACKEND=erpnext`, its tools execute through the same secured ERPNext
  methods instead of ERPClaw SQLite. (Paused for the current phase.)
- **LINE AI Assistant** answers customer questions (price/stock, order and
  payment status, FAQ via RAG) and can re-send the invoice + PromptPay QR —
  through the same secured ERPNext methods only. See
  [docs/AI_ASSISTANT.md](docs/AI_ASSISTANT.md).
- **Typhoon Staff Chat** is a Desk-wide sales copilot for authenticated staff.
  It reads live ERP data and prepares confirmation cards; only ERPNext can
  submit/reserve a high-confidence order. See
  [docs/STAFF_CHAT.md](docs/STAFF_CHAT.md).

High-confidence, exception-free intake is sent directly to the customer for
confirmation. Low-confidence or exceptional intake waits for a human. Customer
confirmation creates and reserves a real Sales Order/Pick List; delivery creates
the Delivery Note and invoice; payment is allocated to that invoice; status and
a time-limited invoice link are delivered through LINE.

## Start locally

1. Copy `.env.example` to `.env` and add the API credentials for the dedicated
   `nextgen-order-service@local.invalid` ERPNext user. Generate its key and secret
   in ERPNext; do not use the Administrator account.
2. Run `./scripts/start.sh`.
3. Open [ERPNext Desk](http://127.0.0.1:8000) and select **NextGen Orders**.

Configure the LINE channel secret/token and the public HTTPS webhook URL in the
**LINE Channel Settings** DocType. Map each LINE user to an ERPNext Customer with
**LINE Customer Map** before accepting live orders.

See [the deployment and integration guide](docs/ERPNEXT_MIGRATION.md) for setup,
security boundaries and test commands.

For a Linux server, use the default [compose.yaml](compose.yaml), which loads
[compose.server.yaml](compose.server.yaml) with `.env.server`, and follow the
[Docker server deployment guide](docs/DOCKER_SERVER.md).

To rehearse the whole flow locally without real LINE, LLM or slip-verifier
accounts, run the [sandbox simulator](docs/SANDBOX.md)
(`python3 scripts/sandbox.py`).

For local development with Dockerized MariaDB and Redis, run
`./scripts/start-local.sh`. ERPNext remains editable in the local Bench while its
infrastructure is isolated in `compose.local-infra.yaml`.

For a fresh clone, initialize the pinned upstreams and apply the tracked local
changes:

```bash
git submodule update --init --recursive
gzip -dc patches/erpclaw-nextgen.patch.gz | git -C vendor/erpclaw apply --index
gzip -dc patches/erpclaw-web-nextgen.patch.gz | git -C vendor/erpclaw-web apply --index
```

## Verify

```bash
cd apps/order-intake-api
NO_PROXY='*' no_proxy='*' python3 -m unittest discover -s tests -v

cd ../../vendor/erpclaw-web
npm run build
```

Run the Frappe suite from the bench:

```bash
bench --site nextgen.localhost set-config allow_tests true
bench --site nextgen.localhost run-tests --app nextgen_erp
bench --site nextgen.localhost set-config allow_tests false
```

## Seed the ICONHOME production demo

The production demo seed is deliberately opt-in, idempotent and non-destructive.
It creates clearly labelled demo customers, suppliers, construction materials,
stock and one **Draft** Sales Invoice; it never changes the site's default
company and never submits the invoice to the ledger. Take a database backup,
then run:

```bash
bench --site <site> execute nextgen_erp.seed.run \
  --kwargs '{"confirm":"SEED_ICONHOME_DEMO"}'
```

Re-running the command preserves existing records and only fills missing demo
data. The `NextGen Customer Invoice` print format is installed during migrate
and is used automatically by signed invoice links sent from the LINE AI flow.

## Repository map

| Path | Purpose |
|---|---|
| `apps/nextgen_erp` | Frappe app and ERPNext workflow |
| `apps/order-intake-api` | LINE receiver and Thai extraction service |
| `vendor/erpclaw` | ERPClaw/OpenClaw tools with ERPNext proxy |
| `vendor/erpclaw-web` | ERPClaw UI; ERP pages redirect to ERPNext Desk |
| `vendor/frappe`, `vendor/erpnext` | Pinned v16.20 upstream sources |
| `patches/` | Reproducible NextGen changes over pinned ERPClaw sources |
| `UPSTREAM.lock` | Exact upstream commits and licences |

## Launch boundary

The technical order-to-cash prototype is working, but production launch still
requires Thai tax/accounting validation, PDPA controls, backups, observability,
public TLS infrastructure and real LINE credentials. Review
[Thailand launch gates](docs/THAILAND_LAUNCH_GATES.md) before a live pilot.
