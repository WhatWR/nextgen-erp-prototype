# Start locally (Podman)

Run the full NextGen ERP site with **Podman Compose**. No `scripts/start*.sh` wrappers.

Default project: `nextgen-erp`  
Default site: `nextgen.localhost` (from `.env.server`)  
Local Desk: [http://127.0.0.1:8180](http://127.0.0.1:8180)

## Prerequisites

- Podman with Compose support (`podman compose version`)
- Copy `.env.server.example` → `.env.server` and replace every `CHANGE_ME`
- Keep `SITE_NAME=nextgen.localhost` and a local `PUBLIC_URL` / `HTTP_PORT=8180` for laptop use

## First start

```bash
cd /path/to/nextgen-erp-prototype

podman compose -p nextgen-erp config --quiet
podman compose -p nextgen-erp build
podman compose -p nextgen-erp up -d
podman compose -p nextgen-erp logs -f create-site
```

When `create-site` finishes, open Desk at `http://127.0.0.1:8180` and sign in as `Administrator` with `ADMIN_PASSWORD` from `.env.server`.

`SERVER_ENV_FILE` defaults to `.env.server` via `compose.yaml`. To use another file:

```bash
SERVER_ENV_FILE=.env.local podman compose -p nextgen-erp up -d
```

## Apply app code changes

After pulling or editing `apps/nextgen_erp`:

```bash
podman compose -p nextgen-erp build
podman compose -p nextgen-erp up -d
```

`create-site` re-runs migrate when the site already exists. To migrate explicitly:

```bash
podman compose -p nextgen-erp exec backend \
  bench --site nextgen.localhost migrate
```

Clear Desk cache after UI changes:

```bash
podman compose -p nextgen-erp exec backend \
  bench --site nextgen.localhost clear-cache
```

## Useful commands

```bash
# Status
podman compose -p nextgen-erp ps

# Logs
podman compose -p nextgen-erp logs -f --tail=200

# Shell in backend
podman compose -p nextgen-erp exec backend bash

# Stop
podman compose -p nextgen-erp down
```

## PO Email Intake demo (on this site)

```bash
podman compose -p nextgen-erp exec backend \
  bench --site nextgen.localhost migrate
```

Then open **AI Procurement Copilot → PO Email Intake** (`/app/po-email-intake`), keep **Use mock fixtures** on, and click **Sync Inbox**.

See [PURCHASE_EMAIL_INTAKE.md](PURCHASE_EMAIL_INTAKE.md).

## Notes

- Prefer `podman compose` over repo shell scripts for day-to-day local runs.
- Do not point a local `.env.server` at the sandbox simulator if you later reuse that file for a real deploy — see [deployment.md](deployment.md).
- MariaDB/Redis-only Bench development (`compose.local-infra.yaml`) is a separate path; this guide is the full containerized site.
