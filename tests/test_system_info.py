"""Tests for the host telemetry helper.

The throttle word packs two different questions into one integer: bits 0-3 are
"right now", bits 16-19 are "has happened since boot". Merging them produces a
console that reports throttling when nothing is being throttled, so the split
is what these tests are mostly about.
"""
import pytest

from web import system_info


def test_throttle_word_splits_now_from_ever():
    # 0xe0000 = bits 17, 18, 19 -> happened since boot, nothing current.
    result = system_info.parse_throttled(0xE0000)
    assert result["now"] == {
        "undervoltage": False, "arm_freq_capped": False,
        "throttled": False, "soft_temp_limit": False,
    }
    assert result["ever"] == {
        "undervoltage": False, "arm_freq_capped": True,
        "throttled": True, "soft_temp_limit": True,
    }


def test_throttle_word_reports_current_undervoltage():
    result = system_info.parse_throttled(0x50005)
    assert result["now"]["undervoltage"] is True
    assert result["now"]["soft_temp_limit"] is False
    assert result["now"]["throttled"] is True
    assert result["ever"]["undervoltage"] is True
    assert result["ever"]["throttled"] is True


def test_throttle_word_all_clear():
    result = system_info.parse_throttled(0x0)
    assert not any(result["now"].values())
    assert not any(result["ever"].values())


def test_read_system_returns_every_key_even_off_a_pi(monkeypatch):
    # No vcgencmd, no thermal zone: the endpoint must still answer.
    monkeypatch.setattr(system_info, "_vcgencmd", lambda *a: None)
    monkeypatch.setattr(system_info, "_read_first_line", lambda p: None)
    data = system_info.read_system()
    for key in ("cpu_temp_c", "load", "cpu_count", "mem_total_mb", "mem_used_mb",
                "disk_total_gb", "disk_used_gb", "uptime_s",
                "core_clock_hz", "core_volts", "throttle"):
        assert key in data
    assert data["cpu_temp_c"] is None
    assert data["throttle"] is None
    assert data["cpu_count"] >= 1


def test_vcgencmd_result_is_cached(monkeypatch):
    calls = []

    def fake_run(*args):
        calls.append(args)
        return "throttled=0x0"

    monkeypatch.setattr(system_info, "_run_vcgencmd", fake_run)
    system_info._CACHE.clear()
    system_info._vcgencmd("get_throttled")
    system_info._vcgencmd("get_throttled")
    assert len(calls) == 1
