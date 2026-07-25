# Touch Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second, auto-cycling front end at `/console/`, sized for a 1280×720 touch panel, plus the kiosk installation that displays it.

**Architecture:** Pure helpers move out of `index.html` into a shared `weather-core.js` consumed by both front ends. The console is a standalone document with hand-written CSS, seven pages rendered from a page table, fed by the existing SSE stream and REST endpoints plus one new `/api/system`. The kiosk is a separate, optional install: labwc + Chromium against loopback.

**Tech Stack:** FastAPI, vanilla JS (no framework, no CDN, no build step), SVG for the wind rose, `node --test` for the shared core, pytest for the endpoint, systemd + labwc for the kiosk.

## Global Constraints

- **Design reference:** `docs/specs/2026-07-25-touch-console-design.md`. Where this plan and the spec disagree, the spec wins — report the conflict rather than silently choosing.
- **Viewport is exactly 1280×720.** Nothing scrolls. Content that does not fit is a layout defect, not a scroll case.
- **No external resources on the console.** No CDN, no webfont, no map tiles. The console must render fully with the internet disconnected.
- **No build step.** Files are served as written from `web/static/`.
- **Repository language is English** — code, comments, commit messages, docs. The console's user-visible strings come from `web/static/i18n/*.json`.
- **This repository is public.** No hostnames, no internal URLs, no absolute paths from a developer machine in any committed file.
- **Branch:** `feature/touch-console`. Commit after every task.
- **`deploy.sh` is not modified by any task in this plan.**
- **Bearing 0 renders as `360°`**, never `000°`.
- **Calm threshold: 10-minute mean wind speed ≤ 0.2 m/s** (WMO). Never re-derive this from the vane standing still.
- **Throttle flags: `now` and `ever` stay separate** everywhere — data, API and display.

---

### Task 1: Shared core module and its test

Creates `weather-core.js` and proves it works. Does not touch `index.html`, so the dashboard keeps running unchanged throughout this task.

**Files:**
- Create: `web/static/js/weather-core.js`
- Test: `tests/test_weather_core.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces: a global `WeatherCore` object with `COMPASS`, `MOON_PHASE_EN`, `TEMP_COLOR_STOPS`, `TREND_ARROWS`, `RAIN_ACTIVE_SECS`, `CALM_MEAN_MS`, `calcDewPoint(T, RH)`, `calcFeelsLike(T, V_ms, RH)`, `fmt(v, dec)`, `timeOf(iso, locale)`, `humanElapsed(ms, tr)`, `degToCompass(deg, lang)`, `formatBearing(deg)`, `moonEmoji(phase)`, `moonPhaseKey(phase)`, `rssiPct(dbm)`, `lerpHex(c1, c2, t)`, `tempColorHex(c)`, `dataAgeState(ageMs)`, `meanOver(samples, windowMs, now)`, `windPointerState(meanSpeed, ageMs)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_weather_core.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const src = await readFile(new URL('../web/static/js/weather-core.js', import.meta.url), 'utf8');
const WC = new Function(src + '; return WeatherCore;')();

test('dew point matches an independently known value', () => {
  // 32.5 °C at 28 % RH -> 11.6 °C (Magnus, a=17.625 b=243.04)
  assert.equal(Math.round(WC.calcDewPoint(32.5, 28) * 10) / 10, 11.6);
  assert.equal(WC.calcDewPoint(null, 28), null);
});

test('bearing 0 renders as 360, not 000', () => {
  assert.equal(WC.formatBearing(0), '360°');
  assert.equal(WC.formatBearing(360), '360°');
  assert.equal(WC.formatBearing(5), '005°');
  assert.equal(WC.formatBearing(72), '072°');
  assert.equal(WC.formatBearing(null), '—');
});

test('compass points follow the language', () => {
  assert.equal(WC.degToCompass(72, 'de'), 'ONO');
  assert.equal(WC.degToCompass(72, 'en'), 'ENE');
  assert.equal(WC.degToCompass(90, 'de'), 'O');
  assert.equal(WC.degToCompass(90, 'en'), 'E');
  assert.equal(WC.degToCompass(359, 'de'), 'N');
  assert.equal(WC.degToCompass(null, 'de'), '—');
});

test('data age state has three bands', () => {
  assert.equal(WC.dataAgeState(5_000), 'fresh');
  assert.equal(WC.dataAgeState(89_000), 'fresh');
  assert.equal(WC.dataAgeState(91_000), 'aging');
  assert.equal(WC.dataAgeState(599_000), 'aging');
  assert.equal(WC.dataAgeState(601_000), 'stale');
  assert.equal(WC.dataAgeState(null), 'stale');
});

test('rolling mean only counts samples inside the window', () => {
  const now = 1_000_000;
  const samples = [
    { t: now - 700_000, v: 9 },   // outside a 10 min window
    { t: now - 300_000, v: 0.1 },
    { t: now - 60_000,  v: 0.3 },
  ];
  assert.equal(WC.meanOver(samples, 600_000, now), 0.2);
  assert.equal(WC.meanOver([], 600_000, now), null);
});

test('pointer state separates calm from silent', () => {
  assert.equal(WC.windPointerState(0.15, 5_000), 'calm');   // WMO calm
  assert.equal(WC.windPointerState(0.20, 5_000), 'calm');   // boundary is inclusive
  assert.equal(WC.windPointerState(0.21, 5_000), 'live');
  assert.equal(WC.windPointerState(0.15, 900_000), 'stale'); // silent wins over calm
  assert.equal(WC.windPointerState(null, 5_000), 'stale');
});

test('moon phase returns a key, not a translated string', () => {
  assert.equal(WC.moonPhaseKey(10.53), 'waxing_gibbous');
  assert.equal(WC.moonPhaseKey(0.5), 'new');
  assert.equal(WC.MOON_PHASE_EN.waxing_gibbous, 'Waxing Gibbous');
});

test('humanElapsed asks the caller for its words', () => {
  const tr = (path, fallback) => ({ 'cards.rain_ago': 's her', 'cards.rain_ago_min': 'min her' }[path] ?? fallback);
  assert.equal(WC.humanElapsed(42_000, tr), '42 s her');
  assert.equal(WC.humanElapsed(300_000, tr), '5 min her');
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test tests/test_weather_core.mjs`
Expected: FAIL — `ENOENT`, `web/static/js/weather-core.js` does not exist yet.

- [ ] **Step 3: Write the module**

Create `web/static/js/weather-core.js`:

```js
// Pure helpers shared by the dashboard (index.html) and the touch console
// (console.html). Nothing in here touches the DOM, reads module state or
// knows about CSS frameworks — that is what makes it shareable.
//
// Loaded in the browser as a classic script (window.WeatherCore) and in
// tests by evaluating the source, so it must stay free of import/export.

const WeatherCore = (function () {
  'use strict';

  const COMPASS = {
    en: ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'],
    de: ['N','NNO','NO','ONO','O','OSO','SO','SSO','S','SSW','SW','WSW','W','WNW','NW','NNW'],
  };

  const MOON_PHASE_EN = {
    new: 'New Moon', waxing_crescent: 'Waxing Crescent', first_quarter: 'First Quarter',
    waxing_gibbous: 'Waxing Gibbous', full: 'Full Moon', waning_gibbous: 'Waning Gibbous',
    last_quarter: 'Last Quarter', waning_crescent: 'Waning Crescent',
  };

  const TREND_ARROWS = { rising: '↗', falling: '↘', steady: '→', unknown: '—' };

  const TEMP_COLOR_STOPS = [
    [-20, '#1e3a8a'], [0, '#1d4ed8'], [5, '#3b82f6'],
    [12,  '#7dd3fc'], [16, '#bae6fd'], [18, '#22c55e'],
    [25,  '#f97316'], [27, '#ef4444'], [30, '#991b1b'], [40, '#991b1b'],
  ];

  const RAIN_ACTIVE_SECS = 1800;   // 30 min decay — after this, rate resets to 0
  const CALM_MEAN_MS     = 0.2;    // WMO definition of calm, 10-minute mean
  const AGE_FRESH_MS     = 90_000;
  const AGE_STALE_MS     = 600_000;

  function calcDewPoint(T, RH) {
    if (T == null || RH == null) return null;
    const a = 17.625, b = 243.04;
    const gamma = (a * T) / (b + T) + Math.log(RH / 100);
    return b * gamma / (a - gamma);
  }

  // Cold + windy (T <= 10 °C, V >= 1.33 m/s): Environment Canada wind chill.
  // Otherwise: Australian BOM apparent temperature.
  function calcFeelsLike(T, V_ms, RH) {
    if (T == null) return null;
    const V = V_ms ?? 0;
    if (T <= 10 && V >= 1.33) {
      const Vkph = V * 3.6;
      return 13.12 + 0.6215 * T - 11.37 * Math.pow(Vkph, 0.16) + 0.3965 * T * Math.pow(Vkph, 0.16);
    }
    const e = RH != null ? (RH / 100) * 6.105 * Math.exp(17.27 * T / (237.7 + T)) : 0;
    return T + 0.33 * e - 0.70 * V - 4.00;
  }

  function fmt(v, dec = 1) {
    return v == null ? '—' : Number(v).toFixed(dec);
  }

  function timeOf(iso, locale = 'en-GB') {
    return new Date(iso).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', hour12: false });
  }

  // The translator is passed in; the core owns no language state.
  function humanElapsed(ms, tr) {
    const s = Math.floor(ms / 1000);
    if (s < 60)   return s + ' ' + tr('cards.rain_ago', 's ago');
    if (s < 3600) return Math.floor(s / 60) + ' ' + tr('cards.rain_ago_min', 'min ago');
    if (s < 86400) return Math.floor(s / 3600) + ' ' + tr('cards.rain_ago_h', 'h ago');
    return Math.floor(s / 86400) + ' ' + tr('cards.rain_ago_d', 'd ago');
  }

  function degToCompass(deg, lang = 'en') {
    if (deg == null) return '—';
    const pts = COMPASS[lang] || COMPASS.en;
    return pts[Math.round(deg / 22.5) % 16];
  }

  // North reads 360, not 000 — the convention in which a bearing is spoken.
  function formatBearing(deg) {
    if (deg == null) return '—';
    const d = ((Math.round(deg) % 360) + 360) % 360;
    return String(d === 0 ? 360 : d).padStart(3, '0') + '°';
  }

  function moonEmoji(phase) {
    if (phase < 1.85 || phase >= 26.15) return '🌑';
    if (phase < 7.38)  return '🌒';
    if (phase < 9.22)  return '🌓';
    if (phase < 14.77) return '🌔';
    if (phase < 16.61) return '🌕';
    if (phase < 22.15) return '🌖';
    if (phase < 23.99) return '🌗';
    return '🌘';
  }

  function moonPhaseKey(phase) {
    if (phase < 1.85 || phase >= 26.15) return 'new';
    if (phase < 7.38)  return 'waxing_crescent';
    if (phase < 9.22)  return 'first_quarter';
    if (phase < 14.77) return 'waxing_gibbous';
    if (phase < 16.61) return 'full';
    if (phase < 22.15) return 'waning_gibbous';
    if (phase < 23.99) return 'last_quarter';
    return 'waning_crescent';
  }

  function rssiPct(dbm) {
    return dbm == null ? 0 : Math.max(0, Math.min(100, ((dbm + 90) / 50) * 100));
  }

  function lerpHex(c1, c2, t) {
    const h = s => [parseInt(s.slice(1, 3), 16), parseInt(s.slice(3, 5), 16), parseInt(s.slice(5, 7), 16)];
    const [r1, g1, b1] = h(c1), [r2, g2, b2] = h(c2);
    return `rgb(${Math.round(r1 + (r2 - r1) * t)},${Math.round(g1 + (g2 - g1) * t)},${Math.round(b1 + (b2 - b1) * t)})`;
  }

  function tempColorHex(c) {
    if (c == null) return '#64748b';
    const s = TEMP_COLOR_STOPS;
    if (c <= s[0][0]) return s[0][1];
    if (c >= s[s.length - 1][0]) return s[s.length - 1][1];
    for (let i = 0; i < s.length - 1; i++) {
      if (c >= s[i][0] && c <= s[i + 1][0]) {
        return lerpHex(s[i][1], s[i + 1][1], (c - s[i][0]) / (s[i + 1][0] - s[i][0]));
      }
    }
    return '#64748b';
  }

  function dataAgeState(ageMs) {
    if (ageMs == null) return 'stale';
    if (ageMs < AGE_FRESH_MS) return 'fresh';
    if (ageMs < AGE_STALE_MS) return 'aging';
    return 'stale';
  }

  // samples: [{ t: epochMs, v: number }]. Returns null when the window is empty.
  function meanOver(samples, windowMs, now) {
    const inWindow = samples.filter(s => s.v != null && now - s.t <= windowMs);
    if (!inWindow.length) return null;
    return inWindow.reduce((a, s) => a + s.v, 0) / inWindow.length;
  }

  // Three states, because "calm" and "the station went silent" must never
  // look the same: a red pointer over an hour-old reading would be a lie.
  function windPointerState(meanSpeed, ageMs) {
    if (dataAgeState(ageMs) === 'stale' || meanSpeed == null) return 'stale';
    return meanSpeed <= CALM_MEAN_MS ? 'calm' : 'live';
  }

  return {
    COMPASS, MOON_PHASE_EN, TREND_ARROWS, TEMP_COLOR_STOPS,
    RAIN_ACTIVE_SECS, CALM_MEAN_MS, AGE_FRESH_MS, AGE_STALE_MS,
    calcDewPoint, calcFeelsLike, fmt, timeOf, humanElapsed,
    degToCompass, formatBearing, moonEmoji, moonPhaseKey,
    rssiPct, lerpHex, tempColorHex,
    dataAgeState, meanOver, windPointerState,
  };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = WeatherCore;
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --test tests/test_weather_core.mjs`
Expected: PASS, 8 tests.

- [ ] **Step 5: Calibrate the test — make it fail on purpose**

A test that has never been red is unproven. Temporarily change `CALM_MEAN_MS` from `0.2` to `2.0` in `weather-core.js`.

Run: `node --test tests/test_weather_core.mjs`
Expected: FAIL in "pointer state separates calm from silent" — `windPointerState(0.21, 5000)` returns `'calm'` instead of `'live'`.

Then revert the value to `0.2` and re-run. Expected: PASS. Do not commit the broken value.

- [ ] **Step 6: Commit**

```bash
git add web/static/js/weather-core.js tests/test_weather_core.mjs
git commit -m "feat: shared weather-core module with node tests"
```

---

### Task 2: Dashboard adopts the shared core

The dashboard must behave exactly as before, minus two deliberate fixes: German compass points, and bearings printed as `360°`.

**Files:**
- Modify: `web/static/index.html`

**Interfaces:**
- Consumes: `WeatherCore` from Task 1.
- Produces: nothing new. Leaves `rssiColor`, `tempColor`, `TREND_COLORS`, `WMO_ICONS`, `tr`, `setLang` in place — they are Tailwind- or DOM-bound and stay.

- [ ] **Step 1: Record the "before" state**

Start the service locally and open the dashboard:

```bash
.venv/bin/uvicorn web.app:app --port 8000
```

Note the displayed values for temperature, dew point (click the temperature card), wind direction, moon phase and the last-tip elapsed time. Confirm the browser console is free of errors. These are the comparison values for Step 6.

- [ ] **Step 2: Load the module and destructure it**

In `web/static/index.html`, add the script tag immediately after the Leaflet script tag in `<head>` (line 11):

```html
  <script src="/static/js/weather-core.js"></script>
```

Then, at the very top of the inline `<script>` block (currently line 483, just before `// ── Constants ──`), insert:

```js
const {
  MOON_PHASE_EN, TREND_ARROWS, TEMP_COLOR_STOPS, RAIN_ACTIVE_SECS,
  calcDewPoint, calcFeelsLike, fmt, timeOf: coreTimeOf, humanElapsed: coreHumanElapsed,
  degToCompass: coreDegToCompass, formatBearing, moonEmoji, moonPhaseKey,
  rssiPct, lerpHex, tempColorHex,
} = WeatherCore;

// Thin wrappers keep the existing call sites unchanged where the core now
// needs the language, the locale or the translator handed to it explicitly.
// timeOf keeps 'de-DE' because that is what the dashboard did before this
// refactor; changing it is a separate decision, not a side effect of one.
const degToCompass = deg => coreDegToCompass(deg, currentLang);
const humanElapsed = ms => coreHumanElapsed(ms, tr);
const timeOf = iso => coreTimeOf(iso, 'de-DE');
function moonPhaseName(phase) {
  const key = moonPhaseKey(phase);
  return tr('moon_phases.' + key, MOON_PHASE_EN[key]);
}
```

- [ ] **Step 3: Delete the now-duplicated definitions**

All line numbers below refer to `index.html` **as it was before this task**.
They shift as soon as you delete the first block, so work from the bottom of
the list upwards, or search by name instead of by line.

Remove from the inline script, leaving everything else untouched:

- `const COMPASS = [...]` (line 486)
- `function calcDewPoint` (lines 555–560)
- `function calcFeelsLike` and its two comment lines (lines 562–574)
- `function degToCompass` (lines 888–890)
- `function fmt` (891–893)
- `function timeOf` (894–896)
- `function rssiPct` (897–899)
- `const TEMP_COLOR_STOPS` (907–911)
- `function lerpHex` (912–916)
- `function tempColorHex` (917–928)
- `function moonEmoji` (937–946)
- `function moonPhaseName` (947–956)
- `function humanElapsed` (644–650)
- `const RAIN_ACTIVE_SECS = 1800;` (642)
- `const TREND_ARROWS = ...` (1537)

Keep `function rssiColor`, `function tempColor`, `const TREND_COLORS`, `const WMO_ICONS`, `const MAX_POINTS`.

- [ ] **Step 4: Use the new bearing format at the one site that prints degrees**

Replace line 996's surroundings — the wind direction degree readout currently written as a raw number followed by `°`:

```js
  setText('wind-dir-deg', s.wind_direction == null ? '—' : String(s.wind_direction));
```

becomes:

```js
  setText('wind-dir-deg', formatBearing(s.wind_direction));
```

Then remove the now-doubled `°` character from the markup at line 241, so the value carries its own unit:

```html
            <span id="wind-dir-deg" class="mono text-slate-400">—</span>
```

- [ ] **Step 5: Verify no definition survives twice**

Run:

```bash
grep -nE '^(function (calcDewPoint|calcFeelsLike|degToCompass|fmt|timeOf|rssiPct|lerpHex|tempColorHex|moonEmoji|moonPhaseName|humanElapsed)|const (COMPASS|TEMP_COLOR_STOPS|TREND_ARROWS|RAIN_ACTIVE_SECS))' web/static/index.html
```

Expected: no output. Any hit is a definition that shadows the core.

- [ ] **Step 6: Compare against the "before" state**

Reload the dashboard. Expected:
- Same values as Step 1 for temperature, dew point, wind speed, moon phase, elapsed time.
- Browser console free of errors.
- Wind direction degrees now show three digits with `°`, and read `360°` when the vane sits on north.
- Switching to German shows `ONO`-style points instead of `ENE`.

- [ ] **Step 7: Confirm the Python suite is untouched**

Run: `.venv/bin/pytest tests/ -v`
Expected: PASS, same count as before this task.

- [ ] **Step 8: Commit**

```bash
git add web/static/index.html
git commit -m "refactor: dashboard consumes weather-core; localise compass, format bearings"
```

---

### Task 3: Console route, document shell and carousel

Produces a reachable, cycling, swipeable console with seven placeholder pages. No live data yet.

**Files:**
- Modify: `web/app.py:120-122`
- Create: `web/static/console.html`
- Create: `web/static/js/console.js`

**Interfaces:**
- Consumes: `WeatherCore` from Task 1.
- Produces: `PAGES` (array of `{ id, title, render(root, state) }`), `state` (shared mutable object), `renderCurrent()`, `goTo(index, direction)`, and the DOM contract: `#stage`, `#page-title`, `#age`, `#age-dot`, `#clock`, `#date`, `#dots`.

- [ ] **Step 1: Add the routes**

In `web/app.py`, directly below the existing `index()` handler (line 120–122), add:

```python
@app.get("/console", include_in_schema=False)
async def console_redirect():
    return RedirectResponse("/console/")


@app.get("/console/", response_class=HTMLResponse)
async def console():
    return (STATIC_DIR / "console.html").read_text()
```

Add the import at the top of the file, beside the existing `fastapi.responses` import:

```python
from fastapi.responses import RedirectResponse
```

- [ ] **Step 2: Write the document shell**

Create `web/static/console.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=1280, height=720, initial-scale=1, user-scalable=no" />
  <title>Weather console</title>
  <link rel="icon" type="image/svg+xml" href="/static/favicon.svg" />
  <script src="/static/js/weather-core.js"></script>
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    html, body {
      width:1280px; height:720px; overflow:hidden;
      background:#0b1120; color:#e2e8f0;
      font-family:-apple-system, "Segoe UI", Roboto, sans-serif;
      user-select:none; -webkit-user-select:none;
      touch-action:none; cursor:none;
    }
    #app { width:1280px; height:720px; padding:0 26px; display:flex; flex-direction:column; }

    /* Header */
    #head { height:56px; flex:0 0 56px; display:flex; align-items:center; gap:18px; border-bottom:1px solid #1e293b; }
    #page-title { font-size:26px; font-weight:700; letter-spacing:.14em; color:#94a3b8; }
    #head .spacer { flex:1; }
    #age-wrap { display:flex; align-items:center; gap:9px; font-size:19px; color:#64748b; font-variant-numeric:tabular-nums; }
    #age-dot { width:13px; height:13px; border-radius:50%; background:#34d399; }
    #age-dot.aging { background:#f59e0b; }
    #age-dot.stale { background:#64748b; }
    #clock { font-size:40px; font-weight:600; font-variant-numeric:tabular-nums; }
    #date { font-size:19px; color:#64748b; }

    /* Stage */
    #stage { flex:1; position:relative; min-height:0; }
    .page { position:absolute; inset:0; padding:22px 0 8px; display:flex; gap:22px; opacity:0; pointer-events:none; }
    .page.active { opacity:1; pointer-events:auto; }
    .page.fade { transition:opacity 180ms linear; }
    .page.slide { transition:transform 200ms ease-out, opacity 200ms ease-out; }
    .page.out-left  { transform:translateX(-90px); opacity:0; }
    .page.out-right { transform:translateX(90px);  opacity:0; }

    /* Tiles */
    .col  { display:flex; flex-direction:column; gap:18px; min-width:0; }
    .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
    .grid4 { display:grid; grid-template-columns:repeat(4,1fr); gap:18px; }
    .tile { background:#131c2e; border:1px solid #1e293b; border-radius:14px; padding:18px 22px;
            display:flex; flex-direction:column; justify-content:center; min-width:0; }
    .tile.ctr { align-items:center; text-align:center; }
    .lbl  { font-size:20px; text-transform:uppercase; letter-spacing:.11em; color:#64748b; font-weight:600; }
    .huge { font-size:196px; line-height:.86; font-weight:300; font-variant-numeric:tabular-nums; letter-spacing:-.03em; }
    .big  { font-size:88px;  line-height:1;   font-weight:300; font-variant-numeric:tabular-nums; }
    .med  { font-size:58px;  line-height:1;   font-weight:300; font-variant-numeric:tabular-nums; }
    .unit  { font-size:34px; color:#64748b; margin-left:8px; }
    .unit-s{ font-size:24px; color:#64748b; margin-left:6px; }
    .sub   { font-size:23px; color:#94a3b8; font-variant-numeric:tabular-nums; }
    .sub-d { font-size:21px; color:#64748b; font-variant-numeric:tabular-nums; }
    .row   { display:flex; align-items:baseline; gap:14px; }
    .mm    { display:flex; gap:22px; font-size:26px; font-variant-numeric:tabular-nums; }
    .bar   { height:16px; background:#1e293b; border-radius:8px; overflow:hidden; margin-top:14px; }
    .bar > i { display:block; height:100%; border-radius:8px; }
    .chip  { display:inline-flex; align-items:center; gap:8px; font-size:20px; padding:7px 15px;
             border-radius:9px; background:#0f1a2b; border:1px solid #1e293b; color:#94a3b8; }
    .chip b { font-weight:600; color:#e2e8f0; }
    .chip.ok   { color:#34d399; border-color:#134e39; }
    .chip.warn { color:#fbbf24; border-color:#78350f; }
    .chips { display:flex; gap:12px; flex-wrap:wrap; }

    /* Ageing: dim at 90 s, grey out at 10 min */
    .aging .value { opacity:.55; }
    .stale .value { opacity:.35; filter:grayscale(1); }

    .amber{color:#fbbf24} .sky{color:#38bdf8} .teal{color:#2dd4bf} .blue{color:#60a5fa}
    .violet{color:#a78bfa} .orange{color:#fdba74} .cyan{color:#67e8f9} .rose{color:#fb7185}
    .slate{color:#94a3b8} .em{color:#34d399} .red{color:#f87171}

    /* Dots */
    #dots { height:44px; flex:0 0 44px; display:flex; align-items:center; justify-content:center; gap:12px; }
    .dotbox { width:44px; height:44px; display:flex; align-items:center; justify-content:center; }
    .dot { width:12px; height:12px; border-radius:50%; background:#334155; transition:width 150ms, background 150ms; }
    .dot.on { background:#94a3b8; width:34px; border-radius:6px; }
  </style>
</head>
<body>
  <div id="app">
    <div id="head">
      <div id="page-title">—</div>
      <div class="spacer"></div>
      <div id="age-wrap"><span id="age-dot"></span><span id="age">—</span></div>
      <div id="clock">--:--</div>
      <div id="date">—</div>
    </div>
    <div id="stage"></div>
    <div id="dots"></div>
  </div>
  <script src="/static/js/console.js"></script>
</body>
</html>
```

- [ ] **Step 3: Write the carousel**

Create `web/static/js/console.js`:

```js
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
    // `from.className = 'page'` would silently strip that and leave the
    // stage showing no page at all on a rapid reverse swipe.
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
```

- [ ] **Step 4: Verify the carousel by hand**

Run: `.venv/bin/uvicorn web.app:app --port 8000`, then open `http://127.0.0.1:8000/console/` in a browser window sized to 1280×720.

Expected:
- Seven placeholder pages cycle every 15 s, wrapping from System back to Now.
- `http://127.0.0.1:8000/console` (no trailing slash) redirects to `/console/`.
- Dragging horizontally by more than 60 px changes page; a short drag does not.
- Tapping a dot jumps to that page and pauses rotation; after 60 s rotation resumes from the page on screen.
- No scrollbar appears, and no browser console errors.

- [ ] **Step 5: Commit**

```bash
git add web/app.py web/static/console.html web/static/js/console.js
git commit -m "feat: console route, document shell and seven-page carousel"
```

---

### Task 4: Live data layer and the Now page

**Files:**
- Modify: `web/static/js/console.js`

**Interfaces:**
- Consumes: `state`, `PAGES`, `renderCurrent`, `tr` from Task 3.
- Produces: `connectStream()`, `startPolling()`, `getJSON(url)`, `tile(label, body, sub, opts)` (returns an HTML string; `opts` takes `{cls}` and/or `{style}`), and a populated `state`. Later page tasks use `tile()` and read `state`.

- [ ] **Step 1: Add the tile helper and the data layer**

Append to `web/static/js/console.js`, before the `boot()` definition:

```js
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
```

- [ ] **Step 2: Call them at boot**

In `boot()`, replace the line `startRotation();` with:

```js
  startRotation();
  connectStream();
  startPolling();
  scheduleNightlyReload();
```

- [ ] **Step 3: Render the Now page**

Replace the `now` entry in `PAGES` with:

```js
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
```

- [ ] **Step 4: Verify against the dashboard**

Open `/console/` and `/` side by side. Expected: temperature, humidity, wind and pressure agree to the displayed precision; today's min/max match the dashboard's temperature card. The page updates without a reload as readings arrive.

- [ ] **Step 5: Verify the ageing bands deliberately**

Stop the service (`Ctrl-C`) while the console stays open. Expected: after 90 s the values dim and the header dot turns amber; after 10 minutes they grey out and the header reads "no data". Restart the service: the stream reconnects on its own and the values return to normal — **without a page reload**.

- [ ] **Step 6: Commit**

```bash
git add web/static/js/console.js
git commit -m "feat: console data layer, ageing states and the Now page"
```

---

### Task 5: Rain and Indoor pages

**Files:**
- Modify: `web/static/js/console.js`

**Interfaces:**
- Consumes: `tile()`, `state`, `tr`, `fmt`, `humanElapsed`, `TREND_ARROWS`.
- Produces: nothing new.

- [ ] **Step 1: Render the Rain page**

Replace the `rain` entry in `PAGES`:

```js
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
```

- [ ] **Step 2: Render the Indoor page**

Replace the `indoor` entry in `PAGES`:

```js
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
```

- [ ] **Step 3: Verify**

Open `/console/`, swipe to Rain and Indoor. Expected: rain totals match `/api/rain/totals`, today's rain matches the dashboard, indoor values match `/api/indoor`. With no tip recorded, "Last tip" shows `—` rather than a wrong number. Neither page scrolls.

- [ ] **Step 4: Commit**

```bash
git add web/static/js/console.js
git commit -m "feat: console rain and indoor pages"
```

---

### Task 6: Wind page with the maritime rose

**Files:**
- Modify: `web/static/js/console.js`

**Interfaces:**
- Consumes: `windPointerState`, `meanOver`, `degToCompass`, `formatBearing`, `state.windSamples`.
- Produces: `buildRose(dir, pointerState, lang)` returning an `SVGElement`.

- [ ] **Step 1: Add the rose builder**

Append to `web/static/js/console.js`, after the `tile()` helper:

```js
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
```

- [ ] **Step 2: Render the Wind page**

Replace the `wind` entry in `PAGES`:

```js
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
```

- [ ] **Step 3: Verify the rose**

Open `/console/` and swipe to Wind. Expected:
- The degree scale is labelled every 10°, and the label at the top reads **360**, not 000.
- The point scale carries `N O S W` in German (`N E S W` in English) plus the four ordinals.
- The centre shows a three-digit bearing and the point abbreviation.
- The pointer is red while the station is calm and blue once the 10-minute mean exceeds 0.2 m/s.

- [ ] **Step 4: Force the stale state and confirm the colour**

Stop the service and wait past 10 minutes (or temporarily lower `AGE_STALE_MS` in `weather-core.js` to `10_000`, observe, then restore it and re-run `node --test tests/test_weather_core.mjs`).

> **Correction (hardware acceptance, 2026-07-25):** the line below, as originally
> written, told a reader to expect the pointer arc/needle suppressed and the
> bearing showing `—` in *every* stale case. That is wrong and was implemented
> wrong — `buildRose()` drew nothing at all whenever `pointerState === 'stale'`,
> so a vane that had simply gone quiet (the design's own motivating scenario)
> made the rose look broken instead of grey. The design
> (`docs/specs/2026-07-25-touch-console-design.md`) actually calls for: when a
> bearing is known but old, draw the arc/needle in grey and keep showing that
> bearing (muted); only when no bearing was ever received (`dir == null`) does
> the pointer stay off and the centre show `—`. Both cases get a note saying
> there is no data **and since when** (e.g. "no data since 19:10"). Fixed in
> `web/static/js/console.js`; see `.superpowers/sdd/grey-needle-report.md`.
> The "Expected" line immediately below describes the ORIGINAL (defective)
> behaviour and is kept only for the historical record of what Task 6 actually
> shipped — do not follow it as current guidance.

Expected: the pointer turns **grey, not red**, and the bearing shows `—`. This is the confusion the third state exists to prevent; an untested third state is only a claim.

- [ ] **Step 5: Commit**

```bash
git add web/static/js/console.js
git commit -m "feat: wind page with a two-scale compass rose and three pointer states"
```

---

### Task 7: Sun & moon and Status pages

**Files:**
- Modify: `web/static/js/console.js`

**Interfaces:**
- Consumes: `state.solar`, `state.outdoor`, `state.today`, `moonEmoji`, `moonPhaseKey`, `MOON_PHASE_EN`, `rssiPct`, `timeOf`.
- Produces: nothing new.

- [ ] **Step 1: Render the Sun & moon page**

Replace the `sun` entry in `PAGES`:

```js
  { id: 'sun', title: 'Sun & moon', age: 'outdoor', render: (root, s) => {
      const sol = s.solar;
      if (!sol) { root.innerHTML = `<div class="tile" style="flex:1"><div class="lbl">${tr('console.no_data', 'no data')}</div></div>`; return; }
      const ms = iso => new Date(iso).getTime();
      const span = ms(sol.dusk) - ms(sol.dawn);
      // Guard on span > 0: when dusk equals dawn the division is 0/0, and
      // Math.min/Math.max propagate NaN rather than clamping it — the marker
      // would render style="left:NaN%", which the browser silently drops.
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
```

- [ ] **Step 2: Render the Status page**

Replace the `status` entry in `PAGES`:

```js
  { id: 'status', title: 'Status', age: 'outdoor', render: (root, s) => {
      const o = s.outdoor || {}, td = s.today || {};
      const pct = Math.round(rssiPct(o.rssi));
      const batt = o.battery_ok == null ? '—' : o.battery_ok ? tr('console.batt_ok', 'OK') : tr('console.batt_low', 'LOW');
      root.innerHTML = `
        <div class="col" style="flex:1">
          <div class="grid2" style="flex:1">
            ${tile(tr('cards.rssi', 'Signal (RSSI)'),
              `<div class="row" style="margin-top:10px"><span class="big em">${o.rssi ?? '—'}<span class="unit-s">dBm</span></span>
               <span class="sub em">${pct} %</span></div>
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
```

- [ ] **Step 3: Verify**

Open both pages. Expected: the sun timeline's *now* marker sits between sunrise and sunset during the day and outside the coloured band before dawn; times match `/api/solar`. The status page's RSSI matches `/api/latest`, and stopping the service flips "Connection" to "reconnecting" within seconds.

- [ ] **Step 4: Commit**

```bash
git add web/static/js/console.js
git commit -m "feat: console sun & moon and status pages"
```

---

### Task 8: `/api/system` endpoint

**Files:**
- Create: `web/system_info.py`
- Modify: `web/app.py`
- Test: `tests/test_system_info.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `web.system_info.read_system() -> dict` and `GET /api/system` returning that dict. Field names are fixed by the spec: `cpu_temp_c`, `load`, `cpu_count`, `mem_total_mb`, `mem_used_mb`, `disk_total_gb`, `disk_used_gb`, `uptime_s`, `core_clock_hz`, `core_volts`, `throttle` (with `now` and `ever` sub-objects, each carrying `undervoltage`, `arm_freq_capped`, `throttled`, `soft_temp_limit`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_system_info.py`:

```python
"""Tests for the host telemetry helper.

The throttle word packs two different questions into one integer: bits 0-3 are
"right now", bits 16-19 are "has happened since boot". Merging them produces a
console that reports throttling when nothing is being throttled, so the split
is what these tests are mostly about.
"""
import pytest

from web import system_info


def test_throttle_word_splits_now_from_ever():
    # 0xe0000 = bits 17, 18, 19 -> happened since boot, nothing current.
    result = system_info.parse_throttled(0xE0000)
    assert result["now"] == {
        "undervoltage": False, "arm_freq_capped": False,
        "throttled": False, "soft_temp_limit": False,
    }
    assert result["ever"] == {
        "undervoltage": False, "arm_freq_capped": True,
        "throttled": True, "soft_temp_limit": True,
    }


def test_throttle_word_reports_current_undervoltage():
    result = system_info.parse_throttled(0x50005)
    assert result["now"]["undervoltage"] is True
    assert result["now"]["soft_temp_limit"] is False
    assert result["now"]["throttled"] is True
    assert result["ever"]["undervoltage"] is True
    assert result["ever"]["throttled"] is True


def test_throttle_word_all_clear():
    result = system_info.parse_throttled(0x0)
    assert not any(result["now"].values())
    assert not any(result["ever"].values())


def test_read_system_returns_every_key_even_off_a_pi(monkeypatch):
    # No vcgencmd, no thermal zone: the endpoint must still answer.
    monkeypatch.setattr(system_info, "_vcgencmd", lambda *a: None)
    monkeypatch.setattr(system_info, "_read_first_line", lambda p: None)
    data = system_info.read_system()
    for key in ("cpu_temp_c", "load", "cpu_count", "mem_total_mb", "mem_used_mb",
                "disk_total_gb", "disk_used_gb", "uptime_s",
                "core_clock_hz", "core_volts", "throttle"):
        assert key in data
    assert data["cpu_temp_c"] is None
    assert data["throttle"] is None
    assert data["cpu_count"] >= 1


def test_vcgencmd_result_is_cached(monkeypatch):
    calls = []

    def fake_run(*args):
        calls.append(args)
        return "throttled=0x0"

    monkeypatch.setattr(system_info, "_run_vcgencmd", fake_run)
    system_info._CACHE.clear()
    system_info._vcgencmd("get_throttled")
    system_info._vcgencmd("get_throttled")
    assert len(calls) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_system_info.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.system_info'`.

- [ ] **Step 3: Write the module**

Create `web/system_info.py`:

```python
"""Host telemetry for the touch console's System page.

Every field is optional. On a development machine that is not a Raspberry Pi
the Pi-specific values are None and the page shows a dash — the endpoint never
fails as a whole because one part is unavailable.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

VCGENCMD = "/usr/bin/vcgencmd"
CACHE_TTL_S = 5.0

# Bit -> field name. The two halves answer different questions and are kept
# apart all the way to the display.
_BITS_NOW = {0: "undervoltage", 1: "arm_freq_capped", 2: "throttled", 3: "soft_temp_limit"}
_BITS_EVER = {16: "undervoltage", 17: "arm_freq_capped", 18: "throttled", 19: "soft_temp_limit"}

_CACHE: dict[str, tuple[float, str | None]] = {}


def parse_throttled(word: int) -> dict[str, dict[str, bool]]:
    """Split the get_throttled word into its 'now' and 'ever' halves."""
    return {
        "now": {name: bool(word & (1 << bit)) for bit, name in _BITS_NOW.items()},
        "ever": {name: bool(word & (1 << bit)) for bit, name in _BITS_EVER.items()},
    }


def _run_vcgencmd(*args: str) -> str | None:
    if not os.path.exists(VCGENCMD):
        return None
    try:
        out = subprocess.run([VCGENCMD, *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _vcgencmd(*args: str) -> str | None:
    """Cached vcgencmd call — a visible System page must not spawn a
    subprocess per request."""
    key = " ".join(args)
    hit = _CACHE.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < CACHE_TTL_S:
        return hit[1]
    value = _run_vcgencmd(*args)
    _CACHE[key] = (now, value)
    return value


def _read_first_line(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.readline().strip()
    except OSError:
        return None


def _number_after(text: str | None, sep: str) -> float | None:
    if not text or sep not in text:
        return None
    tail = text.split(sep, 1)[1]
    digits = "".join(c for c in tail if c.isdigit() or c in ".-")
    try:
        return float(digits)
    except ValueError:
        return None


def read_system() -> dict:
    raw_temp = _read_first_line("/sys/class/thermal/thermal_zone0/temp")
    cpu_temp = round(int(raw_temp) / 1000, 1) if raw_temp and raw_temp.lstrip("-").isdigit() else None

    try:
        load = [round(v, 2) for v in os.getloadavg()]
    except OSError:
        load = None

    # Each field is guarded on its own, narrowly. A malformed line raises
    # IndexError or ValueError, not just OSError, and disk_usage can fail too —
    # any of them unguarded would break the promise that one unreadable value
    # degrades to null instead of failing the whole request.
    mem_total = mem_available = None
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    mem_available = int(line.split()[1]) // 1024
    except (OSError, IndexError, ValueError):
        mem_total = mem_available = None

    try:
        usage = shutil.disk_usage("/")
    except OSError:
        usage = None

    uptime_line = _read_first_line("/proc/uptime")
    try:
        uptime_s = int(float(uptime_line.split()[0])) if uptime_line else None
    except (IndexError, ValueError):
        uptime_s = None

    throttle_out = _vcgencmd("get_throttled")
    throttle = None
    if throttle_out and "=" in throttle_out:
        try:
            throttle = parse_throttled(int(throttle_out.split("=", 1)[1], 16))
        except ValueError:
            throttle = None

    clock = _number_after(_vcgencmd("measure_clock", "arm"), "=")
    volts = _number_after(_vcgencmd("measure_volts", "core"), "=")

    return {
        "cpu_temp_c": cpu_temp,
        "load": load,
        "cpu_count": os.cpu_count() or 1,
        "mem_total_mb": mem_total,
        "mem_used_mb": (mem_total - mem_available) if (mem_total and mem_available) else None,
        "disk_total_gb": round(usage.total / 1e9, 1) if usage else None,
        "disk_used_gb": round(usage.used / 1e9, 1) if usage else None,
        "uptime_s": uptime_s,
        "core_clock_hz": int(clock) if clock else None,
        "core_volts": round(volts, 3) if volts else None,
        "throttle": throttle,
    }
```

- [ ] **Step 4: Add the route**

In `web/app.py`, below the `/api/forecast` handler, add:

```python
@app.get("/api/system")
async def system():
    from . import system_info

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, system_info.read_system)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_system_info.py -v`
Expected: PASS, 5 tests.

Then the whole suite: `.venv/bin/pytest tests/ -v`
Expected: PASS.

- [ ] **Step 6: Verify the endpoint against the real thing**

With the service running: `curl -s http://127.0.0.1:8000/api/system`

Expected: valid JSON with all eleven keys. On a machine without `vcgencmd`, `throttle`, `core_clock_hz` and `core_volts` are `null` and the request still returns 200.

- [ ] **Step 7: Commit**

```bash
git add web/system_info.py web/app.py tests/test_system_info.py
git commit -m "feat: /api/system host telemetry with now/ever throttle split"
```

---

### Task 9: System page

**Files:**
- Modify: `web/static/js/console.js`

**Interfaces:**
- Consumes: `/api/system` from Task 8, `tile()`, `state`.
- Produces: nothing new.

- [ ] **Step 1: Poll only while the page is visible**

Append to `web/static/js/console.js`, after `startPolling()`:

```js
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
```

Then call it at the end of `renderCurrent()`:

```js
  syncSystemPolling();
```

- [ ] **Step 2: Render the System page**

Replace the `system` entry in `PAGES`:

```js
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
```

- [ ] **Step 3: Verify polling really is scoped to the page**

With the service running, watch the access log while the console cycles. Expected: `/api/system` is requested only during the 15 s the System page is on screen, not continuously.

Compare the displayed values against the host: `uptime`, `free -m`, `df -h /`, `cat /sys/class/thermal/thermal_zone0/temp`.

- [ ] **Step 4: Commit**

```bash
git add web/static/js/console.js
git commit -m "feat: console system page, polled only while visible"
```

---

### Task 9b: Locale keys for the console

Added during execution — the original plan had no task for it, and review found
the gap. Every console string calls `tr('console.…')` or `tr('cards.…')`, but
those keys were never added to the locale files, so `?lang=de` produced a
console that stayed English while its compass points localised. The spec
requires the console to read the same locale files as the dashboard.

**Files:**
- Modify: `web/static/i18n/en.json`
- Modify: `web/static/i18n/de.json`

**Interfaces:**
- Consumes: every `tr()` key used by `web/static/js/console.js`.
- Produces: nothing in code.

- [ ] **Step 1: Inventory the keys**

Collect every key from every `tr(path, fallback)` call in `console.js` —
including the calls inside template literals and inside the rose builder, and
the dynamic `console.<page id>` titles. For each, record the inline English
fallback the code already passes.

- [ ] **Step 2: Add them to both files**

Add only the missing keys, using the code's own fallback as the English value
so English behaviour is unchanged. Leave `cards.*` keys that already exist
untouched — the dashboard owns their wording. Preserve each file's existing
indentation and ordering conventions; do not reformat.

- [ ] **Step 3: Prove the coverage, and prove the check**

Write a throwaway script that walks every key used by `console.js` and asserts
it resolves in both files. Run it. Then delete one key on purpose and confirm
the script reports it missing — a checker that has never failed proves nothing.
Restore the key and re-run.

- [ ] **Step 4: Confirm additions only**

`git diff` on both files must show additions (plus the trailing commas that
additions require), never a changed value.

- [ ] **Step 5: Commit**

```bash
git add web/static/i18n/en.json web/static/i18n/de.json
git commit -m "i18n: add the console's locale keys"
```

---

### Task 10: Kiosk installation

**Files:**
- Create: `weather-console.service`
- Create: `install-console.sh`
- Modify: `README.md`

**Interfaces:**
- Consumes: `/console/` from Task 3.
- Produces: an installed, enabled systemd unit.

- [ ] **Step 1: Write the systemd unit**

Create `weather-console.service`:

```ini
[Unit]
Description=Weather touch console (labwc + Chromium kiosk)
After=davis-weather.service
Wants=davis-weather.service

[Service]
Type=simple
User=davis
PAMName=login
TTYPath=/dev/tty1
Environment=XDG_RUNTIME_DIR=/run/user/%U
Environment=WLR_LIBINPUT_NO_DEVICES=0
# Chromium must not win the race against uvicorn at boot: without this the
# display comes up showing a connection error instead of the console.
ExecStartPre=/bin/sh -c 'for i in $(seq 1 60); do curl -sf -o /dev/null http://127.0.0.1:8000/console/ && exit 0; sleep 2; done; exit 1'
ExecStart=/usr/bin/labwc -s '/usr/bin/chromium \
  --kiosk \
  --ozone-platform=wayland \
  --overscroll-history-navigation=0 \
  --disable-session-crashed-bubble \
  --disable-infobars \
  --noerrdialogs \
  --check-for-update-interval=31536000 \
  --app=http://127.0.0.1:8000/console/?lang=en'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write the installer**

Create `install-console.sh`:

```bash
#!/usr/bin/env bash
# install-console.sh — optional kiosk display for the touch console.
#
# Run from the repository root on the machine with the touch panel attached:
#   sudo ./install-console.sh [--lang de] [--rotate 270]
#
# Separate from deploy.sh on purpose: a touch display is optional hardware,
# and an installation without one must not be asked to install a kiosk.

set -euo pipefail

SERVICE_USER=davis
SERVICE_FILE=weather-console.service
LANG_CODE=en
ROTATE=270
OUTPUT=DSI-1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --lang)   LANG_CODE="$2"; shift 2 ;;
        --rotate) ROTATE="$2";    shift 2 ;;
        --output) OUTPUT="$2";    shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root or with sudo." >&2
    exit 1
fi

if [[ ! -f pyproject.toml ]]; then
    echo "ERROR: run this script from the repository root." >&2
    exit 1
fi

if ! id "$SERVICE_USER" > /dev/null 2>&1; then
    echo "ERROR: user '$SERVICE_USER' does not exist — run deploy.sh first." >&2
    exit 1
fi

echo "Installing labwc, wlr-randr and chromium …"
apt-get update -qq
apt-get install -y --no-install-recommends labwc wlr-randr chromium

# vcgencmd needs /dev/vcio_gencmd, which udev grants to group 'video'.
# Without this the System page's throttling tiles stay empty.
echo "Adding '$SERVICE_USER' to group 'video' …"
usermod -aG video "$SERVICE_USER"

echo "Configuring a ${ROTATE}° output transform for $OUTPUT …"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "/home/$SERVICE_USER/.config/labwc"
cat > "/home/$SERVICE_USER/.config/labwc/autostart" <<EOF
wlr-randr --output $OUTPUT --transform $ROTATE &
EOF
chown "$SERVICE_USER:$SERVICE_USER" "/home/$SERVICE_USER/.config/labwc/autostart"
chmod +x "/home/$SERVICE_USER/.config/labwc/autostart"

echo "Installing systemd service …"
sed "s|?lang=en|?lang=$LANG_CODE|" "$SERVICE_FILE" > "/etc/systemd/system/$SERVICE_FILE"
systemctl daemon-reload
systemctl enable "$SERVICE_FILE"
systemctl restart "$SERVICE_FILE"

echo ""
echo "Console kiosk installed."
echo "  systemctl status $SERVICE_FILE"
echo "  journalctl -u $SERVICE_FILE -f"
echo ""
echo "The service user was added to group 'video'; that takes effect on its"
echo "next login, so reboot before judging the throttling tiles."
```

Then: `chmod +x install-console.sh`

- [ ] **Step 3: Document it**

Add to `README.md`, after the "Dashboard" section:

```markdown
### Touch console

A second front end at `/console/` is sized for a 1280×720 touch panel (the
official 7" Touch Display 2 mounted landscape): seven pages — now, rain, wind,
sun & moon, indoor, status, system — cycling every 15 seconds, with swipe and
tap-to-jump. It uses no external resources, so it keeps working with the
internet disconnected.

To run it full-screen on the machine itself:

```bash
sudo ./install-console.sh --lang en --rotate 270
```

This installs labwc and Chromium, adds the service user to group `video` (so
`vcgencmd` can report throttling), configures the display rotation and enables
`weather-console.service`. `deploy.sh` is unaffected — the kiosk is optional.

Design notes: `docs/specs/2026-07-25-touch-console-design.md`.
```

- [ ] **Step 4: Check the installer without running it**

Run: `bash -n install-console.sh && shellcheck install-console.sh || true`
Expected: no syntax errors.

- [ ] **Step 5: Commit**

```bash
git add weather-console.service install-console.sh README.md
git commit -m "feat: optional kiosk installation for the touch console"
```

---

### Task 11: On-device verification

No code. This task is the gate before the pull request, and it is deliberately
built out of ways to use the thing wrongly — the happy path was already proven
by Tasks 3–9.

**Files:** none.

**Interfaces:**
- Consumes: everything.
- Produces: a verification record pasted into the pull request body.

- [ ] **Step 1: Deploy and install**

On the machine with the panel: `sudo ./deploy.sh` then `sudo ./install-console.sh --lang de --rotate 270`, then reboot.

- [ ] **Step 2: Check the rotation with a finger, not with the eyes**

Tap each of the four corner dots in the indicator row and confirm the page that
appears is the one under the finger. A rotated image over an unrotated touch
surface looks entirely correct and is wrong — this is the only test that tells
them apart.

Expected: every tap lands where it was aimed.

- [ ] **Step 3: Prove the way back**

- Reboot. Expected: the console appears unattended, no login prompt, no error page.
- `sudo systemctl restart davis-weather` with the kiosk running. Expected: values dim briefly, then recover on their own. No reload, no error page.
- `sudo systemctl restart weather-console`. Expected: kiosk returns without a "restore pages?" bubble.

- [ ] **Step 4: Disconnect the internet**

Pull the uplink (not the LAN) or block egress. Expected: every one of the seven
pages renders unchanged. This tests the claim that the console has no external
dependency instead of believing it.

- [ ] **Step 5: Swipe hard**

Swipe left and right repeatedly and quickly, including from the screen edges.
Expected: pages change; Chromium never navigates back; no rubber-banding, no
zoom, no text selection, no context menu on a long press.

- [ ] **Step 6: Interrupt the carousel**

Tap a dot, wait 60 s without touching anything. Expected: rotation resumes from
the page on screen, not from the one that was showing when it was interrupted.

- [ ] **Step 7: Force the stale state**

`sudo systemctl stop davis-weather`, wait 10 minutes. Expected: values grey out,
header reads "no data", and the wind pointer is **grey, not red**. Then start
the service again and confirm recovery.

- [ ] **Step 7b: Check the three things only the hardware can answer**

These came out of the code reviews during execution. Each is invisible to any
static check, so they are listed here rather than left to chance.

- **Clock timezone.** The console's clock comes from `toLocaleTimeString`
  without an explicit timezone, so it renders in the host's system timezone —
  not the offset carried in the API's ISO strings. Compare the console's clock
  against `date` on the machine and against the solar times, and fix the host's
  timezone if they disagree.
- **The System page's CORE column** is 300 px wide and holds two chips (clock
  and voltage). Their combined width may exceed it. Because the body is
  `overflow: hidden`, an overflow would clip silently rather than announce
  itself — look at that column specifically.
- **The Rain page's "last tip"** only shows a value once a tip has been
  recorded since the service started. If it reads `—`, confirm that is because
  it has not rained, not because the field is broken.

- [ ] **Step 8: Record what could not be tested**

The calm state cannot be produced on demand. Note it in the pull request as
"pending — check on the next windless day" rather than ticking it off.

- [ ] **Step 9: Open the pull request**

```bash
git push -u origin feature/touch-console
gh pr create --title "Touch console: a 1280x720 front end at /console/" --body "<verification record from steps 2-8>"
```

Stop at the merge gate. Do not merge.

---

## Deferred (not in this plan)

Listed in the spec §11 and deliberately out of scope: night dimming via
`panel_backlight@1`, radar as a tap-reachable page outside the rotation, and
per-page dwell times.
