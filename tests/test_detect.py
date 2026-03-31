"""Tests for jeelink_davis.detect — mocks the serial port list."""

import pytest

from jeelink_davis.detect import (
    JEELINK_USB_PID,
    JEELINK_USB_VID,
    JeeLinkDevice,
    find_jeelink_port,
    find_jeelink_ports,
)


def _make_port_info(device, vid, pid, serial_number=None, description=None, manufacturer=None):
    """Build a minimal mock matching serial.tools.list_ports_common.ListPortInfo."""
    from unittest.mock import MagicMock
    info = MagicMock()
    info.device = device
    info.vid = vid
    info.pid = pid
    info.serial_number = serial_number
    info.description = description
    info.manufacturer = manufacturer
    return info


JEELINK_PORT_INFO = _make_port_info(
    "/dev/cu.usbserial-AI05CBYZ",
    JEELINK_USB_VID,
    JEELINK_USB_PID,
    serial_number="AI05CBYZ",
    description="FT232R USB UART",
    manufacturer="FTDI",
)

OTHER_PORT_INFO = _make_port_info(
    "/dev/cu.Bluetooth-Incoming-Port",
    vid=None,
    pid=None,
)


class TestFindJeeLinkPorts:
    def test_finds_jeelink(self, mocker):
        mocker.patch(
            "jeelink_davis.detect.list_ports.comports",
            return_value=[OTHER_PORT_INFO, JEELINK_PORT_INFO],
        )
        result = find_jeelink_ports()
        assert len(result) == 1
        assert result[0].port == "/dev/cu.usbserial-AI05CBYZ"
        assert result[0].serial_number == "AI05CBYZ"
        assert result[0].manufacturer == "FTDI"

    def test_returns_empty_when_not_found(self, mocker):
        mocker.patch(
            "jeelink_davis.detect.list_ports.comports",
            return_value=[OTHER_PORT_INFO],
        )
        assert find_jeelink_ports() == []

    def test_finds_multiple_jeelinks(self, mocker):
        second = _make_port_info("/dev/cu.usbserial-XXXXXXXX", JEELINK_USB_VID, JEELINK_USB_PID)
        mocker.patch(
            "jeelink_davis.detect.list_ports.comports",
            return_value=[JEELINK_PORT_INFO, second],
        )
        result = find_jeelink_ports()
        assert len(result) == 2

    def test_returns_jeelink_device_dataclass(self, mocker):
        mocker.patch(
            "jeelink_davis.detect.list_ports.comports",
            return_value=[JEELINK_PORT_INFO],
        )
        result = find_jeelink_ports()
        assert isinstance(result[0], JeeLinkDevice)

    def test_ignores_vid_only_match(self, mocker):
        wrong_pid = _make_port_info("/dev/ttyUSB0", JEELINK_USB_VID, 0x9999)
        mocker.patch(
            "jeelink_davis.detect.list_ports.comports",
            return_value=[wrong_pid],
        )
        assert find_jeelink_ports() == []


class TestFindJeeLinkPort:
    def test_returns_port_string(self, mocker):
        mocker.patch(
            "jeelink_davis.detect.list_ports.comports",
            return_value=[JEELINK_PORT_INFO],
        )
        assert find_jeelink_port() == "/dev/cu.usbserial-AI05CBYZ"

    def test_raises_when_not_found(self, mocker):
        mocker.patch(
            "jeelink_davis.detect.list_ports.comports",
            return_value=[],
        )
        with pytest.raises(RuntimeError, match="No JeeLink device found"):
            find_jeelink_port()

    def test_raises_when_multiple_found(self, mocker):
        second = _make_port_info("/dev/cu.usbserial-XXXXXXXX", JEELINK_USB_VID, JEELINK_USB_PID)
        mocker.patch(
            "jeelink_davis.detect.list_ports.comports",
            return_value=[JEELINK_PORT_INFO, second],
        )
        with pytest.raises(RuntimeError, match="Multiple JeeLink devices found"):
            find_jeelink_port()
