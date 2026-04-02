# jeelink-davis

Python library and live web dashboard for **Davis Vantage Pro 2** weather station data received via a **JeeLink USB receiver**.

The JeeLink must be flashed with Davis firmware 0.8e (RFM69, EU 868 MHz). The library auto-detects the JeeLink by USB VID/PID so it works without configuration on macOS, Raspberry Pi, etc.

The bundled web dashboard (`web/`) provides a live SSE-driven display of all sensor values, a 24-hour rolling temperature chart with sun elevation overlay, rain rate tracking, historical data browser (1D / 7D / 1M / 1Y / custom), and a 5-day forecast via Open-Meteo.

## Requirements

- Python 3.11+
- JeeLink USB receiver with Davis firmware 0.8e
- Davis Vantage Pro 2 ISS (outdoor sensor unit)

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .          # library only
pip install -e ".[web]"   # library + web dashboard dependencies
pip install -e ".[dev]"   # library + test dependencies
```

## Usage

```python
from jeelink_davis import DavisStation

with DavisStation() as station:        # auto-detects the JeeLink
    for reading in station.readings():
        print(f"{reading.timestamp}  "
              f"T={reading.temperature}  "
              f"H={reading.humidity}%  "
              f"Wind={reading.wind_speed} @ {reading.wind_direction}°  "
              f"RSSI={reading.rssi} dBm")
```

Pass `port=` explicitly if needed (e.g. multiple USB serial devices):

```python
with DavisStation(port="/dev/ttyUSB0") as station:
    ...
```

## Tools

```bash
# Detect JeeLink and report its port
python tools/detect.py

# Raw listener — prints everything from the JeeLink for 60 seconds
python tools/sniff.py
python tools/sniff.py --duration 120
```

## Running tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Tests do not require hardware — the serial port is fully mocked.

## Data model

`readings()` yields `WeatherReading` dataclass instances. All values are raw firmware values; unit conversion is left to the caller.

| Field | Sensor | Notes |
|---|---|---|
| `temperature` | Temperature | °C (EU firmware confirmed) |
| `pressure` | Barometric pressure | hPa — not yet received (no external barometer fitted) |
| `humidity` | Relative humidity | % |
| `wind_speed` | Wind speed | m/s (EU firmware confirmed) |
| `wind_direction` | Wind direction | degrees 0–359 |
| `wind_gust` | Wind gust speed | m/s (EU firmware confirmed) |
| `rain_tip_count` | Cumulative tip counter | 7-bit (0–127), wraps on overflow, resets on ISS power cycle; 1 tip = 0.2 mm (EU Davis) |
| `rain_secs` | Inter-tip interval | Seconds between the last two consecutive tips; sentinel < 0 = no rain; rain rate = 720 / rain_secs mm/h |
| `solar_radiation` | Solar radiation | W/m² |
| `uv_index` | UV index | |
| `voltage_solar` | Solar panel voltage | V |
| `voltage_capacitor` | Capacitor voltage | V |
| `rssi` | Signal strength | dBm |
| `battery_ok` | Battery status | `True` / `False` |
| `channel` | ISS channel | |
| `soil_temperature` | Soil temperature by zone | dict, zones 1–4 |
| `soil_moisture` | Soil moisture by zone | dict, zones 1–4 |
| `leaf_wetness` | Leaf wetness by zone | dict, zones 1–4 |

## License

MIT
