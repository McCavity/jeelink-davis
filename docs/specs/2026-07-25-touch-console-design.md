# Touch console — design

> Date: 2026-07-25
> Status: approved, not yet implemented
> Scope: a second, resolution-optimised front end for the official Raspberry Pi
> Touch Display 2, served by the existing FastAPI app and shown in a browser
> kiosk on the same machine.

## 1. Goal

The dashboard at `/` is built for a desktop browser: many cards, charts, a map,
a history browser. On a 7" panel it is unreadable at a glance.

This design adds a **second front end** at `/console/` that trades information
density for legibility: fewer values per screen, more screens, cycled
automatically — the way a dedicated weather console works. It is a sibling of
the dashboard, not a replacement, and both are served by the same process.

## 2. Measured constraints

These numbers were measured on a Raspberry Pi 4 Model B with the official
7" Touch Display 2, not assumed. They drive the layout.

| Property | Value | How it was determined |
|---|---|---|
| Panel native mode | 720×1280 (portrait) | `DSI-1` connector mode, `fb0` virtual size |
| Viewing orientation | landscape | panel mounted rotated; console readable at `fbcon=rotate:3` |
| Effective viewport | **1280×720** | text console reports 45 rows × 160 columns at the 8×16 console font |
| Touch controller | Goodix capacitive, I²C | `/proc/bus/input/devices` |
| Pixel density along the long edge | ≈ 8.25 px/mm | 1280 px over the ≈ 155 mm active area |

Two consequences that are easy to get wrong:

- **`fbcon=rotate:3` rotates only the text console.** A graphical stack knows
  nothing about it. The 270° rotation has to be solved a second time, in the
  compositor.
- **Image rotation and touch rotation are separate.** A rotated image with an
  unrotated touch surface looks correct and behaves wrongly. See §10.

## 3. Non-goals

- **No radar page.** Leaflet plus preloaded DWD tiles is the only expensive
  page; in an auto-cycling carousel it would rebuild every couple of minutes on
  a 2 GB device, and it needs the internet. Radar stays on the dashboard.
- **No charts, no forecast.** Both were considered and dropped. With them gone
  the console needs neither Chart.js nor Leaflet nor a CDN — see §4.
- **No night dimming in v1.** The panel exposes `panel_backlight@1`, so it can
  be added later. Left out until it proves necessary.
- **No language switch on screen.** See §7.

## 4. Architecture

### 4.1 Files

| File | Change | Purpose |
|---|---|---|
| `web/static/js/weather-core.js` | new | Pure functions shared by both front ends. No DOM, no state. |
| `web/static/console.html` | new | The console document. Hand-written CSS, no external libraries. |
| `web/static/js/console.js` | new | Carousel, touch handling, the seven page renderers. |
| `web/static/index.html` | edited | Loses the extracted functions, loads `weather-core.js`. Otherwise untouched. |
| `web/app.py` | edited | Adds `GET /console/` (with `GET /console` redirecting to it) and `GET /api/system`. |
| `install-console.sh` | new | Optional kiosk installation. Separate from `deploy.sh`. |
| `weather-console.service` | new | systemd unit for the kiosk. |
| `tests/test_weather_core.mjs` | new | Node test for the shared core. |

`deploy.sh` is deliberately **not** modified. A touch display is optional
hardware; an installation without one must not be asked to install a kiosk.

### 4.2 The shared core

Reading the existing code changed this section. Not everything on the original
list is pure: four functions reach into module state or return Tailwind class
names, and one front end has no Tailwind. The extraction therefore splits three
ways.

**Move unchanged** — genuinely pure: `calcDewPoint`, `calcFeelsLike`, `fmt`,
`rssiPct`, `lerpHex`, `tempColorHex`, `TEMP_COLOR_STOPS`, `moonEmoji`,
`TREND_ARROWS`, `RAIN_ACTIVE_SECS`.

**Move with a signature change** — these depend on the caller's language, which
the core must not own:

| Before | After | Why |
|---|---|---|
| `degToCompass(deg)` | `degToCompass(deg, lang)` | reads the module-level `COMPASS` |
| `moonPhaseName(phase)` | `moonPhaseKey(phase)` returning e.g. `'waxing_gibbous'` | called `tr()` internally; the caller now translates |
| `humanElapsed(ms)` | `humanElapsed(ms, tr)` | same — the translator is passed in |
| `timeOf(iso)` | `timeOf(iso, locale)` | had `'de-DE'` hard-coded |

**Stay in the dashboard** — Tailwind-specific, meaningless to the console:
`rssiColor`, `tempColor`, `TREND_COLORS`.

Plus two additions that change behaviour for **both** front ends:

- **`COMPASS` becomes language-aware.** It is currently a hard-coded English
  array, so the dashboard shows `ENE` even in German mode. German uses `O` for
  east: `N, NNO, NO, ONO, O, OSO, SO, SSO, S, SSW, SW, WSW, W, WNW, NW, NNW`.
  This is a bug fix that falls out of the extraction.
- **`formatBearing(deg)` renders a bearing of 0 as `360°`**, following the
  convention in which north is spoken as "three-six-zero". Applies to the wind
  rose card, its centre readout and the dashboard.

An earlier draft called the extraction "mechanical: move, delete, add a script
tag". Reading the code disproved that for the four functions in the table
above, so each of their call sites in the dashboard has to be updated too. The
rest is mechanical. No function gains new behaviour.

### 4.3 Data flow

The console consumes existing endpoints:

| Endpoint | Feeds | Cadence |
|---|---|---|
| `/api/stream` (SSE) | outdoor values on pages 1–3, 6 | push |
| `/api/history/today` | today's min/max and rain total | every 60 s |
| `/api/rain/totals` | week / month / year rain | every 60 s |
| `/api/indoor` | indoor temperature, humidity, pressure, trend | every 30 s |
| `/api/solar` | dawn / sunrise / noon / sunset / dusk / moon | every 15 min |
| `/api/system` | page 7 — **new endpoint**, see §4.4 | every 10 s, **only while page 7 is visible** |

### 4.4 `GET /api/system`

Returns host telemetry for page 7. Every field is nullable: on a development
machine that is not a Raspberry Pi the Pi-specific fields are `null` and the
page shows `—` rather than failing.

```json
{
  "cpu_temp_c": 57.8,
  "load": [0.55, 0.44, 0.39],
  "cpu_count": 4,
  "mem_total_mb": 1845,
  "mem_used_mb": 315,
  "disk_total_gb": 29.0,
  "disk_used_gb": 6.5,
  "uptime_s": 59460,
  "core_clock_hz": 1800457088,
  "core_volts": 0.926,
  "throttle": {
    "now":  { "undervoltage": false, "arm_freq_capped": false, "throttled": false, "soft_temp_limit": false },
    "ever": { "undervoltage": false, "arm_freq_capped": false, "throttled": false, "soft_temp_limit": false }
  }
}
```

Sources: `/sys/class/thermal/thermal_zone0/temp`, `/proc/loadavg`,
`/proc/meminfo`, `statvfs`, `/proc/uptime` — all readable by the unprivileged
service user. `throttle`, `core_clock_hz` and `core_volts` come from
`vcgencmd`, which needs `/dev/vcio_gencmd`; the stock udev rule grants that to
group `video`, so the service user must be a member (§9). Where `vcgencmd` is
unavailable or fails, those three fields are `null` — the endpoint never fails
as a whole because one part is missing.

The `vcgencmd` result is cached for 5 s so that a visible page 7 cannot spawn a
subprocess per request.

**`now` and `ever` stay separate fields and separate display.** `get_throttled`
packs the current state in bits 0–3 and "has happened since boot" in bits
16–19. Merging them produces a console that reports throttling when nothing is
being throttled.

## 5. Pages

Seven pages, cycled in this order. Each fills exactly 1280×720: a 56 px header,
the body, and a 44 px indicator row. **Nothing scrolls anywhere.** Content that
does not fit is a layout defect and gets fixed as one.

The header carries, left to right: page title, data age with a status dot,
clock, date. The age always refers to the **primary source of the page in
view** — outdoor readings on pages 1–3 and 6, the indoor sensor on page 5, host
telemetry on page 7 — so it never reports one sensor's freshness beside another
sensor's values. The clock is not decoration: a display on a wall that cannot
tell the time wastes its best secondary use.

1. **Now** — temperature dominant (620 of 1280 px, ~196 px digits), with dew
   point and today's min/max; wind, humidity and pressure stacked to the right.
2. **Rain** — rate large, time since the last tip; today / week / month / year
   as four tiles.
3. **Wind** — the rose (§6) on the left, speed, gust and today's maximum right.
4. **Sun & moon** — the day's arc as a horizontal timeline with a *now* marker,
   plus moon phase and day length.
5. **Indoor** — BME280 temperature and humidity large, pressure with trend.
6. **Status** — RSSI with today's range, ISS battery, time of the last reading,
   connection state.
7. **System** — CPU temperature, load, memory, card usage, uptime, throttling
   (`now` and `since boot` shown separately), core clock and voltage.

Page 7 overlaps deliberately with an external monitoring dashboard, if one
exists. The console is the glance in passing; history and alerting belong
elsewhere.

## 6. Wind rose

The rose is the one component with a detailed visual specification, because
"compass rose" underdetermines it.

**Two concentric scales**, as on a real compass card. A single ring cannot
carry both a label every 10° and emphasis at 22.5°, since 22.5 is never a
multiple of 10.

*Outer ring — degree scale* (SVG `viewBox` 0 0 600 600, centre 300,300, outer
radius 246):

| Step | Tick length | Weight |
|---|---|---|
| every 1° | 6 px | hairline, darkest |
| every 5° | 10 px | mid |
| every 10° | 16 px | bright, plus a three-digit label |

Labels sit at radius 213, oriented radially and flipped in the lower half so
none appears upside down. **North is labelled `360`, not `000`.**

*Inner ring — point scale* (outer radius 196):

| Step | Tick | Label |
|---|---|---|
| 90° | longest, brightest, 3 px | cardinal letter, 30 px bold |
| 45° | medium, 2 px | ordinal, 20 px |
| 22.5° | short, 1.5 px | none — the ticks alone keep the card readable |

Cardinal letters follow the language (`O` in German, `E` in English).

*Pointer.* A ring segment ~26° wide running on a track at radius 270, outside
both scales, with a radial line inward to radius 208. The centre stays free for
the bearing (`072°`, 74 px) and the point abbreviation (`ONO` / `ENE`, 40 px).

*Pointer states.* Three, not two:

| State | Colour | Meaning |
|---|---|---|
| moving | blue | direction changed recently |
| calm | red | data is fresh, but the vane has not moved |
| stale | grey | no data at all |

The third state is not cosmetic. Without it, "calm" and "the station has gone
silent" look identical, and the console would report a flat calm while nothing
has been received for an hour.

**Calm threshold: a 10-minute mean wind speed of 0.2 m/s or less.**

This is not an invented number. WMO defines calm as 0.2 m/s (CIMO guide; see
*The Weather Observer's Handbook*, ch. 9), and it defines wind itself as a
10-minute mean — so the averaging window is the standard one rather than a
guess. For comparison, METAR reports calm below 0.5 m/s (1 kt).

An earlier draft triggered on *direction standing still*. That was the wrong
criterion: a vane in still air is just as likely to wander as to sit, so the
rule caught only half of the cases it was meant to. METAR makes the same
distinction from the other side — it declares direction unusable (`VRB`) below
1.5 m/s mean speed. Measuring the speed directly is both simpler and standard.

One honesty note: the WMO figure assumes a 10 m mast in the open. A station
sheltered by buildings or trees is not comparable to a synoptic observation in
absolute terms. What is borrowed here is the threshold's definition, not a
claim that the readings are WMO-grade.

*A note on what the 1° graduation can and cannot do.* At 560 px the rose is
about 68 mm across, so its circumference is ≈ 213 mm and consecutive 1° ticks
are ≈ 0.59 mm apart. From across the room this reads as a fine band rather than
a countable scale; at arm's length it resolves. That is how real instruments
behave, and it is intended — but it is a texture, not a readout.

## 7. Behaviour

**Carousel.** 15 s per page, wrapping; a full cycle takes 1:45. Automatic
advance cross-fades over 180 ms. A **swipe** instead slides the page out in the
swipe direction over 200 ms, so the gesture feels like a gesture.

**Touch takeover.** Any touch — tap or swipe — pauses rotation for 60 s.
Rotation then resumes **from the page currently visible**, not from where it
was interrupted.

**Navigation.** Horizontal swipe moves one page; the indicator row doubles as
direct access, each dot a 44×44 px target.

**Gesture hygiene**, all of it load-bearing:

- `touch-action: none` on the stage, and Chromium started with
  `--overscroll-history-navigation=0`. Without both, a horizontal swipe
  navigates back instead of turning the page.
- A swipe counts from 60 px of horizontal travel at under 45°; anything less is
  a tap.
- No text selection, no context menu on long press, no double-tap zoom, no
  visible cursor.

**Language.** The console is fixed to one language chosen at load and reads the
same `i18n/de.json` / `i18n/en.json` as the dashboard. It defaults to English,
matching the dashboard's default; another language is one query parameter away
(`/console/?lang=de`), and the kiosk installation writes that parameter into
the URL it launches. This keeps a language switcher off a screen that has no
room for one.

**Nightly reload.** Once at 04:00 the page reloads — but **only if a request to
`/api/latest` succeeds first**. Without that guard, the reload is precisely the
mechanism that leaves an error page on the wall whenever the service happens to
be down at 04:00.

## 8. Degradation

**Ageing values**, evaluated **per source** — outdoor arrives by SSE, indoor is
polled, and they age independently:

| Age | Display |
|---|---|
| < 90 s | normal; header shows "N s ago" |
| 90 s – 10 min | values dimmed, header dot amber, "N min ago" |
| > 10 min | values grey, "no data since HH:MM", wind pointer grey |

**Connection loss.** The SSE client reconnects with backoff, as the dashboard
already does. The last values stay on screen and visibly age. Critically, the
page does **not** reload: a dead backend then costs the data stream, not the
document, and there is no white error page on the wall.

**Boot ordering.** The kiosk unit runs `After=davis-weather.service` with an
`ExecStartPre` that polls the service until it answers 200 (up to ~120 s).
Without it, Chromium reliably wins the race at boot and shows a connection
error instead of the console.

**Crash recovery.** `Restart=always` on the unit. Chromium runs with
`--disable-session-crashed-bubble --noerrdialogs` so that no "restore pages?"
prompt parks itself over the display after a restart.

**Clock honesty.** The clock is the host's system time; if NTP drifts, it lies.
The age indicators are computed from server timestamps and stay self-consistent
regardless.

## 9. Kiosk deployment

Installed by `install-console.sh`, separately from `deploy.sh`:

1. Install `labwc` (+ `wlr-randr`) and `chromium` if absent. `labwc` is the
   compositor Raspberry Pi ships, which makes it the best-supported path for
   this panel; touch input follows the output transform in wlroots, so image
   and touch rotate together.
2. Add the service user to group `video` — the same pattern `deploy.sh` already
   uses for `dialout` and `i2c`. Without it `vcgencmd` cannot open
   `/dev/vcio_gencmd` and the throttling tiles stay empty.
3. Configure a 270° output transform for the DSI output.
4. Install and enable `weather-console.service`, which starts labwc on the
   console TTY and, inside it, Chromium with:
   `--kiosk --ozone-platform=wayland --overscroll-history-navigation=0
   --disable-session-crashed-bubble --noerrdialogs --disable-infobars`
   pointing at **`http://127.0.0.1:8000/console/`**.

The kiosk targets the loopback address, not a public URL. The browser and the
service run on the same machine; routing a wall display's every request out
through a tunnel and back adds an internet dependency to a device that has none
of its own.

## 10. Verification

**In the repository**

- `tests/test_weather_core.mjs`, run with `node --test`, calibrated against
  values known independently of the implementation: dew point at 32.5 °C / 28 %
  RH must be 11.6 °C; `formatBearing(0)` must be `360°`; `degToCompass(72)` must
  be `ONO` in German and `ENE` in English.
- The test must be seen **failing** once, by deliberately breaking a formula,
  before it is trusted. A test that has never been red is uncalibrated.
- The existing pytest suite must stay green; the extraction touches no Python.
- Dashboard regression: same values before and after the extraction, no
  JavaScript errors in the browser console.

**On the device — exercised against intent, not along the happy path**

1. **Check rotation with a finger, not with the eyes.** Tap a corner and see
   where the hit lands. A rotated image over an unrotated touch surface looks
   entirely correct and is wrong.
2. **The way back counts.** Reboot: does the kiosk come up unattended? Restart
   the weather service under a running kiosk: does the page recover?
3. **Disconnect the internet.** The claim "this console has no external
   dependency" gets tested, not believed.
4. **Swipe** and confirm Chromium does not navigate back.
5. **Touch takeover:** tap, wait 60 s, confirm rotation resumes from the visible
   page.
6. **Produce the stale state deliberately** by stopping the weather service, and
   confirm the values grey out and the pointer turns **grey, not red**. That
   confusion is the whole reason the third state exists; untested it is only a
   claim.
7. **The calm state cannot be produced on demand.** It is recorded as "check on
   the next windless day", not ticked off.

## 11. Deferred

- Night dimming via `panel_backlight@1`.
- Radar as a tap-reachable page outside the rotation.
- Per-page dwell times.
