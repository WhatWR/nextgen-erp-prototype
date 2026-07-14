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

Edit `.env.server` and replace every `CHANGE_ME` value. A domain is not required
for the first deployment. The default `compose.yaml` automatically loads
`.env.server` and publishes a local-only gateway on `127.0.0.1:8180`:

The Compose project name is fixed to `nextgen-erp`; do not start the same file
with a different `-p` value or `COMPOSE_PROJECT_NAME`.

```bash
docker compose config --quiet
docker compose build
docker compose up -d
docker compose logs -f create-site
```

To use a differently named server environment file, set `SERVER_ENV_FILE`, for
example `SERVER_ENV_FILE=.env.staging docker compose up -d`.

`create-site` exits successfully after it creates or migrates the ERPNext site.
Open `http://127.0.0.1:8180` and sign in as `Administrator` using the password
from `.env.server`.

## Temporary public URL with ngrok

Keep ngrok running on the same server and tunnel the local gateway:

```bash
ngrok http 8180
```

Use the generated HTTPS URL for browser access and append `/webhooks/line` for
the LINE webhook. When ngrok gives you a new URL, update ERPNext's public URL:

```bash
docker compose exec backend bench --site YOUR_SITE_NAME \
  set-config host_name https://YOUR-NGROK-HOST.ngrok-free.app
```

The gateway sends `/webhooks/line` to Order Intake and all other requests to
ERPNext, so one ngrok tunnel is enough.

## Production domain and HTTPS

After DNS is available, set `ERP_DOMAIN`, `ACME_EMAIL`, and `PUBLIC_URL` in
`.env.server`, then enable the Caddy production profile:

```bash
docker compose --profile production up -d
```

## Complete the integration

1. Load `.env.server`, then run the administrator-only bootstrap helper. It
   creates the least-privilege service user, commits its API credentials, and
   configures ERPNext's internal Order Intake URL/key:

   ```bash
   set -a
   source .env.server
   set +a
   docker compose exec -T backend bench --site "$SITE_NAME" execute \
     nextgen_erp.provision.configure_server_integration \
     --kwargs "{\"order_intake_api_key\":\"$ORDER_INTAKE_API_KEY\"}"
   ```

2. Copy the returned `api_key` and `api_secret` into `ERPNEXT_API_KEY` and
   `ERPNEXT_API_SECRET` in `.env.server`. The secret is shown only this time.
   Recreate Order Intake so it receives them:

   ```bash
   docker compose up -d --force-recreate order-intake gateway
   ```

3. Open **NextGen Automation Settings** to choose the confidence threshold,
   automatic routing policy and invoice-link lifetime. The internal service URL
   and key were already set by the bootstrap helper.
4. Open **LINE Channel Settings**, enter the LINE channel secret/access token,
   and use `YOUR_PUBLIC_URL/webhooks/line` as the LINE webhook URL. During the
   pilot, `YOUR_PUBLIC_URL` is the HTTPS URL displayed by ngrok.
5. Create the required **LINE Customer Map** records.

Do not enable `ENABLE_LEGACY_PROTOTYPE` on the server.

## Normal operations

```bash
# Pull the current branch, build, migrate and deploy
./scripts/deploy.sh

# Status
docker compose ps

# Logs
docker compose logs -f --tail=200

# Apply code changes and migrate
git pull
docker compose build
docker compose up -d

# Enter the ERPNext backend
docker compose exec backend bash
```

The one-shot `create-site` service runs again during `up` and performs `bench
migrate` when the site already exists.

## Backup

Create an ERPNext backup before every update and copy it away from the server:

```bash
docker compose exec backend \
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
