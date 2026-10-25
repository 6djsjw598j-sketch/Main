#!/usr/bin/env bash
# Restores a backup created by backup_db.sh. This REPLACES the current watchlists/preferences.
# Usage: ./deployment/scripts/restore_db.sh backups/marketlens-YYYYMMDDTHHMMSSZ.sql.gz
set -euo pipefail
cd "$(dirname "$0")/../.."
file="${1:?Usage: restore_db.sh <backup.sql.gz>}"
set -a
# shellcheck disable=SC1091
. ./.env
set +a
read -r -p "Restore $file and overwrite the current database? Type 'yes': " answer
[ "$answer" = "yes" ] || { echo "Aborted."; exit 1; }
docker compose stop backend
docker compose exec -T db psql -U "${POSTGRES_USER:-marketlens}" -d postgres -c "DROP DATABASE IF EXISTS ${POSTGRES_DB:-marketlens} WITH (FORCE);"
docker compose exec -T db psql -U "${POSTGRES_USER:-marketlens}" -d postgres -c "CREATE DATABASE ${POSTGRES_DB:-marketlens};"
gunzip -c "$file" | docker compose exec -T db psql -U "${POSTGRES_USER:-marketlens}" -d "${POSTGRES_DB:-marketlens}" -q
docker compose start backend
echo "Restored $file"
