"""
Background thread: drives DavisStation.readings() and fans out to the broadcaster.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime

from jeelink_davis import DavisStation

from . import db as weather_db
from .broadcaster import broadcaster
from . import influxdb_writer, mqtt_publisher, plausibility

logger = logging.getLogger(__name__)


def _reading_to_dict(reading) -> dict:
    d = dataclasses.asdict(reading)
    # Convert datetime to ISO-8601 string
    if isinstance(d.get("timestamp"), datetime):
        d["timestamp"] = d["timestamp"].isoformat()
    # Drop empty soil/leaf dicts to keep the payload lean
    for key in ("soil_temperature", "soil_moisture", "leaf_wetness", "extra_fields"):
        if not d.get(key):
            d.pop(key, None)
    return d


def _handle_reading(reading, loop: asyncio.AbstractEventLoop) -> dict:
    """Store one reading and fan it out. Returns the payload that was written.

    Split out of the loop below so a test can drive it with a parsed firmware
    line instead of a JeeLink.
    """
    payload = _reading_to_dict(reading)

    verworfen = plausibility.filter_outdoor(payload)
    if verworfen:
        # One line per rejection, deliberately unthrottled. The ISS sends every
        # ~2.5 s, so a permanently stuck field is ~24 lines/min — noisy, but a
        # sensor failing continuously *should* be loud, and the running total
        # in the message shows at a glance whether this is one glitch or a
        # broken sensor.
        logger.warning(
            "Davis reading implausible, field(s) discarded — %s (%d since start)",
            ", ".join(f"{feld}={wert}" for feld, wert in verworfen.items()),
            plausibility.snapshot()["total"],
        )

    try:
        weather_db.insert_reading(payload)
    except Exception:
        logger.exception("DB insert failed — reading not persisted")
    influxdb_writer.push(payload, "outdoor")
    mqtt_publisher.push(payload, "outdoor")
    asyncio.run_coroutine_threadsafe(broadcaster.broadcast(payload), loop)
    return payload


def station_reader_thread(loop: asyncio.AbstractEventLoop, port: str | None) -> None:
    """Blocking — runs in a daemon thread. Posts readings to the event loop."""
    logger.info("Davis reader thread starting (port=%s)", port or "auto")
    try:
        with DavisStation(port=port) as station:
            for reading in station.readings():
                _handle_reading(reading, loop)
    except Exception:
        logger.exception("Davis reader thread crashed")
