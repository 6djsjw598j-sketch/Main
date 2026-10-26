#!/usr/bin/env bash
# One-time setup of the Level Mount Co. website on a fresh Ubuntu 24.04 EC2 instance.
# Installs Node.js 22, Nginx, Certbot and UFW; creates a locked-down service user;
# installs the app as a systemd service behind Nginx; optionally gets a TLS certificate.
#
# Usage (from a clone of this repository on the instance):
#   sudo ./wallmount-tv/deploy/bootstrap-ec2.sh <domain> [extra-domain ...] [--email you@example.com]
# Example:
#   sudo ./wallmount-tv/deploy/bootstrap-ec2.sh example.com www.example.com --email owner@example.com
# Without --email, the site is served over HTTP only; run certbot later (see README).
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo." >&2; exit 1; fi

DOMAINS=()
EMAIL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --email) EMAIL="${2:?--email needs a value}"; shift 2 ;;
    -*) echo "Unknown option: $1" >&2; exit 1 ;;
    *) DOMAINS+=("$1"); shift ;;
  esac
done
if [ ${#DOMAINS[@]} -eq 0 ]; then
  echo "Usage: sudo $0 <domain> [extra-domain ...] [--email you@example.com]" >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
export DEBIAN_FRONTEND=noninteractive

echo "==> Installing system packages"
apt-get update -y
apt-get install -y ca-certificates curl gnupg rsync nginx certbot python3-certbot-nginx ufw unattended-upgrades

NODE_MAJOR=22
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
  echo "==> Installing Node.js ${NODE_MAJOR} (NodeSource)"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list
  apt-get update -y
  apt-get install -y nodejs
fi
node --version

echo "==> Creating service user and directories"
id levelmount >/dev/null 2>&1 || useradd --system --home /opt/levelmount --shell /usr/sbin/nologin levelmount
install -d -o root -g root -m 0755 /opt/levelmount/app

if [ ! -f /etc/levelmount.env ]; then
  echo "==> Writing /etc/levelmount.env (with a generated ADMIN_TOKEN)"
  umask 077
  cat > /etc/levelmount.env <<ENV
PORT=3000
HOST=127.0.0.1
DATA_DIR=/var/lib/levelmount
TRUST_PROXY=1
QUOTE_RATE_LIMIT=5
ADMIN_TOKEN=$(openssl rand -hex 32)
ENV
  chown root:levelmount /etc/levelmount.env
  chmod 0640 /etc/levelmount.env
fi

echo "==> Installing systemd unit"
install -m 0644 "$HERE/levelmount.service" /etc/systemd/system/levelmount.service
systemctl daemon-reload
systemctl enable levelmount

echo "==> Deploying the app"
"$HERE/deploy.sh"

echo "==> Configuring Nginx"
install -m 0644 "$HERE/proxy.conf" /etc/nginx/snippets/levelmount-proxy.conf
sed "s/__DOMAIN__/${DOMAINS[*]}/" "$HERE/nginx.conf" > /etc/nginx/sites-available/levelmount.conf
ln -sf /etc/nginx/sites-available/levelmount.conf /etc/nginx/sites-enabled/levelmount.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> Firewall (SSH, HTTP, HTTPS)"
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

if [ -n "$EMAIL" ]; then
  echo "==> Requesting TLS certificate"
  CERT_ARGS=()
  for d in "${DOMAINS[@]}"; do CERT_ARGS+=(-d "$d"); done
  certbot --nginx --non-interactive --agree-tos --redirect -m "$EMAIL" "${CERT_ARGS[@]}"
fi

echo
echo "Done. Site: http${EMAIL:+s}://${DOMAINS[0]}/"
echo "Admin token for GET /api/quotes is in /etc/levelmount.env (sudo grep ADMIN_TOKEN /etc/levelmount.env)."
