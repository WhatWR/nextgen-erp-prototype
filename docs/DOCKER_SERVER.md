# Deploy NextGen ERP with Docker Compose

This stack is intended for a single Linux server. It runs ERPNext, the NextGen
Frappe app, MariaDB, Redis, workers, scheduler, websocket, LINE order intake and
Caddy HTTPS. Only ports 80 and 443 are public.

For local development where ERPNext runs from the Bench source tree but MariaDB
and Redis run in Docker, use `./scripts/start-local.sh` instead.

## Server requirements

- A recent Docker Engine with Docker Compose v2
- At least 4 CPU cores, 8 GB RAM and 40 GB SSD for an initial pilot
- A domain whose DNS `A`/`AAAA` record points to the server
- Inbound TCP ports 80 and 443, and UDP 443, allowed by the firewall

## First deployment

```bash
git clone YOUR_REPOSITORY_URL nextgen-erp
cd nextgen-erp
cp .env.server.example .env.server
```

Edit `.env.server` and replace every `CHANGE_ME` value. The ERP domain must be a
real public hostname so Caddy can obtain its TLS certificate. Then validate,
build and start the stack:

```bash
docker compose --env-file .env.server -f compose.server.yaml config --quiet
docker compose --env-file .env.server -f compose.server.yaml build
docker compose --env-file .env.server -f compose.server.yaml up -d
docker compose --env-file .env.server -f compose.server.yaml logs -f create-site
```

`create-site` exits successfully after it creates or migrates the ERPNext site.
Open `https://YOUR_ERP_DOMAIN` and sign in as `Administrator` using the password
from `.env.server`.

## Complete the integration

1. In ERPNext, create or open the dedicated user
   `nextgen-order-service@local.invalid`, assign **NextGen Order Service**,
   **Sales User**, **Stock User** and **Accounts User**, and generate its API key
   and secret.
2. Put those two values in `.env.server`, then apply them:

   ```bash
   docker compose --env-file .env.server -f compose.server.yaml up -d order-intake
   ```

3. Open **NextGen Automation Settings** and set:
   - External Service URL: `https://YOUR_ERP_DOMAIN`
   - External Service API Key: the same `ORDER_INTAKE_API_KEY` from `.env.server`
   - Confidence threshold and invoice-link lifetime
4. Open **LINE Channel Settings**, enter the LINE channel secret/access token,
   and use `https://YOUR_ERP_DOMAIN/webhooks/line` as the LINE webhook URL.
5. Create the required **LINE Customer Map** records.

Do not enable `ENABLE_LEGACY_PROTOTYPE` on the server.

## Normal operations

```bash
# Status
docker compose --env-file .env.server -f compose.server.yaml ps

# Logs
docker compose --env-file .env.server -f compose.server.yaml logs -f --tail=200

# Apply code changes and migrate
git pull
docker compose --env-file .env.server -f compose.server.yaml build
docker compose --env-file .env.server -f compose.server.yaml up -d

# Enter the ERPNext backend
docker compose --env-file .env.server -f compose.server.yaml exec backend bash
```

The one-shot `create-site` service runs again during `up` and performs `bench
migrate` when the site already exists.

## Backup

Create an ERPNext backup before every update and copy it away from the server:

```bash
docker compose --env-file .env.server -f compose.server.yaml exec backend \
  bench --site YOUR_SITE_NAME backup --with-files --compress
```

Also back up the Docker volumes `db-data`, `sites` and `caddy-data` using your
hosting provider's snapshot or backup service. Test restoring onto a separate
server before accepting live customer orders.

## Important cautions

- `.env.server` contains production secrets and is ignored by Git.
- Do not publish MariaDB, Redis, backend or order-intake container ports.
- The default ERPNext version remains pinned to the version tested by this
  repository. Upgrade Frappe and ERPNext together, then rerun both test suites.
- Docker Compose provides process isolation, not managed high availability.
  Production accounting use still needs monitoring, off-server backups, restore
  drills and Thai tax/PDPA review.
