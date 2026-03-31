"""Tests for jeelink_davis.protocol — no hardware required."""

import pytest

from jeelink_davis.protocol import parse_init_dictionary, parse_values_line


SAMPLE_DICT_LINE = (
    "INIT DICTIONARY 1=Temperature,2=Pressure,3=Humidity,4=WindSpeed,"
    "5=WindDirection,6=WindGust,7=WindGustRef,8=RainTipCount,9=RainSecs,"
    "10=Solar,11=VoltageSolar,12=VoltageCapacitor,13=SoilLeaf,14=UV,"
    "15.1=SoilTemperature.1,16.1=SoilMoisture.1,17.1=LeafWetness.1,"
    "20=Channel,21=Battery,22=RSSI,255=PacketDump,"
)

SAMPLE_VALUES_LINE = "OK VALUES DAVIS 0 20=2,22=-72,21=ok,4=0.00,5=155,6=9.65,7=15,"


class TestParseInitDictionary:
    def test_parses_known_fields(self):
        result = parse_init_dictionary(SAMPLE_DICT_LINE)
        assert result["1"] == "Temperature"
        assert result["22"] == "RSSI"
        assert result["255"] == "PacketDump"

    def test_parses_zoned_field(self):
        result = parse_init_dictionary(SAMPLE_DICT_LINE)
        assert result["15.1"] == "SoilTemperature.1"
        assert result["16.1"] == "SoilMoisture.1"

    def test_returns_empty_for_non_init_line(self):
        assert parse_init_dictionary("OK VALUES DAVIS 0 1=20.5,") == {}
        assert parse_init_dictionary("") == {}
        assert parse_init_dictionary("[DAVIS.0.8e compiled ...]") == {}

    def test_handles_trailing_comma(self):
        result = parse_init_dictionary(SAMPLE_DICT_LINE)
        # Trailing comma should not produce an empty key
        assert "" not in result

    def test_handles_leading_whitespace(self):
        result = parse_init_dictionary("  " + SAMPLE_DICT_LINE)
        assert result["1"] == "Temperature"


class TestParseValuesLine:
    def test_parses_station_id(self):
        r = parse_values_line(SAMPLE_VALUES_LINE)
        assert r is not None
        assert r.station_id == 0

    def test_parses_channel(self):
        r = parse_values_line(SAMPLE_VALUES_LINE)
        assert r.channel == 2

    def test_parses_rssi(self):
        r = parse_values_line(SAMPLE_VALUES_LINE)
        assert r.rssi == -72

    def test_parses_battery_ok_true(self):
        r = parse_values_line(SAMPLE_VALUES_LINE)
        assert r.battery_ok is True

    def test_parses_battery_ok_false(self):
        r = parse_values_line("OK VALUES DAVIS 0 21=low,")
        assert r.battery_ok is False

    def test_parses_wind_fields(self):
        r = parse_values_line(SAMPLE_VALUES_LINE)
        assert r.wind_speed == pytest.approx(0.00)
        assert r.wind_direction == 155
        assert r.wind_gust == pytest.approx(9.65)
        assert r.wind_gust_ref == 15

    def test_parses_temperature_and_humidity(self):
        r = parse_values_line("OK VALUES DAVIS 0 1=18.5,3=62,")
        assert r.temperature == pytest.approx(18.5)
        assert r.humidity == pytest.approx(62.0)

    def test_parses_zoned_soil_temperature(self):
        r = parse_values_line("OK VALUES DAVIS 0 15.1=21.3,15.2=19.8,")
        assert r.soil_temperature[1] == pytest.approx(21.3)
        assert r.soil_temperature[2] == pytest.approx(19.8)

    def test_parses_zoned_leaf_wetness(self):
        r = parse_values_line("OK VALUES DAVIS 0 17.1=3.0,")
        assert r.leaf_wetness[1] == pytest.approx(3.0)

    def test_unknown_field_goes_to_extra(self):
        r = parse_values_line("OK VALUES DAVIS 0 99=surprise,")
        assert r.extra_fields["99"] == "surprise"

    def test_packet_dump_ignored(self):
        r = parse_values_line("OK VALUES DAVIS 0 255=deadbeef,")
        assert r is not None
        assert "255" not in r.extra_fields

    def test_timestamp_is_set(self):
        from datetime import timezone
        r = parse_values_line(SAMPLE_VALUES_LINE)
        assert r.timestamp.tzinfo == timezone.utc

    def test_returns_none_for_non_data_line(self):
        assert parse_values_line("INIT DICTIONARY 1=Temperature,") is None
        assert parse_values_line("") is None
        assert parse_values_line("[DAVIS.0.8e compiled ...]") is None

    def test_handles_trailing_comma(self):
        r = parse_values_line("OK VALUES DAVIS 0 1=20.5,")
        assert r.temperature == pytest.approx(20.5)

    def test_handles_whitespace_in_line(self):
        r = parse_values_line("  OK VALUES DAVIS 1 1=15.0,  ")
        assert r is not None
        assert r.station_id == 1
        assert r.temperature == pytest.approx(15.0)

    def test_multiple_stations(self):
        r1 = parse_values_line("OK VALUES DAVIS 0 1=20.0,")
        r2 = parse_values_line("OK VALUES DAVIS 1 1=18.0,")
        assert r1.station_id == 0
        assert r2.station_id == 1
