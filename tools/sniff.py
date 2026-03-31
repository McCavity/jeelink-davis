#!/usr/bin/env python3
"""
sniff.py – Raw JeeLink listener.

Opens the JeeLink serial port, sends the Davis firmware init command,
and prints everything received for the specified duration.

If --port is omitted, the JeeLink is auto-detected by USB VID/PID.

Usage:
    python tools/sniff.py
    python tools/sniff.py --port /dev/ttyUSB0
    python tools/sniff.py --baud 57600 --duration 120
"""

import argparse
import sys
import time

import serial

from jeelink_davis.detect import find_jeelink_port

DEFAULT_BAUD = 57600
INIT_COMMAND = b"0,0s r\n"


def resolve_port(port_arg: str | None) -> str:
    """Return the port to use, auto-detecting if not specified."""
    if port_arg:
        return port_arg
    print("No port specified — auto-detecting JeeLink …")
    try:
        port = find_jeelink_port()
        print(f"Found JeeLink at {port}")
        return port
    except RuntimeError as e:
        print(f"Auto-detect failed: {e}", file=sys.stderr)
        sys.exit(1)


def sniff(port: str, baud: int, duration: int) -> None:
    print(f"Opening {port} at {baud} baud …")
    try:
        ser = serial.Serial(port, baud, timeout=1, rtscts=False, dsrdtr=False)
    except serial.SerialException as e:
        print(f"ERROR: Could not open port: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Sending init command: {INIT_COMMAND!r}")
    ser.write(INIT_COMMAND)
    ser.flush()

    print(f"Listening for {duration}s — press Ctrl+C to stop early.\n")
    deadline = time.monotonic() + duration
    try:
        while time.monotonic() < deadline:
            line = ser.readline()
            if line:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] {line!r}")
                try:
                    decoded = line.decode("ascii").rstrip()
                    print(f"          {decoded}")
                except UnicodeDecodeError:
                    pass
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        ser.close()
        print("Port closed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Raw JeeLink Davis sniffer")
    parser.add_argument(
        "--port",
        default=None,
        help="Serial port (default: auto-detect by USB VID/PID)",
    )
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Baud rate")
    parser.add_argument("--duration", type=int, default=60, help="Listen duration in seconds")
    args = parser.parse_args()

    port = resolve_port(args.port)
    sniff(port, args.baud, args.duration)


if __name__ == "__main__":
    main()
