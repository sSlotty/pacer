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


def assess(fc=None, air=None):
    return weather.assess(fc if fc is not None else forecast(), air, DAY, "config")


def by_hour(default, **hours):
    """24 hourly values: `default`, with h<N>=value overrides."""
    values = [default] * 24
    for key, value in hours.items():
        values[int(key[1:])] = value
    return values


class AssessTests(unittest.TestCase):
    def test_picks_coolest_morning_and_evening_windows(self):
        temps = by_hour(30.0, h5=24.0, h6=25.0, h7=26.5, h18=27.0, h19=27.5, h20=28.0)
        out = assess(forecast(temp=temps))
        self.assertEqual(out["morning"]["window"], "05:00–07:00")
        self.assertEqual((out["morning"]["temp_c"], out["morning"]["temp_max_c"]), (24.0, 26.5))
        self.assertEqual(out["evening"]["window"], "18:00–20:00")
        self.assertEqual(out["better"], "morning")
        self.assertEqual(out["location_source"], "config")

    def test_windows_stay_inside_04_to_21(self):
        cool_outside = by_hour(30.0, h1=15.0, h2=15.0, h3=15.0, h21=15.0, h22=15.0, h23=15.0)
        out = assess(forecast(temp=cool_outside))
        self.assertEqual(out["morning"]["window"], "04:00–06:00")  # ties go to the earliest
        self.assertEqual(out["evening"]["window"], "15:00–17:00")

    def test_storm_and_rain_outrank_heat(self):
        codes = by_hour(3, h4=95, h5=95, h6=95, h7=95)
        chance = by_hour(10, h15=80, h16=80, h17=80, h18=80)
        temps = by_hour(30.0, h4=24.0, h5=24.0, h6=24.0)
        out = assess(forecast(temp=temps, code=codes, chance=chance))
        self.assertEqual(out["morning"]["window"], "08:00–10:00")  # the hot one is the only storm-free morning
        self.assertFalse(out["morning"]["thunderstorm"])
        self.assertEqual(out["evening"]["window"], "19:00–21:00")  # the dry one
        storm_all_morning = assess(forecast(code=by_hour(3, **{f"h{h}": 95 for h in range(4, 11)})))
        self.assertTrue(storm_all_morning["morning"]["thunderstorm"])
        self.assertEqual(storm_all_morning["better"], "evening")

    def test_rain_and_worst_condition(self):
        codes = by_hour(95)
        out = assess(forecast(code=codes, rain=4.0, chance=80))
        self.assertTrue(out["morning"]["thunderstorm"])
        self.assertEqual(out["morning"]["condition"], "พายุฝนฟ้าคะนอง")
        self.assertEqual((out["morning"]["rain_mm"], out["morning"]["rain_chance_pct"]), (12.0, 80))

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

    def test_missing_data(self):
        self.assertIsNone(weather.assess(None, None, DAY))
        self.assertIsNone(weather.assess({"hourly": {"time": ["2020-01-01T07:00"], "temperature_2m": [20]}}, None, DAY))
        air_only = weather.assess(None, hourly(pm2_5=[None] * 7 + [40.0] * 17), DAY)
        slot = air_only["evening"]
        self.assertEqual((slot["pm25"], slot["temp_c"], slot["heat_level"]), (40.0, None, None))
        evening_only = weather.assess(hourly(temperature_2m=[None] * 12 + [30.0] * 12), None, DAY)
        self.assertIsNone(evening_only["morning"])
        self.assertEqual(evening_only["better"], "evening")
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
            out = weather.get_weather(13.75, 100.5, DAY, "Asia/Bangkok", "last_run")
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
        self.assertEqual(result["weather"]["morning"]["heat_level"], "mild")
        json.dumps(result, ensure_ascii=False)

    def test_heat_storm_and_dust_are_yellow_never_red(self):
        codes = [96] * 24
        w = assess(forecast(temp=34, dew=28, code=codes), hourly(pm2_5=90.0))
        result = self.analyze_with(w)
        self.assertEqual(result["status"], "yellow")
        self.assertTrue(has_flag(result, "yellow", "ร้อนชื้นมาก"))
        self.assertTrue(has_flag(result, "yellow", "ฟ้าผ่า"))
        self.assertTrue(has_flag(result, "yellow", "PM2.5"))

    def test_heat_flag_leaves_numbers_to_the_fields(self):
        result = self.analyze_with(assess(forecast(temp=30, dew=26)))
        heat = [f["message"] for f in result["flags"] if "ร้อนชื้น" in f["message"]]
        self.assertEqual(heat, ["ร้อนชื้นมากแม้ช่วงที่ดีที่สุด (04:00–06:00)"])  # °C, dew point and pace % are in the fields
        self.assertEqual(result["weather"]["morning"]["pace_slowdown_pct_max"], 10)

    def test_trouble_only_in_the_worse_window_is_not_a_flag(self):
        codes = by_hour(3, **{f"h{h}": 95 for h in range(15, 22)})
        result = self.analyze_with(assess(forecast(temp=22, dew=15, code=codes)))
        self.assertTrue(result["weather"]["evening"]["thunderstorm"])
        self.assertFalse(has_flag(result, "yellow", "ฟ้าผ่า"))
        self.assertEqual(result["status"], "green")

    def test_info_flags(self):
        result = self.analyze_with(assess(forecast(temp=22, dew=15, rain=5.0, gust=55.0), hourly(pm2_5=50.0)))
        self.assertEqual(result["status"], "green")
        self.assertTrue(has_flag(result, "info", "PM2.5"))
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
        self.assertIn("🌅 เช้า 04:00–06:00 ⭐", names)
        self.assertIn("🌇 เย็น 15:00–17:00", names)
        morning = next(f["value"] for f in embed["fields"] if f["name"].startswith("🌅"))
        self.assertIn("pace ช้าลง ~6–10%", morning)
        self.assertIn("PM2.5 20 ดี", morning)
        self.assertIn("Open-Meteo", embed["footer"]["text"])
        self.assertEqual(len(embed["fields"]) % 3, 1)  # full rows + the weekly chart
        self.assertIn("ลด pace", sections["morning"])
        self.assertIn("🌅 **ถ้าซ้อมเช้า 04:00–06:00 ⭐**: ร้อนชื้น", embed["description"])
        self.assertIn("🌇 **ถ้าซ้อมเย็น 15:00–17:00**:", embed["description"])

    def test_embed_without_weather(self):
        result = self.result(None)
        embed = discord_notify.build_embed(result, summarize.fallback_sections(result), "Claude")
        self.assertFalse(any("อากาศ" in f["name"] for f in embed["fields"]))
        self.assertNotIn("Open-Meteo", embed["footer"]["text"])

    def test_embed_without_tips_skips_tip_lines(self):
        result = self.result(assess())
        embed = discord_notify.build_embed(result, summarize.fallback_sections(result) | {"morning": "", "evening": ""}, "Claude")
        self.assertNotIn("ถ้าซ้อม", embed["description"])

    def test_compact_drops_location(self):
        payload = summarize._compact(self.result(assess()))
        self.assertNotIn("location_source", payload["weather"])
        self.assertIn("heat_level", payload["weather"]["morning"])
        self.assertNotIn("weather_code", payload["weather"]["evening"])
        self.assertEqual(payload["weather"]["better"], "morning")


if __name__ == "__main__":
    unittest.main()
