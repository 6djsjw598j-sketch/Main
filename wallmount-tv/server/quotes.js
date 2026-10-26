import { appendFile, mkdir, readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import {
  CONCEALMENT,
  EXTRAS,
  MAX_TVS,
  MOUNTS,
  TV_SIZES,
  WALL_TYPES,
  estimate,
} from '../public/assets/pricing.js';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const ZIP_RE = /^[A-Za-z0-9][A-Za-z0-9 -]{1,8}[A-Za-z0-9]$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

const str = (v) => (typeof v === 'string' ? v.trim() : '');

/**
 * Validate and normalise a quote request.
 * Accepts the shape produced by both the JSON API and a plain HTML form post.
 * @returns {{ ok: true, value: object } | { ok: false, errors: Record<string, string> }}
 */
export function validateQuote(input, { today = new Date() } = {}) {
  const errors = {};
  const name = str(input.name);
  const phone = str(input.phone);
  const email = str(input.email).toLowerCase();
  const zip = str(input.zip);
  const notes = str(input.notes);
  const preferredDate = str(input.preferred_date);
  const tvSize = str(input.tv_size) || 'medium';
  const wallType = str(input.wall_type) || 'unsure';
  const mount = str(input.mount) || 'own';
  const concealment = str(input.concealment) || 'none';
  const rawExtras = Array.isArray(input.extras) ? input.extras : input.extras ? [input.extras] : [];
  const extras = [...new Set(rawExtras.map(str).filter(Boolean))];
  const tvCount = Number.parseInt(input.tv_count ?? '1', 10);

  if (name.length < 2 || name.length > 100) errors.name = 'Please enter your name.';
  const digits = phone.replace(/\D/g, '');
  if (digits.length < 7 || digits.length > 15 || phone.length > 25) {
    errors.phone = 'Please enter a valid phone number.';
  }
  if (email && (email.length > 254 || !EMAIL_RE.test(email))) errors.email = 'Please enter a valid email address.';
  if (!ZIP_RE.test(zip)) errors.zip = 'Please enter your ZIP / postal code.';
  if (notes.length > 2000) errors.notes = 'Please keep notes under 2000 characters.';

  if (!Object.hasOwn(TV_SIZES, tvSize)) errors.tv_size = 'Choose a TV size.';
  if (!Object.hasOwn(WALL_TYPES, wallType)) errors.wall_type = 'Choose a wall type.';
  if (!Object.hasOwn(MOUNTS, mount)) errors.mount = 'Choose a mount option.';
  if (!Object.hasOwn(CONCEALMENT, concealment)) errors.concealment = 'Choose a cable option.';
  if (extras.some((e) => !Object.hasOwn(EXTRAS, e))) errors.extras = 'Unknown add-on selected.';
  if (!Number.isInteger(tvCount) || tvCount < 1 || tvCount > MAX_TVS) {
    errors.tv_count = `Number of TVs must be between 1 and ${MAX_TVS}.`;
  }

  if (preferredDate) {
    const d = new Date(`${preferredDate}T00:00:00Z`);
    const todayUtc = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
    // Allow "yesterday" to absorb time-zone differences between client and server.
    if (!DATE_RE.test(preferredDate) || Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== preferredDate) {
      errors.preferred_date = 'Please choose a valid date.';
    } else if (d < new Date(todayUtc.getTime() - 86_400_000)) {
      errors.preferred_date = 'Please choose a date in the future.';
    }
  }

  if (Object.keys(errors).length) return { ok: false, errors };

  return {
    ok: true,
    value: {
      name,
      phone,
      email: email || null,
      zip,
      tv_size: tvSize,
      wall_type: wallType,
      mount,
      concealment,
      extras,
      tv_count: tvCount,
      preferred_date: preferredDate || null,
      notes: notes || null,
      estimate: estimate({ tvSize, wallType, mount, concealment, extras, tvCount }).total,
    },
  };
}

/** Append-only JSON Lines store. One file, safe for a single Node process. */
export class QuoteStore {
  constructor(dataDir) {
    this.file = path.join(dataDir, 'quotes.jsonl');
    this.ready = mkdir(dataDir, { recursive: true });
  }

  async add(quote, meta = {}) {
    await this.ready;
    const record = { id: randomUUID(), created_at: new Date().toISOString(), ...quote, ...meta };
    await appendFile(this.file, `${JSON.stringify(record)}\n`, { mode: 0o600 });
    return record;
  }

  async list() {
    await this.ready;
    let text;
    try {
      text = await readFile(this.file, 'utf8');
    } catch (err) {
      if (err.code === 'ENOENT') return [];
      throw err;
    }
    return text
      .split('\n')
      .filter(Boolean)
      .flatMap((line) => {
        try {
          return [JSON.parse(line)];
        } catch {
          return []; // skip a torn final line after a crash
        }
      });
  }
}

const CSV_COLUMNS = [
  'id', 'created_at', 'name', 'phone', 'email', 'zip', 'tv_size', 'tv_count', 'wall_type',
  'mount', 'concealment', 'extras', 'preferred_date', 'estimate', 'notes',
];

export function toCsv(records) {
  const cell = (v) => {
    let s = Array.isArray(v) ? v.join(';') : v == null ? '' : String(v);
    // Neutralise spreadsheet formula injection.
    if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [CSV_COLUMNS.join(','), ...records.map((r) => CSV_COLUMNS.map((c) => cell(r[c])).join(','))].join('\r\n');
}
