"""Running analysis (stdlib only). See CLAUDE.md §8.

`analyze()` is a pure function: its output is JSON-serializable and every value that
cannot be computed is None.
"""

from __future__ import annotations

from datetime import date, timedelta

RUN_TYPE_KEYS = (
    "running",
    "street_running",
    "track_running",
    "treadmill_running",
    "indoor_running",
    "virtual_run",
    "trail_running",
    "ultra_run",
)
INDOOR_TYPE_KEYS = ("treadmill_running", "indoor_running", "virtual_run")
HARD_ANAEROBIC_TE = 2.0
PHASE_NAMES = ("Base", "Build", "Peak", "Taper", "Race day", "Recovery")


# ---------------------------------------------------------------- helpers


def classify_run(type_key, distance_m, elev_gain_m, threshold: float = 20.0) -> str:
    """Return 'trail' or 'road' for a run (§8.1)."""
    if type_key == "trail_running":
        return "trail"
    if type_key == "ultra_run":
        km = (distance_m or 0) / 1000
        if km > 0 and (elev_gain_m or 0) / km >= threshold:
            return "trail"
    return "road"


def _d(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _r(value, ndigits: int = 1):
    return None if value is None else round(value, ndigits)


def _mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _km(run) -> float:
    return (run.get("distance_m") or 0) / 1000


def _run_time_s(run) -> float:
    return run.get("moving_s") or run.get("duration_s") or 0


def _pace_str(sec_per_km):
    if sec_per_km is None:
        return None
    total = int(round(sec_per_km))
    return f"{total // 60}:{total % 60:02d}"


def _pct_change(cur, prev):
    if cur is None or not prev:
        return None
    return (cur - prev) / prev * 100


def _pct(part, whole):
    if part is None or not whole:
        return None
    return part / whole * 100


def _hms_to_s(text):
    h, m, s = (int(x) for x in text.split(":"))
    return h * 3600 + m * 60 + s


def _in(run, start: date, end: date) -> bool:
    return start <= _d(run["date"]) <= end


def _run_load(run) -> float:
    if run.get("training_load") is not None:
        return run["training_load"]
    return (run.get("duration_s") or 0) / 60


def _pace_of(runs):
    """Average pace (s/km) over outdoor runs, weighted by distance."""
    outdoor = [r for r in runs if r.get("type_key") not in INDOOR_TYPE_KEYS and _km(r) > 0]
    km = sum(_km(r) for r in outdoor)
    secs = sum(_run_time_s(r) for r in outdoor)
    return secs / km if km > 0 and secs > 0 else None


def _run_brief(run, with_pace: bool = False, with_elev: bool = False):
    if run is None:
        return None
    out = {"date": str(run["date"])[:10], "name": run.get("name"), "km": _r(_km(run), 2)}
    if with_pace:
        pace = _run_time_s(run) / _km(run) if _km(run) > 0 and _run_time_s(run) else None
        out["pace"] = _pace_str(pace)
    if with_elev:
        out["elev_gain_m"] = _r(run.get("elev_gain_m"), 0)
    return out


def _longest(runs):
    return max(runs, key=_km, default=None)


# ---------------------------------------------------------------- sections


def _run_stats(runs, today: date):
    w_start, p_start, p_end = today - timedelta(days=6), today - timedelta(days=13), today - timedelta(days=7)
    m_start = today - timedelta(days=27)
    week = [r for r in runs if _in(r, w_start, today)]
    prev = [r for r in runs if _in(r, p_start, p_end)]
    month = [r for r in runs if _in(r, m_start, today)]

    week_km = sum(_km(r) for r in week)
    prev_km = sum(_km(r) for r in prev)
    weekly = []
    for w in range(3, -1, -1):
        end = today - timedelta(days=7 * w)
        start = end - timedelta(days=6)
        km = sum(_km(r) for r in runs if _in(r, start, end))
        weekly.append({"start": start.isoformat(), "end": end.isoformat(), "km": _r(km, 1)})

    totals = {
        "km": _r(week_km, 2),
        "hours": _r(sum((r.get("duration_s") or 0) for r in week) / 3600, 2),
        "runs": len(week),
        "prev_week_km": _r(prev_km, 2),
        "km_change_pct": _r(_pct_change(week_km, prev_km)),
        "weekly_km_4w": weekly,
    }

    road_w = [r for r in week if r["run_category"] == "road"]
    road_m = [r for r in month if r["run_category"] == "road"]
    road = {
        "km": _r(sum(_km(r) for r in road_w), 2),
        "runs": len(road_w),
        "avg_pace": _pace_str(_pace_of(road_w)),
        "avg_pace_s_per_km": _r(_pace_of(road_w), 0),
        "long_run_7d": _run_brief(_longest(road_w), with_pace=True),
        "long_run_28d": _run_brief(_longest(road_m), with_pace=True),
        "avg_cadence": _r(_mean(r.get("avg_cadence") for r in road_w), 0),
    }

    trail_w = [r for r in week if r["run_category"] == "trail"]
    trail_p = [r for r in prev if r["run_category"] == "trail"]
    trail_m = [r for r in month if r["run_category"] == "trail"]
    t_km = sum(_km(r) for r in trail_w)
    t_gain = sum((r.get("elev_gain_m") or 0) for r in trail_w)
    trail = {
        "km": _r(t_km, 2),
        "elev_gain_m": _r(t_gain, 0),
        "elev_loss_m": _r(sum((r.get("elev_loss_m") or 0) for r in trail_w), 0),
        "runs": len(trail_w),
        "effort_km": _r(t_km + t_gain / 100, 2),
        "vertical_rate_m_per_km": _r(t_gain / t_km, 0) if t_km > 0 else None,
        "prev_week_elev_gain_m": _r(sum((r.get("elev_gain_m") or 0) for r in trail_p), 0),
        "long_run_7d": _run_brief(_longest(trail_w), with_elev=True),
        "long_run_28d": _run_brief(_longest(trail_m), with_elev=True),
    }
    return totals, road, trail, week


def _load(daily, runs, today: date, week):
    loads = {}
    for r in runs:
        loads[_d(r["date"])] = loads.get(_d(r["date"]), 0) + _run_load(r)
    last28 = [loads.get(today - timedelta(days=i), 0) for i in range(28)]
    acute = sum(last28[:7]) / 7
    chronic = sum(last28) / 28

    known = [_d(x["date"]) for x in daily] + [_d(r["date"]) for r in runs]
    earliest = min(known, default=None)
    days_of_data = (today - earliest).days + 1 if earliest and earliest <= today else 0

    hard = [r for r in week if (r.get("anaerobic_te") or 0) >= HARD_ANAEROBIC_TE]
    run_days = {_d(r["date"]) for r in week}
    return {
        "acute": _r(acute),
        "chronic": _r(chronic),
        "acwr": _r(acute / chronic, 2) if chronic > 0 else None,
        "days_of_data": days_of_data,
        "load_data_insufficient": days_of_data < 21,
        "hard_runs_7d": len(hard),
        "hard_pct_7d": _r(_pct(len(hard), len(week))) if week else None,
        "rest_days_7d": 7 - len(run_days),
    }


def _recovery(by_date, today: date):
    t = by_date.get(today, {})
    y = by_date.get(today - timedelta(days=1), {})

    def window(start_offset, days, key):
        return [by_date.get(today - timedelta(days=start_offset + i), {}).get(key) for i in range(days)]

    hrv = t.get("hrv_last_night")
    base_low, base_high = t.get("hrv_baseline_low"), t.get("hrv_baseline_high")
    vs_baseline = None
    if hrv is not None and base_low is not None and base_high is not None:
        vs_baseline = "below" if hrv < base_low else "above" if hrv > base_high else "within"

    rhr = y.get("resting_hr")
    rhr_avg14 = _mean(window(2, 14, "resting_hr"))

    sleep_s = t.get("sleep_seconds")
    sleep_avg = _mean(window(0, 7, "sleep_seconds"))

    vo2 = None
    for d in sorted(by_date, reverse=True):
        if d <= today and by_date[d].get("vo2max_running") is not None:
            vo2 = {"value": by_date[d]["vo2max_running"], "date": d.isoformat()}
            break

    return {
        "hrv": {
            "last_night": hrv,
            "baseline_low": base_low,
            "baseline_high": base_high,
            "status": t.get("hrv_status"),
            "garmin_weekly_avg": t.get("hrv_weekly_avg"),
            "avg_7d": _r(_mean(window(0, 7, "hrv_last_night"))),
            "vs_baseline": vs_baseline,
        },
        "rhr": {
            "yesterday": rhr,
            "avg_14d": _r(rhr_avg14),
            "diff": _r(rhr - rhr_avg14) if rhr is not None and rhr_avg14 is not None else None,
        },
        "sleep": {
            "last_night_h": _r(sleep_s / 3600, 2) if sleep_s is not None else None,
            "deep_h": _r(t["deep_sleep_seconds"] / 3600, 2) if t.get("deep_sleep_seconds") is not None else None,
            "rem_h": _r(t["rem_sleep_seconds"] / 3600, 2) if t.get("rem_sleep_seconds") is not None else None,
            "awake_h": _r(t["awake_seconds"] / 3600, 2) if t.get("awake_seconds") is not None else None,
            "score": t.get("sleep_score"),
            "avg_7d_h": _r(sleep_avg / 3600, 2) if sleep_avg is not None else None,
        },
        "body_battery_wake": t.get("body_battery_wake"),
        "stress_avg_yesterday": y.get("stress_avg"),
        "readiness": {"score": t.get("readiness_score"), "level": t.get("readiness_level")},
        "vo2max_running": vo2,
    }


def _phase(days: int) -> str:
    if days > 70:
        return "Base"
    if days >= 29:
        return "Build"
    if days >= 15:
        return "Peak"
    if days >= 1:
        return "Taper"
    if days == 0:
        return "Race day"
    return "Recovery"


def _race_readiness(race, runs, today: date, trail_week_gain):
    m_start = today - timedelta(days=27)
    month = [r for r in runs if _in(r, m_start, today)]
    longest = _longest(month)
    longest_km = _km(longest) if longest else 0
    dist = race.get("distance_km")
    out = {
        "long_run_28d_km": _r(longest_km, 2),
        "long_run_pct_of_distance": _r(_pct(longest_km, dist)),
    }
    if race["type"] in ("trail", "mixed"):
        elev = race.get("elevation_m")
        max_single = max((r.get("elev_gain_m") or 0 for r in month), default=0)
        out.update(
            {
                "trail_elev_gain_7d_m": _r(trail_week_gain, 0),
                "trail_elev_gain_7d_pct": _r(_pct(trail_week_gain, elev)),
                "max_single_run_elev_gain_28d_m": _r(max_single, 0),
                "max_single_run_elev_gain_pct": _r(_pct(max_single, elev)),
            }
        )
    if race["type"] in ("road", "mixed"):
        target_pace = None
        if race.get("target_time") and dist:
            target_pace = _hms_to_s(race["target_time"]) / dist
        road_long = _longest(
            [r for r in month if r["run_category"] == "road" and r.get("type_key") not in INDOOR_TYPE_KEYS]
        )
        long_pace = _pace_of([road_long]) if road_long else None
        out.update(
            {
                "target_pace": _pace_str(target_pace),
                "road_long_run_28d": _run_brief(road_long, with_pace=True),
                "long_run_vs_target_s_per_km": (
                    _r(long_pace - target_pace, 0) if long_pace and target_pace else None
                ),
            }
        )
    return out


def _races(races, runs, today: date, trail_week_gain):
    active = []
    for race in races or []:
        days = (_d(race["date"]) - today).days
        if days >= -14:
            active.append({**race, "days_to_race": days})
    if not active:
        return [], None, None, [], []

    def nearness(r):
        return (abs(r["days_to_race"]), r["days_to_race"] < 0)

    a_races = [r for r in active if r.get("priority", "B") == "A"]
    phase_race = min(a_races or active, key=nearness)
    shown = sorted(active, key=nearness)[:3]
    if phase_race not in shown:
        shown[-1] = phase_race
    shown.sort(key=lambda r: r["date"])

    out = []
    for r in shown:
        days = r["days_to_race"]
        is_bc = r.get("priority", "B") in ("B", "C")
        out.append(
            {
                "name": r["name"],
                "type": r["type"],
                "priority": r.get("priority", "B"),
                "date": r["date"],
                "days_to_race": days,
                "distance_km": r.get("distance_km"),
                "elevation_m": r.get("elevation_m"),
                "target_time": r.get("target_time"),
                "phase": _phase(days),
                "sets_training_phase": r is phase_race,
                "mini_taper": is_bc and 0 <= days <= 7,
                "post_race_recovery": is_bc and -7 <= days <= -1,
                "readiness": _race_readiness(r, runs, today, trail_week_gain),
            }
        )

    flags = []
    a_sorted = sorted(a_races, key=lambda r: r["date"])
    for first, second in zip(a_sorted, a_sorted[1:]):
        gap = (_d(second["date"]) - _d(first["date"])).days
        if gap < 42:
            flags.append(
                {
                    "level": "info",
                    "message": f"รายการ A '{first['name']}' และ '{second['name']}' ห่างกันแค่ {gap} วัน เวลาฟื้นตัวระหว่างรายการน้อย",
                }
            )
    return out, _phase(phase_race["days_to_race"]), phase_race["name"], flags, active


def _flags(totals, trail, load, rec, active_races):
    flags = []
    acwr = load["acwr"]
    in_taper_or_recovery = any(-14 <= r["days_to_race"] <= 14 for r in active_races)
    if acwr is not None:
        if acwr > 1.5:
            flags.append({"level": "red", "message": f"ACWR {acwr} สูงกว่า 1.5 เสี่ยงบาดเจ็บ ควรลดโหลด"})
        elif acwr > 1.3:
            flags.append({"level": "yellow", "message": f"ACWR {acwr} อยู่ในช่วง 1.3–1.5 โหลดเพิ่มเร็ว ระวัง"})
        elif acwr < 0.8 and not in_taper_or_recovery:
            flags.append({"level": "yellow", "message": f"ACWR {acwr} ต่ำกว่า 0.8 โหลดลดลงมาก"})

    hrv = rec["hrv"]
    hrv_low = (
        hrv["last_night"] is not None
        and hrv["baseline_low"] is not None
        and hrv["last_night"] < hrv["baseline_low"]
    )
    if hrv_low:
        flags.append(
            {"level": "yellow", "message": f"HRV เมื่อคืน {hrv['last_night']:g} ต่ำกว่า baseline ({hrv['baseline_low']:g})"}
        )

    rhr = rec["rhr"]
    if rhr["yesterday"] is not None and rhr["avg_14d"] is not None and rhr["yesterday"] >= rhr["avg_14d"] + 5:
        if hrv_low:
            flags.append(
                {
                    "level": "red",
                    "message": f"RHR เมื่อวาน {rhr['yesterday']:g} สูงกว่าค่าเฉลี่ย 14 วัน ({rhr['avg_14d']:g}) ร่วมกับ HRV ต่ำ ควรพัก ถ้ามีอาการป่วยหรือเจ็บให้ปรึกษาแพทย์",
                }
            )
        else:
            flags.append(
                {"level": "yellow", "message": f"RHR เมื่อวาน {rhr['yesterday']:g} สูงกว่าค่าเฉลี่ย 14 วัน ({rhr['avg_14d']:g}) ≥ 5 bpm"}
            )

    sleep = rec["sleep"]
    if sleep["last_night_h"] is not None and sleep["last_night_h"] < 6:
        flags.append({"level": "yellow", "message": f"นอนเมื่อคืน {sleep['last_night_h']:.1f} ชม. น้อยกว่า 6 ชม."})
    if sleep["avg_7d_h"] is not None and sleep["avg_7d_h"] < 6.5:
        flags.append({"level": "yellow", "message": f"นอนเฉลี่ย 7 วัน {sleep['avg_7d_h']:.1f} ชม. น้อยกว่า 6.5 ชม."})

    if totals["km_change_pct"] is not None and totals["km_change_pct"] > 30:
        flags.append(
            {"level": "yellow", "message": f"ระยะวิ่งสัปดาห์นี้เพิ่มขึ้น {totals['km_change_pct']}% จากสัปดาห์ก่อน (เกิน 30%)"}
        )
    prev_gain = trail["prev_week_elev_gain_m"]
    if prev_gain and prev_gain > 0:
        change = _pct_change(trail["elev_gain_m"], prev_gain)
        if change is not None and change > 30:
            flags.append(
                {"level": "yellow", "message": f"D+ trail สัปดาห์นี้เพิ่มขึ้น {_r(change)}% (เกิน 30%) ระวังเข่าและน่อง"}
            )

    if load["rest_days_7d"] == 0:
        flags.append({"level": "yellow", "message": "ไม่มีวันพักเลยใน 7 วันที่ผ่านมา"})
    if load["hard_pct_7d"] is not None and load["hard_pct_7d"] > 50:
        flags.append(
            {"level": "yellow", "message": f"{load['hard_pct_7d']}% ของการวิ่ง 7 วันเป็นการซ้อมหนัก ซ้อมหนักถี่เกินไป"}
        )
    level = rec["readiness"]["level"]
    if isinstance(level, str) and level.upper() in ("LOW", "POOR"):
        flags.append({"level": "yellow", "message": f"Training readiness อยู่ระดับ {level}"})
    return flags


def _weather_flags(weather):
    """Weather changes how to train, never whether the body needs rest, so no red here (§8.5)."""
    if not weather:
        return []
    flags = []
    window = weather.get("window") or "ช่วงซ้อม"
    heat_level = weather.get("heat_level")
    if heat_level in ("high", "severe"):
        temp, dew = weather.get("temp_max_c"), weather.get("dew_point_c")
        if heat_level == "severe":
            advice = "เลี่ยงซ้อมหนัก วิ่งเบา ๆ หรือย้ายเข้าในร่ม"
        else:
            advice = (
                f"pace จะช้าลงราว {weather['pace_slowdown_pct_min']:g}–{weather['pace_slowdown_pct_max']:g}% "
                "ลดความหนักและดื่มน้ำให้พอ"
            )
        flags.append(
            {"level": "yellow", "message": f"ร้อนชื้นมากช่วง {window} ({temp:g}°C จุดน้ำค้าง {dew:g}°C) {advice}"}
        )
    if weather.get("thunderstorm"):
        flags.append(
            {"level": "yellow", "message": f"ช่วง {window} มีพายุฝนฟ้าคะนอง ระวังฟ้าผ่า เลี่ยงที่โล่งและสันเขา"}
        )
    pm25 = weather.get("pm25")
    if pm25 is not None and pm25 > 75:
        flags.append({"level": "yellow", "message": f"PM2.5 {pm25:g} µg/m³ มีผลต่อสุขภาพ ควรวิ่งในร่มหรือบนลู่"})
    elif pm25 is not None and pm25 > 37.5:
        flags.append({"level": "info", "message": f"PM2.5 {pm25:g} µg/m³ เริ่มมีผลต่อสุขภาพ ลดความหนักลง"})
    if (weather.get("rain_mm") or 0) >= 10:
        flags.append({"level": "info", "message": f"ฝนหนักช่วง {window} ทางลื่น โดยเฉพาะเทรล"})
    if (weather.get("gust_kmh") or 0) >= 50:
        flags.append({"level": "info", "message": f"ลมกระโชกแรง {weather['gust_kmh']:g} km/h"})
    return flags


def _trend(daily_by_date, runs, today: date):
    out = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        day_runs = [r for r in runs if _d(r["date"]) == d]
        row = daily_by_date.get(d, {})
        sleep_s = row.get("sleep_seconds")
        out.append(
            {
                "date": d.isoformat(),
                "load": _r(sum(_run_load(r) for r in day_runs)),
                "road_km": _r(sum(_km(r) for r in day_runs if r["run_category"] == "road"), 2),
                "trail_km": _r(sum(_km(r) for r in day_runs if r["run_category"] == "trail"), 2),
                "trail_elev_m": _r(
                    sum((r.get("elev_gain_m") or 0) for r in day_runs if r["run_category"] == "trail"), 0
                ),
                "hrv": row.get("hrv_last_night"),
                "sleep_h": _r(sleep_s / 3600, 2) if sleep_s is not None else None,
                "rhr": row.get("resting_hr"),
            }
        )
    return out


# ---------------------------------------------------------------- entry point


def analyze(daily, runs, today, races=None, trail_elev_threshold: float = 20.0, weather=None) -> dict:
    """Compute all metrics and flags. `races` is the validated list from config.parse_races();
    `weather` is the dict from weather.assess() (or None)."""
    today = _d(today)
    daily = [x for x in (daily or []) if x.get("date") and _d(x["date"]) <= today]
    by_date = {_d(x["date"]): x for x in daily}

    clean_runs = []
    for r in runs or []:
        if not r.get("date") or _d(r["date"]) > today or _km(r) <= 0:
            continue
        cat = classify_run(r.get("type_key"), r.get("distance_m"), r.get("elev_gain_m"), trail_elev_threshold)
        clean_runs.append({**r, "run_category": cat})

    totals, road, trail, week = _run_stats(clean_runs, today)
    load = _load(daily, clean_runs, today, week)
    recovery = _recovery(by_date, today)
    race_list, phase, phase_race, race_flags, active = _races(
        races, clean_runs, today, trail["elev_gain_m"] or 0
    )

    flags = _flags(totals, trail, load, recovery, active) + _weather_flags(weather) + race_flags
    levels = {f["level"] for f in flags}
    status = "red" if "red" in levels else "yellow" if "yellow" in levels else "green"

    return {
        "date": today.isoformat(),
        "status": status,
        "flags": flags,
        "totals_7d": totals,
        "road_7d": road,
        "trail_7d": trail,
        "load": load,
        "recovery": recovery,
        "weather": weather or None,
        "training_phase": phase,
        "phase_race": phase_race,
        "races": race_list,
        "trend_7d": _trend(by_date, clean_runs, today),
    }
