'use strict';

const {
  fmt, timeOf, humanElapsed, degToCompass, formatBearing,
  moonEmoji, moonPhaseKey, MOON_PHASE_EN, TREND_ARROWS,
  rssiPct, dataAgeState, meanOver, windPointerState, calcDewPoint,
} = WeatherCore;

// ── Language ───────────────────────────────────────────────────────────────
// Fixed at load; a kiosk has no room for a switcher. /console/?lang=de
const LANG = new URLSearchParams(location.search).get('lang') || 'en';
const LOCALE = LANG === 'de' ? 'de-DE' : 'en-GB';
let t = {};
function tr(path, fallback) {
  const val = path.split('.').reduce((o, k) => o?.[k], t);
  return val ?? fallback ?? path;
}

// ── Shared state, filled by Task 4 ─────────────────────────────────────────
const state = {
  outdoor: null, outdoorAt: null,
  indoor: null,  indoorAt: null,
  today: null, rainTotals: null, solar: null, system: null, systemAt: null,
  windSamples: [],       // [{ t, v }] for the 10-minute mean
  connected: false,
};

// ── Pages ──────────────────────────────────────────────────────────────────
// Each entry: id, title key, ageSource (which clock the header shows),
// render(root, state). Later tasks fill the render functions in.
const PAGES = [
  { id: 'now',    title: 'Now',         age: 'outdoor', render: root => { root.innerHTML = '<div class="tile" style="flex:1"><div class="lbl">Now</div></div>'; } },
  { id: 'rain',   title: 'Rain',        age: 'outdoor', render: root => { root.innerHTML = '<div class="tile" style="flex:1"><div class="lbl">Rain</div></div>'; } },
  { id: 'wind',   title: 'Wind',        age: 'outdoor', render: root => { root.innerHTML = '<div class="tile" style="flex:1"><div class="lbl">Wind</div></div>'; } },
  { id: 'sun',    title: 'Sun & moon',  age: 'outdoor', render: root => { root.innerHTML = '<div class="tile" style="flex:1"><div class="lbl">Sun</div></div>'; } },
  { id: 'indoor', title: 'Indoor',      age: 'indoor',  render: root => { root.innerHTML = '<div class="tile" style="flex:1"><div class="lbl">Indoor</div></div>'; } },
  { id: 'status', title: 'Status',      age: 'outdoor', render: root => { root.innerHTML = '<div class="tile" style="flex:1"><div class="lbl">Status</div></div>'; } },
  { id: 'system', title: 'System',      age: 'system',  render: root => { root.innerHTML = '<div class="tile" style="flex:1"><div class="lbl">System</div></div>'; } },
];

const DWELL_MS  = 15_000;
const RESUME_MS = 60_000;

let current = 0;
let rotateTimer = null;
let resumeTimer = null;

const stage = document.getElementById('stage');
const dotsEl = document.getElementById('dots');
const pageEls = PAGES.map(() => {
  const el = document.createElement('div');
  el.className = 'page';
  stage.appendChild(el);
  return el;
});

PAGES.forEach((p, i) => {
  const box = document.createElement('div');
  box.className = 'dotbox';
  const dot = document.createElement('span');
  dot.className = 'dot';
  box.appendChild(dot);
  box.addEventListener('pointerup', () => { takeOver(); goTo(i, i > current ? 1 : -1); });
  dotsEl.appendChild(box);
});

function renderCurrent() {
  const page = PAGES[current];
  const el = pageEls[current];
  page.render(el, state);
  document.getElementById('page-title').textContent = tr('console.' + page.id, page.title).toUpperCase();
  updateAgeHeader();
}

function updateAgeHeader() {
  const page = PAGES[current];
  const at = page.age === 'indoor' ? state.indoorAt : page.age === 'system' ? state.systemAt : state.outdoorAt;
  const ageMs = at == null ? null : Date.now() - at;
  const st = dataAgeState(ageMs);
  document.getElementById('age-dot').className = st === 'fresh' ? '' : st;
  document.getElementById('age').textContent =
    ageMs == null ? tr('console.no_data', 'no data') : humanElapsed(ageMs, tr);
  pageEls[current].classList.remove('fresh', 'aging', 'stale');
  pageEls[current].classList.add(st);
}

function goTo(index, direction) {
  const next = ((index % PAGES.length) + PAGES.length) % PAGES.length;
  if (next === current) return;
  const from = pageEls[current], to = pageEls[next];
  // classList.add('') throws, so the direction classes are added separately
  // rather than via a conditional that can yield an empty token.
  from.classList.add(direction === 0 ? 'fade' : 'slide');
  if (direction > 0) from.classList.add('out-left');
  else if (direction < 0) from.classList.add('out-right');
  from.classList.remove('active');
  current = next;
  renderCurrent();
  to.classList.add('active');
  setTimeout(() => {
    // Remove only the transition classes this call added — never reset the
    // whole className. A second goTo() may have already made `from` the
    // incoming page again (and re-added 'active', or a fresh/aging/stale
    // class via updateAgeHeader) before this timeout fires; a blanket
    // `from.className = 'page'` would silently strip that.
    from.classList.remove('fade', 'slide', 'out-left', 'out-right');
    to.classList.remove('slide', 'out-left', 'out-right');
  }, 220);
  dotsEl.querySelectorAll('.dot').forEach((d, i) => d.classList.toggle('on', i === current));
}

function startRotation() {
  clearInterval(rotateTimer);
  rotateTimer = setInterval(() => goTo(current + 1, 0), DWELL_MS);
}

// Any touch pauses rotation; it resumes from whatever page is on screen then.
function takeOver() {
  clearInterval(rotateTimer);
  clearTimeout(resumeTimer);
  resumeTimer = setTimeout(startRotation, RESUME_MS);
}

// ── Swipe ──────────────────────────────────────────────────────────────────
let downX = 0, downY = 0, downT = 0;
stage.addEventListener('pointerdown', e => { downX = e.clientX; downY = e.clientY; downT = Date.now(); });
stage.addEventListener('pointerup', e => {
  const dx = e.clientX - downX, dy = e.clientY - downY;
  takeOver();
  if (Math.abs(dx) >= 60 && Math.abs(dx) > Math.abs(dy) && Date.now() - downT < 1200) {
    goTo(current + (dx < 0 ? 1 : -1), dx < 0 ? 1 : -1);
  }
});
document.addEventListener('contextmenu', e => e.preventDefault());

// ── Clock ──────────────────────────────────────────────────────────────────
function tickClock() {
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit', hour12: false });
  document.getElementById('date').textContent =
    now.toLocaleDateString(LOCALE, { weekday: 'short', day: '2-digit', month: '2-digit' });
  updateAgeHeader();
}

// ── Boot ───────────────────────────────────────────────────────────────────
async function boot() {
  try {
    const res = await fetch(`/static/i18n/${LANG}.json`, { cache: 'no-cache' });
    if (res.ok) t = await res.json();
  } catch (_) { /* falls back to the inline English strings */ }
  document.documentElement.lang = LANG;
  pageEls[0].classList.add('active');
  dotsEl.querySelector('.dot').classList.add('on');
  renderCurrent();
  tickClock();
  setInterval(tickClock, 1000);
  startRotation();
}
boot();
