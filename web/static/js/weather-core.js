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

  // Davis packets are partial by design (one carries wind and rssi but no
  // temperature, the next carries temperature but no humidity): a naive
  // Object.assign lets a field's `null` (not carried this packet) overwrite
  // a real previous value. Keys whose incoming value is null/undefined keep
  // whatever prev had; everything else — including 0 and false — is a value,
  // not an absence, and is taken from incoming. prev may be null (first
  // packet ever).
  function mergeReading(prev, incoming) {
    const out = Object.assign({}, prev);
    for (const [key, val] of Object.entries(incoming)) {
      if (val !== null && val !== undefined) out[key] = val;
    }
    return out;
  }

  // rain_secs is the interval between the last two tips, not the age of the
  // last tip — so a rate computed from it must be zeroed once the station
  // has gone quiet for RAIN_ACTIVE_SECS since that last tip, or it reports a
  // rate indefinitely.
  function rainRate(rainSecs, msSinceTip) {
    if (rainSecs == null || msSinceTip == null) return 0;
    if (rainSecs <= 0) return 0;
    if (msSinceTip >= RAIN_ACTIVE_SECS * 1000) return 0;
    return 720 / rainSecs;
  }

  // rain_tip_count is a wrapping 0-127 counter, not a monotonic total (see
  // web/db.py, which applies the same `+ 128` correction to a negative
  // delta). A decrease therefore reads as a tip too — either the counter
  // wrapped past 127, or the station was genuinely reset, and both cases
  // are a tip having happened since the last known count. prevCount must
  // be known (not null/undefined) before anything can be reported, so a
  // fresh boot never guesses a tip into existence; nextCount must also be
  // present, since null means the field was simply absent from this packet.
  function isNewTip(prevCount, nextCount) {
    if (prevCount == null || nextCount == null) return false;
    return nextCount !== prevCount;
  }

  // /api/latest can answer with a snapshot that is arbitrarily old (the
  // station may have gone silent hours ago) — the timestamp it carries, not
  // the moment it happened to be fetched, is the only honest age reference.
  // Returns null (not NaN, not "now") when the field is missing or the
  // string doesn't parse, so a bad/absent timestamp reads as unknown age
  // rather than as fresh.
  //
  // Two formats reach this function, and only one of them says so:
  //   /api/latest     '2026-04-23T14:30:00+00:00'   — carries its offset
  //   /api/lightning  '2026-08-06 19:01:43.481902'  — UTC, but unmarked
  // The second is what SQLite stores for the indoor and lightning tables. Left
  // to `new Date()`, a string with no zone is read as *local* time, so every
  // event would look two hours old in a German summer — plausible enough to go
  // unnoticed, which is exactly what makes it worth handling in one place
  // rather than at each call site.
  function parseTimestampMs(iso) {
    if (!iso) return null;
    const s = String(iso);
    const hatZone = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(s);
    const ms = new Date(hatZone ? s : s.replace(' ', 'T') + 'Z').getTime();
    return Number.isNaN(ms) ? null : ms;
  }

  // Position of a strike distance on the AS3935's own 1…40 km range, as a
  // percentage — a marker on a scale, not a fill: 40 km is not "more" of
  // anything than 1 km, it is further away.
  //
  // The bounds are the chip's, datasheet DS000385 Table 17 (1 km = overhead,
  // then 5…40 km). Deliberately the same numbers the plausibility gate uses,
  // because a bar drawn on a wider range than the gate admits would leave dead
  // zones no reading can ever reach.
  //
  // Returns null for a missing or unusable value rather than 0 — a marker
  // parked at the left end would read as "directly overhead", which is the
  // most alarming thing this scale can say.
  const DISTANCE_MIN_KM = 1;
  const DISTANCE_MAX_KM = 40;

  function distanceScalePercent(km) {
    if (km == null) return null;
    const v = Number(km);
    if (!Number.isFinite(v)) return null;
    const pct = ((v - DISTANCE_MIN_KM) / (DISTANCE_MAX_KM - DISTANCE_MIN_KM)) * 100;
    return Math.max(0, Math.min(100, pct));
  }

  function rssiPct(dbm) {
    return dbm == null ? 0 : Math.max(0, Math.min(100, ((dbm + 90) / 50) * 100));
  }

  // Local midnight (00:00:00) of the calendar day `iso` describes, in epoch
  // ms — derived from the ISO string's own date and UTC offset, not the
  // runtime's timezone. The Sun & moon page's axis is the local day the
  // solar API described, not whatever zone happens to run the console (or
  // this test). Returns null when `iso` isn't a recognisable
  // YYYY-MM-DDThh:mm:ss(.sss)?(Z|+hh:mm) string, so a malformed timestamp
  // never silently becomes "today" in some other zone.
  function localDayStartMs(iso) {
    if (typeof iso !== 'string') return null;
    const m = /^(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$/.exec(iso);
    if (!m) return null;
    const dayStart = Date.parse(`${m[1]}T00:00:00${m[2]}`);
    return Number.isNaN(dayStart) ? null : dayStart;
  }

  // Position of instant `atMs` on a fixed 24h axis starting at `dayStartMs`,
  // as a percentage clamped to 0..100. A full day is a constant span (it can
  // never be zero, unlike the old dawn->dusk span), but the inputs can still
  // be bad: a missing/unparseable timestamp must render as a safe 0%, never
  // NaN, because a `style="left:NaN%"` attribute is silently dropped by the
  // browser and the marker would just vanish instead of clamping visibly.
  function dayArcPercent(atMs, dayStartMs) {
    if (atMs == null || dayStartMs == null || Number.isNaN(atMs) || Number.isNaN(dayStartMs)) return 0;
    const DAY_MS = 24 * 3600 * 1000;
    return Math.max(0, Math.min(100, ((atMs - dayStartMs) / DAY_MS) * 100));
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
    rssiPct, lerpHex, tempColorHex, mergeReading, rainRate, isNewTip,
    distanceScalePercent, DISTANCE_MIN_KM, DISTANCE_MAX_KM,
    dataAgeState, meanOver, windPointerState, parseTimestampMs,
    localDayStartMs, dayArcPercent,
  };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = WeatherCore;
