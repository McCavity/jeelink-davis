# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Python library for receiving **Davis Vantage Pro 2** weather station data via a **JeeLink USB receiver** (FT232R UART, `/dev/cu.usbserial-AI05CBYZ` on macOS). Target firmware on the JeeLink is **Davis 0.8e** (compiled Sep 5 2020, RFM69 radio, EU 868 MHz frequencies, firmware switch `b:2`).

The library is designed to be later embedded in an **IOBroker adapter** (Node.js/TypeScript), either via subprocess with JSON stdout or a similar IPC approach.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Commands

```bash
# Run tests (no hardware required)
.venv/bin/pytest tests/ -v

# Raw hardware sniffer — connects to JeeLink, prints everything for 60s
.venv/bin/python tools/sniff.py
.venv/bin/python tools/sniff.py --port /dev/cu.usbserial-AI05CBYZ --baud 57600 --duration 120
```

## Architecture

```
jeelink_davis/
├── __init__.py       # public API: DavisStation, WeatherReading
├── connection.py     # serial open/close/readline (JeeLinkConnection)
├── protocol.py       # stateless line parsers (parse_init_dictionary, parse_values_line)
├── models.py         # WeatherReading dataclass + FIELD_CODE_MAP constant
└── station.py        # high-level iterator: DavisStation.readings() → WeatherReading
tools/
└── sniff.py          # raw listener for hardware debugging
tests/
└── test_protocol.py  # parser tests, no hardware needed
```

**Data flow**: `JeeLinkConnection.read_lines()` yields raw ASCII lines → `DavisStation.readings()` feeds them through `protocol.py` parsers → yields `WeatherReading` dataclass instances.

**Protocol** (firmware 0.8e):
- Init command sent on open: `0,0s r\n` at 57600 baud, no hardware flow control
- Firmware responds with banner then `INIT DICTIONARY code=Name,...` — parsed at runtime into `DavisStation.field_dictionary`
- Data lines: `OK VALUES DAVIS <station_id> <code>=<value>,...`
- Field codes: 1=Temperature, 2=Pressure, 3=Humidity, 4=WindSpeed, 5=WindDirection, 6=WindGust, 7=WindGustRef, 8=RainTipCount, 9=RainSecs, 10=Solar, 11=VoltageSolar, 12=VoltageCapacitor, 14=UV, 20=Channel, 21=Battery(`ok`/else), 22=RSSI(dBm), 255=PacketDump(ignored). Dotted codes `15.x`/`16.x`/`17.x` are zone-indexed soil/leaf sensors.

**Units**: stored as raw firmware values; EU firmware expected to deliver °C, hPa, m/s — unconfirmed until live packets are received (ISS reception issue pending hardware placement).

## Raspberry Pi bridge (planned)

The ISS signal doesn't reliably reach the JeeLink indoors. Plan: use a Raspberry Pi outdoors/near window with JeeLink directly USB-connected, running this library and streaming readings over LAN/WLAN to IOBroker.
