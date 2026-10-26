#!/usr/bin/env bash
# Copy the current checkout to /opt/levelmount/app, restart the service and health-check it.
# Usage (on the instance, after `git pull`):  sudo ./wallmount-tv/deploy/deploy.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo." >&2; exit 1; fi

APP_SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP_DST=/opt/levelmount/app

# Code is owned by root and read-only to the service user; only /var/lib/levelmount is writable.
rsync -a --delete --chown=root:root --chmod=D755,F644 \
  --exclude data/ --exclude test/ --exclude deploy/ --exclude node_modules/ --exclude '.env*' \
  "$APP_SRC/" "$APP_DST/"

systemctl restart levelmount

PORT="$(grep -E '^PORT=' /etc/levelmount.env | cut -d= -f2 || true)"
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${PORT:-3000}/healthz" >/dev/null; then
    echo "Deployed: service healthy."
    exit 0
  fi
  sleep 0.5
done
echo "Service did not become healthy. Recent logs:" >&2
journalctl -u levelmount -n 50 --no-pager >&2
exit 1
