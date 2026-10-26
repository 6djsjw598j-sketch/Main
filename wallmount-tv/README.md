# Level Mount Co. — TV wall-mounting website

A fast, self-contained marketing site for a TV wall-mounting business, with a live price
estimator and a quote-request form that saves leads on the server. Built to run on a single
small AWS EC2 instance behind Nginx with HTTPS.

"Level Mount Co.", the phone number `(555) 010-0199` and `example.com` are placeholders.
See [Before you launch](#before-you-launch).

## What's included

| | |
|---|---|
| **Pages** | Home (hero, services, mount types, how it works, packages, quote builder, reviews, FAQ, contact), thanks, privacy policy, 404 |
| **Quote builder** | TV size, number of TVs, wall type, mount, cable concealment and add-ons with a live price summary. Prices live in one file and the server re-prices every submission, so the browser can't send a fake total |
| **Lead capture** | `POST /api/quote` validates input, rate-limits per IP, drops bot submissions (honeypot field) and appends to a JSON Lines file. Works without JavaScript too (plain form post → `/thanks`) |
| **Lead export** | `GET /api/quotes` (JSON) or `?format=csv` for a spreadsheet, protected by a bearer token |
| **Design** | Responsive from 320px up, sticky price bar on mobile, keyboard and screen-reader friendly, respects reduced-motion. No web fonts, trackers or cookies |
| **Security** | Strict Content-Security-Policy, security headers, path-traversal-safe static serving, 16 KB body limit, CSV formula-injection protection, hardened systemd unit, Nginx rate limits, HSTS |
| **Dependencies** | None. Plain Node.js 20+ and static HTML/CSS/JS: no `npm install`, no build step |

## Run it locally

```bash
cd wallmount-tv
npm start          # http://127.0.0.1:3000
npm run dev        # same, restarts on file changes
npm test           # 18 unit + HTTP tests (node:test)
```

Submissions go to `./data/quotes.jsonl`. To try the admin export:

```bash
ADMIN_TOKEN=dev npm start
curl -H 'Authorization: Bearer dev' http://127.0.0.1:3000/api/quotes
curl -H 'Authorization: Bearer dev' 'http://127.0.0.1:3000/api/quotes?format=csv' -o quotes.csv
```

## Before you launch

1. **Business details.** Replace the name, phone (`tel:+15550100199`, `sms:`), email, hours and
   service area in `public/index.html`, and the name in `thanks.html`, `privacy.html` and `404.html`.
2. **Domain.** Replace `https://www.example.com` in the canonical link (`index.html`),
   `robots.txt` and `sitemap.xml`.
3. **Prices.** Edit `public/assets/pricing.js`. The estimator, package prices and server all read it.
4. **Reviews.** The three testimonials are **samples**. Replace them with real customer reviews
   (with permission) or remove the section. Publishing invented reviews is deceptive and illegal
   in many places.
5. **Claims.** Check that "licensed & insured", the workmanship guarantee, "within one business
   hour" and the FAQ answers match how you actually operate.
6. **Privacy policy.** `privacy.html` is a starting template; have it reviewed.
7. **Photos (recommended).** Real install photos sell this service. Add them under
   `public/assets/` (WebP/AVIF, ≤ 200 KB each) and reference them with `<img>`; the CSP already allows same-origin images.

## Deploy to AWS EC2

This sets up: Node.js 22 → systemd service on `127.0.0.1:3000` → Nginx (gzip, rate limits)
→ Let's Encrypt HTTPS. A `t4g.micro` or `t3.micro` is plenty.

### 1. Launch the instance

In the EC2 console → **Launch instance**:

- **AMI:** Ubuntu Server 24.04 LTS (x86_64 or Arm64)
- **Type:** `t4g.micro` (Arm, cheapest) or `t3.micro`
- **Key pair:** create or choose one for SSH
- **Security group inbound rules:**
  - SSH (22) from **My IP** only
  - HTTP (80) from anywhere (needed for the certificate check and the redirect)
  - HTTPS (443) from anywhere
- **Storage:** 8–10 GB gp3

Then allocate an **Elastic IP** (EC2 → Elastic IPs → Allocate → Associate with the instance) so the
address survives reboots.

### 2. Point your domain at it

At your DNS provider (or Route 53), create an `A` record for `example.com` and another for
`www.example.com`, both pointing at the Elastic IP. Wait until `dig +short example.com` returns it.

### 3. Install

```bash
ssh -i your-key.pem ubuntu@<elastic-ip>
git clone <this-repo-url> site && cd site
sudo ./wallmount-tv/deploy/bootstrap-ec2.sh example.com www.example.com --email you@example.com
```

The script installs packages, creates a `levelmount` system user, writes `/etc/levelmount.env`
with a random `ADMIN_TOKEN`, installs and starts the systemd service, configures Nginx and UFW,
and obtains the HTTPS certificate (auto-renewed by Certbot's timer). Without `--email` it stops at
HTTP; run `sudo certbot --nginx -d example.com -d www.example.com` once DNS is ready.

### 4. Update after changes

```bash
cd ~/site && git pull && sudo ./wallmount-tv/deploy/deploy.sh
```

`deploy.sh` copies the app to `/opt/levelmount/app`, restarts the service and fails loudly (with
logs) if the health check at `/healthz` doesn't pass.

### Operating it

| Task | Command |
|---|---|
| Download leads as CSV | `curl -H "Authorization: Bearer $TOKEN" https://example.com/api/quotes?format=csv -o quotes.csv` |
| Find the admin token | `sudo grep ADMIN_TOKEN /etc/levelmount.env` |
| Service status / logs | `systemctl status levelmount` · `journalctl -u levelmount -f` |
| Lead file | `/var/lib/levelmount/quotes.jsonl` |
| Change settings | edit `/etc/levelmount.env`, then `sudo systemctl restart levelmount` |
| Back up leads | `aws s3 cp /var/lib/levelmount/quotes.jsonl s3://your-bucket/quotes-$(date +%F).jsonl` (needs an instance role with S3 write) or enable EBS snapshots via Amazon Data Lifecycle Manager |

Leads are stored on the instance's disk. Take regular backups (above) and check them. If you want
an email for every new lead, the place to add it is `handleQuote` in `server/app.js`, after
`store.add` (e.g. Amazon SES via an instance role).

### Docker alternative

If you prefer containers (on EC2 or elsewhere):

```bash
cd wallmount-tv
cp .env.example .env            # set ADMIN_TOKEN
docker compose up -d --build    # listens on 127.0.0.1:3000; leads in the "quotes" volume
```

Put the same Nginx config (`deploy/nginx.conf` + `deploy/proxy.conf`) in front for TLS.

## Configuration

Environment variables (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `3000` | Listen port |
| `HOST` | `127.0.0.1` | Listen address (`0.0.0.0` in Docker) |
| `DATA_DIR` | `./data` | Where `quotes.jsonl` is written |
| `ADMIN_TOKEN` | empty | Bearer token for `GET /api/quotes`; empty disables it |
| `TRUST_PROXY` | off | `1` behind Nginx: use the last `X-Forwarded-For` hop as the client IP |
| `QUOTE_RATE_LIMIT` | `5` | Quote submissions per IP per 10 minutes |

## API

| Method & path | Description |
|---|---|
| `POST /api/quote` | JSON or form-encoded. Required: `name`, `phone`, `zip`. Optional: `email`, `tv_size`, `tv_count`, `wall_type`, `mount`, `concealment`, `extras[]`, `preferred_date` (YYYY-MM-DD), `notes`. Returns `201 {ok, estimate}`, `422 {error, fields}` or `429` |
| `GET /api/quotes` | `Authorization: Bearer <ADMIN_TOKEN>`. JSON list, or CSV with `?format=csv` |
| `GET /healthz` | `{"status":"ok"}` |

## Project structure

```
wallmount-tv/
├── public/                 static site (served as-is)
│   ├── index.html          home page
│   ├── thanks.html, privacy.html, 404.html
│   ├── assets/
│   │   ├── styles.css
│   │   ├── app.js          nav, quote builder, form submit
│   │   └── pricing.js      prices + estimate(): shared by browser and server
│   ├── favicon.svg, robots.txt, sitemap.xml
├── server/
│   ├── index.js            entry point: env config, graceful shutdown
│   ├── app.js              HTTP routing, static files, security headers, rate limit
│   └── quotes.js           validation, JSONL store, CSV export
├── test/server.test.js
├── deploy/
│   ├── bootstrap-ec2.sh    one-time EC2 setup
│   ├── deploy.sh           update + restart + health check
│   ├── levelmount.service  hardened systemd unit
│   ├── nginx.conf, proxy.conf
├── Dockerfile, docker-compose.yml, .env.example
```
