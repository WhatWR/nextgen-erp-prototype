#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BENCH_DIR="${BENCH_DIR:-$HOME/nextgen-bench}"
BENCH_VENV="${BENCH_VENV:-$HOME/.frappe-bench-venv}"

export PATH="/usr/local/bin:$BENCH_VENV/bin:$PATH"

if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

export ERPNEXT_URL="${ERPNEXT_URL:-http://127.0.0.1:8000}"
export ORDER_BACKEND="erpnext"
export LINE_CONFIG_SOURCE="erpnext"
export LINE_WORKFLOW_BACKEND="erpnext"
export ERPCLAW_BACKEND="erpnext"
export ORDER_INTAKE_AUTO_SEED="0"
export PYTHONPATH="$ROOT_DIR/apps/order-intake-api${PYTHONPATH:+:$PYTHONPATH}"

docker compose -f "$ROOT_DIR/compose.local-infra.yaml" up -d --wait

cd "$BENCH_DIR"
bench set-config -g db_host 127.0.0.1
bench set-config -gp db_port 3306
bench set-config -g redis_cache redis://127.0.0.1:13000
bench set-config -g redis_queue redis://127.0.0.1:11000
bench set-config -g redis_socketio redis://127.0.0.1:13000

echo "ERPNext: http://127.0.0.1:8000"
echo "AI Order Intake: http://127.0.0.1:8200/health"
echo "MariaDB and Redis are running in Docker."

cleanup() {
  if [ -n "${INTAKE_PID:-}" ]; then
    kill "$INTAKE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

python3 "$ROOT_DIR/apps/order-intake-api/run.py" \
  --host 127.0.0.1 \
  --port 8200 \
  --db /tmp/nextgen-order-intake.sqlite3 \
  --export-dir /tmp/nextgen-order-intake-exports &
INTAKE_PID=$!

honcho start -f "$ROOT_DIR/docker/Procfile.local"
