#!/usr/bin/env bash
# Creates .env from .env.example with a random database password and your API key.
# Usage: ./deployment/scripts/init_env.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -f .env ]; then
  echo ".env already exists; edit it directly (nano .env)." >&2
  exit 1
fi
cp .env.example .env
chmod 600 .env
password="$(openssl rand -hex 24)"
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${password}/" .env

read -r -s -p "Twelve Data API key (leave empty for crypto-only): " key
echo
if [ -n "$key" ]; then
  # Keys are alphanumeric; reject anything else to keep sed and the file safe.
  if ! [[ "$key" =~ ^[A-Za-z0-9]+$ ]]; then
    echo "Unexpected characters in the key; add it to .env manually." >&2
  else
    sed -i "s/^TWELVEDATA_API_KEY=.*/TWELVEDATA_API_KEY=${key}/" .env
  fi
fi
echo "Created .env (permissions 600). Review it with: nano .env"
