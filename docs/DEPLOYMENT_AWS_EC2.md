# Deploying MarketLens on AWS EC2 (from a Mac)

This guide takes you from an AWS account to `https://your-domain` running MarketLens with
HTTPS, automatic restarts, backups and health checks. Commands to type **on your Mac** are
marked `mac$`; commands **on the server** are marked `ubuntu$`.

What you will build:

```
Internet ──443/80──▶ EC2 (Ubuntu 24.04)
                       ├─ Nginx (host) ── TLS by Let's Encrypt, rate limiting
                       │     └─▶ 127.0.0.1:8080 ── frontend container (Nginx + React build)
                       │                               └─▶ backend container (FastAPI)
                       │                                     ├─ PostgreSQL container (data volume)
                       │                                     └─ Redis container (cache)
                       └─ UFW firewall · fail2ban · automatic security updates
```

---

## 1. Prerequisites and instance choice

You need:

- An AWS account with billing enabled (and MFA on the root user, see step 1b).
- A domain name you control (e.g. from Route 53, Namecheap, Cloudflare). HTTPS certificates
  are issued for a domain, not for a bare IP address.
- A Twelve Data API key if you want stocks/ETFs (free at <https://twelvedata.com/pricing>).
  Crypto works without any key.
- On your Mac: Terminal (built in) and `git` (`xcode-select --install` installs it).

**Recommended instance: `t4g.small`**: 2 vCPU (AWS Graviton/ARM64), 2 GiB RAM, about
US$12/month on demand in us-east-1 (see the cost section for details and caveats). All images
used here are multi-architecture, so ARM works without changes. The bootstrap script adds 2 GB
of swap so Docker image builds don't run out of memory.

| Option | Specs | When to choose |
|---|---|---|
| `t4g.small` (recommended) | 2 vCPU ARM, 2 GiB | Personal use / small team |
| `t4g.micro` | 2 vCPU ARM, 1 GiB | Very light use; image builds are slow (rely on swap) |
| `t3.small` | 2 vCPU x86, 2 GiB | If you need x86 for other software; costs more than t4g |
| `t4g.medium` | 2 vCPU ARM, 4 GiB | Several concurrent users or faster builds |

### 1b. Secure the AWS account first

1. Sign in as root, open **IAM → Security credentials**, and enable **MFA**.
2. Create an IAM user (or IAM Identity Center user) with administrator access for daily
   work. Stop using root.
3. Optional but recommended: **Billing → Budgets → Create budget** with a monthly cost
   budget (e.g. US$25) and an email alert.

## 2. Create the Ubuntu instance

AWS Console → **EC2** → choose a region near you (top right) → **Launch instance**:

1. **Name:** `marketlens`
2. **AMI:** *Ubuntu Server 24.04 LTS (HVM), SSD Volume Type*. Select the **64-bit (Arm)**
   architecture for `t4g`, or *64-bit (x86)* for `t3`.
3. **Instance type:** `t4g.small`.
4. **Key pair:** *Create new key pair*. Name it `marketlens`, type **ED25519**, format **.pem**.
   The browser downloads `marketlens.pem`. Keep it safe; AWS cannot give it to you again.
5. **Network settings → Edit:**
   - Auto-assign public IP: *Enable*
   - Firewall: *Create security group* named `marketlens-sg` (rules in step 3)
6. **Storage:** 20 GiB **gp3** (enough for images, logs and backups).
7. **Advanced details** (optional): *IAM instance profile*. Attach a role that may write to
   your backup S3 bucket (step 13). Leave *Metadata version* as **V2 only** (the default).
8. **Launch instance.**

Give the server a fixed address: **EC2 → Elastic IPs → Allocate Elastic IP address →
Allocate**, then **Actions → Associate** with the `marketlens` instance. Note the IP; this
guide calls it `YOUR_EIP`.

## 3. Security group rules

**EC2 → Security Groups → marketlens-sg → Inbound rules → Edit.** Keep exactly these:

| Type | Port | Source | Why |
|---|---|---|---|
| SSH | 22 | **My IP** (e.g. `203.0.113.7/32`) | Administration from your Mac only |
| HTTP | 80 | `0.0.0.0/0` and `::/0` | Let's Encrypt validation + redirect to HTTPS |
| HTTPS | 443 | `0.0.0.0/0` and `::/0` | The website |

Do **not** open 8000, 8080, 5432 or 6379. The app's internal ports are bound to `127.0.0.1`
or the private Docker network, and UFW blocks everything else as a second layer.

If your home IP changes and SSH stops working, edit the SSH rule again and pick **My IP**.
Find your current IP with `mac$ curl https://checkip.amazonaws.com`.

## 4. Connect with SSH from macOS Terminal

```bash
mac$ mkdir -p ~/.ssh && mv ~/Downloads/marketlens.pem ~/.ssh/
mac$ chmod 400 ~/.ssh/marketlens.pem              # SSH refuses keys readable by others
mac$ ssh -i ~/.ssh/marketlens.pem ubuntu@YOUR_EIP
```

Answer `yes` to the fingerprint prompt the first time. Optional shortcut: add this to
`~/.ssh/config` on your Mac, then connect with `ssh marketlens`:

```
Host marketlens
    HostName YOUR_EIP
    User ubuntu
    IdentityFile ~/.ssh/marketlens.pem
    ServerAliveInterval 60
```

## 5. Install Docker, Docker Compose, Nginx and security tooling

Get the code first (step 6), because the installer script lives in the repository. Ubuntu
already includes `git`.

```bash
ubuntu$ cd ~/marketlens
ubuntu$ sudo ./deployment/scripts/bootstrap_ec2.sh
ubuntu$ exit                    # log out so the new 'docker' group membership applies
mac$    ssh marketlens          # log back in
ubuntu$ docker run --rm hello-world && docker compose version
```

The script (read it first, it is short) installs:

- **Docker Engine** and the **Compose plugin** from Docker's official apt repository, with log rotation
- **Nginx**, **Certbot** with its Nginx plugin, and `apache2-utils` (for optional basic auth)
- **UFW** firewall: allows SSH, HTTP and HTTPS only
- **fail2ban** for SSH brute-force protection
- **unattended-upgrades** for automatic security patches
- a **2 GB swap file**

*Alternative without Docker:* you could run Uvicorn under systemd with a local PostgreSQL,
but Docker Compose is the supported path because it pins every service's version.

## 6. Upload or clone the application

**Option A: clone from GitHub (recommended).** For a private repository, create a read-only
deploy key on the server:

```bash
ubuntu$ ssh-keygen -t ed25519 -f ~/.ssh/marketlens_deploy -N "" -C "marketlens-ec2"
ubuntu$ cat ~/.ssh/marketlens_deploy.pub
```

In GitHub: **repository → Settings → Deploy keys → Add deploy key**. Paste the key and
leave *Allow write access* unchecked. Then:

```bash
ubuntu$ cat >> ~/.ssh/config <<'EOF'
Host github.com
    IdentityFile ~/.ssh/marketlens_deploy
    IdentitiesOnly yes
EOF
ubuntu$ git clone git@github.com:OWNER/REPO.git ~/marketlens
```

(For a public repository: `git clone https://github.com/OWNER/REPO.git ~/marketlens`.)

**Option B: copy from your Mac** without GitHub:

```bash
mac$ cd /path/to/marketlens
mac$ rsync -av --exclude node_modules --exclude .venv --exclude .env --exclude backend/data \
        ./ marketlens:~/marketlens/
```

The systemd units in step 11 assume the code lives in `/home/ubuntu/marketlens`.

## 7. Configure production environment variables and API keys

```bash
ubuntu$ cd ~/marketlens
ubuntu$ ./deployment/scripts/init_env.sh       # asks for the Twelve Data key (input hidden)
ubuntu$ nano .env                              # review; Ctrl+O to save, Ctrl+X to exit
ubuntu$ ls -l .env                             # must show -rw------- (600)
```

`init_env.sh` copies `.env.example`, generates a random `POSTGRES_PASSWORD`, stores your key,
and sets the file mode to 600. Rules:

- **Never commit `.env`** (it is in `.gitignore`), never paste keys into chat or tickets,
  and never put them in frontend code. The browser never sees them.
- Set `TWELVEDATA_CREDITS_PER_MINUTE` / `_PER_DAY` to your plan's limits (Basic: 8 / 800).
- To rotate a key: edit `.env`, then run `docker compose up -d backend`.
- Debug mode is off. `docker-compose.yml` forces `APP_ENV=production` and `DEBUG=false`.

## 8. Start the application

```bash
ubuntu$ ./deployment/scripts/deploy.sh
```

This builds the images (the first build takes a few minutes on a t4g.small), starts PostgreSQL,
Redis, the backend (which applies Alembic migrations) and the frontend, then waits for the
health check. Verify:

```bash
ubuntu$ docker compose ps                                   # all services "healthy"
ubuntu$ curl -s http://127.0.0.1:8080/api/health | python3 -m json.tool
ubuntu$ curl -s http://127.0.0.1:8080/api/market/BTC-USD/quote | head -c 300; echo
```

## 9. Configure Nginx and your domain

1. **DNS:** create an **A record** for your domain (e.g. `markets.example.com`) pointing to
   `YOUR_EIP`. In Route 53: *Hosted zones → your zone → Create record → A → value
   YOUR_EIP*. Add an AAAA record only if you also set up IPv6.
2. Wait until `mac$ dig +short markets.example.com` prints `YOUR_EIP` (usually minutes).
3. Install the site and get the certificate in one step (continues in step 10):

```bash
ubuntu$ sudo ./deployment/scripts/setup_nginx.sh markets.example.com you@example.com
```

The site file (`deployment/nginx/marketlens.conf`) proxies everything to `127.0.0.1:8080`. It
overwrites `X-Forwarded-For` so clients cannot spoof their IP, rate-limits `/api` to 10
req/s per IP (burst 40), and adds HSTS and other security headers.

## 10. HTTPS with Let's Encrypt

`setup_nginx.sh` runs:

```bash
sudo certbot --nginx -d markets.example.com -m you@example.com --agree-tos --redirect --non-interactive
```

Certbot obtains the certificate, adds the 443 server block, and redirects HTTP to HTTPS. Renewal
is automatic (a systemd timer installed with Certbot). Test it:

```bash
ubuntu$ sudo certbot renew --dry-run
ubuntu$ systemctl list-timers | grep certbot
```

Open `https://markets.example.com`. You should see the dashboard with BTC-USD.

### 10b. Optional: make the site private

```bash
ubuntu$ sudo htpasswd -c /etc/nginx/.marketlens_htpasswd yourname
ubuntu$ sudo nano /etc/nginx/sites-available/marketlens.conf   # uncomment the two auth_basic lines
ubuntu$ sudo nginx -t && sudo systemctl reload nginx
```

## 11. Automatic restarts and application logging

- Every container has `restart: unless-stopped`, so containers restart after crashes and
  when Docker starts.
- Install the systemd unit so the stack is also brought up after reboots, even if containers
  were removed:

```bash
ubuntu$ sudo cp deployment/systemd/marketlens*.service deployment/systemd/marketlens*.timer /etc/systemd/system/
ubuntu$ sudo systemctl daemon-reload
ubuntu$ sudo systemctl enable --now marketlens.service marketlens-backup.timer marketlens-healthcheck.timer
ubuntu$ systemctl list-timers | grep marketlens
```

Logs:

```bash
ubuntu$ docker compose logs -f --tail 100 backend      # JSON lines: request_id, path, status, duration_ms
ubuntu$ docker compose logs --since 1h frontend
ubuntu$ journalctl -t marketlens --since today         # health-check results
ubuntu$ sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log
```

Container logs rotate at 10 MB × 5 files per container (set in `docker-compose.yml` and
`/etc/docker/daemon.json`). Nginx logs rotate through logrotate.

## 12. Updating and redeploying

```bash
ubuntu$ cd ~/marketlens
ubuntu$ ./deployment/scripts/deploy.sh            # backs up DB, git pull, rebuild, restart, health check
ubuntu$ ./deployment/scripts/deploy.sh v1.2.0     # deploy a specific tag/commit
```

Rollback: `./deployment/scripts/deploy.sh <previous-tag-or-commit>`. Migrations are
forward-only; if a release added a migration, restore the pre-deploy backup (step 13) as
well.

Keep the OS current: `unattended-upgrades` installs security patches automatically. Reboot
when `/var/run/reboot-required` exists (`sudo reboot`; the stack starts again by itself).

## 13. Backups, health checks and basic monitoring

**Database backups.** `marketlens-backup.timer` runs `deployment/scripts/backup_db.sh` daily at
03:15 UTC. It writes `~/marketlens/backups/marketlens-<timestamp>.sql.gz` (mode 600) and
keeps 14 days. Only watchlists and preferences live in the database; market data is fetched
again from the providers.

Off-site copies (recommended):

1. Create an S3 bucket (e.g. `marketlens-backups-<random>`), with *Block all public access*
   on and default encryption enabled. Optionally add a lifecycle rule to expire objects after
   30 days.
2. Create an IAM role for EC2 that allows `s3:PutObject` on
   `arn:aws:s3:::marketlens-backups-<random>/marketlens/*`, and attach it to the instance
   (**EC2 → Actions → Security → Modify IAM role**).
3. `ubuntu$ sudo snap install aws-cli --classic`
4. Add `BACKUP_S3_BUCKET=marketlens-backups-<random>` to `.env`.

Restore: `./deployment/scripts/restore_db.sh backups/marketlens-<timestamp>.sql.gz`.

Also consider **EBS snapshots**: *EC2 → Lifecycle Manager → Create policy*, daily, keep 7.

**Health checks.** `marketlens-healthcheck.timer` calls `/api/health` every 5 minutes, logs
the result (`journalctl -t marketlens`), and restarts the containers after 3 consecutive
failures. `/api/health` reports database, cache and each provider's status (`ok`, `degraded`,
`down`, `not_configured`).

**Monitoring.**

- **CloudWatch** (free basic metrics): *EC2 → instance → Monitoring*. Create alarms for
  `CPUUtilization > 80% for 15 min` and `StatusCheckFailed >= 1`, with an SNS email
  notification.
- **External uptime check:** a free service such as UptimeRobot, or a Route 53 health check
  (paid), pointed at `https://markets.example.com/api/health`.
- **Disk:** `df -h` and `docker system df`. Clean old images with `docker image prune -f`
  (deploy.sh does this).
- **Provider credits:** the Data status menu in the app shows Twelve Data credits left today.

## 14. Troubleshooting

### Installation

| Symptom | Fix |
|---|---|
| `permission denied while trying to connect to the Docker daemon socket` | Log out and back in after `bootstrap_ec2.sh` (docker group). Check with `groups`. |
| `Could not get lock /var/lib/dpkg/lock-frontend` | Automatic updates are running. Wait a few minutes and retry. |
| Build killed / `exit code 137` / very slow build | Out of memory. Check `free -h` shows the 2 GB swap, or use a larger instance for the build. |
| `no space left on device` | `docker system prune -af` (removes unused images), check `df -h`, enlarge the EBS volume. |
| Corporate network: `certificate verify failed` during build | Pass your proxy's CA bundle: `docker build --secret id=extra_ca,src=/path/ca.pem backend` (same for `frontend`). Not needed on EC2. |
| `ssh: connect to host ... Operation timed out` | Security group SSH rule doesn't match your current IP (step 3), or the instance is stopped. |
| `WARNING: UNPROTECTED PRIVATE KEY FILE!` | `chmod 400 ~/.ssh/marketlens.pem` on the Mac. |

### Market data / API

| Message in the app or API | Meaning and fix |
|---|---|
| `provider_not_configured` | `TWELVEDATA_API_KEY` is empty. Add it to `.env`, then `docker compose up -d backend`. Crypto still works. |
| `provider_auth_failed` | Twelve Data rejected the key: typo, revoked key, or the `demo` key (it only covers a few sample symbols). |
| `provider_rate_limited` (429) | Plan limits reached (Basic: 8 credits/min, 800/day). The app retries and shows cached data when it has some. Use fewer stock symbols in the watchlist, or upgrade the plan and raise `TWELVEDATA_CREDITS_PER_*`. |
| `provider_plan_restriction` (403) | The symbol (often an index or non-US listing) needs a higher Twelve Data plan. |
| `symbol_not_found` | Check the ticker, or pick the asset type (Crypto pairs use `BASE-QUOTE`, e.g. `SOL-USD`). Non-US listings use `TICKER:EXCHANGE` from the search results. |
| `provider_unavailable` / data shows **Stale** | The provider is down or unreachable. Check `docker compose logs backend` and the provider's status page. Outbound HTTPS from the instance must be allowed (it is by default). |
| `insufficient_data` | The instrument has fewer bars than an analysis needs (e.g. a new listing). Choose a lower timeframe or wait. |
| Times look shifted | Use the chart's time-zone selector (Local, Exchange, UTC). Daily and longer bars are labelled by session date. |

### CORS

The frontend calls `/api` on the **same origin**, so CORS does not apply in the standard setup.
If you serve the UI from another origin, set
`CORS_ORIGINS=https://ui.example.com` in `.env` and run `docker compose up -d backend`.
Requests from origins that aren't listed fail in the browser with
`No 'Access-Control-Allow-Origin' header`.

### Nginx

| Symptom | Fix |
|---|---|
| `502 Bad Gateway` | Containers are not running: `docker compose ps`, `docker compose logs frontend backend`, then `./deployment/scripts/deploy.sh`. |
| `nginx: [emerg] ...` on reload | `sudo nginx -t` shows the file and line. Restore the site with `setup_nginx.sh`. |
| Certbot: `Timeout during connect` / `unauthorized` | DNS doesn't point to `YOUR_EIP` yet, or port 80 is closed in the security group or UFW (`sudo ufw status`). |
| `429 Too Many Requests` for normal use | Increase the `rate=` / `burst=` values in `/etc/nginx/sites-available/marketlens.conf` and `RATE_LIMIT_PER_MINUTE` in `.env`. |
| `413 Request Entity Too Large` | Not expected (the API takes only small JSON bodies). Check for misdirected requests. |

### Permissions

| Symptom | Fix |
|---|---|
| `.env: Permission denied` in scripts | Run scripts as `ubuntu`, not root, and keep `.env` owned by `ubuntu` (`sudo chown ubuntu: .env && chmod 600 .env`). |
| Backup script can't write | `mkdir -p backups && chmod 700 backups` as `ubuntu`. |
| systemd unit fails with `status=217/USER` | The unit uses `User=ubuntu` and `/home/ubuntu/marketlens`. Adjust both if you used another user or path. |

## 15. Costs: estimates vs verified prices

**These are estimates.** AWS prices vary by region and change over time, and taxes are
extra. Confirm with the [AWS Pricing Calculator](https://calculator.aws/) and the
[EC2 pricing page](https://aws.amazon.com/ec2/pricing/on-demand/) before relying on them.

### Hosting (us-east-1, on-demand, approximate monthly)

| Item | Estimate | Basis |
|---|---|---|
| EC2 `t4g.small` (24×7) | ≈ $12.30 | $0.0168/h Linux on-demand; third-party price snapshot (Holori, 2026) consistent with AWS's published table. **Verify** |
| EBS gp3 20 GiB | ≈ $1.60 | ≈ $0.08 per GB-month. **Verify** |
| Public IPv4 / Elastic IP | ≈ $3.65 | AWS charges for every public IPv4 address (≈ $0.005/h). **Verify** |
| Data transfer out | $0 for light use | AWS includes a monthly free allowance of internet egress; this app transfers little. **Verify** |
| S3 backups | < $0.10 | A few MB per month |
| CloudWatch basic metrics + a few alarms | $0 – $0.30 | Basic monitoring is free; alarms have a small per-alarm fee beyond the free tier |
| Route 53 hosted zone (optional) | ≈ $0.50 | Plus queries (negligible); domain registration is extra, typically $10–$20/year for `.com` |
| **Total** | **≈ $18–$20 / month** | Can be lowered with a 1-year Savings Plan / reserved pricing, or `t4g.micro` |

New AWS accounts may receive Free Tier credits that cover part of this. Check **Billing →
Free Tier** in your account, because the program's terms have changed over time.

### Market-data subscriptions

Prices from each provider's public page as of 2026-10-25. Plans change, so confirm on the
provider site.

| Provider / plan | Price | What it adds | Needed for |
|---|---|---|---|
| Coinbase Exchange public data | **Free** (no key) | Real-time crypto trades/candles for Coinbase pairs; 10 req/s per IP | Crypto (works out of the box) |
| Twelve Data **Basic** | **Free** | 8 API credits/min, 800/day; the pricing page lists real-time US equities and ETFs | Personal use with a few stock symbols |
| Twelve Data **Grow** | $79/month (or $66/month billed yearly) | 55+ credits/min, no daily cap, more markets | Several users / larger stock watchlists |
| Twelve Data **Pro** | $229/month (or $191/month billed yearly) | 610+ credits/min, WebSocket streaming, pre/post-market, more markets | Heavier use, extended hours |

After upgrading, set `TWELVEDATA_CREDITS_PER_MINUTE` (and set `TWELVEDATA_CREDITS_PER_DAY` to a
large value or remove the daily cap) in `.env` to match your plan.

**Check the providers' terms before making the site public.** Market-data licences often
restrict redistributing data to third parties. A personal or internal dashboard is usually
fine on free plans. A public website showing exchange data to anonymous visitors may need a
commercial/redistribution licence. Twelve Data has business plans for this. Coinbase data
is subject to Coinbase's API terms.
