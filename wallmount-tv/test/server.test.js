import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createApp, RateLimiter } from '../server/app.js';
import { toCsv, validateQuote } from '../server/quotes.js';
import { estimate } from '../public/assets/pricing.js';

const VALID = { name: 'Sam Rivera', phone: '(555) 010-0123', zip: '90210', tv_size: 'large', wall_type: 'drywall', mount: 'tilting', concealment: 'in_wall' };

describe('estimate', () => {
  test('sums per-TV options, extras and multi-TV discount', () => {
    const r = estimate({ tvSize: 'medium', wallType: 'masonry', mount: 'fixed', concealment: 'cover', extras: ['soundbar', 'soundbar'], tvCount: 2 });
    assert.equal(r.perTv, 159 + 60 + 39 + 39);
    assert.equal(r.discount, Math.round(r.perTv * 0.1));
    assert.equal(r.extras, 49); // duplicates counted once
    assert.equal(r.total, r.perTv * 2 - r.discount + 49);
  });

  test('rejects unknown options and prototype keys', () => {
    assert.throws(() => estimate({ tvSize: 'giant', wallType: 'drywall', mount: 'own', concealment: 'none' }), RangeError);
    assert.throws(() => estimate({ tvSize: 'toString', wallType: 'drywall', mount: 'own', concealment: 'none' }), RangeError);
  });
});

describe('validateQuote', () => {
  test('accepts a valid request and prices it server-side', () => {
    const r = validateQuote({ ...VALID, estimate: 1 });
    assert.equal(r.ok, true);
    assert.equal(r.value.estimate, 199 + 0 + 59 + 129);
    assert.equal(r.value.email, null);
  });

  test('applies defaults for a bare no-JS form post', () => {
    const r = validateQuote({ name: 'Al', phone: '5550100123', zip: 'SW1A 1AA' });
    assert.equal(r.ok, true);
    assert.equal(r.value.tv_size, 'medium');
    assert.equal(r.value.tv_count, 1);
  });

  test('reports field errors', () => {
    const r = validateQuote({ name: '', phone: '12', zip: '!', email: 'nope', tv_count: '99', extras: ['laser'], tv_size: '__proto__' });
    assert.equal(r.ok, false);
    for (const f of ['name', 'phone', 'zip', 'email', 'tv_count', 'extras', 'tv_size']) assert.ok(r.errors[f], f);
  });

  test('validates preferred date', () => {
    const today = new Date('2026-06-15T12:00:00Z');
    assert.equal(validateQuote({ ...VALID, preferred_date: '2026-06-20' }, { today }).ok, true);
    assert.equal(validateQuote({ ...VALID, preferred_date: '2026-06-14' }, { today }).ok, true);
    assert.ok(validateQuote({ ...VALID, preferred_date: '2026-06-01' }, { today }).errors.preferred_date);
    assert.ok(validateQuote({ ...VALID, preferred_date: '2026-02-30' }, { today }).errors.preferred_date);
  });
});

test('toCsv escapes quotes and neutralises formulas', () => {
  const csv = toCsv([{ id: '1', name: '=HYPERLINK("x")', notes: 'a,b', extras: ['soundbar', 'setup'] }]);
  const row = csv.split('\r\n')[1];
  assert.match(row, /"'=HYPERLINK\(""x""\)"/);
  assert.match(row, /"a,b"/);
  assert.match(row, /soundbar;setup/);
});

test('RateLimiter resets after the window', () => {
  let now = 0;
  const rl = new RateLimiter(2, 1000, () => now);
  assert.equal(rl.allow('a'), true);
  assert.equal(rl.allow('a'), true);
  assert.equal(rl.allow('a'), false);
  assert.equal(rl.allow('b'), true);
  now = 1000;
  assert.equal(rl.allow('a'), true);
});

describe('HTTP server', () => {
  let server;
  let base;
  let dataDir;
  const TOKEN = 'test-token';

  before(async () => {
    dataDir = await mkdtemp(path.join(tmpdir(), 'quotes-'));
    server = createApp({ dataDir, adminToken: TOKEN, trustProxy: true, quoteRateLimit: 3, logger: () => {} });
    await new Promise((r) => server.listen(0, '127.0.0.1', r));
    base = `http://127.0.0.1:${server.address().port}`;
  });

  after(async () => {
    await new Promise((r) => server.close(r));
    await rm(dataDir, { recursive: true, force: true });
  });

  const postJson = (body, ip = '10.0.0.1') =>
    fetch(`${base}/api/quote`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Forwarded-For': ip },
      body: JSON.stringify(body),
    });

  test('serves the home page with security headers', async () => {
    const res = await fetch(`${base}/`);
    assert.equal(res.status, 200);
    assert.match(res.headers.get('content-type'), /text\/html/);
    assert.match(res.headers.get('content-security-policy'), /default-src 'self'/);
    assert.equal(res.headers.get('x-content-type-options'), 'nosniff');
    assert.match(await res.text(), /Level Mount/);
  });

  test('serves assets, extensionless pages, ETags and a 404 page', async () => {
    const css = await fetch(`${base}/assets/styles.css`);
    assert.equal(css.status, 200);
    assert.match(css.headers.get('content-type'), /text\/css/);
    const again = await fetch(`${base}/assets/styles.css`, { headers: { 'If-None-Match': css.headers.get('etag') } });
    assert.equal(again.status, 304);

    assert.equal((await fetch(`${base}/privacy`)).status, 200);
    const missing = await fetch(`${base}/nope`);
    assert.equal(missing.status, 404);
    assert.match(await missing.text(), /off-level/);
  });

  test('blocks path traversal', async () => {
    for (const p of ['/..%2fserver%2fapp.js', '/assets/..%2f..%2fpackage.json', '/%00']) {
      const res = await fetch(base + p);
      assert.ok([400, 404].includes(res.status), `${p} -> ${res.status}`);
      assert.doesNotMatch(await res.text(), /createApp|"name": "level-mount-site"/);
    }
  });

  test('healthz', async () => {
    const res = await fetch(`${base}/healthz`);
    assert.deepEqual(await res.json(), { status: 'ok' });
  });

  test('accepts a JSON quote and lists it for admins', async () => {
    const res = await postJson({ ...VALID, notes: 'Second floor' }, '10.0.0.2');
    assert.equal(res.status, 201);
    assert.deepEqual(await res.json(), { ok: true, estimate: 387 });

    assert.equal((await fetch(`${base}/api/quotes`)).status, 401);
    assert.equal((await fetch(`${base}/api/quotes`, { headers: { Authorization: 'Bearer wrong' } })).status, 401);

    const list = await fetch(`${base}/api/quotes`, { headers: { Authorization: `Bearer ${TOKEN}` } });
    const body = await list.json();
    const saved = body.quotes.find((q) => q.notes === 'Second floor');
    assert.ok(saved);
    assert.equal(saved.ip, '10.0.0.2');

    const csv = await fetch(`${base}/api/quotes?format=csv`, { headers: { Authorization: `Bearer ${TOKEN}` } });
    assert.match(csv.headers.get('content-type'), /text\/csv/);
    assert.match(await csv.text(), /Second floor/);
  });

  test('returns field errors as 422', async () => {
    const res = await postJson({ name: 'x' }, '10.0.0.3');
    assert.equal(res.status, 422);
    const body = await res.json();
    assert.ok(body.fields.phone);
  });

  test('handles a plain form post with redirect', async () => {
    const form = new URLSearchParams({ ...VALID, extras: 'soundbar' });
    form.append('extras', 'setup');
    const res = await fetch(`${base}/api/quote`, {
      method: 'POST', body: form, redirect: 'manual', headers: { 'X-Forwarded-For': '10.0.0.4' },
    });
    assert.equal(res.status, 303);
    assert.equal(res.headers.get('location'), '/thanks');

    const bad = await fetch(`${base}/api/quote`, {
      method: 'POST', body: new URLSearchParams({ name: 'x' }), redirect: 'manual', headers: { 'X-Forwarded-For': '10.0.0.4' },
    });
    assert.equal(bad.headers.get('location'), '/#quote-error');
  });

  test('silently drops honeypot submissions', async () => {
    const res = await postJson({ ...VALID, website: 'http://spam', notes: 'bot-note' }, '10.0.0.5');
    assert.equal(res.status, 201);
    const list = await (await fetch(`${base}/api/quotes`, { headers: { Authorization: `Bearer ${TOKEN}` } })).json();
    assert.ok(!list.quotes.some((q) => q.notes === 'bot-note'));
  });

  test('rejects bad requests', async () => {
    assert.equal((await fetch(`${base}/api/quote`)).status, 405);
    const text = await fetch(`${base}/api/quote`, { method: 'POST', body: 'hi', headers: { 'Content-Type': 'text/plain' } });
    assert.equal(text.status, 415);
    const broken = await fetch(`${base}/api/quote`, { method: 'POST', body: '{', headers: { 'Content-Type': 'application/json', 'X-Forwarded-For': '10.0.0.6' } });
    assert.equal(broken.status, 400);
    const big = await postJson({ ...VALID, notes: 'x'.repeat(20_000) }, '10.0.0.7');
    assert.equal(big.status, 413);
  });

  test('rate limits per client IP', async () => {
    const codes = [];
    for (let i = 0; i < 4; i++) codes.push((await postJson(VALID, '10.9.9.9')).status);
    assert.deepEqual(codes, [201, 201, 201, 429]);
  });
});
