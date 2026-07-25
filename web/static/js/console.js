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
  { id: 'wind', title: 'Wind', age: 'outdoor', render: (root, s) => {
      const o = s.outdoor || {}, td = s.today || {};
      const mean = meanOver(s.windSamples, 600_000, Date.now());
      const ageMs = s.outdoorAt == null ? null : Date.now() - s.outdoorAt;
      root.innerHTML = `
        <div class="tile ctr rose-tile" style="flex:0 0 600px;padding:6px"></div>
        <div class="col" style="flex:1">
          <div class="tile" style="flex:1">
            <div class="lbl">${tr('cards.wind_speed', 'Speed')}</div>
            <div class="huge sky value" style="font-size:150px;margin-top:8px">${fmt(o.wind_speed, 1)}<span class="unit">m/s</span></div>
          </div>
          <div class="grid2" style="flex:0 0 200px">
            ${tile(tr('cards.gust', 'Gust'), `<span class="big sky">${fmt(o.wind_gust, 1)}</span>`, 'm/s')}
            ${tile(tr('cards.gust_max_today', 'Max today'), `<span class="big rose">${fmt(td.wind_gust_max, 1)}</span>`, 'm/s')}
          </div>
        </div>`;
      root.querySelector('.rose-tile').appendChild(
        buildRose(o.wind_direction, windPointerState(mean, ageMs), LANG));
    } },
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

// ── Wind rose ──────────────────────────────────────────────────────────────
// Two concentric scales, as on a real compass card: an outer degree scale
// (1° / 5° / 10°) and an inner point scale (22.5° / 45° / 90°). A single ring
// cannot carry both, because 22.5 is never a multiple of 10.
const SVG_NS = 'http://www.w3.org/2000/svg';
function svgEl(name, attrs) {
  const e = document.createElementNS(SVG_NS, name);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function polar(r, deg) {
  const a = (deg - 90) * Math.PI / 180;
  return [300 + r * Math.cos(a), 300 + r * Math.sin(a)];
}

const POINTER_COLORS = { live: '#38bdf8', calm: '#f87171', stale: '#475569' };

function buildRose(dir, pointerState, lang) {
  const col = POINTER_COLORS[pointerState];
  const svg = svgEl('svg', { viewBox: '0 0 600 600', width: 560, height: 560, 'aria-hidden': 'true' });

  for (let d = 0; d < 360; d++) {
    const major = d % 10 === 0, mid = d % 5 === 0;
    const [x1, y1] = polar(246, d);
    const [x2, y2] = polar(major ? 230 : mid ? 236 : 240, d);
    svg.appendChild(svgEl('line', { x1, y1, x2, y2,
      stroke: major ? '#64748b' : mid ? '#334155' : '#243247', 'stroke-width': major ? 2 : 1 }));
    if (major) {
      const [lx, ly] = polar(213, d);
      const flip = d > 90 && d < 270;
      const label = svgEl('text', { x: lx, y: ly, fill: '#64748b', 'font-size': 17,
        'text-anchor': 'middle', 'dominant-baseline': 'middle', 'font-family': 'ui-monospace, monospace',
        transform: `rotate(${d + (flip ? 180 : 0)} ${lx} ${ly})` });
      label.textContent = formatBearing(d).replace('°', '');   // north reads 360
      svg.appendChild(label);
    }
  }

  for (let i = 0; i < 16; i++) {
    const a = i * 22.5, cardinal = i % 4 === 0, ordinal = i % 4 === 2;
    const [x1, y1] = polar(196, a);
    const [x2, y2] = polar(cardinal ? 166 : ordinal ? 174 : 181, a);
    svg.appendChild(svgEl('line', { x1, y1, x2, y2,
      stroke: cardinal ? '#e2e8f0' : ordinal ? '#94a3b8' : '#64748b',
      'stroke-width': cardinal ? 3 : ordinal ? 2 : 1.5, 'stroke-linecap': 'round' }));
    if (cardinal || ordinal) {
      const [tx, ty] = polar(cardinal ? 143 : 146, a);
      const label = svgEl('text', { x: tx, y: ty, fill: cardinal ? '#cbd5e1' : '#94a3b8',
        'font-size': cardinal ? 30 : 20, 'font-weight': cardinal ? 700 : 500,
        'text-anchor': 'middle', 'dominant-baseline': 'middle' });
      label.textContent = WeatherCore.COMPASS[lang === 'de' ? 'de' : 'en'][i];
      svg.appendChild(label);
    }
  }

  svg.appendChild(svgEl('circle', { cx: 300, cy: 300, r: 270, fill: 'none', stroke: '#131c2e', 'stroke-width': 16 }));

  if (dir != null && pointerState !== 'stale') {
    const [ax, ay] = polar(270, dir - 13), [bx, by] = polar(270, dir + 13);
    svg.appendChild(svgEl('path', { d: `M${ax} ${ay} A 270 270 0 0 1 ${bx} ${by}`,
      fill: 'none', stroke: col, 'stroke-width': 16, 'stroke-linecap': 'round' }));
    const [nx1, ny1] = polar(254, dir), [nx2, ny2] = polar(208, dir);
    svg.appendChild(svgEl('line', { x1: nx1, y1: ny1, x2: nx2, y2: ny2,
      stroke: col, 'stroke-width': 4, 'stroke-linecap': 'round' }));
  }

  const bearing = svgEl('text', { x: 300, y: 286, fill: pointerState === 'stale' ? '#475569' : '#e2e8f0',
    'font-size': 74, 'text-anchor': 'middle', 'dominant-baseline': 'middle',
    'font-family': 'ui-monospace, monospace', 'font-weight': 300 });
  bearing.textContent = pointerState === 'stale' ? '—' : formatBearing(dir);
  svg.appendChild(bearing);

  const point = svgEl('text', { x: 300, y: 344, fill: col, 'font-size': 40,
    'text-anchor': 'middle', 'dominant-baseline': 'middle', 'font-weight': 600 });
  point.textContent = pointerState === 'stale' ? '' : degToCompass(dir, lang);
  svg.appendChild(point);

  const note = svgEl('text', { x: 300, y: 388, fill: '#475569', 'font-size': 21,
    'text-anchor': 'middle', 'dominant-baseline': 'middle' });
  note.textContent = pointerState === 'calm' ? tr('console.calm', 'calm') : '';
  svg.appendChild(note);

  return svg;
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
