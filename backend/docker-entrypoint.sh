#!/bin/sh
# Applies database migrations, then starts the API.
set -eu

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  attempt=1
  until alembic upgrade head; do
    if [ "$attempt" -ge 10 ]; then
      echo "Database migrations failed after $attempt attempts" >&2
      exit 1
    fi
    echo "Database not ready (attempt $attempt); retrying in 3s..." >&2
    attempt=$((attempt + 1))
    sleep 3
  done
fi

exec "$@"
