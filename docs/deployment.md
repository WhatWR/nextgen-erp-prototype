# Deployment (Podman)

Deploy the NextGen ERP stack with **Podman Compose**. No `scripts/deploy.sh` wrapper.

Compose project name must stay `nextgen-erp`. Server secrets live in `.env.server` (never commit it).

For a longer Docker-oriented reference that this guide supersedes for Podman operators, see [DOCKER_SERVER.md](DOCKER_SERVER.md).

## Server requirements

- Podman with Compose (`podman compose version`)
- Roughly 4 CPU / 8 GB RAM / 40 GB disk for a pilot
- Domain DNS for production HTTPS (optional for first bring-up on `127.0.0.1:8180`)
- Inbound 80/443 only when using the production Caddy profile

## First deployment

```bash
git clone YOUR_REPOSITORY_URL nextgen-erp
cd nextgen-erp
cp .env.server.example .env.server
# Replace every CHANGE_ME. Keep SITE_NAME and PUBLIC_URL accurate.
```

```bash
podman compose -p nextgen-erp config --quiet
podman compose -p nextgen-erp build
podman compose -p nextgen-erp up -d
podman compose -p nextgen-erp logs -f create-site
```

Open `http://127.0.0.1:8180` (or your published gateway) and sign in as `Administrator`.

Alternate env file:

```bash
SERVER_ENV_FILE=.env.staging podman compose -p nextgen-erp up -d
```

## Routine update (pull + rebuild + migrate)

```bash
cd /path/to/nextgen-erp
git pull --ff-only

# Refuse deploy if .env.server still has CHANGE_ME placeholders
grep -E '^[A-Za-z_][A-Za-z0-9_]*=CHANGE_ME' .env.server && exit 1

podman compose -p nextgen-erp config --quiet
podman compose -p nextgen-erp build --pull
podman compose -p nextgen-erp up -d --remove-orphans
podman compose -p nextgen-erp ps
```

`create-site` runs migrate when the site already exists. Explicit migrate:

```bash
set -a && source .env.server && set +a
podman compose -p nextgen-erp exec backend \
  bench --site "$SITE_NAME" migrate
```

## Production HTTPS

Set `ERP_DOMAIN`, `ACME_EMAIL`, and `PUBLIC_URL` in `.env.server`, then:

```bash
podman compose -p nextgen-erp --profile production up -d
```

## Post-deploy integration

1. Bootstrap the least-privilege Order Intake service user:

```bash
set -a && source .env.server && set +a
podman compose -p nextgen-erp exec -T backend bench --site "$SITE_NAME" execute \
  nextgen_erp.provision.configure_server_integration \
  --kwargs "{\"order_intake_api_key\":\"$ORDER_INTAKE_API_KEY\"}"
```

2. Put the returned `api_key` / `api_secret` into `ERPNEXT_API_KEY` / `ERPNEXT_API_SECRET` in `.env.server`, then:

```bash
podman compose -p nextgen-erp up -d --force-recreate order-intake gateway
```

3. Configure LINE Channel Settings, Automation Settings, and LINE Customer Map in Desk.

## Backup

```bash
set -a && source .env.server && set +a
podman compose -p nextgen-erp exec backend \
  bench --site "$SITE_NAME" backup --with-files --compress
```

Also snapshot volumes (`db-data`, `sites`, `caddy-data`) off-box.

## Operations

```bash
podman compose -p nextgen-erp ps
podman compose -p nextgen-erp logs -f --tail=200
podman compose -p nextgen-erp exec backend bash
```

## Cautions

- `.env.server` holds production secrets and is gitignored.
- Do not publish MariaDB, Redis, backend, or order-intake ports publicly.
- Never point a production `.env.server` at the local sandbox (`*:8300`).
- Compose isolates processes; it is not managed HA — keep monitoring and restore drills.
