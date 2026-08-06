"""Tests for the MQTT topic split, the connect retry and the retraction.

The three defects these cover were all *silent* in production: a sawtooth that
looked like real data, a publisher thread that died at boot without a trace, and
a derived value that stood unchanged for 103 days while looking alive. Each test
therefore asserts on the state a consumer would see, not on the code path.
"""

from __future__ import annotations

import pytest

from web import mqtt_publisher as mp


class FakeClient:
    """Records publishes the way a broker would see them."""

    def __init__(self):
        self.published: list[tuple[str, str, bool]] = []

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, retain))
        return self

    def wait_for_publish(self, timeout=None):
        return True

    def topics(self) -> set[str]:
        return {t for t, _, _ in self.published}

    def last(self, topic: str) -> str | None:
        for t, p, _ in reversed(self.published):
            if t == topic:
                return p
        return None


@pytest.fixture(autouse=True)
def sauber():
    mp.reset_state()
    yield
    mp.reset_state()


# --------------------------------------------------------------------------
# Topic split — the sawtooth
# --------------------------------------------------------------------------

def test_beide_quellen_kollidieren_nicht_mehr():
    """The defect itself: indoor and outdoor used to write the same topics."""
    c = FakeClient()
    mp._publish_reading(c, "outdoor", {"temperature": 36.7, "humidity": 24.0})
    mp._publish_reading(c, "indoor", {"temperature": 20.8, "humidity": 52.8,
                                      "pressure": 1001.9})

    assert c.last("davis/outdoor/temperature") == "36.7"
    assert c.last("davis/indoor/temperature") == "20.8"
    # …and no topic carries both.
    assert "davis/weather/temperature" not in c.topics()


def test_druck_gehoert_nur_zum_innensensor():
    """Pressure came from the BME280 but sat among the outdoor fields."""
    c = FakeClient()
    mp._publish_reading(c, "outdoor", {"temperature": 20.0, "pressure": 1002.0})
    assert "davis/outdoor/pressure" not in c.topics()

    mp._publish_reading(c, "indoor", {"pressure": 1002.0})
    assert c.last("davis/indoor/pressure") == "1002.0"


def test_innensensor_veroeffentlicht_keine_aussenfelder():
    c = FakeClient()
    mp._publish_reading(c, "indoor", {"temperature": 21.0, "wind_speed": 3.0,
                                      "rssi": -70, "battery_ok": True})
    assert c.topics() == {"davis/indoor/temperature"}


def test_unbekannte_quelle_wird_abgewiesen():
    with pytest.raises(ValueError):
        mp.push({"temperature": 1.0}, "kellerlicht")


# --------------------------------------------------------------------------
# Derived field — the 103 days
# --------------------------------------------------------------------------

def test_feels_like_aus_zusammengesetzten_paketen():
    """One ISS packet rarely carries all three inputs — that is the point."""
    c = FakeClient()
    mp._publish_reading(c, "outdoor", {"temperature": 20.0}, now=1000.0)
    assert "davis/outdoor/feels_like" not in c.topics()

    mp._publish_reading(c, "outdoor", {"humidity": 50.0}, now=1001.0)
    assert "davis/outdoor/feels_like" not in c.topics()

    mp._publish_reading(c, "outdoor", {"wind_speed": 2.0}, now=1002.0)
    assert c.last("davis/outdoor/feels_like") is not None


def test_fehlender_wind_leert_das_topic_genau_einmal():
    """The actual defect: wind stops, the value must not outlive its input."""
    c = FakeClient()
    mp._publish_reading(c, "outdoor",
                        {"temperature": 20.0, "humidity": 50.0,
                         "wind_speed": 2.0}, now=1000.0)
    assert c.last("davis/outdoor/feels_like") not in (None, "")

    # Wind stops arriving (JeeLink firmware suppresses field 4/5).
    spaeter = 1000.0 + mp.FRESHNESS_S + 1
    mp._publish_reading(c, "outdoor", {"temperature": 20.1}, now=spaeter)
    assert c.last("davis/outdoor/feels_like") == ""

    leerungen = [p for t, p, _ in c.published
                 if t == "davis/outdoor/feels_like" and p == ""]
    assert len(leerungen) == 1, "retraction must not repeat on every packet"

    mp._publish_reading(c, "outdoor", {"temperature": 20.2}, now=spaeter + 3)
    leerungen = [p for t, p, _ in c.published
                 if t == "davis/outdoor/feels_like" and p == ""]
    assert len(leerungen) == 1


def test_leerung_ist_retained_sonst_bleibt_der_alte_wert():
    c = FakeClient()
    mp._publish_reading(c, "outdoor",
                        {"temperature": 20.0, "humidity": 50.0,
                         "wind_speed": 2.0}, now=1000.0)
    mp._publish_reading(c, "outdoor", {"temperature": 20.1},
                        now=1000.0 + mp.FRESHNESS_S + 1)
    leerung = [(t, p, r) for t, p, r in c.published
               if t == "davis/outdoor/feels_like" and p == ""][0]
    assert leerung[2] is True, "an empty payload without retain deletes nothing"


def test_rohfeld_wird_nie_geleert():
    """A missing raw field is normal packet rotation, not a stale value."""
    c = FakeClient()
    mp._publish_reading(c, "outdoor", {"wind_speed": 3.0}, now=1000.0)
    assert c.last("davis/outdoor/wind_speed") == "3.0"

    mp._publish_reading(c, "outdoor", {"temperature": 20.0},
                        now=1000.0 + mp.FRESHNESS_S + 1)
    assert c.last("davis/outdoor/wind_speed") == "3.0"


def test_feels_like_kommt_zurueck_wenn_der_wind_zurueckkommt():
    """The retraction must not be a one-way door."""
    c = FakeClient()
    mp._publish_reading(c, "outdoor",
                        {"temperature": 20.0, "humidity": 50.0,
                         "wind_speed": 2.0}, now=1000.0)
    spaet = 1000.0 + mp.FRESHNESS_S + 1
    mp._publish_reading(c, "outdoor", {"temperature": 20.0}, now=spaet)
    assert c.last("davis/outdoor/feels_like") == ""

    mp._publish_reading(c, "outdoor",
                        {"temperature": 20.0, "humidity": 50.0,
                         "wind_speed": 2.0}, now=spaet + 1)
    assert c.last("davis/outdoor/feels_like") not in (None, "")


# --------------------------------------------------------------------------
# Connect retry — the five silent days
# --------------------------------------------------------------------------

class _StubClient:
    """Minimal stand-in for paho's Client; fails `fehl` times, then connects."""

    def __init__(self, fehl: int, zaehler: dict):
        self.fehl = fehl
        self.zaehler = zaehler

    def username_pw_set(self, *a, **k): pass
    def reconnect_delay_set(self, *a, **k): pass
    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass

    def connect(self, host, port, keepalive=60):
        self.zaehler["n"] += 1
        if self.zaehler["n"] <= self.fehl:
            raise OSError(101, "Network is unreachable")


def _patch_client(monkeypatch, fehl: int) -> dict:
    """Replace only paho's Client class — the import path stays real."""
    import paho.mqtt.client as echt
    zaehler = {"n": 0}
    monkeypatch.setattr(echt, "Client",
                        lambda **k: _StubClient(fehl, zaehler))
    monkeypatch.setattr(mp.time, "sleep", lambda s: None)
    return zaehler


def test_erstverbindung_wird_wiederholt(monkeypatch):
    """A failed first connect used to kill the thread for good."""
    zaehler = _patch_client(monkeypatch, fehl=2)
    mp._q.put(None)                       # terminate the publish loop at once
    mp.publisher_thread("host", 1884, "u", "p", retry_s=0.01)
    assert zaehler["n"] == 3, "must keep trying instead of returning"


def test_erstverbindung_gibt_irgendwann_auf(monkeypatch):
    """The retry must be bounded when asked to be — no endless silent loop."""
    zaehler = _patch_client(monkeypatch, fehl=99)
    mp.publisher_thread("host", 1884, "u", "p", retry_s=0.01, max_attempts=4)
    assert zaehler["n"] == 4


# --------------------------------------------------------------------------
# Unchanged behaviour that the split must not break
# --------------------------------------------------------------------------

def test_regenrate_und_batterie_unveraendert():
    c = FakeClient()
    mp._publish_reading(c, "outdoor", {"rain_secs": 360.0, "battery_ok": False})
    assert c.last("davis/outdoor/rain_rate") == "2.0"
    assert c.last("davis/outdoor/battery_ok") == "0"


def test_kein_regen_ist_null_nicht_abwesend():
    c = FakeClient()
    mp._publish_reading(c, "outdoor", {"rain_secs": 3600.0})
    assert c.last("davis/outdoor/rain_rate") == "0.0"


def test_alles_retained_und_qos1():
    c = FakeClient()
    mp._publish_reading(c, "indoor", {"temperature": 21.0, "humidity": 50.0,
                                      "pressure": 1000.0})
    assert all(r for _, _, r in c.published)
