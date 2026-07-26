"""
Tests for the local-day → UTC range bounds that replaced the
``date(timestamp, 'localtime')`` filters — no hardware, no production DB.

The point of the rewrite was speed (SEARCH instead of SCAN over ~3.5 M rows),
so the only thing worth testing is that it did not change *which* rows are
selected. Every test below therefore compares the new half-open range against
the old expression on the same synthetic data, or pins a boundary the old form
got right by construction.
"""

import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from web.db import _TS_FMT_INDOOR, _TS_FMT_READINGS, _local_day_bounds


@pytest.fixture(autouse=True)
def berlin_tz():
    """Pin the OS timezone: both `localtime` and `utc` in SQLite honour TZ, and
    the whole point of the change is that the two agree. A test that silently
    ran in UTC would pass without exercising any offset at all."""
    vorher = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    yield
    if vorher is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = vorher
    time.tzset()


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    yield c
    c.close()


class TestLocalDayBounds:
    def test_summer_day_is_24h_and_starts_at_22z(self, con):
        lo, hi = _local_day_bounds(con, "2026-07-26", "2026-07-26", _TS_FMT_READINGS)
        assert lo == "2026-07-25T22:00:00"
        assert hi == "2026-07-26T22:00:00"

    def test_winter_day_starts_at_23z(self, con):
        lo, hi = _local_day_bounds(con, "2026-01-15", "2026-01-15", _TS_FMT_READINGS)
        assert lo == "2026-01-14T23:00:00"
        assert hi == "2026-01-15T23:00:00"

    def test_dst_autumn_day_has_25_hours(self, con):
        """2026-10-25: clocks go back, the local day is 25 hours long. A naive
        `start + timedelta(days=1)` with a fixed offset would end an hour early
        and silently drop the last hour of data."""
        lo, hi = _local_day_bounds(con, "2026-10-25", "2026-10-25", _TS_FMT_READINGS)
        assert lo == "2026-10-24T22:00:00"
        assert hi == "2026-10-25T23:00:00"
        spanne = datetime.fromisoformat(hi) - datetime.fromisoformat(lo)
        assert spanne == timedelta(hours=25)

    def test_dst_spring_day_has_23_hours(self, con):
        lo, hi = _local_day_bounds(con, "2026-03-29", "2026-03-29", _TS_FMT_READINGS)
        spanne = datetime.fromisoformat(hi) - datetime.fromisoformat(lo)
        assert spanne == timedelta(hours=23)

    def test_multi_day_range_is_inclusive_of_end_day(self, con):
        lo, hi = _local_day_bounds(con, "2026-07-01", "2026-07-03", _TS_FMT_READINGS)
        assert lo == "2026-06-30T22:00:00"
        assert hi == "2026-07-03T22:00:00"   # midnight *after* the 3rd

    def test_indoor_format_uses_a_space_not_a_T(self, con):
        """The two tables store different lexical shapes. ' ' sorts before 'T',
        so a readings-shaped bound against indoor_readings (or vice versa) would
        match the wrong rows without raising anything."""
        lo, hi = _local_day_bounds(con, "2026-07-26", "2026-07-26", _TS_FMT_INDOOR)
        assert lo == "2026-07-25 22:00:00"
        assert hi == "2026-07-26 22:00:00"
        assert "T" not in lo and "T" not in hi


def _befuellen(con, table, stamps):
    con.execute(f"CREATE TABLE {table} (timestamp TEXT)")
    con.executemany(f"INSERT INTO {table} VALUES (?)", [(s,) for s in stamps])


class TestSelectsTheSameRows:
    """Old expression vs. new range on identical data."""

    def test_same_rows_as_the_old_localtime_filter(self, con):
        basis = datetime(2026, 7, 24, 0, 0, tzinfo=timezone.utc)
        stamps = [(basis + timedelta(minutes=17 * i)).isoformat() for i in range(400)]
        _befuellen(con, "readings", stamps)

        for tag in ("2026-07-24", "2026-07-25"):
            alt = {r[0] for r in con.execute(
                "SELECT timestamp FROM readings WHERE date(timestamp,'localtime') = ?",
                (tag,))}
            lo, hi = _local_day_bounds(con, tag, tag, _TS_FMT_READINGS)
            neu = {r[0] for r in con.execute(
                "SELECT timestamp FROM readings WHERE timestamp >= ? AND timestamp < ?",
                (lo, hi))}
            assert neu == alt, f"{tag}: {len(neu)} != {len(alt)}"
            assert alt, "Testdaten treffen den Tag gar nicht — Test waere wertlos"

    def test_midnight_boundary_row_belongs_to_the_new_day(self, con):
        """A reading exactly at local midnight is the first of the new day, not
        the last of the old one — that is what half-open means."""
        _befuellen(con, "readings", [
            "2026-07-25T21:59:59.999999+00:00",   # 23:59:59 lokal, 25.07.
            "2026-07-25T22:00:00.000000+00:00",   # 00:00:00 lokal, 26.07.
        ])
        lo, hi = _local_day_bounds(con, "2026-07-26", "2026-07-26", _TS_FMT_READINGS)
        treffer = [r[0] for r in con.execute(
            "SELECT timestamp FROM readings WHERE timestamp >= ? AND timestamp < ? ORDER BY 1",
            (lo, hi))]
        assert treffer == ["2026-07-25T22:00:00.000000+00:00"]

    def test_bound_without_offset_still_admits_full_iso_timestamps(self, con):
        """The bounds are truncated to seconds while the stored strings carry
        microseconds and '+00:00'. That is only safe because the stored value
        shares the bound's prefix and is never shorter."""
        _befuellen(con, "readings", ["2026-07-26T21:59:59.000001+00:00"])
        lo, hi = _local_day_bounds(con, "2026-07-26", "2026-07-26", _TS_FMT_READINGS)
        n = con.execute(
            "SELECT COUNT(*) FROM readings WHERE timestamp >= ? AND timestamp < ?",
            (lo, hi)).fetchone()[0]
        assert n == 1

    def test_wrong_format_constant_is_detectably_wrong(self, con):
        """Calibration: an instrument that cannot go red proves nothing. Feeding
        indoor-shaped bounds to readings-shaped data must NOT quietly return the
        right answer — if this ever passes, the format distinction has stopped
        mattering and the guard above is worthless."""
        _befuellen(con, "readings", [
            "2026-07-25T10:00:00.000000+00:00",   # gehoert NICHT zum 26.07. lokal
            "2026-07-26T10:00:00.000000+00:00",
        ])
        lo_falsch, hi_falsch = _local_day_bounds(
            con, "2026-07-26", "2026-07-26", _TS_FMT_INDOOR)
        falsch = {r[0] for r in con.execute(
            "SELECT timestamp FROM readings WHERE timestamp >= ? AND timestamp < ?",
            (lo_falsch, hi_falsch))}
        lo, hi = _local_day_bounds(con, "2026-07-26", "2026-07-26", _TS_FMT_READINGS)
        richtig = {r[0] for r in con.execute(
            "SELECT timestamp FROM readings WHERE timestamp >= ? AND timestamp < ?",
            (lo, hi))}
        assert falsch != richtig
