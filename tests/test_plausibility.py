"""
Tests for the plausibility gate in front of the three stores.

Written after 2026-08-06, when rewiring the BME280 with swapped pins made the
sensor return a valid-looking all-zero sample. No exception was raised, so the
old `try`-only loop wrote 0.00 °C / 0.00 % / 0.00 hPa into SQLite, InfluxDB and
MQTT. 0 hPa is not a low reading, it is an impossible one — and a single such
row flips the pressure trend, which compares a 30-minute mean against the
window 2-4 h back with a ±0.5 hPa threshold.

Two properties matter here, and only the pair of them is worth anything:

  * an impossible sample reaches none of the three stores, and
  * a possible one reaches all three.

A test for the first alone would pass against a function that writes nothing
ever, which is why every rejection test below has a positive counterpart.
"""

import logging
from types import SimpleNamespace

import pytest

from web import bme280_reader, plausibility


def _sample(temperature, humidity, pressure):
    """Stand-in for RPi.bme280's sample object — only these three attributes."""
    return SimpleNamespace(
        temperature=temperature, humidity=humidity, pressure=pressure
    )


@pytest.fixture
def stores(monkeypatch):
    """Capture every write instead of performing it.

    influxdb_writer.push / mqtt_publisher.push return early when their thread
    is not running, so they would swallow a call silently in a test process.
    Patching them is what makes "was not written" observable at all.
    """
    from web import db as weather_db
    from web import influxdb_writer, mqtt_publisher

    geschrieben: dict[str, list] = {"db": [], "influx": [], "mqtt": []}
    monkeypatch.setattr(
        weather_db, "insert_indoor_reading", lambda p: geschrieben["db"].append(p)
    )
    monkeypatch.setattr(
        influxdb_writer, "push", lambda p, m: geschrieben["influx"].append((p, m))
    )
    monkeypatch.setattr(
        mqtt_publisher, "push", lambda p: geschrieben["mqtt"].append(p)
    )
    return geschrieben


@pytest.fixture(autouse=True)
def leere_zaehler():
    plausibility.reset()
    yield
    plausibility.reset()


@pytest.fixture(autouse=True)
def leerer_cache(monkeypatch):
    monkeypatch.setattr(bme280_reader, "_latest", None)
    yield


# ── The incident: an all-zero sample ───────────────────────────────────────

def test_nullsample_erreicht_keinen_einzigen_store(stores):
    assert bme280_reader._handle_sample(_sample(0.0, 0.0, 0.0)) is None
    assert stores == {"db": [], "influx": [], "mqtt": []}


def test_nullsample_laesst_den_letzten_guten_wert_stehen(stores):
    bme280_reader._handle_sample(_sample(22.86, 52.61, 1001.72))
    gut = bme280_reader.get_latest()

    bme280_reader._handle_sample(_sample(0.0, 0.0, 0.0))

    # /api/indoor serves this cache. Overwriting it would put the zeros on the
    # dashboard even with a clean database.
    assert bme280_reader.get_latest() == gut


def test_gutes_sample_erreicht_alle_drei_stores(stores):
    bme280_reader._handle_sample(_sample(22.86, 52.61, 1001.72))

    assert len(stores["db"]) == 1
    assert len(stores["influx"]) == 1
    assert len(stores["mqtt"]) == 1
    assert stores["db"][0]["pressure"] == 1001.72
    assert stores["influx"][0][1] == "indoor"


def test_nullsample_wird_als_warnung_geloggt(stores, caplog):
    with caplog.at_level(logging.WARNING, logger="web.bme280_reader"):
        bme280_reader._handle_sample(_sample(0.0, 0.0, 0.0))

    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert "pressure" in caplog.text


# ── Bounds are the datasheet's, not a guess ────────────────────────────────
#
# Bosch BST-BME280-DS001-23 Rev 1.23: pressure 300…1100 hPa (Table 3,
# "Operating pressure range"), temperature -40…+85 °C (Table 4, "Operating
# range", operational — *not* the 0…65 °C full-accuracy row, which would
# discard genuine winter readings), humidity 0…100 %RH (Table 2).

@pytest.mark.parametrize("druck, erlaubt", [
    (300.0, True),    # datasheet minimum — inclusive
    (1100.0, True),   # datasheet maximum — inclusive
    (299.9, False),
    (1100.1, False),
    (0.0, False),     # the incident
])
def test_druckgrenzen_folgen_dem_datenblatt(stores, druck, erlaubt):
    bme280_reader._handle_sample(_sample(22.0, 50.0, druck))
    assert bool(stores["db"]) is erlaubt


@pytest.mark.parametrize("temperatur, erlaubt", [
    (-40.0, True),
    (85.0, True),
    (-40.1, False),
    (85.1, False),
])
def test_temperaturgrenzen_folgen_dem_datenblatt(stores, temperatur, erlaubt):
    bme280_reader._handle_sample(_sample(temperatur, 50.0, 1000.0))
    assert bool(stores["db"]) is erlaubt


@pytest.mark.parametrize("feuchte, erlaubt", [
    (0.0, True),      # 0 %RH is a legal reading; only 0 hPa is impossible
    (100.0, True),
    (100.1, False),
    (-0.1, False),
])
def test_feuchtegrenzen_folgen_dem_datenblatt(stores, feuchte, erlaubt):
    bme280_reader._handle_sample(_sample(22.0, feuchte, 1000.0))
    assert bool(stores["db"]) is erlaubt


def test_ein_einziges_unmoegliches_feld_verwirft_das_ganze_sample(stores):
    # The three values come from one measurement over one I2C transaction.
    # If the pressure is impossible, the other two are not trustworthy either.
    bme280_reader._handle_sample(_sample(22.86, 52.61, 0.0))
    assert stores["db"] == []


# ── The counter: a rejection has to be visible, not just logged ────────────

def test_verwurf_erhoeht_den_zaehler(stores):
    assert plausibility.snapshot()["total"] == 0

    bme280_reader._handle_sample(_sample(0.0, 0.0, 0.0))

    schnappschuss = plausibility.snapshot()
    assert schnappschuss["total"] == 1
    assert schnappschuss["by_field"]["indoor.pressure"] == 1
    assert schnappschuss["last"]["field"] == "pressure"
    assert schnappschuss["last"]["value"] == 0.0


def test_gutes_sample_erhoeht_den_zaehler_nicht(stores):
    bme280_reader._handle_sample(_sample(22.86, 52.61, 1001.72))
    assert plausibility.snapshot()["total"] == 0


# ── The Davis side ─────────────────────────────────────────────────────────
#
# Shaped differently from the BME280 and therefore handled differently. A
# Davis packet carries only part of the fields, the rest being absent by
# design, and interference on the serial line garbles a digit rather than the
# whole packet. Discarding a whole packet over one bad field would throw away
# the wind, rain and RSSI that arrived intact in the same packet.
#
# These tests drive the real firmware parser rather than a hand-built payload,
# so a change to the wire format cannot pass them unnoticed.

@pytest.fixture
def outdoor_stores(monkeypatch):
    from web import db as weather_db
    from web import influxdb_writer, mqtt_publisher

    geschrieben: dict[str, list] = {"db": [], "influx": [], "mqtt": []}
    monkeypatch.setattr(
        weather_db, "insert_reading", lambda p: geschrieben["db"].append(p)
    )
    monkeypatch.setattr(
        influxdb_writer, "push", lambda p, m: geschrieben["influx"].append((p, m))
    )
    monkeypatch.setattr(
        mqtt_publisher, "push", lambda p: geschrieben["mqtt"].append(p)
    )
    return geschrieben


async def _verarbeite(zeile: str) -> dict:
    """Run one firmware line through the parser and the reader's write path."""
    import asyncio

    from jeelink_davis.protocol import parse_values_line
    from web import reader

    return reader._handle_reading(
        parse_values_line(zeile), asyncio.get_running_loop()
    )


async def test_unmoegliche_temperatur_erreicht_die_stores_nicht(outdoor_stores):
    payload = await _verarbeite("OK VALUES DAVIS 0 20=2,22=-72,1=999.0,4=3.20,5=155")

    assert payload["temperature"] is None
    assert outdoor_stores["db"][0]["temperature"] is None


async def test_uebrige_felder_desselben_pakets_ueberleben(outdoor_stores):
    payload = await _verarbeite("OK VALUES DAVIS 0 20=2,22=-72,1=999.0,4=3.20,5=155")

    assert payload["wind_speed"] == 3.20
    assert payload["wind_direction"] == 155
    assert payload["rssi"] == -72


async def test_verworfen_ist_von_nie_gesendet_unterscheidbar(outdoor_stores):
    payload = await _verarbeite("OK VALUES DAVIS 0 20=2,22=-72,1=999.0,4=3.20,5=155")

    # Both temperature (rejected) and humidity (never sent in this packet) are
    # None. Only rejected_fields tells the two apart — without it a consumer
    # holding the last value cannot know it is now serving a stale reading.
    assert payload["humidity"] is None
    assert payload["rejected_fields"] == {"temperature": 999.0}


async def test_sauberes_paket_meldet_leeres_rejected_fields(outdoor_stores):
    payload = await _verarbeite("OK VALUES DAVIS 0 20=2,22=-72,1=21.30,4=3.20")

    # Present-but-empty, never absent: the broadcaster merges the freshest
    # non-null value per field and keeps it forever, so an omitted key here
    # would leave one packet's rejection standing in /api/latest for good.
    assert payload["rejected_fields"] == {}
    assert payload["temperature"] == 21.30
    assert plausibility.snapshot()["total"] == 0


async def test_fehlendes_feld_gilt_nicht_als_verworfen(outdoor_stores):
    await _verarbeite("OK VALUES DAVIS 0 20=2,22=-72,4=3.20")

    assert plausibility.snapshot()["total"] == 0


async def test_verwurf_wird_pro_feld_gezaehlt(outdoor_stores):
    await _verarbeite("OK VALUES DAVIS 0 20=2,1=999.0,10=99999,4=3.20")

    schnappschuss = plausibility.snapshot()
    assert schnappschuss["by_field"]["outdoor.temperature"] == 1
    assert schnappschuss["by_field"]["outdoor.solar_radiation"] == 1
    assert schnappschuss["total"] == 2


@pytest.mark.parametrize("zeile, feld, erwartet_verworfen", [
    # Davis 6450 spec sheet: "Range … 0 to 1800 W/m2"
    ("OK VALUES DAVIS 0 10=1800", "solar_radiation", False),
    ("OK VALUES DAVIS 0 10=1801", "solar_radiation", True),
    # Davis 6490 spec sheet: "Range … 0 to 16 Index"
    ("OK VALUES DAVIS 0 14=16.0", "uv_index", False),
    ("OK VALUES DAVIS 0 14=16.1", "uv_index", True),
    # Firmware 0.8e: 7-bit tip counter, wraps 127 → 0
    ("OK VALUES DAVIS 0 8=127", "rain_tip_count", False),
    ("OK VALUES DAVIS 0 8=128", "rain_tip_count", True),
    # 200 mph = 89.4 m/s, the wider of the two documented wind figures
    ("OK VALUES DAVIS 0 4=89.4", "wind_speed", False),
    ("OK VALUES DAVIS 0 4=89.5", "wind_speed", True),
    # VP2 outside temperature: -40 to 150 °F = -40 to 65.5 °C
    ("OK VALUES DAVIS 0 1=65.5", "temperature", False),
    ("OK VALUES DAVIS 0 1=65.6", "temperature", True),
])
async def test_aussengrenzen_folgen_der_herstellerangabe(
    outdoor_stores, zeile, feld, erwartet_verworfen
):
    payload = await _verarbeite(zeile)
    assert (feld in payload["rejected_fields"]) is erwartet_verworfen


async def test_zaehler_wird_ueber_api_system_ausgeliefert(stores):
    """The counter has to leave the process, not just the log file.

    /api/system is what the console polls every 10 s. A count that lives only
    in a WARNING makes a sensor which has quietly stopped delivering plausible
    values look identical to one that is working.
    """
    from web.app import system

    bme280_reader._handle_sample(_sample(0.0, 0.0, 0.0))
    antwort = await system()

    assert antwort["plausibility"]["total"] == 1
    assert antwort["plausibility"]["last"]["field"] == "pressure"


async def test_echter_insert_vertraegt_das_zusatzfeld(tmp_path):
    """Guard for an assumption the mocked tests above cannot see.

    insert_reading binds the payload by parameter name. Extra keys are ignored
    by sqlite3 and a *missing* one raises — which is why filter_outdoor nulls a
    rejected field instead of deleting it. Both halves of that are load-bearing,
    so this test drives the real storage layer rather than a stand-in.
    """
    import asyncio

    from jeelink_davis.protocol import parse_values_line
    from web import db as weather_db
    from web import reader

    weather_db._local = type(weather_db._local)()      # fresh per-thread handle
    weather_db.init_db(tmp_path / "test.db")

    reader._handle_reading(
        parse_values_line("OK VALUES DAVIS 0 20=2,22=-72,1=999.0,4=3.20"),
        asyncio.get_running_loop(),
    )

    zeile = weather_db._get_connection().execute(
        "SELECT temperature, wind_speed, rssi FROM readings"
    ).fetchone()
    assert zeile["temperature"] is None      # rejected → NULL, packet still stored
    assert zeile["wind_speed"] == 3.20
    assert zeile["rssi"] == -72


async def test_rssi_bleibt_ungeprueft(outdoor_stores):
    # No manufacturer range exists for the JeeLink's RSSI, so it is deliberately
    # not bounded — an invented bound would read as a specification later.
    payload = await _verarbeite("OK VALUES DAVIS 0 22=-999")

    assert payload["rssi"] == -999
    assert payload["rejected_fields"] == {}
