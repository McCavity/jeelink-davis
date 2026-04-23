#!/usr/bin/env python3
"""
Backfill InfluxDB v2 from the local SQLite database.

Reads outdoor + indoor readings from SQLite and writes them to InfluxDB in
batches.  Safe to re-run: InfluxDB deduplicates on (measurement, tags,
timestamp) so existing points are overwritten with identical data.

Usage (from project root, venv active):
    python tools/backfill_influxdb.py
    python tools/backfill_influxdb.py --since 2026-01-01
    python tools/backfill_influxdb.py --dry-run
    python tools/backfill_influxdb.py --batch-size 1000

Environment:
    INFLUXDB_TOKEN   InfluxDB API token (overrides config.toml token key)
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    cfg_path = PROJECT_ROOT / "config.toml"
    with open(cfg_path, "rb") as f:
        return tomllib.load(f)


def get_influxdb_params(cfg: dict) -> dict:
    idb = cfg.get("influxdb")
    if not idb:
        sys.exit("No [influxdb] section in config.toml — cannot backfill.")
    token = os.environ.get("INFLUXDB_TOKEN") or idb.get("token", "")
    if not token:
        sys.exit("No InfluxDB token — set INFLUXDB_TOKEN env var or add token to config.toml.")
    return {
        "url":    idb.get("url", "http://localhost:8086"),
        "token":  token,
        "org":    idb.get("org", ""),
        "bucket": idb.get("bucket", "weather"),
    }


def open_sqlite(cfg: dict) -> sqlite3.Connection:
    raw = cfg.get("storage", {}).get("db_path", "data/readings.db")
    db_path = Path(raw) if Path(raw).is_absolute() else PROJECT_ROOT / raw
    if not db_path.exists():
        sys.exit(f"SQLite database not found: {db_path}")
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def _parse_ts(ts_raw: str) -> datetime:
    ts_str = str(ts_raw).replace(" ", "T")
    if "+" not in ts_str[10:] and not ts_str.endswith("Z"):
        ts_str += "+00:00"
    return datetime.fromisoformat(ts_str)


def _outdoor_point(row: sqlite3.Row, bucket: str):
    from influxdb_client import Point  # type: ignore

    p = (
        Point("outdoor")
        .time(_parse_ts(row["timestamp"]))
        .tag("station_id", str(row["station_id"]))
    )
    if row["channel"] is not None:
        p = p.tag("channel", str(row["channel"]))

    for field in (
        "temperature", "humidity", "pressure",
        "wind_speed", "wind_direction", "wind_gust",
        "rain_tip_count", "rain_secs",
        "solar_radiation", "uv_index",
        "voltage_solar", "voltage_capacitor",
        "rssi", "battery_ok",
    ):
        val = row[field]
        if val is not None:
            p = p.field(field, float(val))
    return p


def _indoor_point(row: sqlite3.Row, bucket: str):
    from influxdb_client import Point  # type: ignore

    p = Point("indoor").time(_parse_ts(row["timestamp"]))
    for field in ("temperature", "humidity", "pressure"):
        val = row[field]
        if val is not None:
            p = p.field(field, float(val))
    return p


def backfill(
    params: dict,
    con: sqlite3.Connection,
    since: str | None,
    batch_size: int,
    dry_run: bool,
) -> None:
    try:
        from influxdb_client import InfluxDBClient  # type: ignore
        from influxdb_client.client.write_api import SYNCHRONOUS  # type: ignore
    except ImportError:
        sys.exit(
            "influxdb-client not installed. Run: pip install influxdb-client"
        )

    since_clause = f"AND timestamp >= '{since}'" if since else ""
    bucket = params["bucket"]

    outdoor_sql = f"""
        SELECT id, timestamp, station_id, channel, rssi, battery_ok,
               temperature, pressure, humidity,
               wind_speed, wind_direction, wind_gust,
               rain_tip_count, rain_secs,
               solar_radiation, uv_index, voltage_solar, voltage_capacitor
        FROM readings
        WHERE 1=1 {since_clause}
        ORDER BY timestamp ASC
    """
    indoor_sql = f"""
        SELECT id, timestamp, temperature, humidity, pressure
        FROM indoor_readings
        WHERE 1=1 {since_clause}
        ORDER BY timestamp ASC
    """

    outdoor_count = con.execute(
        f"SELECT COUNT(*) FROM readings WHERE 1=1 {since_clause}"
    ).fetchone()[0]
    indoor_count = con.execute(
        f"SELECT COUNT(*) FROM indoor_readings WHERE 1=1 {since_clause}"
    ).fetchone()[0]

    print(f"Outdoor readings to backfill: {outdoor_count}")
    print(f"Indoor readings to backfill:  {indoor_count}")
    if dry_run:
        print("[dry-run] No data written.")
        return

    written = 0
    with InfluxDBClient(url=params["url"], token=params["token"], org=params["org"]) as client:
        write_api = client.write_api(write_options=SYNCHRONOUS)

        # Outdoor
        batch: list = []
        for row in con.execute(outdoor_sql):
            batch.append(_outdoor_point(row, bucket))
            if len(batch) >= batch_size:
                write_api.write(bucket=bucket, record=batch)
                written += len(batch)
                print(f"  outdoor: {written}/{outdoor_count} written…", end="\r")
                batch = []
        if batch:
            write_api.write(bucket=bucket, record=batch)
            written += len(batch)
        print(f"  outdoor: {written} written.          ")

        # Indoor
        written = 0
        batch = []
        for row in con.execute(indoor_sql):
            batch.append(_indoor_point(row, bucket))
            if len(batch) >= batch_size:
                write_api.write(bucket=bucket, record=batch)
                written += len(batch)
                print(f"  indoor:  {written}/{indoor_count} written…", end="\r")
                batch = []
        if batch:
            write_api.write(bucket=bucket, record=batch)
            written += len(batch)
        print(f"  indoor:  {written} written.          ")

    print("Backfill complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", metavar="YYYY-MM-DD", help="Only backfill readings on or after this date")
    parser.add_argument("--dry-run", action="store_true", help="Count rows but do not write to InfluxDB")
    parser.add_argument("--batch-size", type=int, default=500, metavar="N", help="Points per InfluxDB write call (default: 500)")
    args = parser.parse_args()

    cfg = load_config()
    params = get_influxdb_params(cfg)
    con = open_sqlite(cfg)
    backfill(params, con, args.since, args.batch_size, args.dry_run)


if __name__ == "__main__":
    main()
