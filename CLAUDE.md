# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

End-to-end system for receiving **Davis Vantage Pro 2** weather station data via a **JeeLink USB receiver** (FT232R UART, auto-detected by USB VID/PID) and presenting it as a live web dashboard with historical data.

Target firmware on the JeeLink: **Davis 0.8e** (compiled Sep 5 2020, RFM69 radio, EU 868 MHz frequencies, firmware switch `b:2`).

The system runs on a **Raspberry Pi** with the JeeLink plugged into a USB port near a window for reliable ISS reception. The dashboard is served on port 8000 (optionally reverse-proxied for external access).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,web]"
```

Copy `config.toml` and adjust for your location before running the web service.

## Commands

```bash
# Run tests (no hardware required)
# `python -m pytest`, not the pytest binary: only the former puts the project
# root on sys.path, and the `web` package is not installed by `pip install -e .`
# (the distribution ships jeelink_davis alone). The binary fails at collection
# with ModuleNotFoundError: No module named 'web'.
.venv/bin/python -m pytest tests/ -v

# Raw hardware sniffer — connects to JeeLink, prints everything for 60 s
.venv/bin/python tools/sniff.py
.venv/bin/python tools/sniff.py --port /dev/ttyUSB0 --baud 57600 --duration 120

# Start the web dashboard (development)
DAVIS_PORT=/dev/ttyUSB0 .venv/bin/uvicorn web.app:app --host 0.0.0.0 --port 8000
# (omit DAVIS_PORT to auto-detect)

# Production service management
sudo systemctl status davis-weather
sudo systemctl restart davis-weather
sudo journalctl -u davis-weather -f
```

## Configuration

`config.toml` (project root, also at `/opt/jeelink-davis/config.toml` in production):

```toml
[station]
name      = "Davis Vantage Pro 2"
latitude  = 51.500000   # decimal degrees, positive = North
longitude = 0.000000    # decimal degrees, positive = East
elevation = 50          # metres above sea level
timezone  = "Europe/London"

[storage]
db_path = "data/readings.db"   # relative to project root, or absolute
```

See `config.toml.example` for a full template including the optional `[influxdb]` and `[mqtt]` sections.

## Architecture

```
jeelink_davis/
├── __init__.py       # public API: DavisStation, WeatherReading
├── connection.py     # serial open/close/readline (JeeLinkConnection)
│                     #   — sleeps _INIT_SETTLE_SECS (5 s) before sending
│                     #     init command to let the radio settle
├── detect.py         # auto-detect JeeLink by USB VID/PID (0403:6001)
├── protocol.py       # stateless line parsers (parse_init_dictionary, parse_values_line)
├── models.py         # WeatherReading dataclass + FIELD_CODE_MAP constant
└── station.py        # high-level iterator: DavisStation.readings() → WeatherReading

web/
├── app.py            # FastAPI application, lifespan, all API endpoints
├── bme280_reader.py  # daemon thread: polls GY-BME280 every 60 s → DB + in-memory cache
├── broadcaster.py    # fan-out to SSE clients; maintains merged latest-reading state
├── config.py         # loads config.toml
├── db.py             # SQLite storage layer (WAL mode, per-thread connections)
│                     #   tables: readings (outdoor), indoor_readings (BME280),
│                     #           lightning_events (AS3935, µs timestamps)
├── lightning_reader.py # daemon threads: AS3935 IRQ callback + event worker
├── plausibility.py   # range gate in front of all three stores + discard counter
│                     #   bounds are the manufacturer's, cited per line
├── reader.py         # daemon thread: drives DavisStation → broadcaster + DB
├── vendor/           # third-party code kept in-tree with its required patches
│   └── DFRobot_AS3935_Lib.py   # DFRobot, MIT; smbus2 + explicit read length
└── static/
    ├── index.html    # single-page dashboard (Chart.js, Tailwind CDN, vanilla JS)
    └── i18n/
        ├── en.json   # English translations
        └── de.json   # German translations

tools/
├── detect.py         # standalone USB VID/PID port finder
└── sniff.py          # raw JeeLink listener for hardware debugging

tests/
├── test_protocol.py  # parser unit tests, no hardware needed
└── test_detect.py    # detect unit tests
```

**Data flow (outdoor)**:
`JeeLinkConnection.read_lines()` → `DavisStation.readings()` → `WeatherReading`
→ `station_reader_thread` → `db.insert_reading()` + `broadcaster.broadcast()`
→ SSE clients (`/api/stream`) + `/api/latest` snapshot

**Data flow (indoor/BME280)**:
`bme280_reader_thread` (60 s poll) → `db.insert_indoor_reading()` + in-memory cache
→ `/api/indoor` snapshot (polled by frontend every 60 s)

**Data flow (lightning/AS3935)**:
GPIO rising edge → short IRQ callback (reads the interrupt source, which is what
re-arms the line) → bounded queue → `lightning-worker` thread →
`db.insert_lightning_event()` + InfluxDB + MQTT (strikes only)
→ `/api/lightning` snapshot (polled by frontend every 60 s)

## InfluxDB Integration (Phase 3)

InfluxDB v2 export is optional. Configure in `config.toml` under `[influxdb]` and set
`INFLUXDB_TOKEN` env var (or add `token =` to the section).

**Measurements written:**
- `outdoor` — all Davis ISS fields; tag: `station_id`
- `indoor`  — BME280 fields: `temperature`, `humidity`, `pressure`
- `lightning` — AS3935: `distance_km`, `energy`, `strike_count`; tag: `kind`.
  Timestamps carry **microseconds**: InfluxDB deduplicates on
  `(measurement, tags, timestamp)`, and at second resolution a burst collapses
  into one point per second. Measured 2026-08-06 — 137 events in 13 distinct
  seconds became 13 points, an undercount with nothing to indicate it.

**Backfill existing data:**
```bash
INFLUXDB_TOKEN=<token> .venv/bin/python tools/backfill_influxdb.py
INFLUXDB_TOKEN=<token> .venv/bin/python tools/backfill_influxdb.py --since 2026-01-01
```

**Grafana Dashboard:** import `docs/grafana-davis-dashboard.json` via
Dashboards → Import. Select the InfluxDB datasource when prompted.
Bucket must exist in InfluxDB: `weather` (org name as configured in `config.toml`).

## Web API endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Single-page dashboard HTML |
| `GET /api/latest` | Latest merged reading (all fields, best known value) |
| `GET /api/stream` | SSE stream, one JSON event per incoming reading |
| `GET /api/solar` | Today's sun times, moon phase, and elevation curve (30-min intervals) |
| `GET /api/forecast` | 5-day forecast from Open-Meteo (cached 30 min) |
| `GET /api/history/day_temp?day=today\|yesterday` | 5-min bucketed temperature for one day |
| `GET /api/history/range?start=YYYY-MM-DD&end=YYYY-MM-DD` | Auto-bucketed history (5 min / 1 h / 6 h / 1 day depending on range width) |
| `GET /api/history/recent?n=50` | Last n raw readings (used to seed the wind chart) |
| `GET /api/history/today` | Today's min/max stats for card display |
| `GET /api/indoor` | Latest BME280 reading (pressure, indoor temp/humidity) + pressure trend |
| `GET /api/lightning` | AS3935: last strike, last event of *any* kind, today's counts per kind; 204 while the table is empty |

The touch console has a **Lightning** page (`/console/`, page 6 of 8). It carries
`age: null` on purpose: every other sensor page dims at 90 s and greys out at
10 minutes, but here silence is the normal state and a greyed page would report
a fault that isn't there, every quiet evening. Its liveness answer is the "last
signal" tile instead, which names the last event of *any* kind.

It also carries the **Kachelmann lightning-strength scale** as a reference
table (0–3 kA "schwacher Brummler" … ≥ 100 kA "wilder Hausrüttler"). That is a
*foreign* scale, shown for interest and labelled as such — it grades peak
current in kA, which lightning-location networks derive from the arrival-time
geometry of several stations. **Our strikes are never placed on it**, and no
arithmetic can put them there: the AS3935's "energy" is, per the datasheet, "a
pure number [that] has no physical meaning", and the chip's distance estimate
comes from that same measurement, so there are not two independent quantities
to solve. A category name attached to an invented mapping would be the §8
artefact wearing someone else's authority.

What the page does show on a scale is the one range the chip documents:
`distance_km` on 1–40 km, as a **marker**, not a fill — 40 km is not "more" of
anything, it is further away. No reading means no marker at all, because a
marker parked at the left end would read as "directly overhead".
| `GET /api/stats/daily\|monthly\|yearly` | Aggregated stats by period |
| `GET /api/system` | Pi health (temp, load, memory, throttling) + `plausibility` discard counter |

## Protocol (firmware 0.8e)

- Init command sent on open: `0,0s r\n` at 57600 baud, no hardware flow control
- **5-second settle delay before sending the init command** — required for the RFM69 radio to reinitialise; skipping it causes missed packets after a restart
- Firmware responds with a banner then `INIT DICTIONARY code=Name,...` — parsed at runtime into `DavisStation.field_dictionary`
- Data lines: `OK VALUES DAVIS <station_id> <code>=<value>,...`
- Not every packet carries every field — the ISS cycles through packet types; e.g. humidity arrives far less frequently than wind

**Field codes**:
1=Temperature, 2=Pressure, 3=Humidity, 4=WindSpeed, 5=WindDirection, 6=WindGust,
7=WindGustRef, 8=RainTipCount, 9=RainSecs, 10=Solar, 11=VoltageSolar,
12=VoltageCapacitor, 14=UV, 20=Channel, 21=Battery(`ok`/else), 22=RSSI(dBm),
255=PacketDump(ignored). Dotted codes `15.x`/`16.x`/`17.x` are zone-indexed soil/leaf sensors.

## Units (confirmed from live EU firmware data)

| Field | Unit | Notes |
|---|---|---|
| Temperature | °C | |
| Humidity | % | |
| WindSpeed / WindGust | m/s | |
| WindDirection | degrees | 0–359 |
| RainTipCount | tips | 7-bit counter (0–127), wraps 127→0, resets only on ISS power cycle |
| RainSecs | seconds | Inter-tip interval; sentinel value < 0 = no rain |
| Solar | W/m² | |
| RSSI | dBm | Typically −60 to −75 with good placement |
| Pressure | hPa | From GY-BME280 indoor sensor (Davis ISS has no barometer) |

**Rain calculations**:
- 0.2 mm per tip (EU/metric bucket)
- Rain rate: `720 / RainSecs` mm/h (derived from 0.2 mm × 3600 s/h ÷ T s)
- Daily rain: `(MAX(tip_count) − MIN(tip_count)) × 0.2` per calendar day (localtime)
- Rate decays to 0 after 30 min without a new tip in the dashboard display

## Production deployment

- **Host**: Raspberry Pi (any hostname)
- **Install path**: `/opt/jeelink-davis/`
- **Service**: `davis-weather.service` (systemd), runs as user `davis`
- **Shutdown**: `--timeout-graceful-shutdown 3` + `TimeoutStopSec=10` to avoid SSE connections delaying reboots
- **Database**: `/opt/jeelink-davis/data/readings.db` (SQLite, WAL mode)
- **Deploy**: copy changed files to `/opt/jeelink-davis/`, then `sudo systemctl restart davis-weather` for Python changes; static files take effect immediately on browser refresh

## Indoor sensor (GY-BME280)

Connected to Raspberry Pi I²C bus 1 at address **0x76**. Polled every 60 s by `web/bme280_reader.py` daemon thread. Readings stored in `indoor_readings` table (SQLite). Timestamps stored as `YYYY-MM-DD HH:MM:SS` UTC so SQLite `datetime()` comparisons work.

**Pressure trend** (`/api/indoor` → `pressure_trend`): compares avg pressure of the last 30 min vs 2–4 h ago. Threshold ±0.5 hPa → `rising` / `falling` / `steady` / `unknown` (insufficient history).

## Lightning sensor (AS3935)

DFRobot AS3935 on I²C bus 1 at **0x03**, IRQ on physical pin 7 (GPIO4), powered
from **3.3 V** — the IRQ swings to VCC level, so 5 V would drive 5 V into a GPIO
input. The IRQ lead runs straight to the Pi and not through the I²C hub, which
carries only VCC/GND/SDA/SCL. Enabled by a `[lightning]` section in
`config.toml`; without it the thread is never started.

Two deployment prerequisites, both handled by `update.sh` once the section
exists — and both found the hard way on 2026-08-06:

- **The `RPi.GPIO` shim comes from apt, not pip**: `sudo apt install
  python3-rpi-lgpio`. Raspberry Pi OS ships it prebuilt; `pip install rpi-lgpio`
  rebuilds the same C extension from source and dies with `error: command
  'swig' failed`. `update.sh` writes a `.pth` so the venv sees the system
  packages (same effect as `--system-site-packages`, appended, so venv packages
  keep precedence) and refuses to continue if the shim is absent.
- **The service user must be in the `gpio` group** — `/dev/gpiochip*` is
  `root:gpio` mode 660, so otherwise the thread dies at `GPIO.setup()` while
  everything else comes up normally. The restart is when systemd re-reads it.

**lgpio creates its notification FIFO (`.lgd-nfy<n>`) in the working directory**,
which for the service is `/opt/jeelink-davis`. If the tree is not owned by the
service user, the thread dies with `FileNotFoundError: '.lgd-nfy-3'` — a message
that names neither GPIO nor a permission. `update.sh` therefore chowns straight
after the file sync, so an abort in a later step cannot leave that state behind.

**Calibrated values, measured on this unit 2026-08-06 — do not change without
measuring again:** 96 pF antenna capacitance (scope on the IRQ pin in LCO mode,
target 31.25 kHz ±3.5 %, bracketed at 88/96/104 pF), watchdog threshold 6,
spike rejection 2, noise floor 2, indoor mode.

**Three traps that cost a day:**
- `reset()` silently returns the watchdog threshold to 2, and the settings do
  not survive a reboot. The reader therefore applies them at start and **reads
  them back off the chip**, refusing to run on a mismatch.
- **I²C traffic on its own bus triggers it.** At the default threshold of 2, the
  neighbouring BME280's 60-second poll was reported as `lightning` at 8 km and
  12 km. At 6 that is gone — 45 minutes in an empty room gave 4 events, all
  `unknown`, zero disturber, zero lightning. That is the reference baseline.
- **A quiet log and a disconnected IRQ wire look identical.** Never read "no
  events" as "no lightning" without evidence the chain fires; `/api/lightning`
  reports the last event of *any* kind for exactly that reason. The piezo test
  stimulus only works at 3–5 cm — a "failed" test at 20 cm measures the tester.

### One detection is not one event

**Do not build anything on the event count.** That much is settled; what
follows is *why*, because the first explanation was wrong.

Measured 2026-08-06, single piezo clicks, **one stimulus per setting**:

| vendor sleep | handler period | events | burst |
|---|---|---|---|
| 30 ms | 36.3 ms | 28 | 972 ms |
| 5 ms | 12.2 ms | 11 | 249 ms |

That was read as "a slower handler yields more events". Measured 2026-08-07,
**five clicks at an unchanged 5 ms handler**, to establish what a single
setting even scatters by:

| click | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| events | 16 | 13 | 12 | **28** | 23 |
| spacing SD | 24.12 | 27.58 | **0.02** | 27.32 | 36.29 |

One setting produces 12 to 28. **The 28-versus-11 difference was the scatter
between two thumbs on a piezo, not an effect of the handler** — two runs of
n=1 cannot separate the two, and were read as though they could.

What survives, and it is the useful part: the handler puts a **floor** under
the spacing, not a beat. The minimum gap is 10.71–10.77 ms in all five bursts
(5 ms sleep plus ~5.75 ms of I²C), while the outliers reach 185 ms. Click 3
looked like a metronome (SD 0.02 ms) only because it happened to sit on the
floor throughout — and a burst of exactly that shape was the 2026-08-06
measurement. Edges arriving while the callback runs are lost (see the
`lightning_reader` docstring), so:

- **Any rate computed *within* a burst is an artefact of this loop.**
- **The count comes from the stimulus**, but is undercounted, and by how much
  depends on the handler speed. This rig cannot measure that: the scatter
  between stimuli is larger than the effect being looked for.

Still open, and not answered by the above: why the chain sustains itself at
all. The unverified hypothesis remains that the handler's own I²C reads keep
the disturbance alive (this sensor fires on I²C traffic on its own bus — §4 of
the integration plan).

Two further observations from the 2026-08-07 run, neither of them sought:
**classification is not stable** — 2 of 5 clicks came through as `lightning`
where 7 of 7 did on 2026-08-06 — and both of those again reported a distance
(10 km, 8 km) for a spark a few centimetres away.

Consequences: a burst count is not a measure of physical activity, and the
quiet-room baseline in the plan should be read as isolated events only.
`unknown` is the interrupt register reporting no valid bit — a failed read, not
a detection — and storing them as events inflates every total. Whether they
belong in the table at all is still open.

**Out of scope until a real storm has been recorded:** no header warning, no
beacon, no threshold logic. Nothing this sensor has reacted to so far has been
verified against an independent source, and an alarm on an unvalidated sensor
is the worst artefact — authoritative-looking and wrong. See
`docs/plans/2026-08-06-as3935-integration.md` §8.

