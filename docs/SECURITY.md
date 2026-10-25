# Security model and hardening checklist

## Threat model

MarketLens displays public market data and stores only watchlists and display preferences.
The main assets to protect:

1. **Provider API keys.** Theft enables quota abuse or charges on paid plans.
2. **The server.** It must not become a foothold for attackers.
3. **Availability and provider quotas.** Abusive traffic must not exhaust credits.

## Controls in place

**Secrets**

- Keys are read only from environment variables (`.env`, mode 600, git-ignored) and held in
  `SecretStr`. They never appear in `repr()` or logs.
- The Twelve Data key is sent in the `Authorization` header, not the URL, so it never reaches
  access logs.
- The browser never receives keys. It calls only same-origin `/api` endpoints.

**Network exposure**

- The EC2 security group allows SSH (your IP only), 80 and 443. UFW enforces the same
  rules on the host.
- The containers publish only `127.0.0.1:8080`. PostgreSQL and Redis are reachable only on
  the internal Docker network. Published ports are bound to localhost, because Docker can
  bypass UFW for ports bound to all interfaces.
- TLS comes from Let's Encrypt (auto-renewed), with HSTS and an HTTP→HTTPS redirect.

**Input handling**

- Symbols are validated against a strict allow-list pattern before use. Provider parameters
  are passed as query parameters, never concatenated into URLs.
- Every query and body parameter has typed, range-checked Pydantic validation. Unknown
  preference fields are rejected.
- The database is accessed only through SQLAlchemy with bound parameters.

**Abuse protection**

- Nginx `limit_req` (10 req/s per IP, burst 40, on `/api`) plus an API-level per-IP
  limit (240/min) and a limit of 20 workspace creations per IP per hour.
- Nginx overwrites `X-Forwarded-For` with the real client address, so clients cannot spoof
  IPs to evade limits.
- Provider credit budgets are enforced locally before any upstream call, with negative caching
  of permanent errors.

**HTTP security headers**

- Content-Security-Policy `default-src 'self'` (no inline scripts), `frame-ancestors 'none'`,
  `X-Frame-Options DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`.
- `Cache-Control: no-store` on API responses.

**Containers**

- All app containers run as non-root (`app` user; unprivileged Nginx).
- Read-only root filesystems with tmpfs `/tmp`, and `no-new-privileges`.
- Pinned base images and fully pinned Python/npm dependencies (`requirements.txt`,
  `package-lock.json`).

**Production mode**

- `APP_ENV=production` and `DEBUG=false` are forced by `docker-compose.yml`.
- Stack traces are never returned to clients. Unexpected errors return a generic 500 and are
  logged with a request id.

**Host**

- SSH is key-only (the EC2 default), with fail2ban, unattended security upgrades and Docker
  log rotation.

## Identity and the watchlist

MarketLens has **no passwords and no user accounts**. The first visit creates an *anonymous
workspace*: a random 256-bit token kept in the browser's localStorage. The server stores only
its SHA-256 hash. Anyone holding a token can read and change that workspace's watchlist and
preferences, and nothing more. This avoids building custom password handling, which the
requirements specifically asked to avoid.

Options if you need more:

- **Private deployment:** enable Nginx basic auth (deployment guide, step 10b).
- **Real per-user accounts:** put an OpenID Connect proxy in front of the app, such as
  [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/) with Google, GitHub, Okta, or
  AWS Cognito. Then key workspaces by the identity header the proxy forwards
  (`X-Auth-Request-Email`) instead of the token. Use a reputable identity provider; don't
  build password storage yourself.

## Operational checklist

- [ ] MFA on the AWS root user. Daily work uses an IAM/Identity Center user.
- [ ] Security group SSH rule limited to your current IP.
- [ ] `.env` is mode 600 and has never been committed (`git log -p -- .env` shows nothing).
- [ ] Rotate the Twelve Data key if it may have leaked (provider dashboard → regenerate →
      update `.env` → `docker compose up -d backend`).
- [ ] `sudo certbot renew --dry-run` succeeds.
- [ ] Backups run (`ls ~/marketlens/backups`), are copied off-instance (S3), and a restore has
      been tested once.
- [ ] CloudWatch alarms on status checks and CPU; an external uptime check on `/api/health`.
- [ ] Review `docker compose logs backend | grep -i error` and `sudo fail2ban-client status sshd` occasionally.
- [ ] Update regularly: `./deployment/scripts/deploy.sh` (rebuilds with `--pull`), and update
      pinned dependencies when security advisories appear (`pip-audit`, `npm audit`).

## Privacy

Request logs include the client IP, path, status and duration, but no request bodies and no
tokens. Logs rotate at 50 MB per container. If you serve users in jurisdictions with privacy
rules, mention IP logging in a privacy notice, or shorten retention.
