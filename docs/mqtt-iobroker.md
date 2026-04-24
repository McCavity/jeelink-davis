# MQTT / ioBroker Integration

This is an optional integration that publishes live weather readings to an **MQTT broker** as retained topics. It is designed to work with **ioBroker** (MQTT adapter) but is compatible with any MQTT broker — Home Assistant, Node-RED, openHAB, etc.

---

## How It Works

A background daemon thread (`web/mqtt_publisher.py`) picks up each incoming reading from an internal queue and publishes individual numeric topics under the prefix `davis/weather/`. Topics are published with `retain=True` and `QoS 1`, so any subscriber always receives the most recent value immediately on connect.

Readings arrive from two sources:
- **Davis ISS** (outdoor) — every ~41 seconds via the JeeLink receiver
- **GY-BME280** (indoor) — every 60 seconds (pressure appears under `davis/weather/pressure`)

If the broker is unreachable at startup the thread exits silently. If the broker disconnects while running, paho-mqtt reconnects automatically.

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

Once datapoints are received they appear in ioBroker's object tree under `mqtt.0.davis.weather.*` and can be used in scripts, visualisations, and automations like any other state.

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

All topics are published under the prefix `davis/weather/`.

| Topic | Unit | Source | Notes |
|---|---|---|---|
| `davis/weather/temperature` | °C | Davis ISS | Outdoor air temperature |
| `davis/weather/humidity` | % | Davis ISS | Outdoor relative humidity |
| `davis/weather/wind_speed` | m/s | Davis ISS | 10-second average |
| `davis/weather/wind_direction` | ° | Davis ISS | 0–359 |
| `davis/weather/wind_gust` | m/s | Davis ISS | Highest gust in reporting period |
| `davis/weather/rain_rate` | mm/h | Davis ISS | Computed from inter-tip interval; 0.0 if no rain |
| `davis/weather/pressure` | hPa | BME280 (indoor) | Published on each BME280 poll (60 s) |
| `davis/weather/rssi` | dBm | JeeLink receiver | Signal strength of last ISS packet |
| `davis/weather/battery_ok` | 0 / 1 | Davis ISS | 1 = battery OK, 0 = low |
| `davis/weather/feels_like` | °C | Computed | Australian BOM apparent temperature formula: T + 0.33·e − 0.70·v − 4.00 |

All values are published as plain numeric strings (e.g. `"19.5"`).

---

## ioBroker Object Tree

After the first reading arrives, ioBroker creates the following states automatically (object IDs may vary slightly depending on your adapter instance number):

```
mqtt.0.davis.weather.temperature
mqtt.0.davis.weather.humidity
mqtt.0.davis.weather.wind_speed
mqtt.0.davis.weather.wind_direction
mqtt.0.davis.weather.wind_gust
mqtt.0.davis.weather.rain_rate
mqtt.0.davis.weather.pressure
mqtt.0.davis.weather.rssi
mqtt.0.davis.weather.battery_ok
mqtt.0.davis.weather.feels_like
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
