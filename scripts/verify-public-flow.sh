#!/usr/bin/env sh
set -eu

base_url="${1:?usage: verify-public-flow.sh https://erp.example.com}"
allow_temporary="${ALLOW_TEMPORARY_PUBLIC_URL:-0}"
if [ "${2:-}" = "--allow-temporary" ]; then
  allow_temporary=1
fi
case "$base_url" in
  https://localhost*|https://127.*|http://*)
    echo "ERROR: production public URL must be a stable HTTPS domain" >&2
    exit 1
    ;;
esac
case "$base_url" in
  *ngrok-free.app*|*ngrok-free.dev*|*ngrok.app*|*ngrok.io*)
    if [ "$allow_temporary" != "1" ]; then
      echo "ERROR: ngrok requires ALLOW_TEMPORARY_PUBLIC_URL=1" >&2
      exit 1
    fi
    ;;
esac

echo "Checking public ERPNext origin: $base_url"
curl --fail --silent --show-error --location --max-time 20 \
  "$base_url/api/method/ping" >/dev/null
echo "Public HTTPS origin is reachable."

echo "Checking LINE webhook route (a non-2xx signature response is expected)..."
status="$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 20 \
  "$base_url/webhooks/line")"
case "$status" in
  000|404|502|503|504)
    echo "ERROR: LINE webhook route is unavailable (HTTP $status)" >&2
    exit 1
    ;;
esac
echo "LINE webhook route is present (HTTP $status)."
