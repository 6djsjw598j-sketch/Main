#!/usr/bin/env bash
# Pulls the latest code, rebuilds images and restarts the stack with zero manual steps.
# Usage: ./deployment/scripts/deploy.sh [git-ref]
set -euo pipefail
cd "$(dirname "$0")/../.."
# Read HTTP_PORT (and other settings) from .env when present.
if [ -f .env ]; then set -a; . ./.env; set +a; fi

REF="${1:-}"
if [ ! -f .env ]; then
  echo ".env is missing. Run ./deployment/scripts/init_env.sh first." >&2
  exit 1
fi

echo "==> Updating source"
git fetch --all --prune
if [ -n "$REF" ]; then
  git checkout "$REF"
fi
git pull --ff-only || echo "(not on a tracking branch; using current checkout)"

echo "==> Backing up the database before deploying"
if docker compose ps --status running db >/dev/null 2>&1 && [ -n "$(docker compose ps -q db)" ]; then
  ./deployment/scripts/backup_db.sh || echo "Backup failed; continuing (check disk space)."
fi

echo "==> Building images"
docker compose build --pull

echo "==> Starting containers (migrations run automatically)"
docker compose up -d --remove-orphans

echo "==> Waiting for the API to become healthy"
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${HTTP_PORT:-8080}/api/health" >/dev/null 2>&1; then
    echo "Healthy."
    docker compose ps
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 3
done
echo "The API did not become healthy. Recent logs:" >&2
docker compose logs --tail 80 backend >&2
exit 1
