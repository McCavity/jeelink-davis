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
  { id: 'now', title: 'Now', age: 'outdoor', render: (root, s) => {
      const o = s.outdoor || {}, ind = s.indoor || {}, td = s.today || {};
      const dew = calcDewPoint(o.temperature, o.humidity);
      root.innerHTML = `
        <div class="col" style="flex:0 0 620px">
          <div class="tile" style="flex:1">
            <div class="lbl">${tr('cards.temperature', 'Temperature')}</div>
            <div class="huge amber value" style="margin:14px 0 18px">${fmt(o.temperature, 1)}<span class="unit">°C</span></div>
            <div class="mm value">
              <span class="rose">↑ ${fmt(td.temp_max, 1)}</span>
              <span class="sky">↓ ${fmt(td.temp_min, 1)}</span>
              <span class="slate">${tr('cards.dew_point', 'Dew point')} ${fmt(dew, 1)}</span>
            </div>
          </div>
        </div>
        <div class="col" style="flex:1">
          ${tile(tr('cards.wind', 'Wind'),
            `<div class="row"><span class="med sky">${fmt(o.wind_speed, 1)}<span class="unit-s">m/s</span></span>
             <span class="sub sky">${degToCompass(o.wind_direction, LANG)} ${formatBearing(o.wind_direction)}</span></div>`,
            '', { style: 'flex:1' })}
          ${tile(tr('cards.humidity', 'Humidity'),
            `<span class="med teal">${fmt(o.humidity, 0)}<span class="unit-s">%</span></span>
             <div class="bar"><i style="width:${o.humidity ?? 0}%;background:#14b8a6"></i></div>`,
            '', { style: 'flex:1' })}
          ${tile(tr('cards.pressure', 'Pressure'),
            `<div class="row"><span class="med violet">${fmt(ind.pressure, 1)}<span class="unit-s">hPa</span></span>
             <span class="sub violet">${TREND_ARROWS[ind.pressure_trend] || TREND_ARROWS.unknown}</span></div>`,
            '', { style: 'flex:1' })}
        </div>`;
    } },
  { id: 'rain', title: 'Rain', age: 'outdoor', render: (root, s) => {
      const o = s.outdoor || {}, td = s.today || {}, tot = s.rainTotals || {};
      // rain_secs is the interval between the last two tips; -1 means "no tip
      // since the service started", which is empty, not broken.
      const rate = o.rain_secs > 0 ? 720 / o.rain_secs : 0;
      const since = o.rain_secs > 0 ? humanElapsed(o.rain_secs * 1000, tr) : '—';
      root.innerHTML = `
        <div class="col" style="flex:1">
          <div class="tile" style="flex:0 0 250px">
            <div class="lbl">${tr('cards.rain_rate_label', 'Rain rate')}</div>
            <div class="row" style="margin-top:10px">
              <span class="huge blue value" style="font-size:150px">${fmt(rate, 1)}<span class="unit">mm/h</span></span>
              <span style="flex:1"></span>
              <span style="text-align:right">
                <span class="lbl">${tr('cards.rain_last_tip', 'Last tip')}</span>
                <div class="big slate value" style="font-size:64px">${since}</div>
              </span>
            </div>
          </div>
          <div class="grid4" style="flex:1">
            ${tile(tr('cards.rain_today_label', 'Today'), `<span class="big blue">${fmt(td.rain_mm, 1)}</span>`, 'mm', { cls: 'ctr' })}
            ${tile(tr('cards.rain_week', 'Week'),        `<span class="big blue">${fmt(tot.week_mm, 1)}</span>`, 'mm', { cls: 'ctr' })}
            ${tile(tr('cards.rain_month', 'Month'),      `<span class="big blue">${fmt(tot.month_mm, 1)}</span>`, 'mm', { cls: 'ctr' })}
            ${tile(tr('cards.rain_year', 'Year'),        `<span class="big blue">${fmt(tot.year_mm, 1)}</span>`, 'mm', { cls: 'ctr' })}
          </div>
        </div>`;
    } },
  { id: 'wind',   title: 'Wind',        age: 'outdoor', render: root => { root.innerHTML = '<div class="tile" style="flex:1"><div class="lbl">Wind</div></div>'; } },
  { id: 'sun',    title: 'Sun & moon',  age: 'outdoor', render: root => { root.innerHTML = '<div class="tile" style="flex:1"><div class="lbl">Sun</div></div>'; } },
  { id: 'indoor', title: 'Indoor', age: 'indoor', render: (root, s) => {
      const i = s.indoor || {};
      root.innerHTML = `
        <div class="col" style="flex:1">
          <div class="tile" style="flex:1">
            <div class="lbl">${tr('cards.indoor_temp', 'Indoor temp.')}</div>
            <div class="huge orange value" style="font-size:172px;margin-top:10px">${fmt(i.temperature, 1)}<span class="unit">°C</span></div>
          </div>
        </div>
        <div class="col" style="flex:1">
          <div class="tile" style="flex:1">
            <div class="lbl">${tr('cards.indoor_humidity', 'Indoor humidity')}</div>
            <div class="huge cyan value" style="font-size:172px;margin-top:10px">${fmt(i.humidity, 0)}<span class="unit">%</span></div>
            <div class="bar"><i style="width:${i.humidity ?? 0}%;background:#0e7490"></i></div>
          </div>
          ${tile(tr('cards.pressure', 'Pressure'),
            `<div class="row"><span class="med violet">${fmt(i.pressure, 1)}<span class="unit-s">hPa</span></span>
             <span class="sub violet">${TREND_ARROWS[i.pressure_trend] || TREND_ARROWS.unknown}</span></div>`,
            '', { style: 'flex:0 0 150px' })}
        </div>`;
    } },
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

// ── Rendering helper ───────────────────────────────────────────────────────
// body and sub are HTML strings. opts.cls adds a class (e.g. 'ctr'),
// opts.style adds inline style (e.g. 'flex:1') — they are not interchangeable,
// so they get separate keys rather than one overloaded argument.
function tile(label, body, sub, opts = {}) {
  const cls = opts.cls ? ' ' + opts.cls : '';
  const style = opts.style ? ` style="${opts.style}"` : '';
  return `<div class="tile${cls}"${style}>
    <div class="lbl">${label}</div>
    <div class="value">${body}</div>
    ${sub ? `<div class="sub-d value">${sub}</div>` : ''}
  </div>`;
}

// ── Live stream ────────────────────────────────────────────────────────────
let es = null;
let backoff = 1000;

function mergeOutdoor(d) {
  state.outdoor = Object.assign({}, state.outdoor, d);
  state.outdoorAt = Date.now();
  if (d.wind_speed != null) {
    state.windSamples.push({ t: Date.now(), v: d.wind_speed });
    // Keep the array bounded: a 10-minute window never needs more than this.
    const cutoff = Date.now() - 660_000;
    while (state.windSamples.length && state.windSamples[0].t < cutoff) state.windSamples.shift();
  }
  renderCurrent();
}

function connectStream() {
  if (es) es.close();
  es = new EventSource('/api/stream');
  es.onopen = () => { state.connected = true; backoff = 1000; renderCurrent(); };
  es.onmessage = ev => { try { mergeOutdoor(JSON.parse(ev.data)); } catch (_) {} };
  es.onerror = () => {
    state.connected = false;
    es.close();
    // The page is never reloaded on error: a dead backend must cost the data
    // stream, not the document. Values stay on screen and visibly age.
    setTimeout(connectStream, backoff);
    backoff = Math.min(backoff * 2, 30_000);
    renderCurrent();
  };
}

// ── Polled endpoints ───────────────────────────────────────────────────────
async function getJSON(url) {
  try {
    const res = await fetch(url, { cache: 'no-store' });
    return res.ok && res.status !== 204 ? await res.json() : null;
  } catch (_) { return null; }
}

async function pollIndoor() {
  const d = await getJSON('/api/indoor');
  if (d) { state.indoor = d; state.indoorAt = Date.now(); renderCurrent(); }
}
async function pollSlow() {
  const [today, totals] = await Promise.all([getJSON('/api/history/today'), getJSON('/api/rain/totals')]);
  if (today)  state.today = today;
  if (totals) state.rainTotals = totals;
  renderCurrent();
}
async function pollSolar() {
  const d = await getJSON('/api/solar');
  if (d) { state.solar = d; renderCurrent(); }
}

function startPolling() {
  pollIndoor(); setInterval(pollIndoor, 30_000);
  pollSlow();   setInterval(pollSlow, 60_000);
  pollSolar();  setInterval(pollSolar, 900_000);
}

// ── Nightly hygiene reload, guarded ────────────────────────────────────────
// Only reloads when the service actually answers. Without the guard this is
// exactly the mechanism that leaves an error page on the wall at 04:00.
function scheduleNightlyReload() {
  setInterval(async () => {
    const now = new Date();
    if (now.getHours() !== 4 || now.getMinutes() !== 0) return;
    if (await getJSON('/api/latest')) location.reload();
  }, 60_000);
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
  connectStream();
  startPolling();
  scheduleNightlyReload();
}
boot();
