#!/usr/bin/env bash
# Dumps the PostgreSQL database to backups/ (gzipped), keeps the last 14 days, and
# optionally copies the dump to S3 when BACKUP_S3_BUCKET is set (requires the AWS CLI and
# an instance role with s3:PutObject on that bucket).
# Usage: ./deployment/scripts/backup_db.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

set -a
# shellcheck disable=SC1091
. ./.env
set +a
mkdir -p backups
chmod 700 backups
file="backups/marketlens-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
docker compose exec -T db pg_dump -U "${POSTGRES_USER:-marketlens}" -d "${POSTGRES_DB:-marketlens}" --no-owner | gzip > "$file"
chmod 600 "$file"
echo "Wrote $file ($(du -h "$file" | cut -f1))"

find backups -name 'marketlens-*.sql.gz' -mtime +14 -delete

if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  aws s3 cp "$file" "s3://${BACKUP_S3_BUCKET}/marketlens/$(basename "$file")" --only-show-errors
  echo "Uploaded to s3://${BACKUP_S3_BUCKET}/marketlens/"
fi
