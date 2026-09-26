"""Offline tests for fetching, storage, Discord embeds, fallback text and main() (CLAUDE.md §14)."""

import contextlib
import datetime
from datetime import date
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord_notify  # noqa: E402
import garmin_fetch  # noqa: E402
import main  # noqa: E402
import summarize  # noqa: E402
import weather  # noqa: E402
from analysis import analyze  # noqa: E402
from storage import Storage  # noqa: E402
from tests.test_analysis import TODAY, calm_dataset  # noqa: E402

garmin_fetch.REQUEST_DELAY_S = 0


def activity(aid, type_key, distance=10000.0, elev=100.0, start="2026-09-19 06:00:00"):
    return {
        "activityId": aid,
        "activityName": f"{type_key} {aid}",
        "startTimeLocal": start,
        "activityType": {"typeKey": type_key},
        "distance": distance,
        "duration": 3600.0,
        "movingDuration": 3500.0,
        "elevationGain": elev,
        "elevationLoss": elev,
        "averageHR": 145.0,
        "maxHR": 175.0,
        "averageRunningCadenceInStepsPerMinute": 170.0,
        "activityTrainingLoad": 80.0,
        "aerobicTrainingEffect": 3.1,
        "anaerobicTrainingEffect": 1.2,
    }


class FakeDatetime:
    """Freezes main.datetime.now() at a given local time (other attributes pass through)."""

    def __init__(self, text):
        self.moment = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")

    def now(self, tz=None):
        return self.moment.replace(tzinfo=tz)

    def __getattr__(self, name):
        return getattr(datetime.datetime, name)


class FakeApi:
    """Mimics the garminconnect.Garmin methods we call."""

    def __init__(self, activities=None, broken=(), missing_recovery=()):
        self.activities = activities or []
        self.broken = set(broken)
        self.missing_recovery = set(missing_recovery)
        self.calls = 0

    def _maybe_fail(self, name):
        self.calls += 1
        if name in self.broken:
            raise ConnectionError(f"{name} is down")

    def get_activities_by_date(self, start, end):
        self._maybe_fail("activities")
        return self.activities

    def get_stats(self, d):
        self._maybe_fail("stats")
        return {"restingHeartRate": 48, "averageStressLevel": 30, "bodyBatteryHighestValue": 95,
                "bodyBatteryLowestValue": 15, "bodyBatteryAtWakeTime": 85}

    def get_sleep_data(self, d):
        self._maybe_fail("sleep")
        if d in self.missing_recovery:
            return None
        return {"dailySleepDTO": {"sleepTimeSeconds": 27000, "deepSleepSeconds": 5400,
                                  "remSleepSeconds": 6000, "awakeSleepSeconds": 900,
                                  "sleepScores": {"overall": {"value": 84}}}}

    def get_hrv_data(self, d):
        self._maybe_fail("hrv")
        if d in self.missing_recovery:
            return None
        return {"hrvSummary": {"lastNightAvg": 62, "weeklyAvg": 60, "status": "BALANCED",
                               "baseline": {"balancedLow": 52, "balancedUpper": 70}}}

    def get_training_readiness(self, d):
        self._maybe_fail("readiness")
        if d in self.missing_recovery:
            return [{"score": 25, "level": "LOW"}]  # Garmin has readiness long before sleep
        return [{"score": 77, "level": "HIGH"}]

    def get_max_metrics(self, d):
        self._maybe_fail("max")
        return [{"generic": {"vo2MaxPreciseValue": 53.4, "vo2MaxValue": 53}}]


class FetchTests(unittest.TestCase):
    def setUp(self):
        garmin_fetch.reset_request_budget()

    def test_fetch_runs_keeps_only_running(self):
        acts = [
            activity(1, "running"),
            activity(2, "trail_running", elev=600),
            activity(3, "cycling"),
            activity(4, "hiking"),
            activity(5, "walking"),
            activity(6, "lap_swimming"),
            activity(7, "strength_training"),
            activity(8, "treadmill_running", elev=0),
            activity(9, "ultra_run", distance=50000, elev=2500),
            activity(10, "ultra_run", distance=50000, elev=200),
            activity(11, "running", distance=0),  # zero distance
            {**activity(12, "running"), "activityId": None},  # no id
            activity(13, "indoor_rowing"),
            activity(14, "virtual_run"),
        ]
        with self.assertLogs("garmin_fetch", "WARNING"):
            # an unknown "*run*" typeKey is logged (indoor_rowing is not a run — no log for it)
            runs = garmin_fetch.fetch_runs(FakeApi(acts + [activity(15, "obstacle_run")]), "2026-09-01", "2026-09-20")
        ids = sorted(r["activity_id"] for r in runs)
        self.assertEqual(ids, [1, 2, 8, 9, 10, 14])
        by_id = {r["activity_id"]: r for r in runs}
        self.assertEqual(by_id[2]["run_category"], "trail")
        self.assertEqual(by_id[9]["run_category"], "trail")
        self.assertEqual(by_id[10]["run_category"], "road")
        self.assertEqual(by_id[1]["date"], "2026-09-19")
        self.assertEqual(by_id[1]["moving_s"], 3500.0)
        self.assertEqual(by_id[1]["avg_cadence"], 170.0)

    def test_fetch_runs_endpoint_down(self):
        with self.assertLogs("garmin_fetch", "WARNING"):
            self.assertEqual(garmin_fetch.fetch_runs(FakeApi(broken={"activities"}), "a", "b"), [])

    def test_fetch_day_maps_fields(self):
        row = garmin_fetch.fetch_day(FakeApi(), "2026-09-20")
        self.assertEqual(row["resting_hr"], 48)
        self.assertEqual(row["sleep_score"], 84)
        self.assertEqual(row["hrv_baseline_low"], 52)
        self.assertEqual(row["readiness_level"], "HIGH")
        self.assertEqual(row["vo2max_running"], 53.4)
        self.assertNotIn("steps", row)

    def test_fetch_day_survives_broken_endpoints(self):
        api = FakeApi(broken={"stats", "sleep", "hrv", "readiness", "max"})
        with self.assertLogs("garmin_fetch", "WARNING"):
            row = garmin_fetch.fetch_day(api, "2026-09-20")
        self.assertEqual(row["date"], "2026-09-20")
        self.assertTrue(all(v is None for k, v in row.items() if k != "date"))

    def test_request_budget(self):
        api = FakeApi()
        with mock.patch.object(garmin_fetch, "MAX_REQUESTS", 3), self.assertLogs("garmin_fetch", "WARNING"):
            garmin_fetch.fetch_day(api, "2026-09-20")
        self.assertEqual(api.calls, 3)

    def test_g_helper(self):
        g = garmin_fetch._g
        self.assertEqual(g({"a": [{"b": 1}]}, "a", 0, "b"), 1)
        self.assertIsNone(g({"a": []}, "a", 0, "b"))
        self.assertIsNone(g(None, "a"))
        self.assertIsNone(g({"a": 5}, "a", "b"))

    def test_decode_token(self):
        import base64

        good = base64.b64encode(b'{"di_token": "x"}').decode()
        self.assertEqual(garmin_fetch.decode_token_b64(good), '{"di_token": "x"}')
        with self.assertRaises(garmin_fetch.GarminAuthError):
            garmin_fetch.decode_token_b64(base64.b64encode(b"not json").decode())
        with self.assertRaises(garmin_fetch.GarminAuthError):
            garmin_fetch.decode_token_b64("%%%")


class DotenvTests(unittest.TestCase):
    def test_load_dotenv(self):
        import config

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ".env")
            with open(path, "w") as f:
                f.write('# comment\nPACER_T1="quoted"\nexport PACER_T2=plain\nPACER_T3=keep\n\nnot a pair\n')
            with mock.patch.dict(os.environ, {"PACER_T3": "existing"}):
                config.load_dotenv(path)
                self.assertEqual(os.environ["PACER_T1"], "quoted")
                self.assertEqual(os.environ["PACER_T2"], "plain")
                self.assertEqual(os.environ["PACER_T3"], "existing")
            config.load_dotenv(os.path.join(tmp, "missing.env"))


class LocationTests(unittest.TestCase):
    def test_map_activity_rounds_start_coordinates(self):
        run = garmin_fetch.map_activity({**activity(1, "running"), "startLatitude": 13.756331, "startLongitude": 100.501765})
        self.assertEqual((run["start_lat"], run["start_lon"]), (13.76, 100.5))
        run = garmin_fetch.map_activity(activity(2, "treadmill_running"))
        self.assertEqual((run["start_lat"], run["start_lon"]), (None, None))

    def test_last_run_location_skips_indoor_and_missing_gps(self):
        db = Storage(":memory:")
        self.assertIsNone(db.last_run_location())
        gps = {"startLatitude": 18.79, "startLongitude": 98.98}
        db.upsert_runs([
            garmin_fetch.map_activity({**activity(1, "running", start="2026-09-10 06:00:00"), **gps}),
            garmin_fetch.map_activity({**activity(2, "treadmill_running", start="2026-09-12 06:00:00"),
                                       "startLatitude": 1.0, "startLongitude": 1.0}),
            garmin_fetch.map_activity(activity(3, "running", start="2026-09-14 06:00:00")),  # no GPS
        ])
        self.assertEqual(db.last_run_location(), (18.79, 98.98))
        db.close()

    def test_weather_coordinates_from_env(self):
        import config

        with mock.patch.dict(os.environ, {"WEATHER_LAT": "13.75", "WEATHER_LON": "100.5", "RUN_TIME": "06:00"}), \
                mock.patch("config.load_dotenv"):
            cfg = config.load_config()
        self.assertEqual((cfg.weather_lat, cfg.weather_lon, cfg.run_time), (13.75, 100.5, "06:00"))
        with mock.patch.dict(os.environ, {"WEATHER_LAT": "north", "WEATHER_LON": "200"}), \
                mock.patch("config.load_dotenv"), self.assertLogs("config", "WARNING"):
            cfg = config.load_config()
        self.assertEqual((cfg.weather_lat, cfg.weather_lon), (None, None))


class StorageTests(unittest.TestCase):
    def test_upsert_keeps_existing_values(self):
        db = Storage(":memory:")
        self.assertTrue(db.is_empty())
        db.upsert_daily({"date": "2026-09-20", "resting_hr": 50, "sleep_seconds": 27000})
        db.upsert_daily({"date": "2026-09-20", "resting_hr": None, "sleep_seconds": 28000})
        row = db.daily_since("2026-09-01")[0]
        self.assertEqual(row["resting_hr"], 50)
        self.assertEqual(row["sleep_seconds"], 28000)
        self.assertFalse(db.is_empty())

        run = garmin_fetch.map_activity(activity(1, "running"))
        db.upsert_runs([run])
        db.upsert_runs([{**run, "training_load": None, "name": "renamed"}])
        rows = db.runs_since("2026-09-01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["training_load"], 80.0)
        self.assertEqual(rows[0]["name"], "renamed")
        self.assertEqual(db.runs_since("2026-09-20"), [])


def sample_result(races=True):
    daily, runs = calm_dataset()
    race_list = [
        {"name": "Uthai Trail 2026 " + "x" * 300, "type": "trail", "date": "2026-11-20", "distance_km": 50, "elevation_m": 2500, "priority": "A"},
        {"name": "Bangkok Marathon", "type": "road", "date": "2026-09-25", "distance_km": 42.195, "target_time": "04:00:00", "priority": "B"},
    ] if races else []
    return analyze(daily, runs, TODAY, race_list)


def sections(**overrides):
    base = {
        "headline": "วันนี้ร่างกายพร้อม", "recovery": "HRV ปกติ", "running": "วิ่งสม่ำเสมอ",
        "load": "โหลดเหมาะสม", "races": "อีก 60 วันถึง Uthai", "session": "Easy run",
        "recommendation": "Easy run 45 นาที โซน 2",
    }
    base.update(overrides)
    return base


class DiscordTests(unittest.TestCase):
    def check_limits(self, embed):
        self.assertLessEqual(len(embed["title"]), 256)
        self.assertLessEqual(len(embed.get("description", "")), 4096)
        self.assertLessEqual(len(embed["fields"]), 25)
        for f in embed["fields"]:
            self.assertLessEqual(len(f["name"]), 256)
            self.assertLessEqual(len(f["value"]), 1024)
            self.assertTrue(f["value"])
        self.assertLessEqual(discord_notify.embed_length(embed), 6000)

    def named(self, embed):
        return {f["name"]: f["value"] for f in embed["fields"] if f["name"] != discord_notify.BLANK}

    def test_single_dashboard_embed(self):
        payload = discord_notify.build_payload(sample_result(), sections())
        self.assertEqual(payload["username"], "Pacer")
        self.assertEqual(len(payload["embeds"]), 1)
        embed = payload["embeds"][0]
        self.check_limits(embed)
        self.assertEqual(embed["title"], "🏃 อาทิตย์ 20 ก.ย. 2026 · 🟢 พร้อมซ้อม")
        self.assertEqual(embed["color"], 0x2ECC71)
        desc = embed["description"]
        self.assertTrue(desc.startswith("วันนี้ร่างกายพร้อม"))
        self.assertIn("**🎯 แนะนำวันนี้ · Easy run**\nEasy run 45 นาที โซน 2", desc)
        for heading in ("🛌 การฟื้นตัว", "🏃 การวิ่ง 7 วัน", "📈 โหลด", "🏁 รายการแข่ง"):
            self.assertIn(f"**{heading}**", desc)
        self.assertLess(desc.index("แนะนำวันนี้"), desc.index("การฟื้นตัว"))
        self.assertIn("สรุปโดย Claude", embed["footer"]["text"])
        self.assertIn("timestamp", embed)
        chatgpt = discord_notify.build_embed(sample_result(), sections(), source="ChatGPT")
        self.assertIn("สรุปโดย ChatGPT", chatgpt["footer"]["text"])

    def test_field_values_and_formatting(self):
        f = self.named(discord_notify.build_embed(sample_result(), sections()))
        self.assertEqual(f["💓 HRV"], "**60** ms\nปกติ 50–70")
        self.assertEqual(f["❤️ RHR"], "**50** bpm ±0\nเฉลี่ย 50")
        self.assertEqual(f["😴 การนอน"], "**7.5** ชม. · 82\nเฉลี่ย 7.5 ชม.")
        self.assertEqual(f["📊 รวม 7 วัน"], "**24** km · 3 ครั้ง\n±0% จากสัปดาห์ก่อน")
        self.assertIn("5:00 /km", f["🛣️ Road"])
        self.assertIn("D+ 400 m · 50 m/km", f["⛰️ Trail"])
        self.assertEqual(f["⚖️ ACWR"], "**1** 🟢\nเหมาะสม\nโหลด 7 วัน ÷ 28 วัน\nปกติ 0.8–1.3")
        chart = f["📅 ระยะรายสัปดาห์ (km)"]
        self.assertIn("```", chart)
        # the last row covers today, so it must be labelled as this week, with a full range
        self.assertIn("14/09-20/09", chart)
        self.assertTrue(chart.rstrip("`\n").endswith("สัปดาห์นี้"))
        races = [name for name in f if name.startswith(("🅰️", "🅱️"))]
        self.assertEqual(races, ["🅱️ Bangkok Marathon · อีก 5 วัน", "🅰️ Uthai Trail 2026 " + "x" * 300 + " · อีก 61 วัน"][:1] + races[1:])
        bkk = f["🅱️ Bangkok Marathon · อีก 5 วัน"]
        self.assertIn("🪶 mini taper", bkk)
        self.assertIn("target pace 5:41 /km · long run เร็วกว่า 41 วิ/km", bkk)
        uthai = next(v for k, v in f.items() if k.startswith("🅰️"))
        self.assertIn("D+ 2,500 m", uthai)
        self.assertIn("Phase **🏗️ Build**", uthai)
        self.assertIn("`▰▰▱▱▱▱▱▱▱▱` long run 20%", uthai)

    def test_inline_groups_are_padded_to_rows_of_three(self):
        embed = discord_notify.build_embed(sample_result(races=False), sections())
        inline = [f for f in embed["fields"] if f["inline"]]
        self.assertEqual(len(inline) % 3, 0)
        names = [f["name"] for f in embed["fields"]]
        self.assertLess(names.index("🫁 VO2max"), names.index("📊 รวม 7 วัน"))
        self.assertEqual(names.index("📊 รวม 7 วัน") % 3, 0)
        self.assertEqual(names.index("⚖️ ACWR") % 3, 0)

    def test_flags_as_quote_and_red_status(self):
        result = sample_result(races=False)
        result["status"] = "red"
        result["flags"] = [{"level": "red", "message": "ACWR สูง"}, {"level": "yellow", "message": "นอนน้อย"}]
        embed = discord_notify.build_embed(result, sections())
        self.assertEqual(embed["color"], 0xE74C3C)
        self.assertTrue(embed["title"].endswith("🔴 ควรพัก"))
        self.assertIn("> 🔴 ACWR สูง\n> 🟡 นอนน้อย", embed["description"])

    def test_missing_today_data_is_hidden_not_dashed(self):
        result = sample_result(races=False)
        rec = result["recovery"]
        rec["hrv"]["last_night"] = None
        rec["sleep"]["last_night_h"] = None
        rec["body_battery_wake"] = None
        rec["readiness"] = {"score": None, "level": None}
        f = self.named(discord_notify.build_embed(result, sections()))
        self.assertEqual(f["💓 HRV"], "**60** ms\nเฉลี่ย 7 วัน")
        self.assertEqual(f["😴 การนอน"], "**7.5** ชม.\nเฉลี่ย 7 วัน")
        self.assertNotIn("🔋 Body battery", f)
        self.assertNotIn("🎯 Readiness", f)

    def test_no_runs_no_races_and_none_values(self):
        result = analyze([], [], TODAY, [])
        embed = discord_notify.build_embed(result, summarize.fallback_sections(result), source=None)
        self.check_limits(embed)
        f = self.named(embed)
        self.assertEqual(f["📊 รวม 7 วัน"], "ไม่มีการวิ่ง")
        self.assertNotIn("🛣️ Road", f)
        self.assertNotIn("⛰️ Trail", f)
        self.assertNotIn("💓 HRV", f)
        self.assertIn("ไม่ได้ใช้ AI", embed["footer"]["text"])
        text = json.dumps(embed, ensure_ascii=False)
        self.assertNotIn("None", text)
        self.assertNotIn("–––", text)

    def test_huge_content_is_capped(self):
        result = sample_result()
        result["races"] = [{**r, "name": "y" * 2000} for r in result["races"]]
        long_sections = {k: "ข้อความยาว " * 500 for k in sections()}
        self.check_limits(discord_notify.build_embed(result, long_sections))

    def test_acwr_zones(self):
        result = sample_result(races=False)
        for acwr, icon in ((0.5, "🔵"), (1.0, "🟢"), (1.4, "🟡"), (1.8, "🔴"), (None, "⚪")):
            result["load"]["acwr"] = acwr
            self.assertIn(icon, self.named(discord_notify.build_embed(result, sections()))["⚖️ ACWR"])

    def test_number_helpers(self):
        self.assertEqual(discord_notify._num(53.0), "53")
        self.assertEqual(discord_notify._num(21.01), "21.0")
        self.assertEqual(discord_notify._num(2500), "2,500")
        self.assertEqual(discord_notify._change(-30.6, "%"), "▼30.6%")
        self.assertEqual(discord_notify._change(5.5), "▲5.5")

    def test_send_raises_on_http_error(self):
        fake_requests = mock.MagicMock()
        fake_requests.post.return_value = mock.Mock(status_code=400, text="bad")
        with mock.patch.dict(sys.modules, {"requests": fake_requests}):
            with self.assertRaises(RuntimeError) as ctx:
                discord_notify.send("https://discord.example/webhook/secret", {"embeds": []})
        self.assertNotIn("secret", str(ctx.exception))
        fake_requests.post.return_value = mock.Mock(status_code=204, text="")
        with mock.patch.dict(sys.modules, {"requests": fake_requests}):
            discord_notify.send("https://discord.example/webhook", {"embeds": []})
        self.assertEqual(fake_requests.post.call_args.kwargs["timeout"], 20)


class FallbackTests(unittest.TestCase):
    def test_fallback_has_all_sections(self):
        out = summarize.fallback_sections(sample_result())
        self.assertEqual(set(out), set(summarize.SECTION_KEYS))
        self.assertTrue(out["headline"] and out["recommendation"] and out["session"])
        self.assertEqual(out["recovery"], "HRV อยู่ในเกณฑ์ดี")

    def test_fallback_red_starts_with_warning(self):
        result = sample_result(races=False)
        result["status"] = "red"
        result["flags"] = [{"level": "red", "message": "ACWR 1.7 สูง"}]
        out = summarize.fallback_sections(result)
        self.assertTrue(out["headline"].startswith("⚠️"))
        self.assertEqual(out["session"], "พัก")
        self.assertEqual(out["races"], "")

    def test_fallback_empty_result(self):
        for result in (analyze([], [], TODAY), {}):
            out = summarize.fallback_sections(result)
            self.assertTrue(out["recommendation"])


def fake_cfg(**overrides):
    import config

    base = dict(
        garmin_tokens_b64=None, garmin_tokens_path="", anthropic_api_key="a-key",
        discord_webhook_url=None, claude_model="claude-sonnet-5", llm_providers=["anthropic"],
        openai_api_key="o-key", openai_model="gpt-5.4-mini",
    )
    base.update(overrides)
    return config.Config(**base)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.result = sample_result(races=False)

    def test_parse_providers(self):
        from config import parse_providers

        self.assertEqual(parse_providers(None), ["anthropic"])
        self.assertEqual(parse_providers(" OpenAI , anthropic,openai"), ["openai", "anthropic"])
        with self.assertLogs("config", "WARNING"):
            self.assertEqual(parse_providers("gemini,openai"), ["openai"])

    def test_uses_first_provider(self):
        with mock.patch.object(summarize, "_summarize_openai", return_value=sections()) as oa, \
                mock.patch.object(summarize, "_summarize_anthropic") as an:
            out, source = summarize.summarize(self.result, fake_cfg(llm_providers=["openai", "anthropic"]))
        self.assertEqual(source, "ChatGPT")
        self.assertEqual(out, sections())
        oa.assert_called_once_with(self.result, "gpt-5.4-mini", "o-key")
        an.assert_not_called()

    def test_falls_through_to_next_provider(self):
        with mock.patch.object(summarize, "_summarize_openai", side_effect=RuntimeError("quota")), \
                mock.patch.object(summarize, "_summarize_anthropic", return_value=sections()), \
                self.assertLogs("summarize", "WARNING"):
            _, source = summarize.summarize(self.result, fake_cfg(llm_providers=["openai", "anthropic"]))
        self.assertEqual(source, "Claude")

    def test_skips_provider_without_key(self):
        with mock.patch.object(summarize, "_summarize_anthropic") as an, \
                mock.patch.object(summarize, "_summarize_openai", return_value=sections()):
            _, source = summarize.summarize(
                self.result, fake_cfg(llm_providers=["anthropic", "openai"], anthropic_api_key=None)
            )
        self.assertEqual(source, "ChatGPT")
        an.assert_not_called()

    def test_all_fail_raises(self):
        with mock.patch.object(summarize, "_summarize_anthropic", side_effect=RuntimeError("x")), \
                self.assertLogs("summarize", "WARNING"), self.assertRaises(summarize.SummaryError):
            summarize.summarize(self.result, fake_cfg())
        with self.assertRaises(summarize.SummaryError):
            summarize.summarize(self.result, fake_cfg(anthropic_api_key=None))

    def fake_openai(self, status="completed", text=None, reason=None):
        response = mock.Mock(status=status, output_text=text if text is not None else json.dumps(sections()))
        response.usage = mock.Mock(input_tokens=900, output_tokens=700, output_tokens_details=None)
        response.incomplete_details = mock.Mock(reason=reason) if reason else None
        module = mock.MagicMock()
        module.OpenAI.return_value.responses.create.return_value = response
        return module

    def test_openai_request_and_parse(self):
        module = self.fake_openai()
        with mock.patch.dict(sys.modules, {"openai": module}):
            out = summarize._summarize_openai(self.result, "gpt-5.4-mini", "o-key")
        self.assertEqual(out, sections())
        module.OpenAI.assert_called_once_with(api_key="o-key")
        kwargs = module.OpenAI.return_value.responses.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-5.4-mini")
        self.assertEqual(kwargs["instructions"], summarize.SYSTEM_PROMPT)
        fmt = kwargs["text"]["format"]
        self.assertEqual((fmt["type"], fmt["strict"]), ("json_schema", True))
        self.assertEqual(fmt["schema"]["required"], list(summarize.SECTION_KEYS))
        self.assertIn('"status"', kwargs["input"])

    def test_openai_uses_low_reasoning_effort(self):
        module = self.fake_openai()
        with mock.patch.dict(sys.modules, {"openai": module}), self.assertLogs("summarize", "INFO") as logs:
            summarize._summarize_openai(self.result, "gpt-5.4-mini", "o-key")
        kwargs = module.OpenAI.return_value.responses.create.call_args.kwargs
        self.assertEqual(kwargs["reasoning"], {"effort": "low"})
        self.assertLessEqual(kwargs["max_output_tokens"], 4000)
        self.assertTrue(any("usage:" in line for line in logs.output))

    def test_openai_retries_without_reasoning_when_rejected(self):
        module = self.fake_openai()
        bad = type("BadRequestError", (Exception,), {})
        module.BadRequestError = bad
        create = module.OpenAI.return_value.responses.create
        ok = create.return_value
        create.side_effect = [bad("Unsupported parameter: reasoning"), ok]
        with mock.patch.dict(sys.modules, {"openai": module}):
            summarize._summarize_openai(self.result, "gpt-4.1", "o-key")
        self.assertEqual(create.call_count, 2)
        self.assertNotIn("reasoning", create.call_args.kwargs)

    def test_openai_incomplete_or_bad_json(self):
        for module in (self.fake_openai(status="incomplete", reason="max_output_tokens"),
                       self.fake_openai(text="not json"),
                       self.fake_openai(text=json.dumps({"headline": "", "recommendation": "x"}))):
            with mock.patch.dict(sys.modules, {"openai": module}), self.assertRaises(summarize.SummaryError):
                summarize._summarize_openai(self.result, "m", "k")

    def test_anthropic_request_and_parse(self):
        module = mock.MagicMock()
        module.BadRequestError = type("BadRequestError", (Exception,), {})
        block = mock.Mock(type="text", text=json.dumps(sections()))
        module.Anthropic.return_value.messages.create.return_value = mock.Mock(stop_reason="end_turn", content=[block])
        with mock.patch.dict(sys.modules, {"anthropic": module}):
            out = summarize._summarize_anthropic(self.result, "claude-sonnet-5", "a-key")
        self.assertEqual(out, sections())
        kwargs = module.Anthropic.return_value.messages.create.call_args.kwargs
        self.assertEqual(kwargs["thinking"], {"type": "disabled"})
        self.assertEqual(kwargs["output_config"]["format"]["type"], "json_schema")


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        garmin_fetch.reset_request_budget()
        acts = [activity(i, "running", start=f"2026-09-{i:02d} 06:00:00") for i in range(1, 20, 2)]
        acts.append(activity(99, "cycling", start="2026-09-18 06:00:00"))
        self.api = FakeApi(acts)
        self.env = {
            "DB_PATH": os.path.join(self.tmp.name, "garmin.db"),
            "DISCORD_WEBHOOK_URL": "https://discord.example/webhook",
            "RACES": "[{bad json",
            "GARMINTOKENS_BASE64": "",
            "BACKFILL_DAYS": "42",
            "REFRESH_DAYS": "3",
            "WEATHER_LAT": "",
            "WEATHER_LON": "",
        }
        self.forecast = mock.Mock(return_value=(None, None))

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, argv, login=None, summary=None, now=None):
        login = login or mock.Mock(return_value=self.api)
        summary = summary or mock.Mock(return_value=(sections(), "Claude"))
        clock = mock.patch.object(main, "datetime", FakeDatetime(now)) if now else contextlib.nullcontext()
        with mock.patch.dict(os.environ, self.env), \
                mock.patch("config.load_dotenv"), clock, \
                mock.patch.object(garmin_fetch, "login", login), \
                mock.patch.object(summarize, "summarize", summary), \
                mock.patch.object(weather, "fetch_hourly", self.forecast), \
                mock.patch.object(discord_notify, "send_summary") as send, \
                mock.patch.object(discord_notify, "send_auth_alert") as alert, \
                self.assertLogs("garmin-daily", "INFO") as logs:
            code = main.main(argv)
        return code, send, alert, logs

    def test_backfill_then_refresh_and_only_runs_stored(self):
        code, send, _, _ = self.run_main(["--date", "2026-09-20"])
        self.assertEqual(code, 0)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[2], sections())
        self.assertEqual(send.call_args.args[3], "Claude")
        db = Storage(self.env["DB_PATH"])
        self.assertEqual(len(db.daily_since("2000-01-01")), 42)  # BACKFILL_DAYS
        self.assertEqual({r["type_key"] for r in db.runs_since("2000-01-01")}, {"running"})
        db.close()
        # second run: DB not empty → REFRESH_DAYS (--force skips the once-a-day gate)
        calls_before = self.api.calls
        self.run_main(["--date", "2026-09-20", "--force"])
        self.assertEqual(self.api.calls - calls_before, 3 * 5 + 1)

    def test_sends_once_per_day(self):
        code, send, _, _ = self.run_main(["--date", "2026-09-20"])
        self.assertEqual((code, send.call_count), (0, 1))
        calls_before = self.api.calls
        code, send, _, logs = self.run_main(["--date", "2026-09-20"])
        self.assertEqual(code, 0)
        send.assert_not_called()
        # still collects today's data: sleep and runs usually land after the summary
        self.assertEqual(self.api.calls - calls_before, 6)  # 5 day endpoints + activities
        self.assertTrue(any("already sent" in line for line in logs.output))

    def test_readiness_alone_does_not_count_as_synced(self):
        """Garmin publishes readiness hours before the night's sleep upload."""
        self.run_main(["--date", "2026-09-20"])  # populate the DB
        self.api.missing_recovery = {"2026-09-21"}  # sleep + HRV missing, readiness present
        code, send, _, logs = self.run_main([], now="2026-09-21 05:07")
        self.assertEqual(code, 0)
        send.assert_not_called()
        self.assertTrue(any("not synced" in line for line in logs.output))
        self.assertFalse(main.has_recovery_data([{"date": "2026-09-21", "readiness_score": 25}], date(2026, 9, 21)))
        self.assertTrue(main.has_recovery_data([{"date": "2026-09-21", "sleep_seconds": 100}], date(2026, 9, 21)))
        self.assertTrue(main.has_recovery_data([{"date": "2026-09-21", "hrv_last_night": 60}], date(2026, 9, 21)))

    def test_waits_until_recovery_data_is_synced(self):
        self.run_main(["--date", "2026-09-20"])  # populate the DB (first run backfills)
        self.api.missing_recovery = {"2026-09-21"}
        # 05:00 local, before the 06:45 deadline → poll today only, do not send
        calls_before = self.api.calls
        code, send, _, logs = self.run_main([], now="2026-09-21 05:00")
        self.assertEqual(code, 0)
        send.assert_not_called()
        self.assertEqual(self.api.calls - calls_before, 5)  # one fetch_day, no activities call
        self.assertTrue(any("not synced" in line for line in logs.output))

        # 06:50 local, past the deadline → send with what is there
        code, send, _, logs = self.run_main([], now="2026-09-21 06:50")
        self.assertEqual(code, 0)
        send.assert_called_once()
        self.assertTrue(any("sending anyway" in line for line in logs.output))

    def test_sends_as_soon_as_data_arrives(self):
        self.run_main(["--date", "2026-09-20"])  # populate the DB
        code, send, _, _ = self.run_main([], now="2026-09-21 05:30")
        self.assertEqual(code, 0)
        send.assert_called_once()

    def test_weather_from_last_run_location_when_sending(self):
        self.api.activities[-2] = {**self.api.activities[-2], "startLatitude": 13.7563, "startLongitude": 100.5018}
        hourly = {"time": [f"2026-09-20T{h:02d}:00" for h in range(24)],
                  "temperature_2m": [30.0] * 24, "dew_point_2m": [26.0] * 24}
        self.forecast.return_value = ({"hourly": hourly}, None)
        code, send, _, _ = self.run_main(["--date", "2026-09-20"])
        self.assertEqual(code, 0)
        self.forecast.assert_called_once_with(13.76, 100.5, "2026-09-20", "Asia/Bangkok")
        result = send.call_args.args[1]
        self.assertEqual(result["weather"]["heat_level"], "high")
        self.assertEqual(result["weather"]["location_source"], "last_run")

    def test_weather_location_from_config_wins(self):
        self.env.update(WEATHER_LAT="18.79", WEATHER_LON="98.98")
        self.run_main(["--date", "2026-09-20"])
        self.assertEqual(self.forecast.call_args.args[:2], (18.79, 98.98))

    def test_polling_run_skips_weather(self):
        self.run_main(["--date", "2026-09-20"])  # populate the DB
        self.env.update(WEATHER_LAT="13.75", WEATHER_LON="100.5")
        self.forecast.reset_mock()
        self.api.missing_recovery = {"2026-09-21"}
        code, send, _, _ = self.run_main([], now="2026-09-21 05:00")
        self.assertEqual(code, 0)
        send.assert_not_called()
        self.forecast.assert_not_called()

    def test_force_sends_even_when_already_sent(self):
        self.run_main(["--date", "2026-09-20"])
        code, send, _, _ = self.run_main(["--date", "2026-09-20", "--force"])
        self.assertEqual(code, 0)
        send.assert_called_once()

    def test_marks_sent_only_after_discord_succeeds(self):
        with mock.patch.dict(os.environ, self.env), mock.patch("config.load_dotenv"), \
                mock.patch.object(garmin_fetch, "login", return_value=self.api), \
                mock.patch.object(summarize, "summarize", return_value=(sections(), "Claude")), \
                mock.patch.object(discord_notify, "send_summary", side_effect=RuntimeError("boom")), \
                self.assertLogs("garmin-daily", "INFO"):
            self.assertEqual(main.main(["--date", "2026-09-20"]), 1)
        db = Storage(self.env["DB_PATH"])
        self.assertFalse(db.was_sent("2026-09-20"))
        db.close()

    def test_claude_failure_uses_fallback(self):
        boom = mock.Mock(side_effect=RuntimeError("API down"))
        code, send, _, logs = self.run_main(["--date", "2026-09-20"], summary=boom)
        self.assertEqual(code, 0)
        self.assertEqual(send.call_args.args[2]["session"], "Easy run / Workout")
        self.assertIsNone(send.call_args.args[3])
        self.assertTrue(any("fallback" in line for line in logs.output))

    def test_auth_failure_alerts_and_exits_1(self):
        login = mock.Mock(side_effect=garmin_fetch.GarminAuthError("expired"))
        code, send, alert, _ = self.run_main([], login=login)
        self.assertEqual(code, 1)
        alert.assert_called_once()
        send.assert_not_called()

    def test_network_failure_still_sends_summary(self):
        login = mock.Mock(side_effect=ConnectionError("no network"))
        code, send, _, _ = self.run_main(["--date", "2026-09-20"], login=login)
        self.assertEqual(code, 0)
        send.assert_called_once()

    def test_dry_run_does_not_send(self):
        with mock.patch("builtins.print") as printed:
            code, send, _, _ = self.run_main(["--dry-run", "--backfill", "2", "--date", "2026-09-20"])
        self.assertEqual(code, 0)
        send.assert_not_called()
        json.loads(printed.call_args_list[0].args[0])
        self.assertEqual(self.api.calls, 2 * 5 + 1)

    def test_logs_never_contain_secrets(self):
        self.env["GARMINTOKENS_BASE64"] = "c2VjcmV0dG9rZW4="
        _, _, _, logs = self.run_main(["--date", "2026-09-20"])
        joined = "\n".join(logs.output)
        self.assertNotIn("discord.example", joined)
        self.assertNotIn("c2VjcmV0dG9rZW4=", joined)


if __name__ == "__main__":
    unittest.main()
