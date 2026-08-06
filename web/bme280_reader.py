"""
Background thread: polls GY-BME280 via I2C every 60 s and stores readings.

The thread is optional — if smbus2 / RPi.bme280 are not installed it logs a
warning and exits silently so the rest of the application is unaffected.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from . import plausibility

logger = logging.getLogger(__name__)

_latest: dict | None = None
_lock = threading.Lock()


def get_latest() -> dict | None:
    """Return the most-recent indoor reading, or None if not yet available."""
    with _lock:
        return dict(_latest) if _latest else None


def _handle_sample(sample) -> dict | None:
    """Store one BME280 sample in all three stores, unless it is implausible.

    Returns the stored reading, or None when the sample was rejected. Split out
    of the loop below so a test can feed it a sample without any hardware.
    """
    from . import db as weather_db
    from . import influxdb_writer, mqtt_publisher

    reading: dict = {
        "timestamp":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "temperature": round(float(sample.temperature), 2),
        "humidity":    round(float(sample.humidity), 2),
        "pressure":    round(float(sample.pressure), 2),
    }

    verworfen = plausibility.reject_indoor(reading)
    if verworfen:
        # Rejected *before* the cache is updated: /api/indoor serves _latest,
        # so writing it here would put the bad values on the dashboard even
        # though no store received them.
        logger.warning(
            "BME280 sample implausible, discarded (nothing stored) — %s; "
            "whole sample was %.2f °C / %.2f %% / %.2f hPa",
            ", ".join(f"{feld}={wert}" for feld, wert in verworfen.items()),
            reading["temperature"], reading["humidity"], reading["pressure"],
        )
        return None

    global _latest
    with _lock:
        _latest = reading
    try:
        weather_db.insert_indoor_reading(reading)
    except Exception:
        logger.exception("BME280 DB insert failed")
    influxdb_writer.push(reading, "indoor")
    mqtt_publisher.push(reading)
    return reading


def bme280_reader_thread(address: int = 0x76, bus_num: int = 1) -> None:
    """Blocking — intended to run in a daemon thread.

    Polls the BME280 once per minute, writes to the DB, and updates the
    in-memory cache used by /api/indoor.
    """
    logger.info("BME280 reader thread starting (bus=%d addr=0x%02x)", bus_num, address)

    try:
        import smbus2
        import bme280 as _bme280
    except ImportError:
        logger.warning(
            "smbus2 / RPi.bme280 not installed — indoor sensor disabled. "
            "Run: pip install smbus2 RPi.bme280"
        )
        return

    try:
        bus = smbus2.SMBus(bus_num)
        params = _bme280.load_calibration_params(bus, address)
    except Exception:
        logger.exception("BME280 init failed — indoor sensor disabled")
        return

    while True:
        try:
            _handle_sample(_bme280.sample(bus, address, params))
        except Exception:
            logger.exception("BME280 read failed")
        time.sleep(60)
