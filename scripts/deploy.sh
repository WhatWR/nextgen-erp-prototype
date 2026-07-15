#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${SERVER_ENV_FILE:-.env.server}"
GIT_REMOTE="${GIT_REMOTE:-origin}"

log() {
  printf '\n==> %s\n' "$*"
}

fail() {
	printf '\nDeployment stopped: %s\n' "$*" >&2
	exit 1
}

compose() {
	SERVER_ENV_FILE="$ENV_FILE" docker compose -p nextgen-erp "$@"
}

command -v git >/dev/null 2>&1 || fail "git is not installed"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is not available"

cd "$ROOT_DIR"

[[ -f "$ENV_FILE" ]] || fail "$ENV_FILE is missing. Copy .env.server.example and set the server secrets first."
if grep -Eq '^[A-Za-z_][A-Za-z0-9_]*=CHANGE_ME' "$ENV_FILE"; then
  fail "$ENV_FILE still contains CHANGE_ME values"
fi
# The local sandbox (scripts/sandbox.py, port 8300) must never back production.
if grep -Ev '^\s*#' "$ENV_FILE" | grep -Eiq 'sandbox|(127\.0\.0\.1|localhost):8300'; then
  fail "$ENV_FILE points at the local sandbox simulator; use real service URLs for deployment"
fi
chmod 600 "$ENV_FILE"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "$ROOT_DIR is not a Git repository"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  git status --short
  fail "tracked files have uncommitted changes; commit or restore them before deploying"
fi

BRANCH="$(git symbolic-ref --quiet --short HEAD)" || fail "the repository is in detached HEAD state"
BEFORE="$(git rev-parse --short HEAD)"

log "Pulling the latest $GIT_REMOTE/$BRANCH commit"
git pull --ff-only "$GIT_REMOTE" "$BRANCH"
AFTER="$(git rev-parse --short HEAD)"

if [[ "$BEFORE" == "$AFTER" ]]; then
  log "Already up to date at $AFTER"
else
  log "Updated $BEFORE -> $AFTER"
fi

log "Validating Docker Compose configuration"
compose config --quiet

log "Building application images"
compose build --pull

log "Starting ERPNext and applying site migrations"
compose up -d --remove-orphans

log "Deployment status"
compose ps

printf '\nDeployment complete at commit %s.\n' "$AFTER"
