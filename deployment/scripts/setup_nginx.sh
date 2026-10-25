#!/usr/bin/env bash
# Installs the host Nginx site and obtains a Let's Encrypt certificate.
# Usage: sudo ./deployment/scripts/setup_nginx.sh example.com you@example.com
set -euo pipefail

DOMAIN="${1:?Usage: setup_nginx.sh <domain> <email>}"
EMAIL="${2:?Usage: setup_nginx.sh <domain> <email>}"
if ! [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "Invalid domain: $DOMAIN" >&2
  exit 1
fi
HERE="$(cd "$(dirname "$0")/.." && pwd)"

sed "s/__DOMAIN__/${DOMAIN}/g" "$HERE/nginx/marketlens.conf" > /etc/nginx/sites-available/marketlens.conf
ln -sf /etc/nginx/sites-available/marketlens.conf /etc/nginx/sites-enabled/marketlens.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> Requesting certificate for ${DOMAIN} (DNS must already point to this server)"
certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --no-eff-email --redirect --non-interactive
systemctl reload nginx
echo "==> HTTPS enabled: https://${DOMAIN}"
echo "    Certbot installed a timer for automatic renewal; test it with: sudo certbot renew --dry-run"
