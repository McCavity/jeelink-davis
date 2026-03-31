"""
High-level Davis weather station interface.

Combines JeeLinkConnection + protocol parsing into a simple iterator API::

    from jeelink_davis.station import DavisStation

    with DavisStation() as station:
        for reading in station.readings():
            print(reading.temperature, reading.wind_speed)
"""

from __future__ import annotations

import logging
from typing import Iterator

from .connection import JeeLinkConnection
from .models import WeatherReading
from .protocol import parse_init_dictionary, parse_values_line

logger = logging.getLogger(__name__)


class DavisStation:
    """
    High-level interface to a Davis Vantage Pro 2 ISS via JeeLink.

    Parameters mirror :class:`~jeelink_davis.connection.JeeLinkConnection`.

    Attributes
    ----------
    field_dictionary : dict[str, str]
        Populated after the first ``INIT DICTIONARY`` line is received.
        Maps field code strings to their human-readable names,
        e.g. ``{"1": "Temperature", "22": "RSSI", ...}``.
    """

    def __init__(
        self,
        port: str | None = None,
        baud: int | None = None,
        read_timeout: float = 2.0,
    ) -> None:
        kwargs: dict = {"read_timeout": read_timeout}
        if port is not None:
            kwargs["port"] = port
        if baud is not None:
            kwargs["baud"] = baud

        self._conn = JeeLinkConnection(**kwargs)
        self.field_dictionary: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "DavisStation":
        self._conn.open()
        return self

    def __exit__(self, *_: object) -> None:
        self._conn.close()

    def open(self) -> None:
        self._conn.open()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Main iterator
    # ------------------------------------------------------------------

    def readings(self) -> Iterator[WeatherReading]:
        """
        Yield :class:`~jeelink_davis.models.WeatherReading` objects as they
        arrive from the ISS.

        Also processes ``INIT DICTIONARY`` lines internally (updating
        :attr:`field_dictionary`) without yielding them to the caller.

        Raises ``RuntimeError`` if the connection is not open.
        """
        for line in self._conn.read_lines():
            # Handle firmware dictionary line
            dictionary = parse_init_dictionary(line)
            if dictionary:
                self.field_dictionary = dictionary
                logger.info("Received field dictionary: %d entries", len(dictionary))
                continue

            # Handle data lines
            reading = parse_values_line(line)
            if reading is not None:
                yield reading
                continue

            # Anything else (banner, unknown lines) — log at debug level
            logger.debug("Unhandled line: %r", line)
