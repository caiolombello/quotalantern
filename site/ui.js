'use strict';
/* QuotaLantern site: language, the annotated ring (Fig. 1), the Overview demo
   (Fig. 2) and the copy button. Nothing here talks to a provider or a server.
   Strings inside the app mock stay in English, like the real GTK interface. */

const STORE_KEY = 'quotalantern-lang';
let lang = 'pt-BR';
const t = (pt, en) => (lang === 'en' ? en : pt);

/* ── Ring geometry: identical to codexbar_linux/icons.py ──────────────── */
const R = 13;
const CIRC = 2 * Math.PI * R;
const FACHO_PATHS = '<path d="M9 3H15V6H18L23 11H1L6 6H9Z"/><path d="M3 13H7L10 26H18L21 13H25L21 30H7Z"/><path d="M12 14L30 12V20L12 18Z"/>';
const TONE = { ok: 'ok', warning: 'warn', critical: 'crit' };

function ringSVG(pct, state, { center = 'facho', palette = 'app' } = {}) {
  const v = name => `var(--${palette === 'app' ? 'app-' : ''}${name})`;
  const known = state in TONE;
  const color = v(TONE[state] || 'neutral');
  let svg = `<svg viewBox="0 0 32 32" aria-hidden="true" focusable="false"><circle cx="16" cy="16" r="13" fill="none" stroke="${color}" stroke-width="3" opacity=".25"/>`;
  if (known) {
    svg += `<circle cx="16" cy="16" r="13" fill="none" stroke="${color}" stroke-width="3" stroke-dasharray="${(CIRC * pct / 100).toFixed(3)} ${CIRC.toFixed(3)}" transform="rotate(-90 16 16)"/>`;
    if (center === 'facho') svg += `<g transform="translate(7.68 7.68) scale(.52)" fill="${v('ink')}">${FACHO_PATHS}</g>`;
  } else {
    svg += `<circle cx="16" cy="16" r="13" fill="none" stroke="${color}" stroke-width="3" stroke-dasharray="${state === 'unknown' ? '2 3' : '7 3'}"/>`;
    svg += `<path d="${state === 'unknown' ? 'M11 16H21' : 'M16 10V16L20 19'}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round"/>`;
  }
  return svg + '</svg>';
}

/* ── Fig. 1: the tray icon, enlarged and annotated ───────────────────── */
const CALLOUTS = {
  known: pct => [
    t(`<b>Arco.</b> ${pct}% da sessão de 5 h do Codex já foi usado.`, `<b>Arc.</b> ${pct}% of Codex’s 5-hour session is used.`),
    pct >= 95 ? t('<b>Cor.</b> Vermelho: passou do nível crítico, 95% por padrão.', '<b>Color.</b> Red: past the critical level, 95% by default.')
      : pct >= 85 ? t('<b>Cor.</b> Âmbar: passou do aviso de 85%. Vira vermelho em 95%.', '<b>Color.</b> Amber: past the 85% warning. Turns red at 95%.')
        : t('<b>Cor.</b> Verde: abaixo do aviso de 85%. Os dois níveis são ajustáveis.', '<b>Color.</b> Green: below the 85% warning. Both levels are adjustable.'),
    t('<b>Centro.</b> O Facho, marca do app. Só aparece com leitura válida.', '<b>Center.</b> The Facho, the app’s mark. It only shows with a valid reading.'),
  ],
  unknown: () => [
    t('<b>Arco.</b> Nenhum. Sem leitura válida não há número, e o app não inventa 0%.', '<b>Arc.</b> None. Without a valid reading there is no number, and the app won’t invent 0%.'),
    t('<b>Trilha.</b> Pontilhada e cinza.', '<b>Track.</b> Dotted and grey.'),
    t('<b>Centro.</b> Um traço no lugar do Facho.', '<b>Center.</b> A dash instead of the Facho.'),
  ],
  stale: () => [
    t('<b>Arco.</b> Nenhum. A última leitura tem mais de 10 minutos e não passa por atual.', '<b>Arc.</b> None. The last reading is over 10 minutes old and isn’t passed off as current.'),
    t('<b>Trilha.</b> Tracejada e cinza.', '<b>Track.</b> Dashed and grey.'),
    t('<b>Centro.</b> Um relógio.', '<b>Center.</b> A clock.'),
  ],
};

function onRing(angleDeg, radiusPct) {
  const a = angleDeg * Math.PI / 180;
  return { left: `${50 + radiusPct * Math.sin(a)}%`, top: `${50 - radiusPct * Math.cos(a)}%` };
}

function setupRingFigure() {
  const fig = document.querySelector('[data-ring-figure]');
  if (!fig) return;
  const stage = fig.querySelector('[data-ring-stage]');
  const range = fig.querySelector('input[type="range"]');
  const out = fig.querySelector('output');
  const items = fig.querySelectorAll('[data-callout]');
  const pins = fig.querySelectorAll('.pin');
  const ring = (13 / 32) * 100;

  const draw = () => {
    const mode = fig.querySelector('input[name="ring-mode"]:checked').value;
    const pct = Number(range.value);
    const state = mode === 'known' ? (pct >= 95 ? 'critical' : pct >= 85 ? 'warning' : 'ok') : mode;
    out.textContent = mode === 'known' ? `${pct}%` : '—';
    range.disabled = mode !== 'known';
    stage.querySelector('svg')?.remove();
    stage.insertAdjacentHTML('afterbegin', ringSVG(pct, state, { palette: 'site' }));
    Object.assign(pins[0].style, onRing(mode === 'known' ? pct * 3.6 : 0, ring));
    Object.assign(pins[1].style, onRing(225, 50));
    Object.assign(pins[2].style, { left: '70%', top: '31%' });
    const texts = CALLOUTS[mode === 'known' ? 'known' : mode](pct);
    items.forEach((li, i) => { li.innerHTML = texts[i]; });
  };
  range.addEventListener('input', draw);
  fig.querySelectorAll('input[name="ring-mode"]').forEach(input => input.addEventListener('change', draw));
  fig.redraw = draw;
  draw();
}

/* ── Fig. 2: synthetic Overview window ───────────────────────────────── */
const FRESH = 'Source: oauth · Updated: just now · Reported usage — recent';
const SCENARIOS = {
  normal: {
    ring: [42, 'ok'], label: 'Codex Primary (5h): 42% used',
    sub: 'Last check just now  ·  Next reset: Codex Session — resets in 2h',
    sections: [['ACTIVE', [
      { name: 'Codex', pill: ['ok', '✓ Fresh'], pct: [42, 'ok'], plan: 'Pro', meters: [['Session · 5h', 42, 'ok', 'resets in 2h'], ['Weekly · 7d', 31, 'ok', 'resets in 4d']], details: FRESH },
      { name: 'Claude', pill: ['ok', '✓ Fresh'], pct: [27, 'ok'], plan: 'Max', meters: [['Session · 5h', 27, 'ok', 'resets in 3h'], ['Weekly · 7d', 12, 'ok', 'resets Fri 09:00']], details: 'Source: oauth · Updated: 1m ago · Reported usage — recent' },
      { name: 'OpenAI API', pill: ['spend', '$ Spend'], cost: 'Month-to-date cost: $12.50 USD', caption: 'Spend in currency — not a quota or remaining balance.', details: 'Source: admin-api · Updated: 2m ago · Cost / balance only — quota unknown; recent' },
    ]], ['ANNOUNCEMENTS', [
      { name: 'Codex resets', pill: ['neutral', 'All paid plans'], headline: 'Last global Codex reset: 2d ago (regular)', quote: 'Example announcement: usage limits were reset for paid plans.', meters: [], lines: ['41 resets tracked · one every 7.2 days on average'], details: 'Data from Codex Resets (codex-resets.com) · not affiliated with OpenAI', buttons: ['Announcement ↗', 'codex-resets.com ↗'] },
    ], false]],
  },
  near: {
    ring: [96, 'critical'], label: 'Claude Primary (5h): 96% used',
    sub: 'Last check 1m ago  ·  Next reset: Claude Session — resets in 41m',
    sections: [
      ['AT THE LIMIT', [
        { name: 'Claude', tone: 'critical', pill: ['ok', '✓ Fresh'], pct: [96, 'crit'], plan: 'Max', meters: [['Session · 5h', 96, 'crit', 'resets in 41m'], ['Weekly · 7d', 58, 'ok', 'resets Fri 09:00']], details: 'Source: oauth · Updated: 1m ago · Reported usage — recent' },
      ]],
      ['ACTIVE', [
        { name: 'Codex', tone: 'warning', pill: ['ok', '✓ Fresh'], pct: [88, 'warn'], plan: 'Pro', meters: [['Session · 5h', 64, 'ok', 'resets in 2h'], ['Weekly · 7d', 88, 'warn', 'resets in 1d']], details: FRESH },
      ]],
    ],
  },
  stale: {
    ring: [null, 'stale'], label: 'Cached / stale — quota unconfirmed',
    sub: 'Last check 46m ago', unconfirmed: 'Unconfirmed: Codex: stale; Claude: stale',
    sections: [['ACTIVE', [
      { name: 'Codex', pill: ['stale', '◷ Stale'], pct: [64, 'neutral'], plan: 'Pro', meters: [['Session · 5h', 64, 'neutral', 'resets in 2h'], ['Weekly · 7d', 40, 'neutral', 'resets in 4d']], details: 'Source: oauth · Updated: 46m ago · Cached / stale — not confirmed now' },
      { name: 'Claude', pill: ['stale', '◷ Stale'], pct: [31, 'neutral'], meters: [['Session · 5h', 31, 'neutral', 'resets in 3h']], details: 'Source: oauth · Updated: 52m ago · Cached / stale — not confirmed now' },
    ]]],
  },
  unknown: {
    ring: [null, 'unknown'], label: 'Quota unknown',
    sub: 'Last check just now', unconfirmed: 'Unconfirmed: Gemini: unknown / error; Cursor: unknown / error',
    sections: [
      ['PROBLEMS', [
        { name: 'Gemini', tone: 'error', pill: ['error', '! Error'], error: 'HTTP 500 from quota endpoint', fix: 'Fix: Sign in via Antigravity CLI (agy) or ~/.gemini/oauth_creds.json', details: 'Source: oauth · Updated: unknown · Attention — collection reported a problem' },
      ]],
      ['NOT CONNECTED', [
        { name: 'Cursor', pill: ['neutral', '○ Not connected'], soft: 'Not signed in — cookie missing', fix: 'Fix: Save Cookie header to cursor_cookie (chmod 600)', details: 'Source: web · Updated: unknown · Attention — collection reported a problem' },
      ]],
    ],
  },
};

const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function cardHTML(c) {
  let h = `<article class="card ${c.tone || ''}"><div class="card-top"><span class="card-name">${esc(c.name)}</span><span class="app-pill ${c.pill[0]}">${esc(c.pill[1])}</span>`;
  if (c.pct) h += `<span class="card-pct t-${c.pct[1]}">${c.pct[0]}%</span>`;
  h += '</div>';
  if (c.plan) h += `<div class="fine">${esc(c.plan)}</div>`;
  if (c.headline) h += `<div class="headline">${esc(c.headline)}</div>`;
  if (c.quote) h += `<div class="quote">“${esc(c.quote)}”</div>`;
  for (const line of c.lines || []) h += `<div class="fine">${esc(line)}</div>`;
  if (c.cost) h += `<div class="cost">${esc(c.cost)}</div><div class="fine">${esc(c.caption)}</div>`;
  for (const [name, pct, tone, reset] of c.meters || []) {
    h += `<div class="meter"><div class="meter-top"><span>${esc(name)}</span><b class="t-${tone}">${pct}%</b></div><div class="bar"><i class="b-${tone}" style="width:${pct}%"></i></div><div class="fine">${esc(reset)}</div></div>`;
  }
  if (c.error) h += `<div class="err">${esc(c.error)}</div>`;
  if (c.soft) h += `<div class="fine">${esc(c.soft)}</div>`;
  if (c.fix) h += `<div class="fine">${esc(c.fix)}</div>`;
  const buttons = (c.buttons || ['Refresh', 'Open ↗']).map(b => `<span class="app-btn">${esc(b)}</span>`).join('');
  return h + `<div class="card-foot"><span class="fine">${esc(c.details)}</span>${buttons}</div></article>`;
}

function renderScenario(root, key) {
  const s = SCENARIOS[key];
  const [pct, state] = s.ring;
  root.querySelectorAll('[data-tray-ring]').forEach(el => { el.innerHTML = ringSVG(pct, state); });
  const ring = root.querySelector('[data-app-ring]');
  if (ring) ring.innerHTML = ringSVG(pct, state, { center: 'plain' }) + (pct === null ? '' : `<b class="t-${TONE[state]}">${pct}%</b>`);
  const set = (sel, text) => { const el = root.querySelector(sel); if (el) { el.textContent = text || ''; el.hidden = !text; } };
  set('[data-app-title]', s.label);
  set('[data-app-sub]', s.sub);
  set('[data-app-unconfirmed]', s.unconfirmed);
  const body = root.querySelector('[data-app-body]');
  if (body) {
    body.innerHTML = s.sections.map(([title, cards, counted = true]) => `<div class="app-section">${counted ? `${title}  ·  ${cards.length}` : title}</div>${cards.map(cardHTML).join('')}`).join('');
    body.scrollTop = 0;
  }
}

function setupDemo() {
  document.querySelectorAll('[data-demo]').forEach(demo => {
    const note = demo.querySelector('[data-scenario-note]');
    const update = () => {
      const checked = demo.querySelector('input[name="scenario"]:checked');
      renderScenario(demo, checked.value);
      if (note) note.textContent = checked.closest('label').querySelector('.desc').textContent;
    };
    demo.querySelectorAll('input[name="scenario"]').forEach(input => input.addEventListener('change', update));
    demo.redraw = update;
    update();
  });
}

/* ── Copy ────────────────────────────────────────────────────────────── */
function setupCopy() {
  const live = document.getElementById('live');
  document.querySelectorAll('[data-copy]').forEach(button => {
    button.addEventListener('click', async () => {
      const lines = [...document.getElementById(button.dataset.copy).querySelectorAll('[data-cmd]')].map(line => line.textContent);
      try {
        await navigator.clipboard.writeText(lines.join('\n'));
        button.classList.add('done');
        button.textContent = t('copiado', 'copied');
        if (live) live.textContent = t('Comandos copiados.', 'Commands copied.');
      } catch {
        button.textContent = t('selecione e copie', 'select and copy');
      }
      setTimeout(() => { button.classList.remove('done'); button.textContent = t('copiar', 'copy'); }, 1800);
    });
  });
}

/* ── Language ────────────────────────────────────────────────────────── */
function applyLanguage(next, persist) {
  lang = next === 'en' ? 'en' : 'pt-BR';
  const key = lang === 'en' ? 'en' : 'pt';
  const pick = (el, attr) => el.dataset[`${key}${attr}`];
  document.documentElement.lang = lang;
  document.querySelectorAll('[data-pt][data-en]').forEach(el => { el.innerHTML = el.dataset[key]; });
  document.querySelectorAll('[data-pt-aria]').forEach(el => el.setAttribute('aria-label', pick(el, 'Aria')));
  document.querySelectorAll('[data-pt-alt]').forEach(el => el.setAttribute('alt', pick(el, 'Alt')));
  document.querySelectorAll('[data-pt-content]').forEach(el => el.setAttribute('content', pick(el, 'Content')));
  if (document.body.dataset.ptTitle) document.title = pick(document.body, 'Title');
  document.querySelectorAll('[data-lang]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === lang)));
  document.querySelectorAll('[data-copy]').forEach(b => { b.textContent = t('copiar', 'copy'); });
  document.querySelector('[data-ring-figure]')?.redraw?.();
  document.querySelectorAll('[data-demo]').forEach(demo => demo.redraw?.());
  if (persist) { try { localStorage.setItem(STORE_KEY, lang); } catch { /* storage disabled */ } }
}

function initialLanguage() {
  const param = new URLSearchParams(location.search).get('lang');
  if (param) return param.toLowerCase().startsWith('en') ? 'en' : 'pt-BR';
  try { const saved = localStorage.getItem(STORE_KEY); if (saved) return saved; } catch { /* storage disabled */ }
  return (navigator.language || '').toLowerCase().startsWith('pt') ? 'pt-BR' : 'en';
}

document.querySelectorAll('[data-static-ring]').forEach(el => {
  const [pct, state, palette] = el.dataset.staticRing.split(',');
  el.innerHTML = ringSVG(pct === 'none' ? null : Number(pct), state, { palette: palette || 'app' });
});
document.querySelectorAll('[data-lang]').forEach(b => b.addEventListener('click', () => applyLanguage(b.dataset.lang, true)));
setupRingFigure();
setupDemo();
setupCopy();
applyLanguage(initialLanguage(), false);
