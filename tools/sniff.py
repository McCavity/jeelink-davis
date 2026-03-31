#!/usr/bin/env python3
"""
sniff.py – Raw JeeLink listener.

Opens the JeeLink serial port, sends the Davis firmware init command,
and prints everything received for the specified duration.

Usage:
    python tools/sniff.py [--port /dev/cu.usbserial-AI05CBYZ] [--baud 57600] [--duration 60]
"""

import argparse
import sys
import time

import serial

DEFAULT_PORT = "/dev/cu.usbserial-AI05CBYZ"
DEFAULT_BAUD = 57600
INIT_COMMAND = b"0,0s r\n"


def sniff(port: str, baud: int, duration: int) -> None:
    print(f"Opening {port} at {baud} baud …")
    try:
        ser = serial.Serial(port, baud, timeout=1)
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
                # Also print decoded if it looks like ASCII
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
    parser.add_argument("--port", default=DEFAULT_PORT, help="Serial port")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Baud rate")
    parser.add_argument("--duration", type=int, default=60, help="Listen duration in seconds")
    args = parser.parse_args()

    sniff(args.port, args.baud, args.duration)


if __name__ == "__main__":
    main()
