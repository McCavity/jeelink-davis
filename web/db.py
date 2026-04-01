"""
SQLite storage layer for Davis weather station readings.

Thread safety: each OS thread gets its own sqlite3.Connection via threading.local().
WAL mode allows concurrent readers alongside the single writer (reader thread).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path

_db_path: Path | None = None
_local = threading.local()

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;

CREATE TABLE IF NOT EXISTS readings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT    NOT NULL,
    station_id        INTEGER NOT NULL,
    channel           INTEGER,
    rssi              INTEGER,
    battery_ok        INTEGER,
    temperature       REAL,
    pressure          REAL,
    humidity          REAL,
    wind_speed        REAL,
    wind_direction    INTEGER,
    wind_gust         REAL,
    rain_tip_count    REAL,
    rain_secs         INTEGER,
    solar_radiation   REAL,
    uv_index          REAL,
    voltage_solar     REAL,
    voltage_capacitor REAL
);

CREATE INDEX IF NOT EXISTS idx_readings_timestamp ON readings (timestamp);
"""


def init_db(path: Path) -> None:
    """Create the database file, schema, and index if they don't exist."""
    global _db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _db_path = path
    con = _get_connection()
    con.executescript(_SCHEMA)


def _get_connection() -> sqlite3.Connection:
    con = getattr(_local, "con", None)
    if con is None:
        if _db_path is None:
            raise RuntimeError("init_db() must be called before using the database")
        con = sqlite3.connect(str(_db_path), check_same_thread=False)
        con.row_factory = sqlite3.Row
        _local.con = con
    return con


def insert_reading(payload: dict) -> None:
    """Insert a single reading dict (as produced by reader._reading_to_dict)."""
    con = _get_connection()
    con.execute(
        """
        INSERT INTO readings (
            timestamp, station_id, channel, rssi, battery_ok,
            temperature, pressure, humidity,
            wind_speed, wind_direction, wind_gust,
            rain_tip_count, rain_secs,
            solar_radiation, uv_index, voltage_solar, voltage_capacitor
        ) VALUES (
            :timestamp, :station_id, :channel, :rssi, :battery_ok,
            :temperature, :pressure, :humidity,
            :wind_speed, :wind_direction, :wind_gust,
            :rain_tip_count, :rain_secs,
            :solar_radiation, :uv_index, :voltage_solar, :voltage_capacitor
        )
        """,
        payload,
    )
    con.commit()


def query_recent(n: int) -> list[dict]:
    """Return last n readings in ascending chronological order."""
    con = _get_connection()
    rows = con.execute(
        """
        SELECT timestamp, temperature, humidity, wind_speed, wind_direction,
               wind_gust, rain_tip_count, rssi, pressure, solar_radiation, uv_index
        FROM   readings
        ORDER  BY timestamp DESC
        LIMIT  ?
        """,
        (min(n, 1000),),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def query_today_minmax() -> dict:
    """Return today's min/max/max-gust and rain total (tip delta * 0.2 mm)."""
    con = _get_connection()
    row = con.execute(
        """
        SELECT
            MIN(temperature)    AS temp_min,
            MAX(temperature)    AS temp_max,
            MIN(humidity)       AS humidity_min,
            MAX(humidity)       AS humidity_max,
            MIN(wind_speed)     AS wind_speed_min,
            MAX(wind_speed)     AS wind_speed_max,
            MAX(wind_gust)      AS wind_gust_max,
            MIN(rssi)           AS rssi_min,
            MAX(rssi)           AS rssi_max,
            MIN(rain_tip_count) AS rain_tip_min,
            MAX(rain_tip_count) AS rain_tip_max
        FROM readings
        WHERE date(timestamp, 'localtime') = date('now', 'localtime')
        """,
    ).fetchone()
    if row is None:
        return {}
    d = dict(row)
    tip_min = d.pop("rain_tip_min")
    tip_max = d.pop("rain_tip_max")
    if tip_min is not None and tip_max is not None:
        d["rain_mm"] = round((tip_max - tip_min) * 0.2, 1)
    else:
        d["rain_mm"] = None
    return d


def query_day_bucketed(day: str = "today") -> list[dict]:
    """
    Return temperature averaged per 5-minute bucket for a given calendar day.

    day: 'today' | 'yesterday' | 'YYYY-MM-DD'
    Returns list of {minute_bucket, temperature} sorted ascending.
    """
    if day == "today":
        target = date.today().isoformat()
    elif day == "yesterday":
        target = (date.today() - timedelta(days=1)).isoformat()
    else:
        import re
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
            raise ValueError(f"Invalid date: {day!r}")
        target = day

    con = _get_connection()
    rows = con.execute(
        """
        SELECT
            CAST(strftime('%H', timestamp, 'localtime') AS INTEGER) * 60 +
            (CAST(strftime('%M', timestamp, 'localtime') AS INTEGER) / 5) * 5
                AS minute_bucket,
            ROUND(AVG(temperature), 2) AS temperature
        FROM readings
        WHERE date(timestamp, 'localtime') = ?
          AND temperature IS NOT NULL
        GROUP BY minute_bucket
        ORDER BY minute_bucket
        """,
        (target,),
    ).fetchall()
    return [dict(r) for r in rows]


def query_stats(period: str) -> list[dict]:
    """
    Return aggregated stats grouped by period.

    period: 'daily' | 'monthly' | 'yearly'

    Rain is computed as the sum of per-day (MAX-MIN) deltas so that daily
    counter resets don't corrupt monthly/yearly totals.
    """
    if period not in ("daily", "monthly", "yearly"):
        raise ValueError(f"Unknown period: {period!r}")

    con = _get_connection()

    if period == "daily":
        rows = con.execute(
            """
            SELECT
                strftime('%Y-%m-%d', timestamp, 'localtime') AS period,
                MIN(temperature)  AS temp_min,
                MAX(temperature)  AS temp_max,
                AVG(temperature)  AS temp_avg,
                MIN(humidity)     AS humidity_min,
                MAX(humidity)     AS humidity_max,
                AVG(humidity)     AS humidity_avg,
                MIN(wind_speed)   AS wind_speed_min,
                MAX(wind_speed)   AS wind_speed_max,
                AVG(wind_speed)   AS wind_speed_avg,
                MAX(wind_gust)    AS wind_gust_max,
                MIN(rssi)         AS rssi_min,
                MAX(rssi)         AS rssi_max,
                AVG(rssi)         AS rssi_avg,
                (MAX(rain_tip_count) - MIN(rain_tip_count)) * 0.2 AS rain_mm,
                COUNT(*)          AS sample_count
            FROM readings
            GROUP BY period
            ORDER BY period ASC
            """
        ).fetchall()

    else:
        outer_fmt = "'%Y-%m'" if period == "monthly" else "'%Y'"
        rows = con.execute(
            f"""
            SELECT
                strftime({outer_fmt}, day, 'localtime') AS period,
                MIN(temp_min)      AS temp_min,
                MAX(temp_max)      AS temp_max,
                AVG(temp_avg)      AS temp_avg,
                MIN(humidity_min)  AS humidity_min,
                MAX(humidity_max)  AS humidity_max,
                AVG(humidity_avg)  AS humidity_avg,
                MIN(ws_min)        AS wind_speed_min,
                MAX(ws_max)        AS wind_speed_max,
                AVG(ws_avg)        AS wind_speed_avg,
                MAX(wg_max)        AS wind_gust_max,
                MIN(rssi_min)      AS rssi_min,
                MAX(rssi_max)      AS rssi_max,
                AVG(rssi_avg)      AS rssi_avg,
                SUM(daily_rain_mm) AS rain_mm,
                SUM(sample_count)  AS sample_count
            FROM (
                SELECT
                    strftime('%Y-%m-%d', timestamp, 'localtime') AS day,
                    MIN(temperature)  AS temp_min,
                    MAX(temperature)  AS temp_max,
                    AVG(temperature)  AS temp_avg,
                    MIN(humidity)     AS humidity_min,
                    MAX(humidity)     AS humidity_max,
                    AVG(humidity)     AS humidity_avg,
                    MIN(wind_speed)   AS ws_min,
                    MAX(wind_speed)   AS ws_max,
                    AVG(wind_speed)   AS ws_avg,
                    MAX(wind_gust)    AS wg_max,
                    MIN(rssi)         AS rssi_min,
                    MAX(rssi)         AS rssi_max,
                    AVG(rssi)         AS rssi_avg,
                    (MAX(rain_tip_count) - MIN(rain_tip_count)) * 0.2 AS daily_rain_mm,
                    COUNT(*)          AS sample_count
                FROM readings
                GROUP BY day
            )
            GROUP BY period
            ORDER BY period ASC
            """
        ).fetchall()

    return [dict(r) for r in rows]
