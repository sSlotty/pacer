"""Offline tests for analysis.py and config.parse_races (CLAUDE.md §14). Stdlib only."""

import json
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import analyze, classify_run  # noqa: E402
from config import parse_races  # noqa: E402

TODAY = date(2026, 9, 20)


def iso(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


_ids = iter(range(1, 10_000))


def run(offset, type_key="running", km=10.0, secs=3000, elev=0, load=60, anaerobic=1.0, **extra):
    return {
        "activity_id": next(_ids),
        "date": iso(offset),
        "start_time": iso(offset) + " 06:00:00",
        "name": f"{type_key} {offset}",
        "type_key": type_key,
        "distance_m": km * 1000,
        "duration_s": secs,
        "moving_s": secs,
        "elev_gain_m": elev,
        "elev_loss_m": elev,
        "avg_hr": 140,
        "max_hr": 170,
        "avg_cadence": 172,
        "training_load": load,
        "aerobic_te": 3.0,
        "anaerobic_te": anaerobic,
        **extra,
    }


def day(offset, **overrides):
    row = {
        "date": iso(offset),
        "resting_hr": 50,
        "stress_avg": 25,
        "body_battery_high": 90,
        "body_battery_low": 20,
        "body_battery_wake": 80,
        "sleep_seconds": 7.5 * 3600,
        "deep_sleep_seconds": 1.5 * 3600,
        "rem_sleep_seconds": 1.8 * 3600,
        "awake_seconds": 600,
        "sleep_score": 82,
        "hrv_last_night": 60,
        "hrv_weekly_avg": 60,
        "hrv_status": "BALANCED",
        "hrv_baseline_low": 50,
        "hrv_baseline_high": 70,
        "readiness_score": 75,
        "readiness_level": "HIGH",
        "vo2max_running": 52.0,
    }
    row.update(overrides)
    return row


def calm_dataset(days=42):
    """Steady training: every 7-day window has exactly one road, one trail and one treadmill run."""
    daily = [day(i) for i in range(days)]
    runs = []
    for i in range(days):
        r = i % 7
        if r == 0:
            runs.append(run(i, "running", km=10, secs=3000, load=60))  # 5:00/km
        elif r == 2:
            runs.append(run(i, "trail_running", km=8, secs=3600, elev=400, load=70))
        elif r == 4:
            runs.append(run(i, "treadmill_running", km=6, secs=1500, load=40))  # 4:10/km
    return daily, runs


def has_flag(result, level, text):
    return any(f["level"] == level and text in f["message"] for f in result["flags"])


class ClassificationTests(unittest.TestCase):
    def test_classify_run(self):
        self.assertEqual(classify_run("trail_running", 10000, 0), "trail")
        self.assertEqual(classify_run("running", 10000, 2000), "road")
        self.assertEqual(classify_run("treadmill_running", 10000, 0), "road")
        self.assertEqual(classify_run("street_running", 10000, 0), "road")
        # ultra_run: 40 m/km → trail, 4 m/km → road, exactly at threshold → trail
        self.assertEqual(classify_run("ultra_run", 50000, 2000, 20), "trail")
        self.assertEqual(classify_run("ultra_run", 50000, 200, 20), "road")
        self.assertEqual(classify_run("ultra_run", 50000, 1000, 20), "trail")
        self.assertEqual(classify_run("ultra_run", 50000, None, 20), "road")

    def test_ultra_run_both_cases_in_analysis(self):
        daily, runs = calm_dataset()
        runs += [
            run(1, "ultra_run", km=50, secs=30000, elev=2500),  # trail
            run(3, "ultra_run", km=50, secs=20000, elev=100),  # road
        ]
        res = analyze(daily, runs, TODAY)
        self.assertEqual(res["trail_7d"]["runs"], 2)
        self.assertEqual(res["road_7d"]["runs"], 3)
        self.assertEqual(res["trail_7d"]["km"], 58.0)
        self.assertEqual(res["road_7d"]["km"], 66.0)

    def test_threshold_is_configurable(self):
        daily, runs = calm_dataset()
        runs.append(run(1, "ultra_run", km=50, secs=30000, elev=1500))  # 30 m/km
        self.assertEqual(analyze(daily, runs, TODAY, trail_elev_threshold=20)["trail_7d"]["runs"], 2)
        self.assertEqual(analyze(daily, runs, TODAY, trail_elev_threshold=40)["trail_7d"]["runs"], 1)


class RunStatsTests(unittest.TestCase):
    def test_treadmill_excluded_from_pace(self):
        daily, runs = calm_dataset()
        res = analyze(daily, runs, TODAY)
        road = res["road_7d"]
        self.assertEqual(road["runs"], 2)  # outdoor + treadmill both count as road
        self.assertEqual(road["km"], 16.0)
        self.assertEqual(road["avg_pace"], "5:00")  # treadmill 4:10/km ignored

    def test_pace_uses_moving_time(self):
        daily, runs = calm_dataset()
        for r in runs:
            if r["type_key"] == "running":
                r["duration_s"] = 3600
        self.assertEqual(analyze(daily, runs, TODAY)["road_7d"]["avg_pace"], "5:00")

    def test_totals_and_trail(self):
        daily, runs = calm_dataset()
        res = analyze(daily, runs, TODAY)
        tot, trail = res["totals_7d"], res["trail_7d"]
        self.assertEqual(tot["km"], 24.0)
        self.assertEqual(tot["runs"], 3)
        self.assertEqual(tot["km_change_pct"], 0.0)
        self.assertEqual([w["km"] for w in tot["weekly_km_4w"]], [24.0] * 4)
        self.assertEqual(trail["elev_gain_m"], 400)
        self.assertEqual(trail["effort_km"], 12.0)  # 8 + 400/100
        self.assertEqual(trail["vertical_rate_m_per_km"], 50)
        self.assertEqual(res["road_7d"]["long_run_28d"]["km"], 10.0)
        self.assertEqual(res["road_7d"]["long_run_28d"]["pace"], "5:00")

    def test_effort_km(self):
        runs = [run(0, "trail_running", km=10, elev=500), run(2, "trail_running", km=5.5, elev=250)]
        res = analyze([], runs, TODAY)
        self.assertEqual(res["trail_7d"]["effort_km"], 23.0)  # 15.5 + 750/100


class LoadTests(unittest.TestCase):
    def test_acwr(self):
        runs = [run(i, load=50) for i in range(7, 28)] + [run(i, load=100) for i in range(7)]
        res = analyze([day(i) for i in range(28)], runs, TODAY)
        load = res["load"]
        self.assertEqual(load["acute"], 100.0)
        self.assertEqual(load["chronic"], 62.5)
        self.assertEqual(load["acwr"], 1.6)
        self.assertFalse(load["load_data_insufficient"])

    def test_missing_training_load_uses_duration(self):
        runs = [run(0, secs=3600, load=None)]
        self.assertEqual(analyze([], runs, TODAY)["load"]["acute"], round(60 / 7, 1))

    def test_insufficient_data(self):
        daily, runs = calm_dataset(days=14)
        self.assertTrue(analyze(daily, runs, TODAY)["load"]["load_data_insufficient"])

    def test_calm_dataset_is_green(self):
        daily, runs = calm_dataset()
        res = analyze(daily, runs, TODAY)
        self.assertEqual(res["load"]["acwr"], 1.0)
        self.assertEqual(res["load"]["rest_days_7d"], 4)
        self.assertEqual(res["load"]["hard_pct_7d"], 0.0)
        self.assertEqual(res["flags"], [])
        self.assertEqual(res["status"], "green")


class FlagTests(unittest.TestCase):
    def setUp(self):
        self.daily, self.runs = calm_dataset()

    def analyze(self, races=None):
        return analyze(self.daily, self.runs, TODAY, races)

    def set_day(self, offset, **values):
        self.daily[offset].update(values)

    def test_acwr_red(self):
        self.runs += [run(i, load=150) for i in range(7)]
        res = self.analyze()
        self.assertTrue(has_flag(res, "red", "ACWR"))
        self.assertEqual(res["status"], "red")

    def test_acwr_yellow(self):
        # base: 170 load/week → acute = chronic; +105 this week → 4·275/785 ≈ 1.40
        self.runs += [run(i, km=1, load=35) for i in (1, 3, 5)]
        res = self.analyze()
        acwr = res["load"]["acwr"]
        self.assertTrue(1.3 < acwr <= 1.5, acwr)
        self.assertTrue(has_flag(res, "yellow", "ACWR"))

    def test_acwr_low_and_taper_exception(self):
        self.runs = [r for r in self.runs if not (0 <= (TODAY - date.fromisoformat(r["date"])).days <= 6)]
        res = self.analyze()
        self.assertTrue(has_flag(res, "yellow", "โหลดลดลงมาก"))
        taper = [{"name": "X", "type": "road", "date": iso(-10), "priority": "C"}]
        self.assertFalse(has_flag(self.analyze(taper), "yellow", "โหลดลดลงมาก"))
        recovery = [{"name": "X", "type": "road", "date": iso(5), "priority": "A"}]
        self.assertFalse(has_flag(self.analyze(recovery), "yellow", "โหลดลดลงมาก"))

    def test_hrv_low(self):
        self.set_day(0, hrv_last_night=45)
        res = self.analyze()
        self.assertTrue(has_flag(res, "yellow", "HRV"))
        self.assertEqual(res["recovery"]["hrv"]["vs_baseline"], "below")

    def test_rhr_high_yellow(self):
        self.set_day(1, resting_hr=55)
        res = self.analyze()
        self.assertTrue(has_flag(res, "yellow", "RHR"))
        self.assertEqual(res["status"], "yellow")

    def test_rhr_high_with_low_hrv_is_red(self):
        self.set_day(1, resting_hr=56)
        self.set_day(0, hrv_last_night=40)
        res = self.analyze()
        self.assertTrue(has_flag(res, "red", "RHR"))
        self.assertTrue(has_flag(res, "red", "แพทย์"))
        self.assertEqual(res["status"], "red")

    def test_rhr_uses_yesterday_not_today(self):
        self.set_day(0, resting_hr=70)
        self.assertFalse(has_flag(self.analyze(), "yellow", "RHR"))

    def test_short_sleep(self):
        self.set_day(0, sleep_seconds=5 * 3600)
        res = self.analyze()
        self.assertTrue(has_flag(res, "yellow", "น้อยกว่า 6 ชม."))
        self.assertFalse(has_flag(res, "yellow", "6.5"))

    def test_sleep_avg_7d(self):
        for i in range(7):
            self.set_day(i, sleep_seconds=6.2 * 3600)
        self.assertTrue(has_flag(self.analyze(), "yellow", "6.5"))

    def test_weekly_km_jump(self):
        self.runs.append(run(1, km=10, load=1))
        res = self.analyze()
        self.assertGreater(res["totals_7d"]["km_change_pct"], 30)
        self.assertTrue(has_flag(res, "yellow", "ระยะวิ่งสัปดาห์นี้"))

    def test_trail_elev_jump(self):
        self.runs.append(run(1, "trail_running", km=1, elev=300, load=1))
        self.assertTrue(has_flag(self.analyze(), "yellow", "ระวังเข่าและน่อง"))

    def test_trail_elev_no_flag_when_prev_week_zero(self):
        runs = [run(1, "trail_running", km=10, elev=800)]
        self.assertFalse(has_flag(analyze([], runs, TODAY), "yellow", "ระวังเข่าและน่อง"))

    def test_no_rest_days(self):
        self.runs += [run(i, km=0.5, load=1) for i in (1, 3, 5, 6)]
        res = self.analyze()
        self.assertEqual(res["load"]["rest_days_7d"], 0)
        self.assertTrue(has_flag(res, "yellow", "ไม่มีวันพัก"))

    def test_too_many_hard_runs(self):
        for r in self.runs:
            if (TODAY - date.fromisoformat(r["date"])).days <= 6 and r["type_key"] != "treadmill_running":
                r["anaerobic_te"] = 2.5
        res = self.analyze()
        self.assertEqual(res["load"]["hard_pct_7d"], 66.7)
        self.assertTrue(has_flag(res, "yellow", "ซ้อมหนักถี่เกินไป"))

    def test_exactly_half_hard_is_not_flagged(self):
        runs = [run(0, anaerobic=2.0), run(2, anaerobic=1.0)]
        self.assertFalse(has_flag(analyze([], runs, TODAY), "yellow", "ซ้อมหนัก"))

    def test_readiness_low(self):
        for level in ("LOW", "POOR"):
            self.set_day(0, readiness_level=level)
            self.assertTrue(has_flag(self.analyze(), "yellow", "readiness"))
        self.set_day(0, readiness_level="MODERATE")
        self.assertFalse(has_flag(self.analyze(), "yellow", "readiness"))


class RaceTests(unittest.TestCase):
    def setUp(self):
        self.daily, self.runs = calm_dataset()

    def race(self, days, priority="A", rtype="road", name=None, **extra):
        return {"name": name or f"{priority}{days}", "type": rtype, "date": iso(-days), "priority": priority, **extra}

    def test_no_races(self):
        res = analyze(self.daily, self.runs, TODAY, [])
        self.assertIsNone(res["training_phase"])
        self.assertEqual(res["races"], [])

    def test_phase_boundaries(self):
        cases = {
            100: "Base", 71: "Base", 70: "Build", 29: "Build", 28: "Peak", 15: "Peak",
            14: "Taper", 1: "Taper", 0: "Race day", -1: "Recovery", -14: "Recovery",
        }
        for days, phase in cases.items():
            with self.subTest(days=days):
                res = analyze(self.daily, self.runs, TODAY, [self.race(days)])
                self.assertEqual(res["training_phase"], phase)
                self.assertEqual(res["races"][0]["days_to_race"], days)

    def test_old_race_not_active(self):
        res = analyze(self.daily, self.runs, TODAY, [self.race(-15)])
        self.assertIsNone(res["training_phase"])
        self.assertEqual(res["races"], [])

    def test_phase_from_nearest_a_race(self):
        races = [self.race(100, "A"), self.race(20, "A", name="Near A"), self.race(5, "B")]
        res = analyze(self.daily, self.runs, TODAY, races)
        self.assertEqual(res["training_phase"], "Peak")
        self.assertEqual(res["phase_race"], "Near A")

    def test_phase_from_nearest_race_without_a(self):
        races = [self.race(40, "C"), self.race(5, "B", name="Near B")]
        res = analyze(self.daily, self.runs, TODAY, races)
        self.assertEqual(res["training_phase"], "Taper")
        self.assertEqual(res["phase_race"], "Near B")

    def test_max_three_races_and_phase_race_kept(self):
        races = [self.race(d, "B") for d in (2, 4, 6, 8)] + [self.race(60, "A", name="Goal")]
        res = analyze(self.daily, self.runs, TODAY, races)
        self.assertEqual(len(res["races"]), 3)
        self.assertIn("Goal", [r["name"] for r in res["races"]])
        self.assertEqual(res["training_phase"], "Build")

    def test_mini_taper_and_post_race_recovery(self):
        races = [
            self.race(7, "B", name="b7"), self.race(8, "C", name="c8"), self.race(-3, "C", name="c-3"),
        ]
        by_name = {r["name"]: r for r in analyze(self.daily, self.runs, TODAY, races)["races"]}
        self.assertTrue(by_name["b7"]["mini_taper"])
        self.assertFalse(by_name["c8"]["mini_taper"])
        self.assertTrue(by_name["c-3"]["post_race_recovery"])
        races = [self.race(-8, "B", name="b-8"), self.race(3, "A", name="a3")]
        by_name = {r["name"]: r for r in analyze(self.daily, self.runs, TODAY, races)["races"]}
        self.assertFalse(by_name["b-8"]["post_race_recovery"])
        self.assertFalse(by_name["a3"]["mini_taper"])

    def test_a_races_too_close(self):
        races = [self.race(30, "A"), self.race(60, "A")]
        res = analyze(self.daily, self.runs, TODAY, races)
        self.assertTrue(has_flag(res, "info", "30 วัน"))
        self.assertEqual(res["status"], "green")  # info does not change status
        res = analyze(self.daily, self.runs, TODAY, [self.race(30, "A"), self.race(80, "A")])
        self.assertFalse(any(f["level"] == "info" for f in res["flags"]))

    def test_trail_readiness(self):
        r = self.race(60, rtype="trail", distance_km=50, elevation_m=2500)
        rd = analyze(self.daily, self.runs, TODAY, [r])["races"][0]["readiness"]
        self.assertEqual(rd["long_run_28d_km"], 10.0)
        self.assertEqual(rd["long_run_pct_of_distance"], 20.0)
        self.assertEqual(rd["trail_elev_gain_7d_m"], 400)
        self.assertEqual(rd["trail_elev_gain_7d_pct"], 16.0)
        self.assertEqual(rd["max_single_run_elev_gain_28d_m"], 400)
        self.assertEqual(rd["max_single_run_elev_gain_pct"], 16.0)
        self.assertNotIn("target_pace", rd)

    def test_road_readiness(self):
        r = self.race(60, rtype="road", distance_km=42.195, target_time="04:00:00")
        rd = analyze(self.daily, self.runs, TODAY, [r])["races"][0]["readiness"]
        self.assertEqual(rd["long_run_pct_of_distance"], round(10 / 42.195 * 100, 1))
        self.assertEqual(rd["target_pace"], "5:41")
        self.assertEqual(rd["road_long_run_28d"]["pace"], "5:00")
        self.assertEqual(rd["long_run_vs_target_s_per_km"], -41)
        self.assertNotIn("trail_elev_gain_7d_m", rd)

    def test_mixed_readiness(self):
        r = self.race(60, rtype="mixed", distance_km=30, elevation_m=800, target_time="03:30:00")
        rd = analyze(self.daily, self.runs, TODAY, [r])["races"][0]["readiness"]
        self.assertEqual(rd["trail_elev_gain_7d_pct"], 50.0)
        self.assertEqual(rd["target_pace"], "7:00")
        self.assertIn("road_long_run_28d", rd)

    def test_readiness_without_optional_fields(self):
        rd = analyze(self.daily, self.runs, TODAY, [self.race(60, rtype="mixed")])["races"][0]["readiness"]
        self.assertIsNone(rd["long_run_pct_of_distance"])
        self.assertIsNone(rd["trail_elev_gain_7d_pct"])
        self.assertIsNone(rd["target_pace"])


class ParseRacesTests(unittest.TestCase):
    def test_invalid_json(self):
        self.assertEqual(parse_races("[{not json"), [])
        self.assertEqual(parse_races(""), [])
        self.assertEqual(parse_races(None), [])
        self.assertEqual(parse_races('"just a string"'), [])

    def test_skips_only_bad_entries(self):
        raw = json.dumps(
            [
                {"name": "Good Trail", "type": "trail", "date": "2026-12-01", "distance_km": 50, "elevation_m": 2500, "priority": "A"},
                {"type": "road", "date": "2026-11-01"},  # no name
                {"name": "Bad type", "type": "swim", "date": "2026-11-01"},
                {"name": "Bad date", "type": "road", "date": "2026-13-45"},
                {"name": "Bad time", "type": "road", "date": "2026-11-01", "target_time": "4h"},
                {"name": "Bad prio", "type": "road", "date": "2026-11-01", "priority": "Z"},
                {"name": "Bad dist", "type": "road", "date": "2026-11-01", "distance_km": "far"},
                "not an object",
                {"name": "Good Road", "type": "road", "date": "2026-10-15", "distance_km": 42.195, "target_time": "04:00:00"},
            ]
        )
        races = parse_races(raw)
        self.assertEqual([r["name"] for r in races], ["Good Road", "Good Trail"])  # sorted by date
        self.assertEqual(races[0]["priority"], "B")  # default

    def test_bad_race_entries_do_not_crash_analysis(self):
        daily, runs = calm_dataset()
        races = parse_races('[{"name": "X"}, {"name": "Y", "type": "trail", "date": "2026-10-01"}]')
        res = analyze(daily, runs, TODAY, races)
        self.assertEqual(len(res["races"]), 1)
        json.dumps(res)


class RobustnessTests(unittest.TestCase):
    def test_empty_everything(self):
        res = analyze([], [], TODAY, [])
        json.dumps(res)
        self.assertEqual(res["totals_7d"]["km"], 0)
        self.assertIsNone(res["road_7d"]["avg_pace"])
        self.assertIsNone(res["load"]["acwr"])
        self.assertTrue(res["load"]["load_data_insufficient"])
        self.assertEqual(res["status"], "green")

    def test_week_without_runs(self):
        daily, runs = calm_dataset()
        runs = [r for r in runs if (TODAY - date.fromisoformat(r["date"])).days > 6]
        res = analyze(daily, runs, TODAY)
        self.assertEqual(res["totals_7d"]["runs"], 0)
        self.assertEqual(res["totals_7d"]["km_change_pct"], -100.0)
        self.assertEqual(res["load"]["rest_days_7d"], 7)
        self.assertIsNone(res["load"]["hard_pct_7d"])
        self.assertIsNone(res["road_7d"]["long_run_7d"])
        json.dumps(res)

    def test_all_none_values(self):
        daily = [{"date": iso(i), **{k: None for k in day(0) if k != "date"}} for i in range(42)]
        runs = [
            {"activity_id": i, "date": iso(i), "type_key": "running", "distance_m": 5000,
             "duration_s": None, "moving_s": None, "elev_gain_m": None, "elev_loss_m": None,
             "training_load": None, "anaerobic_te": None, "avg_cadence": None}
            for i in range(0, 42, 2)
        ]
        races = [{"name": "R", "type": "mixed", "date": iso(-30), "priority": "A"}]
        res = analyze(daily, runs, TODAY, races)
        json.dumps(res)
        self.assertIsNone(res["recovery"]["hrv"]["last_night"])
        self.assertIsNone(res["recovery"]["sleep"]["last_night_h"])
        self.assertIsNone(res["road_7d"]["avg_pace"])
        self.assertIsNone(res["recovery"]["vo2max_running"])

    def test_future_and_zero_distance_runs_ignored(self):
        runs = [run(-1, km=10), run(0, km=0)]
        self.assertEqual(analyze([], runs, TODAY)["totals_7d"]["runs"], 0)

    def test_accepts_string_date(self):
        daily, runs = calm_dataset()
        self.assertEqual(analyze(daily, runs, TODAY.isoformat())["date"], TODAY.isoformat())

    def test_output_is_json_serializable(self):
        daily, runs = calm_dataset()
        races = parse_races(json.dumps([
            {"name": "Uthai Trail", "type": "trail", "date": iso(-45), "distance_km": 50, "elevation_m": 2500, "priority": "A"},
            {"name": "BKK", "type": "road", "date": iso(-5), "distance_km": 21.1, "target_time": "01:55:00"},
        ]))
        res = analyze(daily, runs, TODAY, races)
        text = json.dumps(res, ensure_ascii=False)
        self.assertIn("Uthai Trail", text)
        self.assertEqual(len(res["trend_7d"]), 7)
        self.assertEqual(res["trend_7d"][-1]["date"], TODAY.isoformat())


if __name__ == "__main__":
    unittest.main()
