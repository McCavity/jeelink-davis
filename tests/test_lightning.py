"""
Tests for the AS3935 integration — no hardware, no production DB.

The sensor's whole history is one of lying convincingly, so the tests here are
aimed at the specific lies, not at coverage:

  * it reports interference as `lightning`, complete with a distance. On
    2026-08-06 the neighbouring BME280's own 60-second I²C poll was logged as a
    strike at 8 km and at 12 km. Hence the plausibility tests, and hence the
    rule that a rejected distance must never be inherited by the next strike.
  * ``reset()`` silently returns the watchdog threshold to 2, which is the
    setting under which those false strikes happened. A reader that starts at
    the wrong sensitivity produces numbers that cannot be compared with
    anything — so a mismatch has to stop it, and that is asserted here.
  * a quiet log is indistinguishable from a disconnected IRQ wire. The event
    counts must therefore keep the non-strike kinds, and both the "some
    events" and "no events at all" cases are pinned below.

Every rejection test has a positive counterpart. A gate that rejects everything
would pass the first half of this file on its own.
"""

from __future__ import annotations

import os
import time

import pytest

from web import lightning_reader, plausibility


# ──────────────────────────────────────────────────────────────────────────
# A fake chip: registers in a dict, behaving like the real one where it matters
# ──────────────────────────────────────────────────────────────────────────

class FakeSensor:
    """Stand-in for DFRobot_AS3935 — same call surface, no I²C.

    ``reset()`` puts the watchdog threshold back to 2 exactly as the chip does,
    because that is the trap the read-back exists to catch.
    """

    def __init__(self, *, reset_ok=True, sticky=True):
        self._reset_ok = reset_ok
        self._sticky = sticky          # False = writes are silently ignored
        self.register = [0]
        self.capacitance = 0
        self.afe = 0
        self.watchdog = 2
        self.spike = 2
        self.noise = 2
        self.statistics_cleared = False
        self.calls: list[str] = []

        # What the IRQ handler will find, set by the test.
        self.interrupt_src = 0
        self.distance = 0
        self.energy = 0.0

    # -- what reset()/manual_cal() do -------------------------------------
    def reset(self):
        self.calls.append("reset")
        if not self._reset_ok:
            return 0
        self.watchdog = 2              # the documented, silent side effect
        self.spike = 2
        self.noise = 2
        self.capacitance = 0
        self.afe = 0
        return 1

    def manual_cal(self, capacitance, location, disturber):
        self.calls.append(f"manual_cal({capacitance},{location},{disturber})")
        if self._sticky:
            self.capacitance = min(capacitance, 120) // 8 * 8
            self.afe = lightning_reader._AFE_OUTDOOR if location else lightning_reader._AFE_INDOOR

    # -- setters ----------------------------------------------------------
    def set_watchdog_threshold(self, v):
        self.calls.append("set_watchdog_threshold")
        if self._sticky:
            self.watchdog = v & 0x0F

    def set_spike_rejection(self, v):
        if self._sticky:
            self.spike = v & 0x0F

    def set_noise_floor_lv1(self, v):
        if self._sticky:
            self.noise = v & 0x07

    def clear_statistics(self):
        self.statistics_cleared = True

    # -- getters ----------------------------------------------------------
    def get_watchdog_threshold(self):
        return self.watchdog

    def get_spike_rejection(self):
        return self.spike

    def get_noise_floor_lv1(self):
        return self.noise

    def sing_reg_read(self, reg):
        if reg == 0x08:
            self.register = [self.capacitance // 8]
        elif reg == 0x00:
            self.register = [self.afe]
        else:
            self.register = [0]

    def get_interrupt_src(self):
        return self.interrupt_src

    def get_lightning_distKm(self):
        return self.distance

    def get_strike_energy_raw(self):
        return self.energy


MESSWERTE = {
    "capacitance": 96, "watchdog": 6, "spike_rejection": 2,
    "noise_floor": 2, "indoor": True,
}


# ──────────────────────────────────────────────────────────────────────────
# Settings: applied, read back, and refused when they do not stick
# ──────────────────────────────────────────────────────────────────────────

class TestSettings:
    def test_settings_are_applied_after_reset_not_before(self):
        """``reset()`` first, then everything else — that is the trap.

        A reader that configured and then reset would look correct in its own
        log and listen at watchdog=2, the setting under which the BME280 poll
        was reported as a strike at 8 km.

        The second assertion is narrower: ``manual_cal()`` writes register 0x08
        and the gain word, not the thresholds, so putting it after them would
        not actually lose anything today. It is pinned anyway because it is the
        order the calibration measurements in the plan were taken in, and a
        vendor library is free to widen what its own convenience wrapper
        touches between releases.
        """
        s = FakeSensor()
        ist = lightning_reader.apply_settings(s, MESSWERTE)

        assert s.calls.index("reset") < s.calls.index("manual_cal(96,0,1)")
        assert s.calls.index("manual_cal(96,0,1)") < s.calls.index("set_watchdog_threshold")
        assert ist == {"capacitance": 96, "mode": "indoor", "watchdog": 6,
                       "spike_rejection": 2, "noise_floor": 2}
        assert lightning_reader.settings_mismatch(MESSWERTE, ist) == {}

    def test_disturber_detection_stays_enabled(self):
        """Masking disturbers would hide the noise record the baseline needs."""
        s = FakeSensor()
        lightning_reader.apply_settings(s, MESSWERTE)
        assert "manual_cal(96,0,1)" in s.calls        # third argument: disturber on

    def test_silently_ignored_writes_are_caught(self):
        """The failure this guard exists for: the write returns, nothing sticks."""
        s = FakeSensor(sticky=False)
        ist = lightning_reader.apply_settings(s, MESSWERTE)
        abweichung = lightning_reader.settings_mismatch(MESSWERTE, ist)

        assert abweichung["watchdog"] == (6, 2)       # back at the dangerous default
        assert abweichung["capacitance"] == (96, 0)
        assert abweichung["mode"] == ("indoor", "unknown(0x00)")

    def test_dead_sensor_raises_instead_of_listening(self):
        s = FakeSensor(reset_ok=False)
        with pytest.raises(RuntimeError):
            lightning_reader.apply_settings(s, MESSWERTE)

    def test_unknown_afe_word_is_not_called_outdoor(self):
        """An unexpected gain word means the write did not land. Folding it into
        'outdoor' would turn a broken register into a plausible setting."""
        s = FakeSensor()
        s.afe = 0x02
        assert lightning_reader.read_settings(s)["mode"] == "unknown(0x02)"

    def test_outdoor_mode_is_recognised(self):
        """The negative case above is only worth something if the positive one
        works — otherwise 'unknown' would be the answer to everything."""
        s = FakeSensor()
        lightning_reader.apply_settings(s, {**MESSWERTE, "indoor": False})
        assert lightning_reader.read_settings(s)["mode"] == "outdoor"


# ──────────────────────────────────────────────────────────────────────────
# Reading an event off the chip
# ──────────────────────────────────────────────────────────────────────────

class TestReadEvent:
    def test_strike_carries_distance_and_energy(self):
        s = FakeSensor()
        s.interrupt_src, s.distance, s.energy = 1, 12, 0.523
        event = lightning_reader._read_event(s)

        assert event["kind"] == "lightning"
        assert event["distance_km"] == 12.0
        assert event["energy"] == 0.523

    def test_timestamp_has_sub_second_resolution(self):
        """Not precision — InfluxDB deduplicates on (measurement, tags,
        timestamp), so at second resolution a burst collapses into one point
        per second. Measured on 2026-08-06 during the acceptance test: 137
        events fell into 13 distinct seconds, and InfluxDB held exactly 13
        points. The count was wrong and nothing said so.
        """
        s = FakeSensor()
        s.interrupt_src = 2
        ts = lightning_reader._read_event(s)["timestamp"]

        datum, _, bruchteil = ts.partition(".")
        assert len(datum) == len("2026-08-06 19:01:23")
        assert len(bruchteil) == 6 and bruchteil.isdigit()

    def test_two_events_in_one_second_stay_distinct(self):
        """The property that actually matters downstream."""
        s = FakeSensor()
        s.interrupt_src = 2
        stempel = {lightning_reader._read_event(s)["timestamp"] for _ in range(5)}
        assert len(stempel) == 5

    @pytest.mark.parametrize("src,kind", [(2, "disturber"), (3, "noise"), (0, "unknown")])
    def test_non_strike_events_carry_no_numbers(self, src, kind):
        """Distance and energy registers hold the *last strike*, so copying them
        onto a disturber would date-stamp an old reading as new."""
        s = FakeSensor()
        s.interrupt_src, s.distance, s.energy = src, 12, 0.9
        event = lightning_reader._read_event(s)

        assert event["kind"] == kind
        assert event["distance_km"] is None
        assert event["energy"] is None


# ──────────────────────────────────────────────────────────────────────────
# Plausibility — the acceptance criterion
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def leere_zaehler():
    plausibility.reset()
    yield
    plausibility.reset()


class TestPlausibility:
    def test_out_of_range_code_is_rejected_but_event_survives(self):
        """63 km is the chip's 'out of range' code, not a distant storm."""
        event = {"kind": "lightning", "distance_km": 63.0, "energy": 0.5}
        verworfen = plausibility.filter_lightning(event)

        assert verworfen == {"distance_km": 63.0}
        assert event["distance_km"] is None
        assert event["energy"] == 0.5           # the detection itself is kept
        assert plausibility.snapshot()["by_field"]["lightning.distance_km"] == 1

    def test_plausible_distance_passes_untouched(self):
        event = {"kind": "lightning", "distance_km": 12.0, "energy": 0.5}
        assert plausibility.filter_lightning(event) == {}
        assert event["distance_km"] == 12.0
        assert plausibility.snapshot()["total"] == 0

    @pytest.mark.parametrize("d", [1.0, 40.0])
    def test_both_documented_bounds_are_inside(self, d):
        """1 km is 'overhead' and 40 km the largest estimate the chip makes —
        a gate that clipped either would delete real readings."""
        event = {"kind": "lightning", "distance_km": d, "energy": 0.1}
        assert plausibility.filter_lightning(event) == {}

    def test_missing_distance_is_not_a_rejection(self):
        """A disturber has no distance. Counting that as implausible would bury
        the real rejections in noise."""
        event = {"kind": "disturber", "distance_km": None, "energy": None}
        assert plausibility.filter_lightning(event) == {}
        assert plausibility.snapshot()["total"] == 0


# ──────────────────────────────────────────────────────────────────────────
# The write path: what reaches which store
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
def stores(monkeypatch):
    """Capture every write instead of performing it — the push() functions
    return early when their thread is idle and would swallow the call."""
    from web import db as weather_db
    from web import influxdb_writer, mqtt_publisher

    geschrieben: dict[str, list] = {"db": [], "influx": [], "mqtt": []}
    monkeypatch.setattr(
        weather_db, "insert_lightning_event", lambda e: geschrieben["db"].append(dict(e))
    )
    monkeypatch.setattr(
        weather_db, "query_lightning_today",
        lambda: {"lightning": 3, "disturber": 1, "noise": 0, "unknown": 0},
    )
    monkeypatch.setattr(
        influxdb_writer, "push", lambda p, m: geschrieben["influx"].append((m, dict(p)))
    )
    monkeypatch.setattr(
        mqtt_publisher, "push", lambda p, q: geschrieben["mqtt"].append((q, dict(p)))
    )
    return geschrieben


class TestWritePath:
    def test_strike_reaches_all_three_stores(self, stores):
        lightning_reader._handle_event(
            {"timestamp": "2026-08-06 12:00:00", "kind": "lightning",
             "distance_km": 12.0, "energy": 0.5}
        )
        assert len(stores["db"]) == 1
        assert stores["influx"][0][0] == "lightning"
        assert stores["mqtt"][0][0] == "lightning"
        assert stores["mqtt"][0][1]["strike_count"] == 3

    def test_disturber_is_stored_but_not_published(self, stores):
        """It belongs in the noise record; on MQTT it would only republish the
        previous strike's retained distance under a new timestamp."""
        lightning_reader._handle_event(
            {"timestamp": "2026-08-06 12:00:00", "kind": "disturber",
             "distance_km": None, "energy": None}
        )
        assert len(stores["db"]) == 1
        assert len(stores["influx"]) == 1
        assert stores["mqtt"] == []

    def test_implausible_distance_is_stored_as_null(self, stores):
        lightning_reader._handle_event(
            {"timestamp": "2026-08-06 12:00:00", "kind": "lightning",
             "distance_km": 63.0, "energy": 0.5}
        )
        assert stores["db"][0]["distance_km"] is None
        assert stores["db"][0]["energy"] == 0.5

    def test_db_failure_does_not_lose_the_export(self, stores, monkeypatch):
        """SQLite is the primary store, but a failed insert must not also cost
        the InfluxDB point — otherwise one broken store silences all three."""
        from web import db as weather_db

        def boom(_e):
            raise RuntimeError("disk full")

        monkeypatch.setattr(weather_db, "insert_lightning_event", boom)
        lightning_reader._handle_event(
            {"timestamp": "2026-08-06 12:00:00", "kind": "lightning",
             "distance_km": 5.0, "energy": 0.2}
        )
        assert len(stores["influx"]) == 1


# ──────────────────────────────────────────────────────────────────────────
# InfluxDB point shape
# ──────────────────────────────────────────────────────────────────────────

class TestInfluxPoint:
    def test_disturber_point_still_carries_a_field(self):
        """InfluxDB rejects a point with no fields, and a disturber has neither
        distance nor energy — strike_count is what keeps it writable."""
        pytest.importorskip("influxdb_client")
        from web import influxdb_writer

        line = influxdb_writer._build_point(
            {"timestamp": "2026-08-06 12:00:00", "kind": "disturber",
             "distance_km": None, "energy": None, "strike_count": 3},
            "lightning",
        ).to_line_protocol()

        assert line.startswith("lightning,kind=disturber ")
        assert "strike_count=3" in line

    def test_unknown_measurement_fails_loudly(self):
        """It used to fall through to the indoor field list, which for a
        lightning payload would have produced a point with no fields at all —
        rejected far from the call that caused it."""
        pytest.importorskip("influxdb_client")
        from web import influxdb_writer

        with pytest.raises(KeyError):
            influxdb_writer._build_point({"timestamp": "2026-08-06 12:00:00"}, "tippfehler")


# ──────────────────────────────────────────────────────────────────────────
# MQTT: a rejected distance must not inherit the previous strike's value
# ──────────────────────────────────────────────────────────────────────────

class TestMqttRetraction:
    def test_rejected_distance_retracts_the_retained_topic(self):
        from web import mqtt_publisher as mp

        class FakeClient:
            def __init__(self):
                self.published: list[tuple[str, str, bool]] = []

            def publish(self, topic, payload=None, qos=0, retain=False):
                self.published.append((topic, payload, retain))
                return self

            def last(self, topic):
                for t, p, _ in reversed(self.published):
                    if t == topic:
                        return p
                return None

        mp.reset_state()
        try:
            c = FakeClient()
            mp._publish_reading(c, "lightning",
                                {"distance_km": 12.0, "energy": 0.5, "strike_count": 1})
            assert c.last("davis/lightning/distance_km") == "12.0"

            # Next strike, distance rejected by the gate → None.
            mp._publish_reading(c, "lightning",
                                {"distance_km": None, "energy": 0.7, "strike_count": 2})
            assert c.last("davis/lightning/distance_km") == ""     # retracted
            assert c.last("davis/lightning/energy") == "0.7"
        finally:
            mp.reset_state()


# ──────────────────────────────────────────────────────────────────────────
# The read-back line has to be *visible*
# ──────────────────────────────────────────────────────────────────────────

class TestLoggingVisibility:
    """The settings read-back is the only evidence the sensor listens at the
    sensitivity it was calibrated at — and on 2026-08-06 that line never
    reached the journal, because uvicorn leaves the root logger alone and INFO
    fell on the floor. "No error in the log" was then the only thing to go on,
    which proves nothing when the channel itself is mute.
    """

    def test_package_info_lines_are_emitted(self, capsys):
        import logging
        from web.app import _configure_logging

        log = logging.getLogger("web")
        vorher = list(log.handlers)
        log.handlers.clear()
        try:
            _configure_logging()
            logging.getLogger("web.lightning_reader").info("AS3935 ready — indoor")
            assert "AS3935 ready — indoor" in capsys.readouterr().err
        finally:
            log.handlers.clear()
            log.handlers.extend(vorher)

    def test_calling_it_twice_does_not_double_every_line(self, capsys):
        """The lifespan can run more than once in one process — under TestClient
        it does. Without the handler guard the second call stacks a second
        handler and every line from then on appears twice, which quietly
        doubles anything one later counts in the journal.
        """
        import logging
        from web.app import _configure_logging

        log = logging.getLogger("web")
        vorher, vorher_prop = list(log.handlers), log.propagate
        log.handlers.clear()
        try:
            _configure_logging()
            _configure_logging()
            assert len(log.handlers) == 1
            logging.getLogger("web.lightning_reader").info("einmal")
            assert capsys.readouterr().err.count("einmal") == 1
        finally:
            log.handlers.clear()
            log.handlers.extend(vorher)
            log.propagate = vorher_prop


# ──────────────────────────────────────────────────────────────────────────
# The database queries behind /api/lightning
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    """A real SQLite file — the schema and the local-day bounds are the point."""
    from web import db as weather_db

    vorher = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()

    monkeypatch.setattr(weather_db._local, "con", None, raising=False)
    weather_db.init_db(tmp_path / "test.db")
    yield weather_db
    weather_db._local.con.close()
    weather_db._local.con = None
    weather_db._db_path = None

    if vorher is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = vorher
    time.tzset()


class TestQueries:
    def _add(self, db, kind, distance=None, energy=None, ts=None):
        from datetime import datetime, timezone
        db.insert_lightning_event({
            "timestamp": ts or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind, "distance_km": distance, "energy": energy,
        })

    def test_counts_cover_every_kind_including_the_zeros(self, db):
        """A missing key would be indistinguishable from a kind that never
        occurred, and the disturber count is what proves the sensor is awake."""
        self._add(db, "lightning", 12.0, 0.5)
        self._add(db, "disturber")
        counts = db.query_lightning_today()
        assert counts == {"lightning": 1, "disturber": 1, "noise": 0, "unknown": 0}

    def test_yesterdays_events_do_not_count_towards_today(self, db):
        self._add(db, "lightning", 5.0, 0.1, ts="2020-01-01 12:00:00")
        assert db.query_lightning_today()["lightning"] == 0

    def test_microsecond_stamps_land_on_the_right_side_of_midnight(self, db):
        """The day bounds are truncated to whole seconds and compared as
        strings. A longer stamp sharing that prefix must still sort correctly —
        otherwise the first event of each local day would be miscounted, and
        nothing would say so.

        The boundary is derived with zoneinfo rather than written down as
        22:00 UTC: that figure is only right in summer, and a test that is
        wrong for half the year is worse than none.
        """
        import zoneinfo
        from datetime import date, datetime, time, timedelta, timezone

        tz = zoneinfo.ZoneInfo("Europe/Berlin")
        mitternacht_utc = (
            datetime.combine(date.today(), time(0, 0), tzinfo=tz)
            .astimezone(timezone.utc)
        )
        fmt = "%Y-%m-%d %H:%M:%S.%f"

        self._add(db, "lightning", 5.0, 0.1,
                  ts=(mitternacht_utc - timedelta(microseconds=1)).strftime(fmt))
        self._add(db, "lightning", 6.0, 0.2,
                  ts=(mitternacht_utc + timedelta(microseconds=1)).strftime(fmt))

        assert db.query_lightning_today()["lightning"] == 1

    def test_last_event_and_last_strike_are_different_questions(self, db):
        """The liveness answer: a disturber arrived after the last strike, so
        the sensor is demonstrably still hearing something."""
        self._add(db, "lightning", 12.0, 0.5)
        self._add(db, "disturber")

        assert db.query_lightning_last()["kind"] == "disturber"
        assert db.query_lightning_last("lightning")["distance_km"] == 12.0

    def test_empty_table_answers_none_rather_than_raising(self, db):
        assert db.query_lightning_last() is None
        assert db.query_lightning_today() == {
            "lightning": 0, "disturber": 0, "noise": 0, "unknown": 0}
