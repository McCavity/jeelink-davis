"""
Background thread: writes weather readings to InfluxDB v2.

Optional — only active when started via writer_thread(). Readings are queued
non-blocking; if InfluxDB is unavailable readings are dropped with a log
warning (SQLite remains the primary store).
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Bounded queue: (payload_dict, measurement_name) or None (poison pill)
_q: "queue.Queue[tuple[dict, str] | None]" = queue.Queue(maxsize=500)
_running = False
_lock = threading.Lock()


def push(payload: dict, measurement: str) -> None:
    """Enqueue a reading for InfluxDB export. Non-blocking; drops if full."""
    if not _running:
        return
    try:
        _q.put_nowait((payload, measurement))
    except queue.Full:
        logger.warning("InfluxDB write queue full — dropping %s reading", measurement)


def writer_thread(url: str, token: str, org: str, bucket: str) -> None:
    """Blocking — run in a daemon thread.

    Connects to InfluxDB, sets _running=True, then consumes the queue until
    a None poison pill arrives or the process exits (daemon thread).
    """
    global _running
    try:
        from influxdb_client import InfluxDBClient  # type: ignore
        from influxdb_client.client.write_api import SYNCHRONOUS  # type: ignore
    except ImportError:
        logger.warning(
            "influxdb-client not installed — InfluxDB export disabled. "
            "Install with: pip install influxdb-client"
        )
        return

    logger.info("InfluxDB writer thread starting (url=%s bucket=%s org=%s)", url, bucket, org)
    with InfluxDBClient(url=url, token=token, org=org) as client:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        with _lock:
            _running = True
        logger.info("InfluxDB writer ready")
        while True:
            item = _q.get()
            if item is None:
                break
            payload, measurement = item
            try:
                point = _build_point(payload, measurement)
                write_api.write(bucket=bucket, record=point)
            except Exception:
                logger.exception("InfluxDB write failed (measurement=%s)", measurement)
    with _lock:
        _running = False


# Fields written for each measurement (None values are always skipped)
_OUTDOOR_FIELDS = (
    "temperature", "humidity", "pressure",
    "wind_speed", "wind_direction", "wind_gust",
    "rain_tip_count", "rain_secs",
    "solar_radiation", "uv_index",
    "voltage_solar", "voltage_capacitor",
    "rssi", "battery_ok",
)
_INDOOR_FIELDS = ("temperature", "humidity", "pressure")


def _build_point(payload: dict, measurement: str):
    from influxdb_client import Point  # type: ignore

    ts_raw = payload.get("timestamp", "")
    try:
        # reader.py produces "2026-04-23T14:30:00+00:00"
        # bme280_reader.py produces "2026-04-23 14:30:00" (UTC, no tz suffix)
        ts_str = str(ts_raw).replace(" ", "T")
        if "+" not in ts_str[10:] and not ts_str.endswith("Z"):
            ts_str += "+00:00"
        ts = datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        ts = datetime.now(timezone.utc)

    p = Point(measurement).time(ts)

    if measurement == "outdoor":
        station_id = payload.get("station_id")
        if station_id is not None:
            p = p.tag("station_id", str(station_id))
        # channel bleibt Feld, nicht Tag — als Tag würde es pro Channel-Wert
        # eine separate Zeitreihe erzeugen und Grafana-Queries fragmentieren

    fields = _OUTDOOR_FIELDS if measurement == "outdoor" else _INDOOR_FIELDS
    for field_name in fields:
        val = payload.get(field_name)
        if val is None:
            continue
        # Store everything as float for consistency; battery_ok (bool) → 0.0/1.0
        p = p.field(field_name, float(val))

    return p
