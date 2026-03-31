"""
JeeLink USB detection.

Uses pyserial's port enumeration to locate the JeeLink by USB VID/PID,
so the same code works on macOS (/dev/cu.usbserial-*) and Linux/Raspberry Pi
(/dev/ttyUSB*) without any configuration changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from serial.tools import list_ports
from serial.tools.list_ports_common import ListPortInfo

# FTDI FT232R — the chip used by JeeLink
JEELINK_USB_VID = 0x0403
JEELINK_USB_PID = 0x6001


@dataclass
class JeeLinkDevice:
    port: str
    serial_number: str | None
    description: str | None
    manufacturer: str | None


def find_jeelink_ports() -> list[JeeLinkDevice]:
    """
    Return all serial ports that match the JeeLink USB VID/PID.

    Typically returns one device, but handles the case of multiple
    JeeLinks being connected.
    """
    matches: list[JeeLinkDevice] = []
    for info in list_ports.comports():
        if info.vid == JEELINK_USB_VID and info.pid == JEELINK_USB_PID:
            matches.append(
                JeeLinkDevice(
                    port=info.device,
                    serial_number=info.serial_number,
                    description=info.description,
                    manufacturer=info.manufacturer,
                )
            )
    return matches


def find_jeelink_port() -> str:
    """
    Return the port of the first detected JeeLink.

    Raises
    ------
    RuntimeError
        If no JeeLink is found, or if multiple are found and the caller
        must choose explicitly.
    """
    devices = find_jeelink_ports()
    if not devices:
        raise RuntimeError(
            "No JeeLink device found. "
            f"Looking for USB VID=0x{JEELINK_USB_VID:04X} PID=0x{JEELINK_USB_PID:04X}. "
            "Check that the device is plugged in and drivers are loaded."
        )
    if len(devices) > 1:
        ports = ", ".join(d.port for d in devices)
        raise RuntimeError(
            f"Multiple JeeLink devices found ({ports}). "
            "Pass the desired port explicitly to DavisStation(port=...)."
        )
    return devices[0].port
