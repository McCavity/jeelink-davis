'use strict';

const {
  fmt, timeOf, humanElapsed, degToCompass, formatBearing,
  moonEmoji, moonPhaseKey, MOON_PHASE_EN, TREND_ARROWS,
  rssiPct, dataAgeState, meanOver, windPointerState, calcDewPoint,
  mergeReading, rainRate,
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
  lastTipAt: null,       // wall-clock ms of the last rain_tip_count increase;
                          // in-memory only — unknown again after a restart.
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
      // rain_secs is the interval between the last two tips, not the age of
      // the last tip — the rate must decay off that age (s.lastTipAt), and
      // "last tip" must show elapsed-since-tip, not that interval.
      const msSinceTip = s.lastTipAt == null ? null : Date.now() - s.lastTipAt;
      const rate = rainRate(o.rain_secs, msSinceTip);
      const since = msSinceTip == null ? '—' : humanElapsed(msSinceTip, tr);
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
  { id: 'sun', title: 'Sun & moon', age: null, render: (root, s) => {
      const sol = s.solar;
      if (!sol) { root.innerHTML = `<div class="tile" style="flex:1"><div class="lbl">${tr('console.no_data', 'no data')}</div></div>`; return; }
      const ms = iso => new Date(iso).getTime();
      const span = ms(sol.dusk) - ms(sol.dawn);
      const pos = v => span > 0 ? Math.max(0, Math.min(100, ((ms(v) - ms(sol.dawn)) / span) * 100)) : 0;
      const nowPct = span > 0 ? Math.max(0, Math.min(100, ((Date.now() - ms(sol.dawn)) / span) * 100)) : 0;
      const dayMs = ms(sol.sunset) - ms(sol.sunrise);
      const hhmm = m => `${Math.floor(m / 60)}:${String(Math.round(m % 60)).padStart(2, '0')}`;
      const marks = [
        [sol.dawn, '🌒', tr('solar.dawn', 'Dawn')], [sol.sunrise, '🌅', tr('solar.sunrise', 'Sunrise')],
        [sol.noon, '☀️', tr('solar.noon', 'Noon')], [sol.sunset, '🌇', tr('solar.sunset', 'Sunset')],
        [sol.dusk, '🌘', tr('solar.dusk', 'Dusk')],
      ];
      const key = moonPhaseKey(sol.moon_phase);
      root.innerHTML = `
        <div class="col" style="flex:1">
          <div class="tile" style="flex:1">
            <div class="lbl">${tr('console.day_arc', 'Day arc')}</div>
            <div style="position:relative;height:118px;margin:26px 10px 0">
              <div style="position:absolute;top:52px;left:0;right:0;height:5px;border-radius:3px;
                          background:linear-gradient(90deg,#1e293b 0%,#1e293b 8%,#fbbf24 22%,#fde68a 50%,#fb923c 78%,#1e293b 92%,#1e293b 100%)"></div>
              ${marks.map(([iso, icon, label]) => `
                <div style="position:absolute;top:0;left:${pos(iso)}%;transform:translateX(-50%);width:150px;text-align:center">
                  <div style="font-size:30px;line-height:1">${icon}</div>
                  <div style="font-size:27px;margin-top:26px;font-weight:500;font-variant-numeric:tabular-nums">${timeOf(iso, LOCALE)}</div>
                  <div style="font-size:17px;color:#64748b;text-transform:uppercase;letter-spacing:.06em">${label}</div>
                </div>`).join('')}
              <div style="position:absolute;top:34px;left:${nowPct}%;width:4px;height:40px;background:#34d399;border-radius:2px;transform:translateX(-50%)"></div>
            </div>
          </div>
          <div class="grid2" style="flex:0 0 230px">
            ${tile(tr('solar.moon', 'Moon'),
              `<div class="row" style="margin-top:12px">
                 <span style="font-size:76px;line-height:1">${moonEmoji(sol.moon_phase)}</span>
                 <span><span class="med orange" style="font-size:46px">${fmt(sol.moon_phase, 1)}<span class="unit-s">d</span></span>
                 <div class="sub-d">${tr('moon_phases.' + key, MOON_PHASE_EN[key])}</div></span>
               </div>`, '')}
            ${tile(tr('console.day_length', 'Day length'),
              `<span class="big amber">${hhmm(dayMs / 60000)}<span class="unit-s">h</span></span>`,
              `${tr('console.until_sunset', 'until sunset')} ${hhmm(Math.max(0, (ms(sol.sunset) - Date.now()) / 60000))}`)}
          </div>
        </div>`;
    } },
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
  { id: 'status', title: 'Status', age: 'outdoor', render: (root, s) => {
      const o = s.outdoor || {}, td = s.today || {};
      const pct = Math.round(rssiPct(o.rssi));
      const pctLabel = o.rssi == null ? '—' : `${pct} %`;
      const batt = o.battery_ok == null ? '—' : o.battery_ok ? tr('console.batt_ok', 'OK') : tr('console.batt_low', 'LOW');
      root.innerHTML = `
        <div class="col" style="flex:1">
          <div class="grid2" style="flex:1">
            ${tile(tr('cards.rssi', 'Signal (RSSI)'),
              `<div class="row" style="margin-top:10px"><span class="big em">${o.rssi ?? '—'}<span class="unit-s">dBm</span></span>
               <span class="sub em">${pctLabel}</span></div>
               <div class="bar"><i style="width:${pct}%;background:#10b981"></i></div>`,
              `${tr('console.today_range', 'today')} ${td.rssi_min ?? '—'} … ${td.rssi_max ?? '—'} dBm`)}
            ${tile(tr('cards.battery', 'ISS battery'),
              `<span class="big ${o.battery_ok === false ? 'red' : 'em'}">${batt}</span>`, '')}
          </div>
          <div class="grid2" style="flex:1">
            ${tile(tr('console.last_reading', 'Last reading'),
              `<span class="big slate" style="font-size:76px">${s.outdoorAt ? timeOf(new Date(s.outdoorAt).toISOString(), LOCALE) : '—'}</span>`,
              `${tr('console.channel', 'channel')} ${o.channel ?? '—'} · ${tr('console.station', 'station')} ${o.station_id ?? '—'}`)}
            ${tile(tr('console.connection', 'Connection'),
              `<span class="big ${s.connected ? 'em' : 'red'}" style="font-size:60px">${s.connected ? tr('console.live', 'live') : tr('console.reconnecting', 'reconnecting')}</span>`,
              `${tr('console.tips_today', 'tips')} ${o.rain_tip_count ?? '—'}`)}
          </div>
        </div>`;
    } },
  { id: 'system', title: 'System', age: 'system', render: (root, s) => {
      const y = s.system || {};
      const memPct = y.mem_total_mb && y.mem_used_mb != null ? Math.round(y.mem_used_mb / y.mem_total_mb * 100) : 0;
      const diskPct = y.disk_total_gb ? Math.round(y.disk_used_gb / y.disk_total_gb * 100) : 0;
      const tempPct = y.cpu_temp_c != null ? Math.max(0, Math.min(100, y.cpu_temp_c / 80 * 100)) : 0;
      const up = y.uptime_s != null ? `${Math.floor(y.uptime_s / 3600)}:${String(Math.floor(y.uptime_s % 3600 / 60)).padStart(2, '0')}` : '—';
      const flags = (half) => {
        if (!y.throttle) return `<span class="chip">${tr('console.unavailable', 'unavailable')}</span>`;
        const f = y.throttle[half];
        const one = (on, label) => `<span class="chip ${on ? 'warn' : 'ok'}">${label} <b>${on ? tr('console.yes', 'yes') : tr('console.no', 'no')}</b></span>`;
        return one(f.undervoltage, tr('console.voltage', 'Voltage'))
             + one(f.arm_freq_capped, tr('console.clock', 'Clock'))
             + one(f.throttled, tr('console.throttled', 'Throttled'))
             + one(f.soft_temp_limit, tr('console.temp', 'Temp'));
      };
      root.innerHTML = `
        <div class="col" style="flex:1">
          <div style="display:flex;gap:22px;flex:1;min-height:0">
            <div class="tile" style="flex:0 0 520px">
              <div class="lbl">${tr('console.cpu_temp', 'CPU temperature')}</div>
              <div class="huge em value" style="font-size:150px;margin-top:12px">${fmt(y.cpu_temp_c, 1)}<span class="unit">°C</span></div>
              <div class="bar" style="height:22px;margin-top:22px"><i style="width:${tempPct}%;background:linear-gradient(90deg,#10b981,#34d399 60%,#fbbf24 100%)"></i></div>
              <div class="sub-d" style="margin-top:12px;display:flex;justify-content:space-between"><span>${tr('console.soft_limit', 'Soft limit')} 60 °C</span><span>${tr('console.hard_limit', 'Hard')} 80 °C</span></div>
            </div>
            <div class="grid2" style="flex:1">
              ${tile(tr('console.load', 'Load (1/5/15)'),
                `<span class="med slate">${y.load ? y.load[0].toFixed(2) : '—'}</span>`,
                y.load ? `${y.load[1].toFixed(2)} · ${y.load[2].toFixed(2)} — ${y.cpu_count} ${tr('console.cores', 'cores')}` : '')}
              ${tile(tr('console.memory', 'Memory'),
                `<span class="med violet">${y.mem_used_mb ?? '—'}<span class="unit-s">MB</span></span>
                 <div class="bar"><i style="width:${memPct}%;background:#7c3aed"></i></div>`,
                `${tr('console.of', 'of')} ${y.mem_total_mb ?? '—'} MB · ${memPct} %`)}
              ${tile(tr('console.disk', 'Storage'),
                `<span class="med blue">${fmt(y.disk_used_gb, 1)}<span class="unit-s">GB</span></span>
                 <div class="bar"><i style="width:${diskPct}%;background:#2563eb"></i></div>`,
                `${tr('console.of', 'of')} ${fmt(y.disk_total_gb, 1)} GB · ${diskPct} %`)}
              ${tile(tr('console.uptime', 'Uptime'), `<span class="med amber">${up}<span class="unit-s">h</span></span>`, '')}
            </div>
          </div>
          <div class="tile" style="flex:0 0 168px">
            <div class="lbl">${tr('console.throttling', 'Throttling')}</div>
            <div style="display:flex;gap:40px;margin-top:16px">
              <div style="flex:1"><div class="sub-d" style="margin-bottom:10px">${tr('console.now', 'NOW')}</div><div class="chips">${flags('now')}</div></div>
              <div style="flex:1"><div class="sub-d" style="margin-bottom:10px">${tr('console.since_boot', 'SINCE BOOT')}</div><div class="chips">${flags('ever')}</div></div>
              <div style="flex:0 0 300px"><div class="sub-d" style="margin-bottom:10px">${tr('console.core', 'CORE')}</div>
                <div class="chips"><span class="chip">${tr('console.clock', 'Clock')} <b>${y.core_clock_hz ? (y.core_clock_hz / 1e9).toFixed(2) + ' GHz' : '—'}</b></span>
                <span class="chip">${tr('console.voltage', 'Voltage')} <b>${y.core_volts != null ? y.core_volts.toFixed(3) + ' V' : '—'}</b></span></div>
              </div>
            </div>
          </div>
        </div>`;
    } },
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
  syncSystemPolling();
}

function updateAgeHeader() {
  const page = PAGES[current];
  pageEls[current].classList.remove('fresh', 'aging', 'stale');
  const ageDot = document.getElementById('age-dot');
  const ageEl = document.getElementById('age');
  // Solar times are computed astronomically and are never stale — a page
  // with no age source (age: null) gets no clock, no dot, and no ageing
  // class, rather than borrowing another sensor's freshness.
  if (page.age == null) {
    ageDot.style.display = 'none';
    ageEl.style.display = 'none';
    ageEl.textContent = '';
    return;
  }
  ageDot.style.display = '';
  ageEl.style.display = '';
  const at = page.age === 'indoor' ? state.indoorAt : page.age === 'system' ? state.systemAt : state.outdoorAt;
  const ageMs = at == null ? null : Date.now() - at;
  const st = dataAgeState(ageMs);
  ageDot.className = st === 'fresh' ? '' : st;
  ageEl.textContent = ageMs == null ? tr('console.no_data', 'no data') : humanElapsed(ageMs, tr);
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
  // Automatic advance is a cross-fade: the incoming page gets 'fade' too, so
  // it eases in over 180ms instead of popping in at full opacity. Swipe
  // (direction !== 0) is unchanged — it slides and needs no fade on `to`.
  if (direction === 0) to.classList.add('fade');
  setTimeout(() => {
    // Remove only the transition classes this call added — never reset the
    // whole className. A second goTo() may have already made `from` the
    // incoming page again (and re-added 'active', or a fresh/aging/stale
    // class via updateAgeHeader) before this timeout fires; a blanket
    // `from.className = 'page'` would silently strip that.
    from.classList.remove('fade', 'slide', 'out-left', 'out-right');
    to.classList.remove('slide', 'out-left', 'out-right', 'fade');
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
  const prevTipCount = state.outdoor ? state.outdoor.rain_tip_count : null;
  state.outdoor = mergeReading(state.outdoor, d);
  state.outdoorAt = Date.now();
  if (d.rain_tip_count != null && prevTipCount != null && d.rain_tip_count > prevTipCount) {
    state.lastTipAt = Date.now();
  }
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

// The System page is the only one whose endpoint costs a subprocess, so it is
// polled only while it is on screen.
let systemTimer = null;
async function pollSystem() {
  const d = await getJSON('/api/system');
  if (d) { state.system = d; state.systemAt = Date.now(); renderCurrent(); }
}
function syncSystemPolling() {
  const visible = PAGES[current].id === 'system';
  if (visible && !systemTimer) { pollSystem(); systemTimer = setInterval(pollSystem, 10_000); }
  if (!visible && systemTimer) { clearInterval(systemTimer); systemTimer = null; }
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
