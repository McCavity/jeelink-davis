"""Host telemetry for the touch console's System page.

Every field is optional. On a development machine that is not a Raspberry Pi
the Pi-specific values are None and the page shows a dash — the endpoint never
fails as a whole because one part is unavailable.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

VCGENCMD = "/usr/bin/vcgencmd"
CACHE_TTL_S = 5.0

# Bit -> field name. The two halves answer different questions and are kept
# apart all the way to the display.
_BITS_NOW = {0: "undervoltage", 1: "arm_freq_capped", 2: "throttled", 3: "soft_temp_limit"}
_BITS_EVER = {16: "undervoltage", 17: "arm_freq_capped", 18: "throttled", 19: "soft_temp_limit"}

_CACHE: dict[str, tuple[float, str | None]] = {}


def parse_throttled(word: int) -> dict[str, dict[str, bool]]:
    """Split the get_throttled word into its 'now' and 'ever' halves."""
    return {
        "now": {name: bool(word & (1 << bit)) for bit, name in _BITS_NOW.items()},
        "ever": {name: bool(word & (1 << bit)) for bit, name in _BITS_EVER.items()},
    }


def _run_vcgencmd(*args: str) -> str | None:
    if not os.path.exists(VCGENCMD):
        return None
    try:
        out = subprocess.run([VCGENCMD, *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _vcgencmd(*args: str) -> str | None:
    """Cached vcgencmd call — a visible System page must not spawn a
    subprocess per request."""
    key = " ".join(args)
    hit = _CACHE.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < CACHE_TTL_S:
        return hit[1]
    value = _run_vcgencmd(*args)
    _CACHE[key] = (now, value)
    return value


def _read_first_line(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.readline().strip()
    except OSError:
        return None


def _number_after(text: str | None, sep: str) -> float | None:
    if not text or sep not in text:
        return None
    tail = text.split(sep, 1)[1]
    digits = "".join(c for c in tail if c.isdigit() or c in ".-")
    try:
        return float(digits)
    except ValueError:
        return None


def read_system() -> dict:
    raw_temp = _read_first_line("/sys/class/thermal/thermal_zone0/temp")
    cpu_temp = round(int(raw_temp) / 1000, 1) if raw_temp and raw_temp.lstrip("-").isdigit() else None

    try:
        load = [round(v, 2) for v in os.getloadavg()]
    except OSError:
        load = None

    mem_total = mem_available = None
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    mem_available = int(line.split()[1]) // 1024
    except OSError:
        pass

    usage = shutil.disk_usage("/")
    uptime_line = _read_first_line("/proc/uptime")

    throttle_out = _vcgencmd("get_throttled")
    throttle = None
    if throttle_out and "=" in throttle_out:
        try:
            throttle = parse_throttled(int(throttle_out.split("=", 1)[1], 16))
        except ValueError:
            throttle = None

    clock = _number_after(_vcgencmd("measure_clock", "arm"), "=")
    volts = _number_after(_vcgencmd("measure_volts", "core"), "=")

    return {
        "cpu_temp_c": cpu_temp,
        "load": load,
        "cpu_count": os.cpu_count() or 1,
        "mem_total_mb": mem_total,
        "mem_used_mb": (mem_total - mem_available) if (mem_total and mem_available) else None,
        "disk_total_gb": round(usage.total / 1e9, 1),
        "disk_used_gb": round(usage.used / 1e9, 1),
        "uptime_s": int(float(uptime_line.split()[0])) if uptime_line else None,
        "core_clock_hz": int(clock) if clock else None,
        "core_volts": round(volts, 3) if volts else None,
        "throttle": throttle,
    }
