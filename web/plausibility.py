"""
Plausibility gate in front of SQLite, InfluxDB and MQTT.

Both reader threads used to guard their write path with `try`/`except` only.
That catches a sensor that *fails*, not one that lies: on 2026-08-06 a
mis-wired BME280 returned a perfectly well-formed all-zero sample, no
exception was raised, and 0.00 hPa was written to all three stores.

Every bound below is the manufacturer's, quoted at the line it applies to. A
guessed bound is worse than none: it looks like a specification afterwards.

What this catches and what it does not
--------------------------------------
It catches the impossible — dropouts to zero, wild outliers, NaN (which fails
every comparison and is therefore rejected). It does **not** catch a corrupted
digit: a bit error turns 21.3 °C into 27.3 °C far more readily than into 999,
and 27.3 passes every bound here. Data that has been through this gate is free
of the impossible, not verified.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

# ── Bounds ────────────────────────────────────────────────────────────────
#
# Bosch BME280, datasheet BST-BME280-DS001-23 Rev 1.23.
# The temperature bound is the "operational" row of Table 4, not the
# "full accuracy" row (0…65 °C) — the latter is an accuracy statement, and
# using it here would discard genuine winter readings as implausible.
INDOOR_RANGES: dict[str, tuple[float, float]] = {
    "temperature": (-40.0, 85.0),     # Table 4, operating range, operational
    "humidity":    (0.0, 100.0),      # Table 2, operating range
    "pressure":    (300.0, 1100.0),   # Table 3, operating pressure range
}

# Davis Vantage Pro 2. Sources per line:
#   [ISS]  Vantage Pro2 ISS manual, doc. 7395.333 Rev M, Specifications
#   [VP2]  Vantage Pro2 product specifications (sensor range column)
#   [6450] Davis solar radiation sensor spec sheet
#   [6490] Davis UV sensor spec sheet
#   [FW]   JeeLink Davis firmware 0.8e — see README, "Field codes"
OUTDOOR_RANGES: dict[str, tuple[float, float]] = {
    "temperature":     (-40.0, 65.5),    # [VP2] -40 to 150 °F (-40 to 65.5 °C);
                                         #       [ISS] rounds this to -40…65 °C
    "humidity":        (0.0, 100.0),     # [VP2] Humidity (Outside) 0-100%
    "wind_speed":      (0.0, 89.4),      # 200 mph — see note below
    "wind_gust":       (0.0, 89.4),      # 200 mph — see note below
    "wind_direction":  (0.0, 360.0),     # [VP2] 1° resolution over the full circle
    "solar_radiation": (0.0, 1800.0),    # [6450] "Range … 0 to 1800 W/m2"
    "uv_index":        (0.0, 16.0),      # [6490] "Range … 0 to 16 Index"
    "rain_tip_count":  (0.0, 127.0),     # [FW] 7-bit counter, wraps 127 → 0
}
#
# Wind: [VP2] gives the *measuring* range as 2 to 150 mph (3 to 241 km/h), while
# Davis states the station reports real-time speeds up to 200 mph. The wider of
# the two documented figures is used deliberately — this gate exists to reject
# the impossible, and a bound that clips a real storm gust would delete exactly
# the reading one keeps a weather station for. The lower bound is 0, not 2 mph:
# below its measuring threshold the anemometer legitimately reports calm.

# AMS AS3935 franklin lightning sensor, datasheet DS000385.
# Register 0x07 (DISTANCE), Table 17: the reported estimate is 1 km ("storm
# overhead") and then 5…40 km; 0b111111 (63) is the sensor's own "out of range"
# code. 40 is therefore the largest number the chip can mean, and anything above
# it is the out-of-range code or a garbled read, not a distant storm.
LIGHTNING_RANGES: dict[str, tuple[float, float]] = {
    "distance_km": (1.0, 40.0),      # Table 17, distance estimation
}

# Deliberately unchecked, because no manufacturer range exists for them:
#   rssi, voltage_solar, voltage_capacitor  — JeeLink/RFM69 internals
#   rain_secs                               — inter-tip interval, negative is
#                                             the firmware's "no rain" sentinel
#   wind_gust_ref, channel, station_id, battery_ok
#   energy                                  — AS3935 "energy" is explicitly a
#                                             pure number with no physical unit
#                                             and no documented scale; a bound
#                                             here would be invented outright
# Inventing bounds for these would dress a guess up as a specification.


# ── Counter ───────────────────────────────────────────────────────────────
#
# A rejected field must not be silently absent. Downstream, "absent" already
# means "this packet did not carry it" — an unannounced rejection would be
# indistinguishable from that, and consumers that hold the last value (a
# retained MQTT topic, the merged snapshot behind /api/latest) would keep
# serving a stale reading with nothing anywhere to say why.

_lock = threading.Lock()
_total = 0
_by_field: dict[str, int] = {}
_last: dict | None = None


def _record(source: str, field: str, value: float) -> None:
    global _total, _last
    with _lock:
        _total += 1
        key = f"{source}.{field}"
        _by_field[key] = _by_field.get(key, 0) + 1
        _last = {
            "source": source,
            "field": field,
            "value": value,
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        }


def snapshot() -> dict:
    """Counts since process start — served by /api/system."""
    with _lock:
        return {
            "total": _total,
            "by_field": dict(_by_field),
            "last": dict(_last) if _last else None,
        }


def reset() -> None:
    """Zero the counters. Tests only."""
    global _total, _last
    with _lock:
        _total = 0
        _by_field.clear()
        _last = None


# ── Checks ────────────────────────────────────────────────────────────────

def _out_of_range(ranges: dict[str, tuple[float, float]], field: str, value) -> bool:
    bounds = ranges.get(field)
    if bounds is None or value is None:
        return False
    lo, hi = bounds
    try:
        # NaN fails both comparisons and is therefore rejected — intended.
        return not (lo <= float(value) <= hi)
    except (TypeError, ValueError):
        return True


def reject_indoor(reading: dict) -> dict[str, float]:
    """Return ``{field: value}`` for every implausible field of a BME280 sample.

    An empty dict means the sample is fine. A non-empty one means the *whole*
    sample must be discarded: its three values come from a single measurement
    over a single I2C transaction, so one impossible value discredits all of
    them — the incident that prompted this module produced three zeros at once.
    """
    verworfen = {
        feld: reading[feld]
        for feld in INDOOR_RANGES
        if _out_of_range(INDOOR_RANGES, feld, reading.get(feld))
    }
    for feld, wert in verworfen.items():
        _record("indoor", feld, wert)
    return verworfen


def filter_lightning(event: dict) -> dict[str, float]:
    """Null out every implausible field of an AS3935 event, in place.

    Field-wise, and never discarding the record — the third variant among the
    three sources, for a reason particular to this sensor. A BME280 sample is
    discarded whole because its three values share one measurement; a Davis
    packet is filtered field-wise because a garbled serial line corrupts a
    digit. Here the event itself *is* the measurement: that the sensor fired is
    a fact independent of the distance register, and 63 in that register is the
    chip's documented way of saying "further than I can estimate". Dropping the
    event over it would delete a detection in order to reject a number.

    Returns ``{field: value}`` of what was rejected — empty when nothing was.
    """
    verworfen = {
        feld: event[feld]
        for feld in LIGHTNING_RANGES
        if _out_of_range(LIGHTNING_RANGES, feld, event.get(feld))
    }
    for feld, wert in verworfen.items():
        event[feld] = None
        _record("lightning", feld, wert)
    return verworfen


def filter_outdoor(payload: dict) -> dict[str, float]:
    """Null out every implausible field of a Davis payload, in place.

    Returns ``{field: value}`` of what was rejected, and records the same under
    ``payload["rejected_fields"]``.

    Field-wise, unlike the BME280: a Davis packet carries only some of the
    fields and a garbled serial line corrupts a digit rather than the packet,
    so discarding everything over one bad field would throw away the wind, rain
    and RSSI that arrived intact alongside it.

    The field is set to None rather than deleted, because db.insert_reading
    binds the payload by parameter name — a missing key raises, and the whole
    packet would be lost to the very guard meant to save it.

    ``rejected_fields`` is always present, empty when nothing was rejected.
    That is deliberate: absent already means "this packet did not carry it", so
    a rejection announced only by absence would be invisible. Empty rather than
    omitted, because broadcaster keeps the freshest non-null value per field
    forever — an omitted key would leave one packet's rejection standing in
    /api/latest indefinitely.
    """
    verworfen = {
        feld: payload[feld]
        for feld in OUTDOOR_RANGES
        if _out_of_range(OUTDOOR_RANGES, feld, payload.get(feld))
    }
    for feld, wert in verworfen.items():
        payload[feld] = None
        _record("outdoor", feld, wert)
    payload["rejected_fields"] = verworfen
    return verworfen
