"""
Background thread: publishes weather readings to an MQTT broker.

Optional — only active when started via publisher_thread(). Each reading is
queued non-blocking; if the broker is unavailable readings are dropped with a
log warning (SQLite and InfluxDB remain the primary stores).

Topic layout: ``davis/<source>/<field>``, all retained, QoS 1.

  davis/outdoor/temperature     °C     Davis ISS
  davis/outdoor/humidity        %      Davis ISS
  davis/outdoor/wind_speed      m/s    Davis ISS
  davis/outdoor/wind_direction  deg    Davis ISS
  davis/outdoor/wind_gust       m/s    Davis ISS
  davis/outdoor/rain_rate       mm/h   computed from rain_secs; 0.0 if no rain
  davis/outdoor/rssi            dBm    JeeLink receiver
  davis/outdoor/battery_ok      0 / 1  Davis ISS
  davis/outdoor/feels_like      °C     derived, see below

  davis/indoor/temperature      °C     BME280
  davis/indoor/humidity         %      BME280
  davis/indoor/pressure         hPa    BME280

  davis/lightning/distance_km   km     AS3935, last strike (see below)
  davis/lightning/energy        —      AS3935, unitless, no documented scale
  davis/lightning/strike_count  count  strikes so far today (local day)

WHY ONLY STRIKES REACH THE LIGHTNING TOPICS
-------------------------------------------
The AS3935 also fires on disturbers and noise, and those are stored — they are
the record of how quiet the corner is. They are not published: a disturber
carries no distance and no energy, so publishing one would leave the *previous*
strike's retained values standing under a fresh event. Consumers would see a
distance that belongs to a different hour.

For the same reason ``distance_km`` is retractable. The sensor's own
out-of-range code (63 km) is rejected by the plausibility gate, and a strike
whose distance was rejected must not inherit the last one that passed.

WHY THE SOURCE IS PART OF THE TOPIC
-----------------------------------
Until 2026-08-06 every source published under one flat prefix
(``davis/weather/…``). Two threads — the Davis ISS reader and the BME280 poller
— therefore wrote *the same* ``temperature`` and ``humidity`` topics and each
overwrote the other. Consumers saw a sawtooth between outdoor and indoor:
measured on 2026-07-29, ten samples in two minutes alternated between 20.8 °C
and 36.7 °C. ``pressure`` came from the indoor sensor while sitting among
outdoor fields, which the old docstring had to explain in prose.

A prefix per source removes the collision by construction instead of by
convention, and leaves room for a third source without repeating the mistake.

WHY A DERIVED FIELD CAN BE RETRACTED
------------------------------------
``feels_like`` needs temperature, humidity *and* wind speed. A single ISS packet
carries only a subset of fields, so it is derived from the most recent value of
each — but only while all three are fresher than FRESHNESS_S. When an input
stops arriving, the derived value must not outlive it: the topic is retracted
with an empty retained payload instead of quietly keeping the last one.

This is not hypothetical. ``davis/weather/feels_like`` stood at 17.9 °C from
2026-04-24 to 2026-08-06 — 103 days, looking perfectly alive in ioBroker.

The cause was **this computation, not the sensor**. Measured on 2026-08-06 over
30 minutes of live traffic:

    15 279 packets       temperature in 25 %
                         humidity    in 11 %
                         wind speed  in 50 %
    all three in ONE packet:  0 of 15 279

The previous version computed the apparent temperature from a single packet, so
at least one input was always ``None`` and the field was skipped on every
publish — the retained message from April simply survived. Deriving from the
freshest value of each input is therefore the *fix*, not a workaround.

An earlier version of this docstring blamed a JeeLink firmware defect (it
suppresses field 4/5 under some conditions). That was a plausible chain built
from a known upstream bug — and wrong: wind speed is in fact the most frequently
transmitted of the three. The retraction path below is still needed, but for the
honest reason: an input can genuinely stop, and then the derived value must not
outlive it.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_q: "queue.Queue[tuple[str, dict] | None]" = queue.Queue(maxsize=200)
_running = False
_lock = threading.Lock()

TOPIC_ROOT = "davis"

#: Sources and the fields each one owns. A field belongs to exactly one source —
#: that is the whole point of the split.
SOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "outdoor": ("temperature", "humidity", "wind_speed", "wind_direction",
                "wind_gust", "rssi", "battery_ok", "rain_rate", "feels_like"),
    "indoor": ("temperature", "humidity", "pressure"),
    "lightning": ("distance_km", "energy", "strike_count"),
}

#: Fields that are derived rather than measured.
DERIVED_FIELDS: frozenset[str] = frozenset({"feels_like"})

#: Fields whose absence is *information* and must therefore delete the retained
#: value. Everything else keeps its last value when a payload does not carry
#: it: a missing raw Davis field just means "not in this packet" (the ISS
#: rotates through field types), which is normal and must not delete anything.
#:
#: ``feels_like``  — derived; when an input goes stale the value stops being
#:                   true, and it once stood unchanged for 103 days.
#: ``distance_km`` — belongs to one strike. A strike whose distance failed the
#:                   plausibility gate would otherwise be published carrying
#:                   the distance of an earlier one.
RETRACTABLE_FIELDS: frozenset[str] = DERIVED_FIELDS | frozenset({"distance_km"})

#: How long an input may be reused for a derived value.
FRESHNESS_S = 600

#: Last payload written per topic, so a topic is retracted once instead of on
#: every packet that happens to lack the input.
_last_payload: dict[str, str] = {}

#: Most recent value per (source, field) with the monotonic time it arrived —
#: the basis for derived fields. Raw fields are never taken from here.
_recent: dict[tuple[str, str], tuple[float, float]] = {}

_DERIVED_INPUTS = ("temperature", "humidity", "wind_speed")


def reset_state() -> None:
    """Drop remembered inputs and topic history. For tests."""
    _last_payload.clear()
    _recent.clear()


def push(payload: dict, source: str) -> None:
    """Enqueue a reading for MQTT export. Non-blocking; drops if full.

    ``source`` must be a key of SOURCE_FIELDS and decides the topic prefix.
    Mirrors ``influxdb_writer.push(payload, measurement)``.
    """
    if source not in SOURCE_FIELDS:
        raise ValueError(f"unknown MQTT source {source!r}")
    if not _running:
        return
    try:
        _q.put_nowait((source, payload))
    except queue.Full:
        logger.warning("MQTT publish queue full — dropping %s reading", source)


def publisher_thread(host: str, port: int, username: str, password: str,
                     retry_s: float = 10.0, max_retry_s: float = 300.0,
                     max_attempts: int | None = None) -> None:
    """Blocking — run in a daemon thread."""
    global _running
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        logger.warning(
            "paho-mqtt not installed — MQTT export disabled. "
            "Install with: pip install paho-mqtt"
        )
        return

    logger.info("MQTT publisher thread starting (broker=%s:%d)", host, port)

    client = mqtt.Client(
        client_id="davis-weather",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.username_pw_set(username, password)
    client.reconnect_delay_set(min_delay=5, max_delay=60)

    _connected = threading.Event()

    def on_connect(c, userdata, flags, rc, props=None):
        rc_val = rc.value if hasattr(rc, "value") else rc
        if rc_val == 0:
            logger.info("MQTT connected to %s:%d", host, port)
            _connected.set()
            with _lock:
                global _running
                _running = True
        else:
            logger.warning("MQTT connect failed: %s", rc)

    def on_disconnect(c, userdata, rc, props=None, reason=None):
        logger.warning("MQTT disconnected (rc=%s) — will reconnect", rc)
        _connected.clear()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    # The initial connect is retried instead of being fatal.
    #
    # paho's reconnect_delay_set only governs re-connects *after* one successful
    # connect. Before 2026-08-06 a failed first attempt hit `return` and killed
    # this thread for the lifetime of the process — silently, because the
    # dashboard kept working and only MQTT went quiet. That is the normal case
    # at boot, where the unit starts before the network is usable, and it is
    # what happened here: the last reading reached ioBroker on 2026-08-01 at
    # 15:19, and nothing followed for five days.
    delay = retry_s
    attempt = 0
    while True:
        try:
            client.connect(host, port, keepalive=60)
            break
        except Exception as exc:
            attempt += 1
            if max_attempts is not None and attempt >= max_attempts:
                logger.error(
                    "MQTT initial connect to %s:%d failed %d times — giving up",
                    host, port, attempt,
                )
                return
            logger.warning(
                "MQTT initial connect to %s:%d failed (%s) — retry %d in %.0f s",
                host, port, exc, attempt, delay,
            )
            time.sleep(delay)
            delay = min(delay * 2, max_retry_s)

    client.loop_start()

    while True:
        item = _q.get()
        if item is None:
            break
        source, payload = item
        if not _connected.is_set():
            _connected.wait(timeout=10)
        try:
            _publish_reading(client, source, payload)
        except Exception:
            logger.exception("MQTT publish failed (%s)", source)

    client.loop_stop()
    client.disconnect()
    with _lock:
        _running = False


def _remember(source: str, payload: dict, now: float) -> None:
    """Record raw inputs that derived fields are built from."""
    for field in _DERIVED_INPUTS:
        v = payload.get(field)
        if v is not None:
            _recent[(source, field)] = (float(v), now)


def _fresh(source: str, field: str, now: float) -> float | None:
    entry = _recent.get((source, field))
    if entry is None:
        return None
    value, seen = entry
    return value if (now - seen) <= FRESHNESS_S else None


def _publish_reading(client: Any, source: str, payload: dict,
                     now: float | None = None) -> None:
    """Publish the fields this source owns, retract what it can no longer say."""
    now = time.monotonic() if now is None else now
    owned = SOURCE_FIELDS[source]
    _remember(source, payload, now)

    values: dict[str, Any] = {}

    for field in ("temperature", "humidity", "wind_speed", "wind_direction",
                  "wind_gust", "pressure", "rssi", "distance_km", "energy",
                  "strike_count"):
        if field not in owned:
            continue
        v = payload.get(field)
        if v is not None:
            values[field] = round(float(v), 2)

    if "battery_ok" in owned and payload.get("battery_ok") is not None:
        values["battery_ok"] = 1 if payload["battery_ok"] else 0

    if "rain_rate" in owned:
        rain_rate = _compute_rain_rate(payload.get("rain_secs"))
        if rain_rate is not None:
            values["rain_rate"] = rain_rate

    # Derived from the freshest inputs, not from this packet alone — a single
    # ISS packet almost never carries all three.
    if "feels_like" in owned:
        feels = _apparent_temperature(
            _fresh(source, "temperature", now),
            _fresh(source, "humidity", now),
            _fresh(source, "wind_speed", now),
        )
        if feels is not None:
            values["feels_like"] = round(feels, 1)

    for field in owned:
        topic = f"{TOPIC_ROOT}/{source}/{field}"
        if field in values:
            body = str(values[field])
            client.publish(topic, payload=body, qos=1, retain=True)
            _last_payload[topic] = body
        elif field in RETRACTABLE_FIELDS and _last_payload.get(topic):
            # The value can no longer be stated. Retract the retained one
            # rather than let it stand; an empty retained payload deletes it
            # at the broker.
            client.publish(topic, payload="", qos=1, retain=True)
            _last_payload[topic] = ""
            grund = (f"inputs older than {FRESHNESS_S} s"
                     if field in DERIVED_FIELDS else "not carried by this event")
            logger.warning("MQTT %s: %s — retained value retracted", topic, grund)


def _compute_rain_rate(rain_secs: float | None) -> float | None:
    """Convert inter-tip interval to mm/h. Returns 0.0 if no rain."""
    if rain_secs is None:
        return None
    if rain_secs < 0 or rain_secs >= 1800:
        return 0.0
    return round(720.0 / rain_secs, 2)


def _apparent_temperature(
    temp_c: float | None,
    humidity: float | None,
    wind_ms: float | None,
) -> float | None:
    """Australian BOM apparent temperature — works year-round (cold + warm)."""
    if temp_c is None or humidity is None or wind_ms is None:
        return None
    e = (humidity / 100.0) * 6.105 * math.exp(
        17.27 * temp_c / (237.7 + temp_c)
    )
    return temp_c + 0.33 * e - 0.70 * wind_ms - 4.00
