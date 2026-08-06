#!/usr/bin/env python3
"""List and retract retained MQTT messages under a topic prefix.

Retained messages outlive the publisher. When the topic layout changed on
2026-08-06 (``davis/weather/…`` → ``davis/<source>/…``) the old topics kept
their last values at the broker: consumers would have gone on reading a
sawtooth of mixed indoor/outdoor readings, and ``davis/weather/feels_like``
would have kept showing a value from 2026-04-24. Deploying the new code is
therefore only half the change — the old topics have to be retracted.

Default is READ-ONLY: it lists what is retained and exits. ``--clear`` retracts,
then re-reads and reports what is left, so the result is a measurement rather
than an assertion.

    python tools/clear_retained.py --prefix 'davis/weather/#'
    python tools/clear_retained.py --prefix 'davis/weather/#' --clear

Credentials come from config.toml (section [mqtt]) or the environment
(MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD). Nothing is printed that
would expose them.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import tomllib
from pathlib import Path

import paho.mqtt.client as mqtt


def load_cfg(path: Path) -> dict:
    cfg: dict = {}
    if path.is_file():
        with path.open("rb") as fh:
            cfg = tomllib.load(fh).get("mqtt", {}) or {}
    return {
        "host": os.environ.get("MQTT_HOST", cfg.get("host", "localhost")),
        "port": int(os.environ.get("MQTT_PORT", cfg.get("port", 1883))),
        "username": os.environ.get("MQTT_USERNAME", cfg.get("username", "")),
        "password": os.environ.get("MQTT_PASSWORD", cfg.get("password", "")),
    }


def collect(cfg: dict, prefix: str, seconds: float) -> dict[str, str]:
    """Subscribe and gather retained messages. Retained ones arrive at once."""
    found: dict[str, str] = {}

    def on_connect(c, u, flags, rc, props=None):
        c.subscribe(prefix, qos=1)

    def on_message(c, u, msg):
        found[msg.topic] = msg.payload.decode(errors="replace")

    client = mqtt.Client(
        client_id="clear-retained-probe",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.username_pw_set(cfg["username"], cfg["password"])
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(cfg["host"], cfg["port"], keepalive=30)
    client.loop_start()
    time.sleep(seconds)
    client.loop_stop()
    client.disconnect()
    return found


def retract(cfg: dict, topics: list[str]) -> None:
    client = mqtt.Client(
        client_id="clear-retained-writer",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.username_pw_set(cfg["username"], cfg["password"])
    client.connect(cfg["host"], cfg["port"], keepalive=30)
    client.loop_start()
    for topic in topics:
        # Empty payload + retain=True is the MQTT way to delete a retained
        # message. Publishing "0" or "null" would leave a value standing.
        info = client.publish(topic, payload=b"", qos=1, retain=True)
        info.wait_for_publish(timeout=5)
    time.sleep(1.0)
    client.loop_stop()
    client.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml", type=Path)
    ap.add_argument("--prefix", default="davis/weather/#",
                    help="topic filter, e.g. 'davis/weather/#'")
    ap.add_argument("--wait", type=float, default=3.0,
                    help="seconds to collect retained messages")
    ap.add_argument("--clear", action="store_true",
                    help="retract what was found (default: read-only)")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    print(f"Broker {cfg['host']}:{cfg['port']}, filter {args.prefix!r}\n")

    before = collect(cfg, args.prefix, args.wait)
    if not before:
        print("No retained messages under this prefix — nothing to do.")
        return 0

    print(f"Retained before: {len(before)}")
    for topic in sorted(before):
        print(f"  {topic} = {before[topic]!r}")

    if not args.clear:
        print("\nRead-only. Re-run with --clear to retract these.")
        return 0

    print(f"\nRetracting {len(before)} topics …")
    retract(cfg, sorted(before))

    after = collect(cfg, args.prefix, args.wait)
    print(f"\nRetained after: {len(after)}")
    for topic in sorted(after):
        print(f"  {topic} = {after[topic]!r}")

    if after:
        print("\nNOT clean — some topics still carry a retained payload.")
        return 1
    print("\nClean: the broker no longer serves a value under this prefix.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
