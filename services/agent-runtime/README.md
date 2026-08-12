# NextGen Agent Runtime

> **Document status:** Current capability of the Phase 1 orchestration microservice. The architecture it implements is owned by [docs/AI_HANDOFF](../../docs/AI_HANDOFF/README.md).

A separately deployed Python service that owns agent orchestration, model calls,
prompt versions and the tool loop. It owns no ERP data.

## Trust boundary

| The runtime does | The runtime never does |
|---|---|
| Call the model provider and retry it | Connect to Frappe MariaDB or share Frappe Redis |
| Choose which allowlisted tool to call next | Mount site files or run arbitrary Python |
| Send sanitized steps to Frappe | Call generic DocType CRUD or raw SQL |
| Ask Frappe to run a domain tool | Calculate an authoritative quantity, price or permission |
| Ask Frappe to create an Action Proposal | Approve or execute a proposal |
| Receive dispatch from Frappe | Receive browser, LINE, webhook or Desk traffic |

Frappe authenticates the requester, establishes company context and creates a
queued `NextGen Agent Run` before anything reaches this service. There is no
`approve_proposal` or `execute_proposal` on the gateway client, so approval
cannot be reached from here even by mistake.

## Endpoints

| Route | Auth | Purpose |
|---|---|---|
| `GET /healthz` | none | Liveness, runtime version, contract version, enabled agents |
| `GET /readyz` | none | `503` until gateway, dispatch and model credentials are configured |
| `GET /openapi.json` | none | OpenAPI 3.1 generated from the enforced `v1` schemas |
| `POST /v1/runs/dispatch` | service token | Accept a queued run for asynchronous execution |
| `POST /v1/runs/resume` | service token | Re-claim a persisted run and continue from its next sequence |
| `POST /v1/runs/cancel` | service token | Request cooperative cancellation |

Dispatch returns `202` immediately. A caller timeout means unknown delivery, not
failure: redelivering the same run ID while it is in flight is a no-op, and every
step, tool call and proposal carries a deterministic idempotency key
(`{run_id}:{sequence}:{operation}`) so a restart replays instead of duplicating.

## Configuration

All configuration is environment-only; nothing is read from source control.

| Variable | Purpose |
|---|---|
| `NEXTGEN_FRAPPE_BASE_URL` | Private base URL of the Frappe site |
| `NEXTGEN_FRAPPE_API_KEY` / `NEXTGEN_FRAPPE_API_SECRET` | Credentials of the restricted runtime service user |
| `NEXTGEN_RUNTIME_SERVICE_TOKEN` | Secret Frappe presents when dispatching; rotated separately from the above |
| `NEXTGEN_MODEL_BASE_URL` / `NEXTGEN_MODEL_API_KEY` | OpenAI-compatible model provider |
| `NEXTGEN_MODEL_DEFAULT` | Model used when the run does not name one |
| `NEXTGEN_ENABLED_AGENTS` | Comma-separated agent keys. **Empty by default** — rollout step 2 deploys with no enabled agents |
| `NEXTGEN_MAX_CONCURRENT_RUNS` | Bound on in-flight runs |
| `NEXTGEN_REQUEST_TIMEOUT_SECONDS`, `NEXTGEN_LOG_LEVEL` | Transport and logging |

## Run it

```bash
NEXTGEN_ENABLED_AGENTS=sales,procurement \
  uvicorn nextgen_agent_runtime.api.asgi:application --host 127.0.0.1 --port 8400
```

Bind to the private service network only. The code has no third-party
dependencies; the ASGI server is supplied by the deployment image.

## Tests

```bash
PYTHONPATH=src:tests python -m unittest discover -s tests -t tests
```

On Windows use `PYTHONPATH="src;tests"`. `tests/test_contracts.py` also loads the
Frappe app's copy of the `v1` contracts when the repository is checked out and
fails if the two fingerprints diverge; it skips when the app is absent so the
service stays independently testable.
