"""
Low-level serial connection to the JeeLink.

Handles open/close and raw line reading. No protocol parsing here.
"""

from __future__ import annotations

import logging
import time
from typing import Iterator

import serial

from .detect import find_jeelink_port

logger = logging.getLogger(__name__)

DEFAULT_BAUD = 57600
INIT_COMMAND = b"0,0s r\n"

# Time to wait after sending the init command before reading.
# The firmware needs ~5 s to reinitialise the radio and start receiving
# ISS packets; 0.5 s was not enough in practice.
_INIT_SETTLE_SECS = 5


class JeeLinkConnection:
    """
    Manages the serial connection to a JeeLink running Davis firmware 0.8e.

    Usage::

        conn = JeeLinkConnection()          # auto-detects the JeeLink
        conn = JeeLinkConnection("/dev/ttyUSB0")  # explicit port
        conn.open()
        for raw_line in conn.read_lines():
            ...
        conn.close()

    Or as a context manager::

        with JeeLinkConnection() as conn:
            for raw_line in conn.read_lines():
                ...
    """

    def __init__(
        self,
        port: str | None = None,
        baud: int = DEFAULT_BAUD,
        read_timeout: float = 2.0,
    ) -> None:
        # Defer auto-detection to open() so construction never raises
        self._port_override = port
        self.port: str = port or ""
        self.baud = baud
        self.read_timeout = read_timeout
        self._serial: serial.Serial | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the serial port and send the firmware init command."""
        if self._serial and self._serial.is_open:
            return

        if not self._port_override:
            self.port = find_jeelink_port()
            logger.debug("Auto-detected JeeLink at %s", self.port)

        logger.debug("Opening %s at %d baud", self.port, self.baud)
        self._serial = serial.Serial(
            self.port,
            self.baud,
            timeout=self.read_timeout,
            rtscts=False,
            dsrdtr=False,
        )
        logger.debug(f"Delaying for {_INIT_SETTLE_SECS} seconds...")
        time.sleep(_INIT_SETTLE_SECS)
        logger.debug("Sending init command")
        self._serial.write(INIT_COMMAND)
        self._serial.flush()

    def close(self) -> None:
        """Close the serial port if open."""
        if self._serial and self._serial.is_open:
            logger.debug("Closing %s", self.port)
            self._serial.close()

    def __enter__(self) -> "JeeLinkConnection":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_lines(self) -> Iterator[str]:
        """
        Yield decoded lines from the JeeLink indefinitely.

        Skips blank lines and lines that cannot be decoded as ASCII.
        The caller is responsible for stopping iteration (e.g. KeyboardInterrupt
        or a timeout wrapper).
        """
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("Connection is not open. Call open() first.")

        while True:
            raw = self._serial.readline()
            if not raw:
                continue  # read timeout, try again
            try:
                line = raw.decode("ascii").strip()
            except UnicodeDecodeError:
                logger.warning("Received non-ASCII bytes: %r — skipping", raw)
                continue
            if line:
                yield line
