"""
Background thread: drives DavisStation.readings() and fans out to the broadcaster.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime

from jeelink_davis import DavisStation

from .broadcaster import broadcaster

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


def station_reader_thread(loop: asyncio.AbstractEventLoop, port: str | None) -> None:
    """Blocking — runs in a daemon thread. Posts readings to the event loop."""
    logger.info("Davis reader thread starting (port=%s)", port or "auto")
    try:
        with DavisStation(port=port) as station:
            for reading in station.readings():
                payload = _reading_to_dict(reading)
                asyncio.run_coroutine_threadsafe(broadcaster.broadcast(payload), loop)
    except Exception:
        logger.exception("Davis reader thread crashed")
