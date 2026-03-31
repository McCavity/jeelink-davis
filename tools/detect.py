#!/usr/bin/env python3
"""
detect.py – Find JeeLink USB receiver(s) on this machine.

Scans the serial port list for devices matching the JeeLink USB VID/PID
(FTDI FT232R: VID=0x0403 PID=0x6001) and prints their port path and
identifying information.

Usage:
    python tools/detect.py

Exit codes:
    0 — at least one JeeLink found
    1 — no JeeLink found
"""

import sys

from jeelink_davis.detect import JEELINK_USB_PID, JEELINK_USB_VID, find_jeelink_ports


def main() -> int:
    print(
        f"Scanning for JeeLink (USB VID=0x{JEELINK_USB_VID:04X} "
        f"PID=0x{JEELINK_USB_PID:04X}) …\n"
    )

    devices = find_jeelink_ports()

    if not devices:
        print("No JeeLink device found.")
        print("  • Check the USB cable / hub connection.")
        print("  • On Linux, verify the 'ftdi_sio' kernel module is loaded.")
        return 1

    print(f"Found {len(devices)} JeeLink device(s):\n")
    for i, dev in enumerate(devices, start=1):
        print(f"  [{i}] Port        : {dev.port}")
        print(f"      Serial No.  : {dev.serial_number or '(unknown)'}")
        print(f"      Description : {dev.description or '(unknown)'}")
        print(f"      Manufacturer: {dev.manufacturer or '(unknown)'}")
        print()

    if len(devices) == 1:
        print(f"Use this port in your code:")
        print(f"  DavisStation(port={devices[0].port!r})")
        print()
        print("Or rely on auto-detection (no port argument needed when")
        print("exactly one JeeLink is connected).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
