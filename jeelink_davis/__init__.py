"""
jeelink_davis — Python library for Davis Vantage Pro 2 via JeeLink USB receiver.

Typical usage::

    from jeelink_davis import DavisStation

    with DavisStation() as station:
        for reading in station.readings():
            print(f"{reading.timestamp}  T={reading.temperature}°  "
                  f"H={reading.humidity}%  RSSI={reading.rssi}dBm")
"""

from .detect import find_jeelink_port, find_jeelink_ports
from .models import WeatherReading
from .station import DavisStation

__all__ = ["DavisStation", "WeatherReading", "find_jeelink_port", "find_jeelink_ports"]
