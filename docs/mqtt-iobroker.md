# MQTT / ioBroker Integration

This is an optional integration that publishes live weather readings to an **MQTT broker** as retained topics. It is designed to work with **ioBroker** (MQTT adapter) but is compatible with any MQTT broker — Home Assistant, Node-RED, openHAB, etc.

---

## How It Works

A background daemon thread (`web/mqtt_publisher.py`) picks up each incoming reading from an internal queue and publishes individual numeric topics under `davis/<source>/<field>`. Topics are published with `retain=True` and `QoS 1`, so any subscriber always receives the most recent value immediately on connect.

Readings arrive from two sources, and **each owns its own prefix**:
- **Davis ISS** (outdoor) — every ~41 seconds via the JeeLink receiver → `davis/outdoor/…`
- **GY-BME280** (indoor) — every 60 seconds → `davis/indoor/…`

`davis/lightning/…` is reserved for the AS3935 lightning sensor and not published yet.

If the broker is unreachable at startup, the thread retries with exponential backoff (10 s, doubling, capped at 5 min) instead of giving up. If the broker disconnects while running, paho-mqtt reconnects automatically.

---

## Prerequisites

- An MQTT broker reachable from the Raspberry Pi (Mosquitto, ioBroker MQTT adapter, etc.)
- `paho-mqtt>=2.0` — already included in the `[web]` extras:
  ```bash
  pip install -e ".[web]"
  ```

### ioBroker

Install the **MQTT adapter** from the ioBroker adapter list. The adapter can act as a broker itself (default port `1883`) or connect to an external broker. In either case note:

- **Port** — the ioBroker MQTT adapter defaults to port `1883`; if another adapter already occupies it, it is commonly moved to `1884`.
- **Authentication** — set a username and password in the adapter settings.
- **Retain** — make sure "Store retain messages" is enabled in the adapter settings so that values survive adapter restarts.

Once datapoints are received they appear in ioBroker's object tree under `mqtt.0.davis.<source>.*` and can be used in scripts, visualisations, and automations like any other state.

---

## Configuration

Add an `[mqtt]` section to `config.toml`:

```toml
[mqtt]
host     = "192.168.1.100"   # IP or hostname of your MQTT broker / ioBroker host
port     = 1883              # default; use 1884 if the standard port is taken
username = "your-username"
# password = "your-password" # or use MQTT_PASSWORD env var (preferred)
```

The password should be provided via the environment rather than stored in `config.toml`:

```bash
# /etc/davis-weather.env  (loaded by the systemd service via EnvironmentFile=)
MQTT_PASSWORD=your-password-here
```

If `[mqtt]` is absent from `config.toml` the publisher thread never starts.

---

## Topic Reference

Each source publishes under its own prefix. A field belongs to exactly one source.

| Topic | Unit | Source | Notes |
|---|---|---|---|
| `davis/outdoor/temperature` | °C | Davis ISS | Outdoor air temperature |
| `davis/outdoor/humidity` | % | Davis ISS | Outdoor relative humidity |
| `davis/outdoor/wind_speed` | m/s | Davis ISS | 10-second average |
| `davis/outdoor/wind_direction` | ° | Davis ISS | 0–359 |
| `davis/outdoor/wind_gust` | m/s | Davis ISS | Highest gust in reporting period |
| `davis/outdoor/rain_rate` | mm/h | Davis ISS | Computed from inter-tip interval; 0.0 if no rain |
| `davis/outdoor/rssi` | dBm | JeeLink receiver | Signal strength of last ISS packet |
| `davis/outdoor/battery_ok` | 0 / 1 | Davis ISS | 1 = battery OK, 0 = low |
| `davis/outdoor/feels_like` | °C | Derived | Australian BOM apparent temperature: T + 0.33·e − 0.70·v − 4.00 |
| `davis/indoor/temperature` | °C | BME280 | Indoor air temperature |
| `davis/indoor/humidity` | % | BME280 | Indoor relative humidity |
| `davis/indoor/pressure` | hPa | BME280 | Published on each poll (60 s) |

### Why the source is in the topic

Before 2026-08-06 all sources shared one prefix (`davis/weather/`). The Davis reader
and the BME280 poller therefore wrote **the same** `temperature` and `humidity`
topics and overwrote each other; consumers saw a sawtooth between outdoor and
indoor values (measured 2026-07-29: ten samples in two minutes alternating
between 20.8 °C and 36.7 °C). Splitting by source removes the collision by
construction.

### `feels_like` can be retracted

It is derived from temperature, humidity **and** wind speed. A single ISS packet
carries only a subset of fields, so it is computed from the most recent value of
each — but only while all three are younger than 10 minutes. If an input stops
arriving, the topic is **retracted** (empty retained payload) rather than left
standing.

That is not theoretical: the old `davis/weather/feels_like` showed 17.9 °C from
2026-04-24 to 2026-08-06 because a JeeLink firmware defect suppressed the wind
fields and the retained message simply survived. In ioBroker the state looked
alive the whole time.

### Migrating from `davis/weather/`

The old retained topics keep their values at the broker until someone deletes
them — deploying the new code is only half the change. Retract them with:

```bash
python tools/clear_retained.py --prefix 'davis/weather/#'          # read-only
python tools/clear_retained.py --prefix 'davis/weather/#' --clear  # retract
```

The tool re-reads afterwards and reports what is left, so the result is a
measurement and not an assertion. Stale ioBroker objects under
`mqtt.0.davis.weather.*` have to be deleted in ioBroker itself.

All values are published as plain numeric strings (e.g. `"19.5"`).

---

## ioBroker Object Tree

After the first reading arrives, ioBroker creates the following states automatically (object IDs may vary slightly depending on your adapter instance number):

```
mqtt.0.davis.outdoor.temperature
mqtt.0.davis.outdoor.humidity
mqtt.0.davis.outdoor.wind_speed
mqtt.0.davis.outdoor.wind_direction
mqtt.0.davis.outdoor.wind_gust
mqtt.0.davis.outdoor.rain_rate
mqtt.0.davis.outdoor.rssi
mqtt.0.davis.outdoor.battery_ok
mqtt.0.davis.outdoor.feels_like
mqtt.0.davis.indoor.temperature
mqtt.0.davis.indoor.humidity
mqtt.0.davis.indoor.pressure
```

These states update automatically whenever a new reading is published. Use them in **Blockly scripts**, **JavaScript adapter**, **VIS dashboards**, or **ButtonPlus** button configurations like any other ioBroker state.

---

## Troubleshooting

**Topics never appear on the broker**

1. Check that `[mqtt]` exists in `config.toml` on the deployed host (not just locally).
2. Verify `MQTT_PASSWORD` is set in `/etc/davis-weather.env`.
3. Check service logs: `sudo journalctl -u davis-weather -n 50 | grep -i mqtt`
4. Confirm the broker is reachable: `nc -zv <host> <port>`

**`on_connect` with paho-mqtt 2.x**

paho-mqtt 2.x passes a `ReasonCode` object (not a plain integer) as the `rc` argument. The publisher uses `rc.value if hasattr(rc, "value") else rc` to handle both API versions. If you see repeated reconnection attempts without a "connected" log message, check that paho-mqtt is version 2.0 or newer (`pip show paho-mqtt`).
