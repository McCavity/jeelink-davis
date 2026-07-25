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

test('mergeReading keeps previous values when incoming keys are null/undefined', () => {
  const prev = { temperature: 21.5, humidity: 55, rssi: -60 };
  const incoming = { temperature: null, humidity: undefined, wind_speed: 3.2 };
  assert.deepEqual(WC.mergeReading(prev, incoming), {
    temperature: 21.5, humidity: 55, rssi: -60, wind_speed: 3.2,
  });
});

test('mergeReading keeps incoming 0 and false — values, not absences', () => {
  const prev = { rain_secs: 42, battery_ok: true };
  const incoming = { rain_secs: 0, battery_ok: false };
  assert.deepEqual(WC.mergeReading(prev, incoming), { rain_secs: 0, battery_ok: false });
});

test('mergeReading tolerates a null prev (first packet ever)', () => {
  // No previous value exists to keep for the null key, so it is simply
  // absent from the result — not present-with-a-null-value.
  assert.deepEqual(WC.mergeReading(null, { temperature: 12.3, humidity: null }),
    { temperature: 12.3 });
});

test('mergeReading returns a new object, not a mutated prev', () => {
  const prev = { temperature: 10 };
  const merged = WC.mergeReading(prev, { humidity: 50 });
  assert.notEqual(merged, prev);
  assert.deepEqual(prev, { temperature: 10 });
});

test('rainRate applies the 1800s cutoff since the last tip', () => {
  assert.equal(WC.rainRate(120, 1_799_000), 6);              // 720/120 = 6, still inside
  assert.equal(WC.rainRate(120, 1_800_001), 0);               // just past the cutoff
  assert.equal(WC.rainRate(120, 0), 6);                       // fresh tip
});

test('rainRate returns 0 for null/non-positive inputs', () => {
  assert.equal(WC.rainRate(null, 100), 0);
  assert.equal(WC.rainRate(120, null), 0);
  assert.equal(WC.rainRate(0, 100), 0);
  assert.equal(WC.rainRate(-1, 100), 0);
});

test('rainRate at the exact 1_800_000 ms cutoff', () => {
  // The cutoff check is msSinceTip >= RAIN_ACTIVE_SECS * 1000, so the exact
  // boundary value already falls on the "expired" side, same as one ms past it.
  assert.equal(WC.rainRate(120, 1_800_000), 0);
});

test('isNewTip detects a normal increment', () => {
  assert.equal(WC.isNewTip(4, 5), true);
});

test('isNewTip detects the 127 -> 0 wrap', () => {
  assert.equal(WC.isNewTip(127, 0), true);
});

test('isNewTip reports no tip when the count is unchanged', () => {
  assert.equal(WC.isNewTip(5, 5), false);
});

test('isNewTip reports no tip when nothing was known yet (prev null/undefined)', () => {
  assert.equal(WC.isNewTip(null, 5), false);
  assert.equal(WC.isNewTip(undefined, 5), false);
});

test('isNewTip reports no tip when the field was absent from this packet (next null)', () => {
  assert.equal(WC.isNewTip(5, null), false);
});
