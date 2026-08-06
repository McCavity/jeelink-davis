"""
Background threads: AS3935 lightning sensor — IRQ listener plus a worker.

Modelled on ``bme280_reader.py``, with one structural difference: the AS3935 is
interrupt-driven, not polled. Two threads instead of one:

  * the **IRQ callback** does only what has to happen at the sensor — read the
    interrupt source (which is what re-arms the line) and, for a strike, the
    distance and energy registers. Rising-edge detection loses events while the
    callback runs, so nothing slower than an I²C read belongs in it.
  * the **worker** takes the queued event and does the slow part: the
    plausibility gate, SQLite, InfluxDB, MQTT.

WHAT THIS SENSOR HAS AND HAS NOT BEEN SHOWN TO DO
-------------------------------------------------
As of 2026-08-06 it has reacted to interference and to a piezo spark a few
centimetres away. It has never been checked against a real thunderstorm, so
neither its distance nor its energy figure has ever been compared with an
independent source. This module therefore *collects*; it does not warn, and
nothing downstream should treat a `lightning` event as a validated strike.

THE SETTINGS ARE VOLATILE, AND ``reset()`` WIPES THEM
-----------------------------------------------------
``reset()`` returns the watchdog threshold to its power-up value of 2, and a
reboot loses everything. Both were observed on 2026-08-06 — a threshold
measurement was invalidated by a background listener resetting the sensor after
the value had been set and verified.

So the settings are applied from config at every start and then **read back off
the chip**; on a mismatch the reader refuses to run rather than listen at an
unknown sensitivity. An event count means nothing without the sensitivity it was
counted at.

WHY THE CALIBRATION VALUES ARE IN CONFIG AND NOT HERE
------------------------------------------------------
``capacitance = 96`` (pF) was measured on this unit with an oscilloscope on the
IRQ pin in LCO mode, bracketed on both sides of the 31.25 kHz target. It is a
property of this antenna, not of the model — and ``manual_cal()`` does not
calibrate anything, it writes the number it is handed into register 0x08. On
another board the same 96 would be a guess.

``watchdog = 6`` is the smallest threshold that rejects the local interferer
while still passing a real stimulus, measured with a control burst between every
step. It matters more than it looks: at the default of 2, the neighbouring
BME280's own 60-second I²C poll was reported as `lightning` at 8 and 12 km.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timezone

from . import plausibility

logger = logging.getLogger(__name__)

#: Return values of ``get_interrupt_src()`` — datasheet Table 18.
IRQ_SOURCES: dict[int, str] = {
    0: "unknown",      # interrupt with no bit set; see below
    1: "lightning",
    2: "disturber",
    3: "noise",
}

# AFE gain word in register 0x00, bits 5:1 — the library writes these two
# constants and offers no getter, so the read-back compares against them.
_AFE_INDOOR  = 0x24
_AFE_OUTDOOR = 0x1C
_AFE_MASK    = 0x3E

#: Bounded so a burst of interference cannot grow the process. Dropping is
#: logged: these events are the noise-floor record, and a silent drop would
#: make a busy period look like a quiet one.
_events: "queue.Queue[dict]" = queue.Queue(maxsize=500)

_latest: dict | None = None
_lock = threading.Lock()


def get_latest() -> dict | None:
    """Most recent event of any kind, or None if none has arrived yet."""
    with _lock:
        return dict(_latest) if _latest else None


# ── Settings ──────────────────────────────────────────────────────────────

def read_settings(sensor) -> dict:
    """Read the sensitivity settings back off the chip.

    Deliberately reads registers rather than returning what was written: the
    whole point is to catch the case where the write did not stick.
    """
    sensor.sing_reg_read(0x08)
    capacitance = (sensor.register[0] & 0x0F) * 8

    sensor.sing_reg_read(0x00)
    afe = sensor.register[0] & _AFE_MASK
    if afe == _AFE_INDOOR:
        mode = "indoor"
    elif afe == _AFE_OUTDOOR:
        mode = "outdoor"
    else:
        # Not folded into "outdoor": an unexpected word means the register does
        # not hold what either call writes, and calling that "outdoor" would
        # turn a broken write into a plausible-looking setting.
        mode = f"unknown(0x{afe:02x})"

    return {
        "capacitance":     capacitance,
        "mode":            mode,
        "watchdog":        sensor.get_watchdog_threshold(),
        "spike_rejection": sensor.get_spike_rejection(),
        "noise_floor":     sensor.get_noise_floor_lv1(),
    }


def apply_settings(sensor, cfg: dict) -> dict:
    """Reset, write the configured settings, and return what the chip reports.

    Order matters. ``manual_cal()`` runs ``power_up()`` and sets the indoor /
    outdoor gain, so it has to come after ``reset()`` and before the three
    thresholds — otherwise it is the thresholds that are lost.
    """
    if not sensor.reset():
        raise RuntimeError("AS3935 reset failed — sensor not answering on I²C")

    # Disturber detection stays *enabled*. Masking it would hide exactly the
    # events that tell us whether the corner got noisier — see §4 of the
    # integration plan, where the quiet-room baseline was established.
    sensor.manual_cal(int(cfg["capacitance"]), 0 if cfg["indoor"] else 1, 1)

    sensor.set_noise_floor_lv1(int(cfg["noise_floor"]))
    sensor.set_watchdog_threshold(int(cfg["watchdog"]))
    sensor.set_spike_rejection(int(cfg["spike_rejection"]))
    sensor.clear_statistics()

    return read_settings(sensor)


def settings_mismatch(cfg: dict, ist: dict) -> dict[str, tuple]:
    """Return ``{setting: (wanted, got)}`` for everything that did not stick."""
    soll = {
        "capacitance":     int(cfg["capacitance"]),
        "mode":            "indoor" if cfg["indoor"] else "outdoor",
        "watchdog":        int(cfg["watchdog"]),
        "spike_rejection": int(cfg["spike_rejection"]),
        "noise_floor":     int(cfg["noise_floor"]),
    }
    return {k: (v, ist.get(k)) for k, v in soll.items() if ist.get(k) != v}


# ── Event handling ────────────────────────────────────────────────────────

def _handle_event(event: dict) -> dict:
    """Store one event in all three stores. Returns the stored event.

    Unlike a BME280 sample, an implausible field does **not** discard the
    record. ``distance_km`` has its own out-of-range code (63), so a rejected
    distance still describes a real detection — throwing the event away would
    delete the very fact that the sensor fired. The field is nulled, the
    detection is kept.
    """
    from . import db as weather_db
    from . import influxdb_writer, mqtt_publisher

    verworfen = plausibility.filter_lightning(event)
    if verworfen:
        logger.warning(
            "AS3935 %s event: implausible %s — field discarded, event kept",
            event["kind"],
            ", ".join(f"{feld}={wert}" for feld, wert in verworfen.items()),
        )

    try:
        weather_db.insert_lightning_event(event)
    except Exception:
        logger.exception("AS3935 DB insert failed")

    # Counted from the database rather than in memory, so the figure is right
    # after a service restart instead of starting the day again at zero.
    try:
        event["strike_count"] = weather_db.query_lightning_today()["lightning"]
    except Exception:
        logger.exception("AS3935 strike count query failed")
        event["strike_count"] = None

    global _latest
    with _lock:
        _latest = event

    influxdb_writer.push(event, "lightning")

    # Only a strike goes to MQTT. A disturber carries no distance and no
    # energy, so publishing it would republish the retained values of the
    # *previous* strike under a fresh timestamp — the failure mode that left
    # davis/weather/feels_like standing for 103 days.
    if event["kind"] == "lightning":
        mqtt_publisher.push(event, "lightning")

    if event["kind"] == "lightning":
        logger.info(
            "AS3935 strike: %s km, energy %s (today: %s)",
            event.get("distance_km"), event.get("energy"),
            event.get("strike_count"),
        )
    else:
        logger.info("AS3935 %s event", event["kind"])
    return event


def _worker() -> None:
    """Blocking — drains the event queue. Runs in a daemon thread."""
    while True:
        event = _events.get()
        try:
            _handle_event(event)
        except Exception:
            logger.exception("AS3935 event handling failed")


def _read_event(sensor) -> dict:
    """Read one event off the sensor. Called from the IRQ callback.

    Reading register 0x03 is what clears the interrupt and lets the line fall,
    so this cannot be deferred to the worker — without it no further rising
    edge would ever arrive.
    """
    # Datasheet: the interrupt register is only valid ~2 ms after the IRQ.
    # get_interrupt_src() waits as well; this is the same belt-and-braces
    # ordering the calibration script ran with for the measurements in the plan.
    time.sleep(0.005)
    kind = IRQ_SOURCES.get(sensor.get_interrupt_src(), "unknown")

    distance = energy = None
    if kind == "lightning":
        distance = float(sensor.get_lightning_distKm())
        energy = round(float(sensor.get_strike_energy_raw()), 3)

    return {
        # Microseconds, unlike the other two sources — and not for precision.
        # InfluxDB deduplicates on (measurement, tags, timestamp), so at second
        # resolution a burst collapses into one point per second. Measured on
        # 2026-08-06 during the acceptance test: 137 `unknown` events fell into
        # 13 distinct seconds, and InfluxDB held exactly 13 points. The Davis
        # and BME280 readers arrive every 41 and 60 seconds and can never
        # collide; lightning arrives in bursts, which is the whole point.
        #
        # Longer strings stay correct in the day-range queries: those compare
        # lexically against a bound truncated to whole seconds, and a longer
        # string sharing that prefix still sorts on the right side of it.
        "timestamp":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f"),
        "kind":        kind,
        "distance_km": distance,
        "energy":      energy,
    }


def lightning_reader_thread(cfg: dict) -> None:
    """Blocking — intended to run in a daemon thread.

    *cfg* is the ``[lightning]`` section of config.toml. Missing keys fall back
    to the values measured on this unit on 2026-08-06.
    """
    settings = {
        "capacitance":     cfg.get("capacitance", 96),
        "watchdog":        cfg.get("watchdog", 6),
        "spike_rejection": cfg.get("spike_rejection", 2),
        "noise_floor":     cfg.get("noise_floor", 2),
        "indoor":          cfg.get("indoor", True),
    }
    address = int(cfg.get("address", 0x03))
    bus_num = int(cfg.get("bus", 1))
    irq_pin = int(cfg.get("irq_pin", 7))

    logger.info(
        "AS3935 reader thread starting (bus=%d addr=0x%02x irq=board pin %d)",
        bus_num, address, irq_pin,
    )

    try:
        import RPi.GPIO as GPIO
    except ImportError:
        logger.warning(
            "RPi.GPIO not installed — lightning sensor disabled. "
            "On Debian 13 install the shim: pip install rpi-lgpio "
            "(do NOT install RPi.GPIO alongside it)"
        )
        return

    try:
        from .vendor.DFRobot_AS3935_Lib import DFRobot_AS3935
    except ImportError:
        logger.exception("AS3935 library import failed — lightning sensor disabled")
        return

    try:
        sensor = DFRobot_AS3935(address, bus=bus_num)
        ist = apply_settings(sensor, settings)
    except Exception:
        logger.exception("AS3935 init failed — lightning sensor disabled")
        return

    abweichung = settings_mismatch(settings, ist)
    if abweichung:
        # Refusing is the point. Listening at an unknown sensitivity produces
        # events that look like data and cannot be compared with anything —
        # including the quiet-room baseline this sensor was calibrated against.
        logger.error(
            "AS3935 settings did not stick — lightning sensor disabled. %s",
            "; ".join(f"{k}: wanted {soll}, chip reports {ist_}"
                      for k, (soll, ist_) in abweichung.items()),
        )
        return

    logger.info(
        "AS3935 ready — %s mode, %d pF, watchdog=%d spike=%d noise_floor=%d",
        ist["mode"], ist["capacitance"], ist["watchdog"],
        ist["spike_rejection"], ist["noise_floor"],
    )

    threading.Thread(target=_worker, daemon=True, name="lightning-worker").start()

    def on_irq(_channel) -> None:
        try:
            event = _read_event(sensor)
        except Exception:
            # A single failed read must not kill the callback registration —
            # the next edge should still be handled.
            logger.exception("AS3935 IRQ read failed")
            return
        try:
            _events.put_nowait(event)
        except queue.Full:
            logger.warning("AS3935 event queue full — dropping %s event", event["kind"])

    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(irq_pin, GPIO.IN)
    GPIO.add_event_detect(irq_pin, GPIO.RISING, callback=on_irq)

    # The thread stays alive to own the GPIO registration; the callback runs on
    # the GPIO library's own thread and the worker on its own.
    while True:
        time.sleep(3600)
