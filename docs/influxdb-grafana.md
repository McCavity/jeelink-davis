# InfluxDB & Grafana Integration

This is an optional integration that stores all weather readings in **InfluxDB v2** for long-term retention and visualises them in a pre-built **Grafana dashboard**. It also covers exporting system metrics from the Raspberry Pi host via **Telegraf** so that CPU, memory, temperature, and service health appear in the same dashboard.

---

## Prerequisites

| Component | Tested version | Notes |
|---|---|---|
| InfluxDB v2 | 2.7 | Self-hosted or InfluxDB Cloud |
| Grafana | 9.3+ | Self-hosted |
| Telegraf | 1.33+ | Optional, for host metrics |

Both InfluxDB and Grafana are commonly installed on a home server or NAS. The weather station host (Raspberry Pi) only needs network access to the InfluxDB HTTP API.

---

## InfluxDB Setup

### 1. Create a bucket

In the InfluxDB UI under **Data → Buckets**, create a bucket named `weather`. A retention policy of 90 days covers typical use; the downsampling task (see below) creates hourly summaries that can be kept indefinitely.

### 2. Create an API token

Under **Data → API Tokens**, create an **All-Access** token or a scoped write token for the `weather` bucket. Copy the token — you will need it in the next step.

### 3. Configure jeelink-davis

Add an `[influxdb]` section to `config.toml`:

```toml
[influxdb]
url    = "http://192.168.1.100:8086"   # InfluxDB host
org    = "My Home"                      # your InfluxDB organisation name
bucket = "weather"
# token = "paste-token-here"           # or use INFLUXDB_TOKEN env var (preferred)
```

The token should be provided via the environment rather than stored in `config.toml`:

```bash
# /etc/davis-weather.env  (loaded by the systemd service via EnvironmentFile=)
INFLUXDB_TOKEN=your-token-here
```

If `[influxdb]` is absent from `config.toml` the writer thread never starts and no metrics are sent to InfluxDB.

### 4. Measurement layout

Two measurements are written to the `weather` bucket:

| Measurement | Source | Key fields |
|---|---|---|
| `davis_weather` | Davis ISS (outdoor) | temperature, humidity, wind_speed, wind_direction, wind_gust, rain_rate, rssi, battery_ok, feels_like |
| `indoor_climate` | GY-BME280 (indoor) | temperature, humidity, pressure |

---

## Hourly Downsampling Task

Raw readings arrive every ~41 seconds and accumulate quickly. The included Flux task aggregates them into hourly summaries (mean/min/max/sum where appropriate) and writes them to a separate `weather_hourly` bucket, reducing storage and speeding up long-range Grafana queries.

### Setup

1. Create a second bucket named `weather_hourly` in InfluxDB (unlimited retention recommended).
2. In the InfluxDB UI under **Tasks**, click **Create Task → Import Task** and upload `docs/influxdb-task-hourly-downsample.json`, or paste the Flux script from `docs/influxdb-task-hourly-downsample.flux` into a new task.
3. Set the task interval to `1h` if not already set in the script.

### Backfill

If you have existing data in the `weather` bucket and want to populate `weather_hourly` retroactively:

```bash
# From the project root on the Pi:
.venv/bin/python tools/backfill_influxdb.py \
  --url http://192.168.1.100:8086 \
  --token "$INFLUXDB_TOKEN" \
  --org "My Home" \
  --start 2024-01-01T00:00:00Z
```

---

## Grafana Dashboard

The pre-built dashboard covers outdoor conditions, the indoor BME280 sensor, signal quality, rain totals, and (optionally) host system metrics from Telegraf.

### Import

1. In Grafana, go to **Dashboards → Import**.
2. Upload `docs/grafana-davis-dashboard.json` or paste its contents.
3. On the import screen, select your InfluxDB data source for both the `weather` and `ProxMox` (or your system metrics) data source variables.
4. Click **Import**.

The dashboard uses Flux queries throughout. Make sure your InfluxDB data source in Grafana is configured with **Flux** as the query language (not InfluxQL).

---

## Telegraf — Host Metrics (optional)

Telegraf collects CPU, memory, disk, and temperature from the Raspberry Pi and publishes them to InfluxDB. The Grafana dashboard has a dedicated "System" row that reads from these metrics.

### Install

```bash
# Raspberry Pi (arm64) — download the .deb directly if apt-repo GPG fails on newer Debian:
wget https://dl.influxdata.com/telegraf/releases/telegraf_1.33.0-1_arm64.deb
sudo dpkg -i telegraf_1.33.0-1_arm64.deb
sudo systemctl enable telegraf
```

### Configure

Create `/etc/telegraf/telegraf.conf` (may be empty — Telegraf requires the file to exist) and add a drop-in at `/etc/telegraf/telegraf.d/host.conf`:

```toml
[agent]
  interval = "30s"
  flush_interval = "30s"

[[outputs.influxdb_v2]]
  urls    = ["http://192.168.1.100:8086"]
  token   = "$INFLUX_TOKEN"
  org     = "My Home"
  bucket  = "ProxMox"          # or any bucket you prefer for system metrics

[[inputs.cpu]]
  percpu = false
  totalcpu = true
  collect_cpu_time = false

[[inputs.mem]]

[[inputs.disk]]
  ignore_fs = ["tmpfs", "devtmpfs", "devfs", "iso9660", "overlay", "aufs", "squashfs"]

[[inputs.system]]

[[inputs.temp]]

[[inputs.systemd_units]]
  unittype = "service"
  pattern = "davis-weather.service"
```

Store the InfluxDB token in `/etc/default/telegraf` so it is not in the config file:

```bash
# /etc/default/telegraf
INFLUX_TOKEN=your-token-here
```

```bash
sudo systemctl restart telegraf
sudo systemctl status telegraf
```

### Grafana — System metrics data source

The dashboard's "System" row queries a data source named `ProxMox` (the default name in the exported JSON). In Grafana under **Data Sources**, either rename your InfluxDB source to `ProxMox` or edit the panel queries to match your actual data source name.

The `active_code` field from `systemd_units` maps to service state: `0` = running (green), `2` = inactive (orange), `3` = failed (red).
