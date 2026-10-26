import {
  CONCEALMENT,
  EXTRAS,
  MAX_TVS,
  MOUNTS,
  TV_SIZES,
  WALL_TYPES,
  estimate,
} from './pricing.js';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const money = (n) => `$${n.toLocaleString('en-US')}`;

const GROUPS = {
  tv_size: { table: TV_SIZES, type: 'radio', initial: 'medium' },
  wall_type: { table: WALL_TYPES, type: 'radio', initial: 'drywall' },
  mount: { table: MOUNTS, type: 'radio', initial: 'own' },
  concealment: { table: CONCEALMENT, type: 'radio', initial: 'none' },
  extras: { table: EXTRAS, type: 'checkbox' },
};

/* ---------- Mobile navigation ---------- */
function initNav() {
  const toggle = $('[data-nav-toggle]');
  const nav = $('[data-nav]');
  if (!toggle || !nav) return;
  const setOpen = (open) => {
    toggle.setAttribute('aria-expanded', String(open));
    nav.classList.toggle('is-open', open);
  };
  toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  nav.addEventListener('click', (e) => { if (e.target.closest('a')) setOpen(false); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setOpen(false); });
}

/* ---------- Quote builder ---------- */
function priceHint(key, price) {
  if (key === 'extras') return `+${money(price)}`;
  if (key === 'tv_size') return money(price);
  return price ? `+${money(price)}` : 'Included';
}

function renderOptions(form) {
  for (const [name, { table, type, initial }] of Object.entries(GROUPS)) {
    const fieldset = $(`[data-options="${name}"]`, form);
    if (!fieldset) continue;
    const wrap = document.createElement('div');
    wrap.className = 'options';
    for (const [value, { label, price }] of Object.entries(table)) {
      const option = document.createElement('label');
      option.className = 'option';
      const input = document.createElement('input');
      input.type = type;
      input.name = name;
      input.value = value;
      input.checked = value === initial;
      const body = document.createElement('span');
      body.className = 'option-body';
      body.innerHTML = '<span class="option-label"></span><span class="option-price"></span>';
      body.firstChild.textContent = label;
      body.lastChild.textContent = priceHint(name, price);
      option.append(input, body);
      wrap.append(option);
    }
    fieldset.append(wrap);
  }
}

function readSelection(form) {
  const data = new FormData(form);
  const count = Math.min(MAX_TVS, Math.max(1, Number.parseInt(data.get('tv_count'), 10) || 1));
  return {
    tvSize: data.get('tv_size'),
    wallType: data.get('wall_type'),
    mount: data.get('mount'),
    concealment: data.get('concealment'),
    extras: data.getAll('extras'),
    tvCount: count,
  };
}

function updateSummary(form) {
  const sel = readSelection(form);
  const result = estimate(sel);
  const lines = [
    [`${TV_SIZES[sel.tvSize].label} TV${sel.tvCount > 1 ? ` × ${sel.tvCount}` : ''}`, TV_SIZES[sel.tvSize].price * sel.tvCount],
  ];
  const perTvLine = (table, key, prefix = '') => {
    const { label, price } = table[key];
    if (price) lines.push([prefix + label, price * sel.tvCount]);
  };
  perTvLine(WALL_TYPES, sel.wallType);
  perTvLine(MOUNTS, sel.mount, 'Mount: ');
  perTvLine(CONCEALMENT, sel.concealment);
  for (const key of sel.extras) lines.push([EXTRAS[key].label, EXTRAS[key].price]);

  const dl = $('[data-summary-lines]', form);
  dl.replaceChildren(
    ...lines.map(([label, amount]) => row(label, money(amount))),
    ...(result.discount ? [row('Multi-TV discount', `−${money(result.discount)}`, 'discount')] : []),
  );
  $('[data-summary-total]', form).value = money(result.total);
}

function row(label, value, cls) {
  const div = document.createElement('div');
  if (cls) div.className = cls;
  const dt = document.createElement('dt');
  dt.textContent = label;
  const dd = document.createElement('dd');
  dd.textContent = value;
  div.append(dt, dd);
  return div;
}

function initStepper(form) {
  const input = $('#tv_count', form);
  const clamp = () => {
    const n = Number.parseInt(input.value, 10);
    input.value = String(Math.min(MAX_TVS, Math.max(1, Number.isNaN(n) ? 1 : n)));
  };
  $$('[data-step]', form).forEach((btn) => {
    btn.addEventListener('click', () => {
      input.value = String((Number.parseInt(input.value, 10) || 1) + Number(btn.dataset.step));
      clamp();
      updateSummary(form);
    });
  });
  input.addEventListener('change', () => { clamp(); updateSummary(form); });
}

function setFieldErrors(form, errors = {}) {
  $$('[data-error-for]', form).forEach((el) => {
    const name = el.dataset.errorFor;
    const input = form.elements[name];
    el.textContent = errors[name] || '';
    if (input && 'setAttribute' in input) {
      if (errors[name]) {
        input.setAttribute('aria-invalid', 'true');
        el.id ||= `err-${name}`;
        input.setAttribute('aria-describedby', el.id);
      } else {
        input.removeAttribute('aria-invalid');
        input.removeAttribute('aria-describedby');
      }
    }
  });
}

// Mirrors the server's checks so most mistakes are caught before a round trip.
function validateClient(form) {
  const errors = {};
  const v = (n) => form.elements[n].value.trim();
  if (v('name').length < 2) errors.name = 'Please enter your name.';
  const digits = v('phone').replace(/\D/g, '');
  if (digits.length < 7 || digits.length > 15) errors.phone = 'Please enter a valid phone number.';
  if (v('email') && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v('email'))) errors.email = 'Please enter a valid email address.';
  if (!/^[A-Za-z0-9][A-Za-z0-9 -]{1,8}[A-Za-z0-9]$/.test(v('zip'))) errors.zip = 'Please enter your ZIP / postal code.';
  return errors;
}

function initSubmit(form) {
  const status = $('[data-form-status]', form);
  const submit = $('[data-submit]', form);
  const setStatus = (msg, kind) => {
    status.textContent = msg;
    status.className = `form-status${kind ? ` is-${kind}` : ''}`;
  };

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    setStatus('');
    const clientErrors = validateClient(form);
    setFieldErrors(form, clientErrors);
    if (Object.keys(clientErrors).length) {
      form.elements[Object.keys(clientErrors)[0]].focus();
      setStatus('Please check the highlighted fields.', 'error');
      return;
    }

    const data = new FormData(form);
    const payload = Object.fromEntries(data);
    payload.extras = data.getAll('extras');

    submit.disabled = true;
    submit.textContent = 'Sending…';
    try {
      const res = await fetch('/api/quote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(payload),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        showSuccess(form, body.estimate);
        return;
      }
      if (res.status === 422 && body.fields) {
        setFieldErrors(form, body.fields);
        const first = Object.keys(body.fields)[0];
        form.elements[first]?.focus?.();
      }
      setStatus(body.error || 'Something went wrong. Please call us instead.', 'error');
    } catch {
      setStatus('Network error. Please check your connection or call (555) 010-0199.', 'error');
    } finally {
      submit.disabled = false;
      submit.textContent = 'Request this quote';
    }
  });
}

function showSuccess(form, total) {
  const done = document.createElement('div');
  done.className = 'quote-done';
  done.tabIndex = -1;
  done.innerHTML = `
    <svg class="quote-done-icon" viewBox="0 0 64 64" aria-hidden="true">
      <circle cx="32" cy="32" r="30" fill="#15803d"/>
      <path d="M19 33l9 9 17-19" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
    <h3>Thanks! Your request is in.</h3>
    <p>We'll call or text within one business hour to confirm your appointment.</p>
    <p class="done-total"></p>`;
  if (typeof total === 'number') {
    done.querySelector('.done-total').textContent = `Your estimate: ${money(total)}`;
  }
  form.replaceWith(done);
  done.focus();
}

function initPackages(form) {
  $$('[data-package]').forEach((card) => {
    const cfg = JSON.parse(card.dataset.package);
    const priceEl = $('[data-package-price]', card);
    if (priceEl) priceEl.textContent = money(estimate(cfg).total);
    $('[data-package-select]', card)?.addEventListener('click', () => {
      const set = (name, value) => {
        const input = $(`input[name="${name}"][value="${value}"]`, form);
        if (input) input.checked = true;
      };
      set('tv_size', cfg.tvSize);
      set('wall_type', cfg.wallType);
      set('mount', cfg.mount);
      set('concealment', cfg.concealment);
      $$('input[name="extras"]', form).forEach((cb) => { cb.checked = (cfg.extras || []).includes(cb.value); });
      updateSummary(form);
    });
  });
}

function initQuote() {
  const form = $('[data-quote-form]');
  if (!form) return;
  renderOptions(form);
  initStepper(form);
  initPackages(form);
  initSubmit(form);

  const dateInput = $('#preferred_date', form);
  if (dateInput) {
    const d = new Date();
    dateInput.min = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }
  form.addEventListener('change', (e) => {
    if (e.target.name && e.target.name in GROUPS) updateSummary(form);
  });
  updateSummary(form);
}

function initYear() {
  $$('[data-year]').forEach((el) => { el.textContent = String(new Date().getFullYear()); });
}

initNav();
initQuote();
initYear();
