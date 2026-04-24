"""
Background thread: publishes weather readings to an MQTT broker.

Optional — only active when started via publisher_thread(). Each reading is
queued non-blocking; if the broker is unavailable readings are dropped with a
log warning (SQLite and InfluxDB remain the primary stores).

Topic layout (all retained, QoS 1):
  davis/weather/temperature      °C
  davis/weather/humidity         %
  davis/weather/wind_speed       m/s
  davis/weather/wind_direction   deg
  davis/weather/wind_gust        m/s
  davis/weather/rain_rate        mm/h  (computed from rain_secs; 0 if no rain)
  davis/weather/pressure         hPa   (BME280 indoor sensor)
  davis/weather/rssi             dBm
  davis/weather/battery_ok       0 or 1
  davis/weather/feels_like       °C    (apparent temperature)
"""

from __future__ import annotations

import logging
import math
import queue
import threading
from typing import Any

logger = logging.getLogger(__name__)

_q: "queue.Queue[dict | None]" = queue.Queue(maxsize=200)
_running = False
_lock = threading.Lock()

# Topic prefix — change in config if needed
_TOPIC_PREFIX = "davis/weather"


def push(payload: dict) -> None:
    """Enqueue a reading for MQTT export. Non-blocking; drops if full."""
    if not _running:
        return
    try:
        _q.put_nowait(payload)
    except queue.Full:
        logger.warning("MQTT publish queue full — dropping reading")


def publisher_thread(host: str, port: int, username: str, password: str) -> None:
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

    try:
        client.connect(host, port, keepalive=60)
    except Exception:
        logger.exception("MQTT initial connect failed")
        return

    client.loop_start()

    while True:
        item = _q.get()
        if item is None:
            break
        if not _connected.is_set():
            _connected.wait(timeout=10)
        try:
            _publish_reading(client, item)
        except Exception:
            logger.exception("MQTT publish failed")

    client.loop_stop()
    client.disconnect()
    with _lock:
        _running = False


def _publish_reading(client: Any, payload: dict) -> None:
    """Publish all available fields from a reading payload."""
    fields: dict[str, Any] = {}

    for field in ("temperature", "humidity", "wind_speed", "wind_direction",
                  "wind_gust", "pressure", "rssi"):
        v = payload.get(field)
        if v is not None:
            fields[field] = round(float(v), 2)

    battery = payload.get("battery_ok")
    if battery is not None:
        fields["battery_ok"] = 1 if battery else 0

    rain_rate = _compute_rain_rate(payload.get("rain_secs"))
    if rain_rate is not None:
        fields["rain_rate"] = rain_rate

    feels = _apparent_temperature(
        payload.get("temperature"),
        payload.get("humidity"),
        payload.get("wind_speed"),
    )
    if feels is not None:
        fields["feels_like"] = round(feels, 1)

    for field, value in fields.items():
        topic = f"{_TOPIC_PREFIX}/{field}"
        client.publish(topic, payload=str(value), qos=1, retain=True)


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
