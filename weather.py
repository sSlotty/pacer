"""Weather for today's best morning and evening run windows from Open-Meteo (free, no API key). See CLAUDE.md §6.3.

`fetch_hourly()` does the network calls; `assess()` is pure and stdlib-only.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
FORECAST_HOURLY = (
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "apparent_temperature",
    "precipitation_probability",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
    "uv_index",
)
TIMEOUT_S = 15
WINDOW_HOURS = 3  # the start hour and the two after it: covers a run of about 2 hours
MORNING_STARTS = range(4, 9)  # 04:00–06:00 … 08:00–10:00
EVENING_STARTS = range(15, 20)  # 15:00–17:00 … 19:00–21:00
WET_CHANCE_PCT = 60
WET_MM = 2.0
SOURCE = "Open-Meteo"

# temp °F + dew point °F → (pace slowdown % min, max, heat_level). None means hard running is not advised.
HEAT_TABLE = (
    (100, 0, 0, "none"),
    (120, 0, 1, "none"),
    (140, 1, 3, "mild"),
    (160, 3, 6, "moderate"),
    (180, 6, 10, "high"),
)

# Thai Pollution Control Department PM2.5 bands (µg/m³)
PM25_LEVELS = (
    (15, "ดีมาก"),
    (25, "ดี"),
    (37.5, "ปานกลาง"),
    (75, "เริ่มมีผลต่อสุขภาพ"),
)
PM25_UNHEALTHY = "มีผลต่อสุขภาพ"

WMO_TEXT = {
    0: "ท้องฟ้าแจ่มใส",
    1: "แจ่มใสเป็นส่วนใหญ่",
    2: "มีเมฆบางส่วน",
    3: "เมฆมาก",
    45: "หมอก",
    48: "หมอกน้ำค้างแข็ง",
    51: "ฝนปรอยเล็กน้อย",
    53: "ฝนปรอย",
    55: "ฝนปรอยหนาแน่น",
    56: "ฝนปรอยเยือกแข็ง",
    57: "ฝนปรอยเยือกแข็ง",
    61: "ฝนเล็กน้อย",
    63: "ฝนปานกลาง",
    65: "ฝนหนัก",
    66: "ฝนเยือกแข็ง",
    67: "ฝนเยือกแข็งหนัก",
    71: "หิมะเล็กน้อย",
    73: "หิมะ",
    75: "หิมะหนัก",
    77: "เม็ดหิมะ",
    80: "ฝนเป็นช่วง ๆ",
    81: "ฝนเป็นช่วง ๆ ปานกลาง",
    82: "ฝนเป็นช่วง ๆ หนัก",
    85: "หิมะเป็นช่วง ๆ",
    86: "หิมะเป็นช่วง ๆ หนัก",
    95: "พายุฝนฟ้าคะนอง",
    96: "พายุฝนฟ้าคะนอง มีลูกเห็บ",
    99: "พายุฝนฟ้าคะนอง มีลูกเห็บหนัก",
}


# ---------------------------------------------------------------- pure assessment


def _r(value, ndigits: int = 1):
    return None if value is None else round(value, ndigits)


def _f(celsius: float) -> float:
    return celsius * 9 / 5 + 32


def _window(hourly: dict | None, day: str, hours: list[int]) -> dict[str, list]:
    """Values of each hourly variable at the given hours of `day` (missing hours are skipped)."""
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        return {}
    wanted = {f"{day}T{h:02d}:00" for h in hours}
    idx = [i for i, t in enumerate(hourly["time"]) if t in wanted]
    out = {}
    for key, values in hourly.items():
        if key == "time" or not isinstance(values, list):
            continue
        out[key] = [values[i] for i in idx if i < len(values) and isinstance(values[i], (int, float))]
    return out


def _max(values):
    return max(values) if values else None


def _first(values):
    return values[0] if values else None


def heat(temp_c, dew_c):
    """(heat_score_f, pace slowdown % min, % max, heat_level) from air temperature and dew point."""
    if temp_c is None or dew_c is None:
        return None, None, None, None
    score = round(_f(temp_c) + _f(dew_c))
    for limit, lo, hi, level in HEAT_TABLE:
        if score <= limit:
            return score, lo, hi, level
    return score, None, None, "severe"


def pm25_level(pm25):
    if pm25 is None:
        return None
    for limit, label in PM25_LEVELS:
        if pm25 <= limit:
            return label
    return PM25_UNHEALTHY


def _slot(fh: dict | None, ah: dict | None, day: str, start: int) -> dict | None:
    """Summary of one run window starting at `start`. None when it has no data."""
    hours = list(range(start, start + WINDOW_HOURS))
    fw = _window(fh, day, hours)
    aw = _window(ah, day, hours)
    if not any(fw.values()) and not any(aw.values()):
        return None

    temp_max = _max(fw.get("temperature_2m", []))
    dew_max = _max(fw.get("dew_point_2m", []))
    score, slow_lo, slow_hi, heat_level = heat(temp_max, dew_max)
    code = _max([int(c) for c in fw.get("weather_code", [])])
    rain = fw.get("precipitation", [])
    pm25 = _max(aw.get("pm2_5", []))
    return {
        "window": f"{hours[0]:02d}:00–{hours[-1]:02d}:00",
        "temp_c": _r(_first(fw.get("temperature_2m", []))),
        "temp_max_c": _r(temp_max),
        "feels_like_max_c": _r(_max(fw.get("apparent_temperature", []))),
        "humidity_pct": _r(_first(fw.get("relative_humidity_2m", [])), 0),
        "dew_point_c": _r(dew_max),
        "rain_chance_pct": _r(_max(fw.get("precipitation_probability", [])), 0),
        "rain_mm": _r(sum(rain)) if rain else None,
        "weather_code": code,
        "condition": WMO_TEXT.get(code) if code is not None else None,
        "thunderstorm": code is not None and code >= 95,
        "wind_kmh": _r(_max(fw.get("wind_speed_10m", [])), 0),
        "gust_kmh": _r(_max(fw.get("wind_gusts_10m", [])), 0),
        "uv_max": _r(_max(fw.get("uv_index", []))),
        "pm25": _r(pm25, 0),
        "pm25_level": pm25_level(pm25),
        "heat_score_f": score,
        "heat_level": heat_level,
        "pace_slowdown_pct_min": slow_lo,
        "pace_slowdown_pct_max": slow_hi,
    }


def _rank(slot: dict) -> tuple:
    """Lower is better: storms first, then rain, then heat, then dust."""
    wet = (slot["rain_chance_pct"] or 0) >= WET_CHANCE_PCT or (slot["rain_mm"] or 0) >= WET_MM
    return (bool(slot["thunderstorm"]), wet, slot["heat_score_f"] or 0, slot["pm25"] or 0)


def _best(fh, ah, day: str, starts) -> dict | None:
    slots = [s for s in (_slot(fh, ah, day, h) for h in starts) if s]
    return min(slots, key=_rank) if slots else None  # min() keeps the earliest on ties


def assess(forecast: dict | None, air: dict | None, day: str, location_source: str | None = None) -> dict | None:
    """Best morning and evening run windows of the day. None when there is nothing usable."""
    fh, ah = (forecast or {}).get("hourly"), (air or {}).get("hourly")
    morning = _best(fh, ah, day, MORNING_STARTS)
    evening = _best(fh, ah, day, EVENING_STARTS)
    if not morning and not evening:
        return None
    if morning and evening:
        better = "morning" if _rank(morning) <= _rank(evening) else "evening"
    else:
        better = "morning" if morning else "evening"
    return {
        "source": SOURCE,
        "location_source": location_source,
        "better": better,
        "morning": morning,
        "evening": evening,
    }


# ---------------------------------------------------------------- network


def _get(url: str, params: dict) -> dict | None:
    import requests

    name = "air quality" if url == AIR_QUALITY_URL else "forecast"
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001 — weather is optional; never stop the run
        log.warning("Open-Meteo %s failed: %s: %s", name, type(e).__name__, e)
        return None
    return data if isinstance(data, dict) else None


def fetch_hourly(lat: float, lon: float, day: str, tz_name: str) -> tuple[dict | None, dict | None]:
    """(forecast, air quality) Open-Meteo responses for one day; either is None on failure."""
    base = {"latitude": lat, "longitude": lon, "timezone": tz_name, "start_date": day, "end_date": day}
    forecast = _get(FORECAST_URL, {**base, "hourly": ",".join(FORECAST_HOURLY)})
    air = _get(AIR_QUALITY_URL, {**base, "hourly": "pm2_5"})
    return forecast, air


def get_weather(lat: float, lon: float, day: str, tz_name: str, location_source: str | None = None) -> dict | None:
    forecast, air = fetch_hourly(lat, lon, day, tz_name)
    result = assess(forecast, air, day, location_source)
    if result:
        for key in ("morning", "evening"):
            slot = result[key]
            if slot:
                log.info(
                    "Weather %s %s %s: %s°C dew %s°C, rain %s%%, PM2.5 %s, heat %s",
                    day, key, slot["window"], slot["temp_max_c"], slot["dew_point_c"],
                    slot["rain_chance_pct"], slot["pm25"], slot["heat_level"],
                )
        log.info("Weather %s: better window is %s", day, result["better"])
    return result
