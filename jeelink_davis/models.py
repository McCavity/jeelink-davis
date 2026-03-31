"""
Data models for JeeLink Davis readings.

Unit notes (EU firmware 0.8e, unconfirmed until live packets observed):
  Temperature     °C
  Pressure        hPa
  Humidity        %
  WindSpeed       m/s
  WindDirection   degrees (0-359)
  WindGust        m/s
  WindGustRef     (firmware-internal reference value)
  RainTipCount    tip count (each tip = 0.2 mm on EU Davis)
  RainSecs        seconds since last tip
  Solar           W/m²
  VoltageSolar    V
  VoltageCapacitor V
  UV              UV index
  RSSI            dBm
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class WeatherReading:
    """A single decoded reading from the Davis ISS."""

    timestamp: datetime
    station_id: int

    # Meta / link quality
    channel: int | None = None
    rssi: int | None = None          # dBm
    battery_ok: bool | None = None

    # Atmospheric
    temperature: float | None = None
    pressure: float | None = None
    humidity: float | None = None

    # Wind
    wind_speed: float | None = None
    wind_direction: int | None = None
    wind_gust: float | None = None
    wind_gust_ref: int | None = None

    # Rain
    rain_tip_count: float | None = None
    rain_secs: int | None = None

    # Solar / UV
    solar_radiation: float | None = None
    voltage_solar: float | None = None
    voltage_capacitor: float | None = None
    uv_index: float | None = None

    # Soil / leaf (indexed by zone 1-4)
    soil_temperature: dict[int, float] = field(default_factory=dict)
    soil_moisture: dict[int, float] = field(default_factory=dict)
    leaf_wetness: dict[int, float] = field(default_factory=dict)

    # Any fields the library does not recognise yet
    extra_fields: dict[str, str] = field(default_factory=dict)


# Mapping from INIT DICTIONARY field codes to WeatherReading attribute names.
# Dotted codes (e.g. "15.1") are handled separately in the parser.
FIELD_CODE_MAP: dict[str, str] = {
    "1": "temperature",
    "2": "pressure",
    "3": "humidity",
    "4": "wind_speed",
    "5": "wind_direction",
    "6": "wind_gust",
    "7": "wind_gust_ref",
    "8": "rain_tip_count",
    "9": "rain_secs",
    "10": "solar_radiation",
    "11": "voltage_solar",
    "12": "voltage_capacitor",
    "14": "uv_index",
    "20": "channel",
    "21": "battery_ok",
    "22": "rssi",
}
