"""
JeeLink Davis firmware protocol parser.

Handles two line types emitted by firmware 0.8e:

  INIT DICTIONARY 1=Temperature,2=Pressure,...
  OK VALUES DAVIS <station_id> <code>=<value>,...
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .models import FIELD_CODE_MAP, WeatherReading

logger = logging.getLogger(__name__)

# Prefix constants
_PREFIX_INIT = "INIT DICTIONARY "
_PREFIX_DATA = "OK VALUES DAVIS "


def parse_init_dictionary(line: str) -> dict[str, str]:
    """
    Parse an ``INIT DICTIONARY`` line into a {code: name} mapping.

    Example input:
        "INIT DICTIONARY 1=Temperature,2=Pressure,..."

    Returns e.g. {"1": "Temperature", "2": "Pressure", ...}
    Returns an empty dict if the line is not an INIT DICTIONARY line.
    """
    line = line.strip()
    if not line.startswith(_PREFIX_INIT):
        return {}

    payload = line[len(_PREFIX_INIT):]
    result: dict[str, str] = {}
    for entry in payload.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            logger.warning("Unexpected INIT DICTIONARY entry: %r", entry)
            continue
        code, name = entry.split("=", 1)
        result[code.strip()] = name.strip()

    return result


def parse_values_line(line: str) -> WeatherReading | None:
    """
    Parse an ``OK VALUES DAVIS`` line into a :class:`WeatherReading`.

    Example input:
        "OK VALUES DAVIS 0 20=2,22=-72,21=ok,4=0.00,5=155,6=9.65,7=15,"

    Returns ``None`` if the line cannot be parsed.
    """
    line = line.strip()
    if not line.startswith(_PREFIX_DATA):
        return None

    payload = line[len(_PREFIX_DATA):]

    # Split off station_id (first token) from the key=value pairs
    parts = payload.split(" ", 1)
    if len(parts) != 2:
        logger.warning("Cannot parse station_id from: %r", line)
        return None

    try:
        station_id = int(parts[0])
    except ValueError:
        logger.warning("Non-integer station_id %r in: %r", parts[0], line)
        return None

    reading = WeatherReading(
        timestamp=datetime.now(tz=timezone.utc),
        station_id=station_id,
    )

    for entry in parts[1].split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            logger.warning("Skipping malformed field %r in: %r", entry, line)
            continue

        code, raw_value = entry.split("=", 1)
        code = code.strip()
        raw_value = raw_value.strip()

        if not _apply_field(reading, code, raw_value):
            reading.extra_fields[code] = raw_value

    return reading


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _apply_field(reading: WeatherReading, code: str, raw: str) -> bool:
    """
    Map a field code + raw string value onto *reading*.

    Returns True if the field was handled, False if unknown.
    """
    # Skip packet dump (code 255)
    if code == "255":
        return True

    # Dotted codes: "15.1", "16.2", "17.3" etc. (soil/leaf, zone-indexed)
    if "." in code:
        return _apply_zoned_field(reading, code, raw)

    attr = FIELD_CODE_MAP.get(code)
    if attr is None:
        return False

    try:
        if attr == "battery_ok":
            setattr(reading, attr, raw.lower() == "ok")
        elif attr in ("wind_direction", "rain_secs", "channel", "rssi",
                      "wind_gust_ref"):
            setattr(reading, attr, int(float(raw)))
        else:
            setattr(reading, attr, float(raw))
    except (ValueError, TypeError):
        logger.warning("Cannot convert field %s=%r to expected type", code, raw)

    return True


def _apply_zoned_field(reading: WeatherReading, code: str, raw: str) -> bool:
    """Handle dotted zone codes like 15.1, 16.2, 17.1."""
    try:
        base, zone_str = code.split(".", 1)
        zone = int(zone_str)
        value = float(raw)
    except (ValueError, TypeError):
        logger.warning("Cannot parse zoned field %s=%r", code, raw)
        return False

    if base == "15":
        reading.soil_temperature[zone] = value
    elif base == "16":
        reading.soil_moisture[zone] = value
    elif base == "17":
        reading.leaf_wetness[zone] = value
    else:
        return False

    return True
