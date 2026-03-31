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
import os
import threading
import time
import zoneinfo
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

import httpx
from astral import LocationInfo
from astral.moon import phase as moon_phase
from astral.sun import sun as astral_sun
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .broadcaster import broadcaster
from .config import load_config
from .reader import station_reader_thread

STATIC_DIR = Path(__file__).parent / "static"

# Simple in-process cache for the Open-Meteo forecast (30-minute TTL)
_forecast_cache: dict = {"data": None, "expires": 0.0}


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text()


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

    return {
        "dawn":        s["dawn"].isoformat(),
        "sunrise":     s["sunrise"].isoformat(),
        "noon":        s["noon"].isoformat(),
        "sunset":      s["sunset"].isoformat(),
        "dusk":        s["dusk"].isoformat(),
        "moon_phase":  round(mp, 2),
        "moon_name":   _phase_name(mp),
    }


@app.get("/api/history/recent")
async def history_recent(n: int = 50):
    """Last n readings in chronological order, for chart pre-seeding."""
    from . import db as weather_db
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: weather_db.query_recent(n))


@app.get("/api/history/today")
async def history_today():
    """Today's min/max stats for card display."""
    from . import db as weather_db
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, weather_db.query_today_minmax)
    if not result:
        return Response(status_code=204)
    return result


@app.get("/api/stats/daily")
async def stats_daily():
    from . import db as weather_db
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: weather_db.query_stats("daily"))


@app.get("/api/stats/monthly")
async def stats_monthly():
    from . import db as weather_db
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: weather_db.query_stats("monthly"))


@app.get("/api/stats/yearly")
async def stats_yearly():
    from . import db as weather_db
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: weather_db.query_stats("yearly"))


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
