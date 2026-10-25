#!/usr/bin/env bash
# External health check. Logs to syslog and restarts the stack after repeated failures.
# Run by marketlens-healthcheck.timer every 5 minutes.
# Usage: ./deployment/scripts/healthcheck.sh [url]
set -uo pipefail
cd "$(dirname "$0")/../.."
# Read HTTP_PORT (and other settings) from .env when present.
if [ -f .env ]; then set -a; . ./.env; set +a; fi
URL="${1:-http://127.0.0.1:${HTTP_PORT:-8080}/api/health}"
STATE=/tmp/marketlens-health-failures

if body="$(curl -fsS --max-time 10 "$URL")"; then
  status="$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","unknown"))' 2>/dev/null || echo unknown)"
  logger -t marketlens "health ok (status=$status)"
  echo 0 > "$STATE"
  [ "$status" = "degraded" ] && logger -t marketlens "WARNING: a market-data provider is degraded"
  exit 0
fi

failures=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$failures" > "$STATE"
logger -t marketlens "health check FAILED ($failures in a row) for $URL"
if [ "$failures" -ge 3 ]; then
  logger -t marketlens "restarting MarketLens containers"
  docker compose restart backend frontend
  echo 0 > "$STATE"
fi
exit 1
