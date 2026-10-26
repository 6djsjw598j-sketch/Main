import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { timingSafeEqual, createHash } from 'node:crypto';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { QuoteStore, toCsv, validateQuote } from './quotes.js';

const PUBLIC_DIR = fileURLToPath(new URL('../public/', import.meta.url));
const MAX_BODY_BYTES = 16 * 1024;
const RATE_WINDOW_MS = 10 * 60 * 1000;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
  '.xml': 'application/xml; charset=utf-8',
  '.webmanifest': 'application/manifest+json',
};

const SECURITY_HEADERS = {
  'Content-Security-Policy':
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; " +
    "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), interest-cohort=()',
  'Cross-Origin-Opener-Policy': 'same-origin',
};

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/** Fixed-window per-key counter, pruned as it goes. */
export class RateLimiter {
  constructor(limit, windowMs = RATE_WINDOW_MS, now = Date.now) {
    this.limit = limit;
    this.windowMs = windowMs;
    this.now = now;
    this.hits = new Map();
  }

  allow(key) {
    const t = this.now();
    if (this.hits.size > 10_000) {
      for (const [k, v] of this.hits) if (t - v.start >= this.windowMs) this.hits.delete(k);
    }
    const entry = this.hits.get(key);
    if (!entry || t - entry.start >= this.windowMs) {
      this.hits.set(key, { start: t, count: 1 });
      return true;
    }
    entry.count += 1;
    return entry.count <= this.limit;
  }
}

function send(res, status, body, headers = {}) {
  const payload = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': typeof body === 'string' ? 'text/plain; charset=utf-8' : 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'Content-Length': Buffer.byteLength(payload),
    ...headers,
  });
  res.end(payload);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const declared = Number(req.headers['content-length'] || 0);
    if (declared > MAX_BODY_BYTES) return reject(new HttpError(413, 'Request too large'));
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new HttpError(413, 'Request too large'));
        req.destroy();
      } else {
        chunks.push(chunk);
      }
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function parseForm(text) {
  const out = {};
  for (const [key, value] of new URLSearchParams(text)) {
    if (key in out) out[key] = [].concat(out[key], value);
    else out[key] = value;
  }
  return out;
}

function clientIp(req, trustProxy) {
  if (trustProxy) {
    const fwd = req.headers['x-forwarded-for'];
    // Nginx appends the real peer as the last hop; earlier hops are client-controlled.
    if (fwd) return fwd.split(',').at(-1).trim();
  }
  return req.socket.remoteAddress || 'unknown';
}

function tokenMatches(header, expected) {
  if (!expected || !header?.startsWith('Bearer ')) return false;
  const digest = (s) => createHash('sha256').update(s).digest();
  return timingSafeEqual(digest(header.slice(7)), digest(expected));
}

async function serveStatic(req, res, pathname) {
  let rel;
  try {
    rel = decodeURIComponent(pathname);
  } catch {
    throw new HttpError(400, 'Bad request');
  }
  if (rel.includes('\0')) throw new HttpError(400, 'Bad request');
  if (rel.endsWith('/')) rel += 'index.html';

  let file = path.join(PUBLIC_DIR, path.normalize(rel));
  if (!file.startsWith(PUBLIC_DIR)) throw new HttpError(404, 'Not found');

  let info = await stat(file).catch(() => null);
  // Extensionless URLs: /privacy -> privacy.html
  if (!info && !path.extname(file)) {
    file += '.html';
    info = await stat(file).catch(() => null);
  }
  if (info?.isDirectory()) {
    return send(res, 301, '', { Location: `${pathname}/` });
  }

  let status = 200;
  if (!info?.isFile()) {
    status = 404;
    file = path.join(PUBLIC_DIR, '404.html');
    info = await stat(file);
  }

  const ext = path.extname(file);
  const etag = `W/"${info.size.toString(16)}-${Math.floor(info.mtimeMs).toString(16)}"`;
  const headers = {
    'Content-Type': MIME[ext] || 'application/octet-stream',
    'Cache-Control': ext === '.html' || status !== 200 ? 'no-cache' : 'public, max-age=3600',
    ETag: etag,
    'Last-Modified': info.mtime.toUTCString(),
  };
  if (status === 200 && req.headers['if-none-match'] === etag) {
    res.writeHead(304, headers);
    return res.end();
  }
  res.writeHead(status, { ...headers, 'Content-Length': info.size });
  if (req.method === 'HEAD') return res.end();
  createReadStream(file).pipe(res);
}

export function createApp(config) {
  const store = new QuoteStore(config.dataDir);
  const limiter = new RateLimiter(config.quoteRateLimit);
  const log = config.logger ?? ((entry) => console.log(JSON.stringify({ time: new Date().toISOString(), ...entry })));

  async function handleQuote(req, res) {
    const type = (req.headers['content-type'] || '').split(';')[0].trim().toLowerCase();
    const isForm = type === 'application/x-www-form-urlencoded';
    if (!isForm && type !== 'application/json') throw new HttpError(415, 'Unsupported content type');

    const ip = clientIp(req, config.trustProxy);
    if (!limiter.allow(ip)) {
      return send(res, 429, { error: 'Too many requests. Please call us instead.' }, { 'Retry-After': '600' });
    }

    const text = await readBody(req);
    let input;
    try {
      input = isForm ? parseForm(text) : JSON.parse(text);
    } catch {
      throw new HttpError(400, 'Invalid JSON');
    }
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new HttpError(400, 'Invalid body');

    // Honeypot: real visitors never see or fill this field. Pretend success for bots.
    const isBot = typeof input.website === 'string' && input.website.trim() !== '';
    const result = validateQuote(input);

    if (!result.ok) {
      if (isForm) return send(res, 303, '', { Location: '/#quote-error' });
      return send(res, 422, { error: 'Please check the highlighted fields.', fields: result.errors });
    }
    if (!isBot) {
      const record = await store.add(result.value, { ip, user_agent: String(req.headers['user-agent'] || '').slice(0, 200) });
      log({ level: 'info', event: 'quote_received', id: record.id, zip: record.zip, estimate: record.estimate });
    }
    if (isForm) return send(res, 303, '', { Location: '/thanks' });
    return send(res, 201, { ok: true, estimate: result.value.estimate });
  }

  async function handleListQuotes(req, res, url) {
    if (!tokenMatches(req.headers.authorization, config.adminToken)) {
      return send(res, 401, { error: 'Unauthorized' }, { 'WWW-Authenticate': 'Bearer' });
    }
    const records = await store.list();
    if (url.searchParams.get('format') === 'csv') {
      return send(res, 200, toCsv(records), {
        'Content-Type': 'text/csv; charset=utf-8',
        'Content-Disposition': 'attachment; filename="quotes.csv"',
      });
    }
    return send(res, 200, { count: records.length, quotes: records });
  }

  async function route(req, res) {
    for (const [k, v] of Object.entries(SECURITY_HEADERS)) res.setHeader(k, v);
    const url = new URL(req.url, 'http://localhost');
    const { pathname } = url;

    if (pathname === '/healthz') return send(res, 200, { status: 'ok' });
    if (pathname === '/api/quote') {
      if (req.method !== 'POST') return send(res, 405, { error: 'Method not allowed' }, { Allow: 'POST' });
      return handleQuote(req, res);
    }
    if (pathname === '/api/quotes') {
      if (req.method !== 'GET') return send(res, 405, { error: 'Method not allowed' }, { Allow: 'GET' });
      return handleListQuotes(req, res, url);
    }
    if (pathname.startsWith('/api/')) return send(res, 404, { error: 'Not found' });
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      return send(res, 405, 'Method not allowed', { Allow: 'GET, HEAD' });
    }
    return serveStatic(req, res, pathname);
  }

  return http.createServer((req, res) => {
    route(req, res).catch((err) => {
      const status = err instanceof HttpError ? err.status : 500;
      if (status === 500) log({ level: 'error', event: 'unhandled', message: err.message, stack: err.stack });
      if (res.headersSent) return res.destroy();
      const message = status === 500 ? 'Internal server error' : err.message;
      send(res, status, req.url.startsWith('/api/') ? { error: message } : message);
    });
  });
}
