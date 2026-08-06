"""
FastAPI web application for the Davis weather station dashboard.

Run with:
    uvicorn web.app:app --host 0.0.0.0 --port 8000

Override the serial port with the DAVIS_PORT environment variable:
    DAVIS_PORT=/dev/ttyUSB0 uvicorn web.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import zoneinfo
from contextlib import asynccontextmanager
from datetime import date, datetime as _dt, time as _time
from pathlib import Path

import httpx
from astral import LocationInfo
from astral.moon import phase as moon_phase
from astral.sun import elevation as sun_elevation
from astral.sun import sun as astral_sun
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .broadcaster import broadcaster
from .bme280_reader import bme280_reader_thread
from .config import load_config
from .lightning_reader import lightning_reader_thread
from .reader import station_reader_thread
from . import influxdb_writer, mqtt_publisher

STATIC_DIR = Path(__file__).parent / "static"

# Simple in-process cache for the Open-Meteo forecast (30-minute TTL)
_forecast_cache: dict = {"data": None, "expires": 0.0}

# ── TTL cache with single-flight, for the whole-table aggregates ────────────
#
# The /api/stats/* endpoints aggregate the *entire* readings table by design —
# there is no date filter an index could serve, so the cost grows with the
# archive and cannot be optimised away like the day/range queries were.
# Measured 2026-07-26 on 3.5 M rows: daily 50.3 s, monthly 38.9 s, yearly 33.3 s,
# rain totals 24.2 s. All four are reachable through the public Cloudflare
# tunnel, which turns a single GET into a ~50 s CPU amplifier on a 2 GB Pi 4.
#
# A plain cache would not fix that. On a cold or just-expired entry, N
# simultaneous requests all miss and all start the same query, so an attacker
# simply requests in parallel. The per-key lock makes the first caller compute
# while the others await *that* result, so concurrency can no longer multiply
# the cost — the bound is one run per key per TTL, whatever the request rate.
#
# Deliberately in-process: the values are cheap to recompute after a restart and
# an external cache would add a moving part for no gain at this size.
_STATS_TTL = 300.0        # stats change at most once per reading; 5 min is invisible
_RAIN_TTL  = 300.0        # rain totals move in 0.2 mm steps — 5 min loses nothing

_agg_cache: dict[str, tuple[float, object]] = {}
_agg_locks: dict[str, asyncio.Lock] = {}


async def _cached_aggregate(key: str, ttl: float, produce) -> tuple[object, int]:
    """Return ``(value, max_age)`` for *key*, computing it at most once per TTL.

    *produce* is a zero-argument callable run in the default executor (these are
    blocking sqlite3 calls and must not occupy the event loop).

    *max_age* is the *remaining* lifetime of this cache entry in whole seconds,
    not the full TTL. That matters as soon as a downstream cache (Cloudflare, the
    browser) honours it: handing out a flat 300 s for a value that has already
    sat here for four minutes would let the edge serve it for another five, so
    the reading could reach a visitor nine minutes old. Counting down instead
    makes both layers expire at the same instant, and the worst case stays one
    TTL rather than two.
    """
    now = time.monotonic()
    hit = _agg_cache.get(key)
    if hit is not None and now < hit[0]:
        return hit[1], max(1, int(hit[0] - now))

    lock = _agg_locks.setdefault(key, asyncio.Lock())
    async with lock:
        # Re-check under the lock: while we waited, the caller ahead of us has
        # very likely filled the entry — that is the whole point of the lock.
        hit = _agg_cache.get(key)
        now = time.monotonic()
        if hit is not None and now < hit[0]:
            return hit[1], max(1, int(hit[0] - now))
        loop = asyncio.get_running_loop()
        value = await loop.run_in_executor(None, produce)
        expires = time.monotonic() + ttl
        _agg_cache[key] = (expires, value)
        return value, max(1, int(ttl))


def _cacheable(data, max_age: int) -> JSONResponse:
    """JSON response that downstream caches may keep for *max_age* seconds.

    'public' is deliberate: these are aggregate weather statistics with no
    per-visitor content, so a shared cache holding one copy for everyone is
    exactly what we want — it is what lets a Cloudflare cache rule shield the
    origin instead of every visitor's request reaching the Pi.
    """
    return JSONResponse(
        content=jsonable_encoder(data),
        headers={"Cache-Control": f"public, max-age={max_age}"},
    )


def _configure_logging() -> None:
    """Make the background threads' INFO lines reach the journal.

    uvicorn configures its own loggers and leaves the root logger alone, so a
    `logging.getLogger(__name__)` in this package lands on the last-resort
    handler: WARNING and above go to stderr, INFO is dropped entirely.

    That was silently the case for every reader thread since the beginning, and
    it cost a deploy on 2026-08-06: the AS3935 reader reads its settings back
    off the chip and logs them, and that line is the only evidence the sensor
    is listening at the sensitivity it was calibrated at. It never appeared —
    which left "no error in the log" as the only thing to go on, and an absent
    error proves nothing when the channel itself is mute.
    """
    log = logging.getLogger("web")
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
        log.addHandler(handler)
        log.propagate = False          # otherwise every line appears twice
    log.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    # Initialise database before the reader thread starts
    from . import db as weather_db
    cfg = load_config()
    raw_path = cfg.get("storage", {}).get("db_path", "data/readings.db")
    db_path = Path(raw_path)
    if not db_path.is_absolute():
        db_path = Path(__file__).parent.parent / db_path
    weather_db.init_db(db_path)

    loop = asyncio.get_running_loop()
    port = os.environ.get("DAVIS_PORT")
    t = threading.Thread(
        target=station_reader_thread,
        args=(loop, port),
        daemon=True,
        name="davis-reader",
    )
    t.start()
    sensor_cfg = cfg.get("sensors", {})
    bme_bus     = sensor_cfg.get("bme280_bus", 1)
    bme_address = sensor_cfg.get("bme280_address", 0x76)
    bme_t = threading.Thread(
        target=bme280_reader_thread,
        args=(bme_address, bme_bus),
        daemon=True,
        name="bme280-reader",
    )
    bme_t.start()

    # No [lightning] section means no sensor — like [influxdb] and [mqtt].
    # The thread is not started rather than started and disabled, so an
    # installation without the hardware logs nothing about it at all.
    lightning_cfg = cfg.get("lightning")
    if lightning_cfg:
        lightning_t = threading.Thread(
            target=lightning_reader_thread,
            args=(lightning_cfg,),
            daemon=True,
            name="lightning-reader",
        )
        lightning_t.start()

    idb_cfg = cfg.get("influxdb")
    if idb_cfg:
        token = os.environ.get("INFLUXDB_TOKEN") or idb_cfg.get("token", "")
        if token:
            idb_t = threading.Thread(
                target=influxdb_writer.writer_thread,
                kwargs={
                    "url":    idb_cfg.get("url", "http://localhost:8086"),
                    "token":  token,
                    "org":    idb_cfg.get("org", ""),
                    "bucket": idb_cfg.get("bucket", "weather"),
                },
                daemon=True,
                name="influxdb-writer",
            )
            idb_t.start()
        else:
            logging.getLogger(__name__).warning(
                "InfluxDB config found but no token — set INFLUXDB_TOKEN env var"
            )

    mqtt_cfg = cfg.get("mqtt")
    if mqtt_cfg:
        mqtt_password = os.environ.get("MQTT_PASSWORD") or mqtt_cfg.get("password", "")
        mqtt_t = threading.Thread(
            target=mqtt_publisher.publisher_thread,
            kwargs={
                "host":     mqtt_cfg.get("host", "localhost"),
                "port":     int(mqtt_cfg.get("port", 1883)),
                "username": mqtt_cfg.get("username", ""),
                "password": mqtt_password,
            },
            daemon=True,
            name="mqtt-publisher",
        )
        mqtt_t.start()

    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

#: Paths whose content IS the application: the two HTML entry points (the
#: dashboard carries its whole script inline) and everything under /static.
_REVALIDATE_PREFIXES = ("/static/",)
_REVALIDATE_PATHS = ("/", "/console/")


@app.middleware("http")
async def revalidate_app_assets(request, call_next):
    """Send ``Cache-Control: no-cache`` for the dashboard's own code.

    Not "do not store" — "ask before using". The response already carries an
    ETag, so a revalidation is a 304 with no body: the cost is one conditional
    request per file per page load, against a Pi that answers those from the
    page cache. Cheap enough that it does not need a TTL to hide behind.

    WHY THIS EXISTS
    ---------------
    Without it the origin says nothing about freshness, and both layers above
    invent an answer. Measured on 2026-08-06 through the public tunnel:

        /static/js/console.js   cf-cache-status: HIT
                                cache-control:   max-age=14400   (4 h)
                                last-modified:   7 hours older than the file
                                                 actually on the Pi

    The `max-age` is not ours — Cloudflare caches .js by extension and adds a
    browser TTL of its own. The effect is that a deploy does not reach anyone
    for up to four hours, silently, and the console kiosk that prompted this
    was still showing seven pages after the eighth had been deployed.

    HTML and JSON are `DYNAMIC` at the edge and carried no header at all, so
    browsers fell back to heuristic freshness there. index.html is the whole
    dashboard including its script, so that is the same defect one layer over.

    This is the origin's half. The edge only honours it if the zone's Browser
    Cache TTL is "Respect Existing Headers" — a fixed value overrides what we
    send here. Verify at the public URL, not at the Pi:

        curl -sI https://<host>/static/js/console.js | grep -i cache
    """
    response = await call_next(request)
    path = request.url.path
    if path in _REVALIDATE_PATHS or path.startswith(_REVALIDATE_PREFIXES):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text()


@app.get("/console", include_in_schema=False)
async def console_redirect():
    return RedirectResponse("/console/")


@app.get("/console/", response_class=HTMLResponse)
async def console():
    return (STATIC_DIR / "console.html").read_text()


@app.get("/api/latest")
async def latest():
    """Returns the most-recent reading as JSON, or 204 if none received yet."""
    data = broadcaster.latest
    if data is None:
        return Response(status_code=204)
    return data


@app.get("/api/stream")
async def stream():
    """SSE endpoint — pushes a JSON event for each incoming reading."""
    q = broadcaster.add_client()

    async def event_generator():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            broadcaster.remove_client(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/location")
async def location():
    """Returns station latitude and longitude for client-side map centering."""
    cfg = load_config()["station"]
    return {"lat": cfg["latitude"], "lon": cfg["longitude"]}


@app.get("/api/solar")
async def solar():
    """Returns today's sun times and moon phase for the configured location."""
    cfg = load_config()["station"]
    tz = zoneinfo.ZoneInfo(cfg["timezone"])
    loc = LocationInfo(
        name=cfg["name"],
        region="",
        timezone=cfg["timezone"],
        latitude=cfg["latitude"],
        longitude=cfg["longitude"],
    )
    s = astral_sun(loc.observer, date=date.today(), tzinfo=tz)
    mp = moon_phase(date.today())

    def _phase_name(p: float) -> str:
        if p < 1.85 or p >= 26.15:  return "New Moon"
        if p < 7.38:                 return "Waxing Crescent"
        if p < 9.22:                 return "First Quarter"
        if p < 14.77:                return "Waxing Gibbous"
        if p < 16.61:                return "Full Moon"
        if p < 22.15:                return "Waning Gibbous"
        if p < 23.99:                return "Last Quarter"
        return "Waning Crescent"

    # Sun elevation every 30 minutes across the full day
    elevation_curve = []
    for i in range(48):
        m = i * 30
        hour, minute = divmod(m, 60)
        local_dt = _dt.combine(date.today(), _time(hour, minute), tzinfo=tz)
        el = round(sun_elevation(loc.observer, dateandtime=local_dt), 1)
        elevation_curve.append({"minute": m, "elevation": el})

    return {
        "dawn":            s["dawn"].isoformat(),
        "sunrise":         s["sunrise"].isoformat(),
        "noon":            s["noon"].isoformat(),
        "sunset":          s["sunset"].isoformat(),
        "dusk":            s["dusk"].isoformat(),
        "moon_phase":      round(mp, 2),
        "moon_name":       _phase_name(mp),
        "elevation_curve": elevation_curve,
    }


@app.get("/api/history/day_temp")
async def history_day_temp(day: str = "today"):
    """Returns temperature per 5-min bucket for today or yesterday."""
    from . import db as weather_db
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, lambda: weather_db.query_day_bucketed(day))
    except ValueError:
        return Response(status_code=400)


@app.get("/api/history/range")
async def history_range(start: str, end: str):
    """Returns auto-bucketed readings for a localtime date range."""
    from . import db as weather_db
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, lambda: weather_db.query_range_bucketed(start, end))
    except ValueError:
        return Response(status_code=400)


@app.get("/api/history/recent")
async def history_recent(n: int = 50):
    """Last n readings in chronological order, for chart pre-seeding."""
    from . import db as weather_db
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: weather_db.query_recent(n))


@app.get("/api/history/indoor_range")
async def history_indoor_range(start: str, end: str):
    """Returns bucketed average pressure from the BME280 for a date range."""
    from . import db as weather_db
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, lambda: weather_db.query_indoor_range_bucketed(start, end)
        )
    except ValueError:
        return Response(status_code=400)


@app.get("/api/history/today")
async def history_today():
    """Today's min/max stats for card display."""
    from . import db as weather_db
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, weather_db.query_today_minmax)
    if not result:
        return Response(status_code=204)
    return result


@app.get("/api/indoor")
async def indoor():
    """Returns the latest BME280 indoor reading plus a pressure trend, or 204."""
    from .bme280_reader import get_latest
    from . import db as weather_db
    data = get_latest()
    if data is None:
        return Response(status_code=204)
    loop = asyncio.get_running_loop()
    trend = await loop.run_in_executor(None, weather_db.query_pressure_trend)
    return {**data, "pressure_trend": trend}


@app.get("/api/lightning")
async def lightning():
    """AS3935 status: last strike, last event of any kind, today's counts.

    ``last_event`` is not decoration. A silent lightning sensor and a
    disconnected IRQ wire produce exactly the same empty log, and this endpoint
    is the only place that tells them apart: the disturber events keep arriving
    when the chain works, so a recent event of *any* kind is the proof that
    "no lightning" means the sky and not the wiring.

    Answers 204 while the table is empty, matching /api/indoor.
    """
    from . import db as weather_db

    loop = asyncio.get_running_loop()

    def _collect() -> dict:
        return {
            "last_strike": weather_db.query_lightning_last("lightning"),
            "last_event":  weather_db.query_lightning_last(),
            "today":       weather_db.query_lightning_today(),
        }

    data = await loop.run_in_executor(None, _collect)
    if data["last_event"] is None:
        return Response(status_code=204)
    return data


@app.get("/api/rain/totals")
async def rain_totals():
    """Return rain totals (mm) for the current week, month, and year."""
    from . import db as weather_db
    data, max_age = await _cached_aggregate(
        "rain:totals", _RAIN_TTL, weather_db.query_rain_totals
    )
    return _cacheable(data, max_age)


@app.get("/api/stats/daily")
async def stats_daily():
    from . import db as weather_db
    data, max_age = await _cached_aggregate(
        "stats:daily", _STATS_TTL, lambda: weather_db.query_stats("daily")
    )
    return _cacheable(data, max_age)


@app.get("/api/stats/monthly")
async def stats_monthly():
    from . import db as weather_db
    data, max_age = await _cached_aggregate(
        "stats:monthly", _STATS_TTL, lambda: weather_db.query_stats("monthly")
    )
    return _cacheable(data, max_age)


@app.get("/api/stats/yearly")
async def stats_yearly():
    from . import db as weather_db
    data, max_age = await _cached_aggregate(
        "stats:yearly", _STATS_TTL, lambda: weather_db.query_stats("yearly")
    )
    return _cacheable(data, max_age)


@app.get("/api/forecast")
async def forecast():
    """Returns a 5-day forecast from Open-Meteo (cached 30 min)."""
    now = time.monotonic()
    if _forecast_cache["data"] is not None and now < _forecast_cache["expires"]:
        return _forecast_cache["data"]

    cfg = load_config()["station"]
    lat = cfg["latitude"]
    lon = cfg["longitude"]
    tz  = cfg["timezone"]

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min"
        ",precipitation_sum,precipitation_probability_max,wind_speed_10m_max"
        f"&timezone={tz}"
        "&wind_speed_unit=kmh"
        "&forecast_days=5"
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return Response(
            content=json.dumps({"error": str(exc)}),
            status_code=503,
            media_type="application/json",
        )

    _forecast_cache["data"] = data
    _forecast_cache["expires"] = now + 1800.0
    return data


@app.get("/api/system")
async def system():
    from . import plausibility, system_info

    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, system_info.read_system)
    # Discarded readings are counted here because dropping them is otherwise
    # invisible: a sensor that has stopped delivering plausible values would
    # look exactly like one that is working.
    return {**data, "plausibility": plausibility.snapshot()}
