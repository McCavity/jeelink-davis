# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

End-to-end system for receiving **Davis Vantage Pro 2** weather station data via a **JeeLink USB receiver** (FT232R UART, auto-detected by USB VID/PID) and presenting it as a live web dashboard with historical data.

Target firmware on the JeeLink: **Davis 0.8e** (compiled Sep 5 2020, RFM69 radio, EU 868 MHz frequencies, firmware switch `b:2`).

The system runs on a **Raspberry Pi** (hostname `dwsapp01`) with the JeeLink plugged into a USB port near a window for reliable ISS reception. The dashboard is served at `https://wetter.halfpap.io/` (port 8000 internally, reverse-proxied externally).

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
.venv/bin/pytest tests/ -v

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
latitude  = 50.174533   # decimal degrees, positive = North
longitude = 8.719422    # decimal degrees, positive = East
elevation = 167         # metres above sea level
timezone  = "Europe/Berlin"

[storage]
db_path = "data/readings.db"   # relative to project root, or absolute
```

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
│                     #   tables: readings (outdoor), indoor_readings (BME280)
├── reader.py         # daemon thread: drives DavisStation → broadcaster + DB
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
| `GET /api/stats/daily\|monthly\|yearly` | Aggregated stats by period |

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

- **Host**: Raspberry Pi, hostname `dwsapp01`
- **Install path**: `/opt/jeelink-davis/`
- **Service**: `davis-weather.service` (systemd), runs as user `davis`
- **Shutdown**: `--timeout-graceful-shutdown 3` + `TimeoutStopSec=10` to avoid SSE connections delaying reboots
- **Database**: `/opt/jeelink-davis/data/readings.db` (SQLite, WAL mode)
- **Deploy**: copy changed files to `/opt/jeelink-davis/`, then `sudo systemctl restart davis-weather` for Python changes; static files take effect immediately on browser refresh
- **Screenshots**: `chromium --headless=new --screenshot=/tmp/shot.png --window-size=1400,900 https://wetter.halfpap.io/` then read `/tmp/shot.png`

## Indoor sensor (GY-BME280)

Connected to Raspberry Pi I²C bus 1 at address **0x76**. Polled every 60 s by `web/bme280_reader.py` daemon thread. Readings stored in `indoor_readings` table (SQLite). Timestamps stored as `YYYY-MM-DD HH:MM:SS` UTC so SQLite `datetime()` comparisons work.

**Pressure trend** (`/api/indoor` → `pressure_trend`): compares avg pressure of the last 30 min vs 2–4 h ago. Threshold ±0.5 hPa → `rising` / `falling` / `steady` / `unknown` (insufficient history).
