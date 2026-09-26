"""Offline tests for the weather assessment, flags and embed (CLAUDE.md §6.3, §8.5)."""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord_notify  # noqa: E402
import summarize  # noqa: E402
import weather  # noqa: E402
from analysis import analyze  # noqa: E402
from tests.test_analysis import TODAY, calm_dataset, has_flag  # noqa: E402

DAY = TODAY.isoformat()


def hourly(**series):
    """Open-Meteo style {"hourly": {...}} for DAY; each series gives the 24 hourly values."""
    out = {"time": [f"{DAY}T{h:02d}:00" for h in range(24)]}
    for key, values in series.items():
        out[key] = values if isinstance(values, list) else [values] * 24
    return {"hourly": out}


def forecast(temp=24.0, dew=23.0, code=3, rain=0.0, chance=20, gust=20.0, **extra):
    return hourly(
        temperature_2m=temp, dew_point_2m=dew, relative_humidity_2m=90, apparent_temperature=28.0,
        precipitation_probability=chance, precipitation=rain, weather_code=code,
        wind_speed_10m=10.0, wind_gusts_10m=gust, uv_index=0.5, **extra,
    )


def assess(fc=None, air=None, run_time="07:00"):
    return weather.assess(fc if fc is not None else forecast(), air, DAY, run_time, "config")


class AssessTests(unittest.TestCase):
    def test_uses_run_window_only(self):
        temps = [30.0] * 24
        temps[7], temps[8], temps[9] = 24.0, 25.0, 26.5
        out = assess(forecast(temp=temps))
        self.assertEqual(out["window"], "07:00–09:00")
        self.assertEqual((out["temp_c"], out["temp_max_c"]), (24.0, 26.5))

    def test_window_follows_run_time_and_stops_at_midnight(self):
        self.assertEqual(assess(run_time="05:30")["window"], "05:00–07:00")
        self.assertEqual(assess(run_time="23:00")["window"], "23:00–23:00")
        with self.assertLogs("weather", "WARNING"):
            self.assertEqual(assess(run_time="soon")["window"], "07:00–09:00")

    def test_heat_table(self):
        self.assertEqual(weather.heat(10, 5), (91, 0, 0, "none"))
        self.assertEqual(weather.heat(24, 23.6), (150, 3, 6, "moderate"))  # a real Bangkok 07:00
        self.assertEqual(weather.heat(30, 26), (165, 6, 10, "high"))
        self.assertEqual(weather.heat(35, 30), (181, None, None, "severe"))
        self.assertEqual(weather.heat(None, 20), (None, None, None, None))

    def test_pm25_levels(self):
        self.assertEqual(weather.pm25_level(12), "ดีมาก")
        self.assertEqual(weather.pm25_level(37.5), "ปานกลาง")
        self.assertEqual(weather.pm25_level(76), "มีผลต่อสุขภาพ")
        self.assertIsNone(weather.pm25_level(None))

    def test_rain_and_worst_condition(self):
        codes = [0] * 24
        codes[8] = 95
        out = assess(forecast(code=codes, rain=4.0, chance=[10] * 8 + [80] * 16))
        self.assertTrue(out["thunderstorm"])
        self.assertEqual(out["condition"], "พายุฝนฟ้าคะนอง")
        self.assertEqual((out["rain_mm"], out["rain_chance_pct"]), (12.0, 80))

    def test_missing_data(self):
        self.assertIsNone(weather.assess(None, None, DAY))
        self.assertIsNone(weather.assess({"hourly": {"time": ["2020-01-01T07:00"], "temperature_2m": [20]}}, None, DAY))
        air_only = weather.assess(None, hourly(pm2_5=[None] * 7 + [40.0] * 17), DAY)
        self.assertEqual((air_only["pm25"], air_only["temp_c"], air_only["heat_level"]), (40.0, None, None))
        json.dumps(air_only)

    def test_fetch_failure_returns_none(self):
        import requests

        with mock.patch.object(requests, "get", side_effect=requests.ConnectionError("offline")), \
                self.assertLogs("weather", "WARNING"):
            self.assertIsNone(weather.get_weather(13.75, 100.5, DAY, "Asia/Bangkok"))

    def test_fetch_passes_location_and_day(self):
        import requests

        resp = mock.Mock(status_code=200, json=mock.Mock(return_value=forecast()))
        with mock.patch.object(requests, "get", return_value=resp) as get:
            out = weather.get_weather(13.75, 100.5, DAY, "Asia/Bangkok", "07:00", "last_run")
        params = get.call_args_list[0].kwargs["params"]
        self.assertEqual((params["latitude"], params["start_date"], params["timezone"]), (13.75, DAY, "Asia/Bangkok"))
        self.assertIn("dew_point_2m", params["hourly"])
        self.assertEqual(get.call_args_list[1].kwargs["params"]["hourly"], "pm2_5")
        self.assertEqual(out["location_source"], "last_run")


class WeatherFlagTests(unittest.TestCase):
    def analyze_with(self, w):
        daily, runs = calm_dataset()
        return analyze(daily, runs, TODAY, weather=w)

    def test_calm_weather_keeps_green(self):
        result = self.analyze_with(assess(forecast(temp=22, dew=15)))
        self.assertEqual(result["status"], "green")
        self.assertEqual(result["weather"]["heat_level"], "mild")
        json.dumps(result, ensure_ascii=False)

    def test_heat_storm_and_dust_are_yellow_never_red(self):
        codes = [96] * 24
        w = assess(forecast(temp=34, dew=28, code=codes), hourly(pm2_5=90.0))
        result = self.analyze_with(w)
        self.assertEqual(result["status"], "yellow")
        self.assertTrue(has_flag(result, "yellow", "ร้อนชื้นมาก"))
        self.assertTrue(has_flag(result, "yellow", "ฟ้าผ่า"))
        self.assertTrue(has_flag(result, "yellow", "PM2.5 90"))

    def test_high_heat_flag_quotes_pace_range(self):
        result = self.analyze_with(assess(forecast(temp=30, dew=26)))
        self.assertTrue(has_flag(result, "yellow", "6–10%"))

    def test_info_flags(self):
        result = self.analyze_with(assess(forecast(temp=22, dew=15, rain=5.0, gust=55.0), hourly(pm2_5=50.0)))
        self.assertEqual(result["status"], "green")
        self.assertTrue(has_flag(result, "info", "PM2.5 50"))
        self.assertTrue(has_flag(result, "info", "ฝนหนัก"))
        self.assertTrue(has_flag(result, "info", "ลมกระโชก"))

    def test_no_weather(self):
        daily, runs = calm_dataset()
        self.assertIsNone(analyze(daily, runs, TODAY)["weather"])


class WeatherOutputTests(unittest.TestCase):
    def result(self, w):
        daily, runs = calm_dataset()
        return analyze(daily, runs, TODAY, weather=w)

    def test_embed_weather_row_and_credit(self):
        result = self.result(assess(forecast(temp=30, dew=26), hourly(pm2_5=20.0)))
        sections = summarize.fallback_sections(result)
        embed = discord_notify.build_embed(result, sections, None)
        names = [f["name"] for f in embed["fields"]]
        self.assertIn("☁️ อากาศ 07:00–09:00", names)
        self.assertIn("💦 ร้อนชื้น", names)
        heat = next(f["value"] for f in embed["fields"] if f["name"] == "💦 ร้อนชื้น")
        self.assertIn("pace ช้าลง ~6–10%", heat)
        self.assertIn("Open-Meteo", embed["footer"]["text"])
        self.assertEqual(len(embed["fields"]) % 3, 1)  # full rows + the weekly chart
        self.assertIn("ลด pace", sections["recommendation"])

    def test_embed_without_weather(self):
        result = self.result(None)
        embed = discord_notify.build_embed(result, summarize.fallback_sections(result), "Claude")
        self.assertFalse(any("อากาศ" in f["name"] for f in embed["fields"]))
        self.assertNotIn("Open-Meteo", embed["footer"]["text"])

    def test_compact_drops_location(self):
        payload = summarize._compact(self.result(assess()))
        self.assertNotIn("location_source", payload["weather"])
        self.assertIn("heat_level", payload["weather"])


if __name__ == "__main__":
    unittest.main()
