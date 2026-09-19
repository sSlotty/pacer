"""Garmin Connect login and data fetching (running only). See CLAUDE.md §5–6."""

from __future__ import annotations

import base64
import binascii
import logging
import time

from analysis import RUN_TYPE_KEYS, classify_run

log = logging.getLogger(__name__)

REQUEST_DELAY_S = 0.4
MAX_REQUESTS = 250


class GarminAuthError(Exception):
    """Stored tokens are missing, invalid or rejected — setup_tokens.py must be re-run."""


class _Budget:
    count = 0


def reset_request_budget() -> None:
    _Budget.count = 0


def _g(obj, *keys):
    """Safely read a nested value from dicts/lists; returns None if any step is missing."""
    for key in keys:
        if obj is None:
            return None
        if isinstance(key, int):
            if not isinstance(obj, (list, tuple)) or not -len(obj) <= key < len(obj):
                return None
            obj = obj[key]
        elif isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
    return obj


def _safe(fn, *args):
    """Call a Garmin endpoint; on any failure log a warning and return None."""
    name = getattr(fn, "__name__", "call")
    if _Budget.count >= MAX_REQUESTS:
        log.warning("Request budget (%d) reached; skipping %s", MAX_REQUESTS, name)
        return None
    _Budget.count += 1
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 — any endpoint failure must not stop the run
        log.warning("Garmin %s%s failed: %s: %s", name, args, type(e).__name__, e)
        return None
    finally:
        time.sleep(REQUEST_DELAY_S)


def decode_token_b64(value: str) -> str:
    """Decode GARMINTOKENS_BASE64 into the JSON token string garminconnect expects."""
    try:
        text = base64.b64decode(value.strip(), validate=False).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as e:
        raise GarminAuthError("GARMINTOKENS_BASE64 is not valid base64") from e
    if not text.lstrip().startswith("{"):
        raise GarminAuthError("GARMINTOKENS_BASE64 does not contain token JSON")
    return text


def login(tokens_b64: str | None, tokens_path: str):
    """Log in with stored tokens only (never a password). Raises GarminAuthError on auth failure."""
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
    )

    tokenstore = decode_token_b64(tokens_b64) if tokens_b64 else tokens_path
    api = Garmin()
    try:
        api.login(tokenstore)
    except GarminConnectAuthenticationError as e:
        # With no password available, any auth failure means the stored tokens are unusable.
        raise GarminAuthError(f"stored tokens missing or rejected ({str(e).splitlines()[0]})") from e
    except FileNotFoundError as e:
        raise GarminAuthError("Token folder not found") from e
    except GarminConnectConnectionError as e:
        # Unusable tokens surface as a connection error ("Username and password are required"
        # is raised as an auth error; a broken token file lands here).
        if "token" in str(e).lower():
            raise GarminAuthError(str(e).splitlines()[0]) from e
        raise
    return api


def _first(obj):
    if isinstance(obj, list):
        return obj[0] if obj else None
    return obj


def fetch_day(api, day: str) -> dict:
    """Recovery metrics for one day (§6.1). Missing values are None."""
    stats = _safe(api.get_stats, day)
    sleep = _g(_safe(api.get_sleep_data, day), "dailySleepDTO")
    hrv = _g(_safe(api.get_hrv_data, day), "hrvSummary")
    readiness = _first(_safe(api.get_training_readiness, day))
    generic = _g(_first(_safe(api.get_max_metrics, day)), "generic")

    return {
        "date": day,
        "resting_hr": _g(stats, "restingHeartRate"),
        "stress_avg": _g(stats, "averageStressLevel"),
        "body_battery_high": _g(stats, "bodyBatteryHighestValue"),
        "body_battery_low": _g(stats, "bodyBatteryLowestValue"),
        "body_battery_wake": _g(stats, "bodyBatteryAtWakeTime"),
        "sleep_seconds": _g(sleep, "sleepTimeSeconds"),
        "deep_sleep_seconds": _g(sleep, "deepSleepSeconds"),
        "rem_sleep_seconds": _g(sleep, "remSleepSeconds"),
        "awake_seconds": _g(sleep, "awakeSleepSeconds"),
        "sleep_score": _g(sleep, "sleepScores", "overall", "value"),
        "hrv_last_night": _g(hrv, "lastNightAvg"),
        "hrv_weekly_avg": _g(hrv, "weeklyAvg"),
        "hrv_status": _g(hrv, "status"),
        "hrv_baseline_low": _g(hrv, "baseline", "balancedLow"),
        "hrv_baseline_high": _g(hrv, "baseline", "balancedUpper"),
        "readiness_score": _g(readiness, "score"),
        "readiness_level": _g(readiness, "level"),
        "vo2max_running": _g(generic, "vo2MaxPreciseValue") or _g(generic, "vo2MaxValue"),
    }


def map_activity(act: dict, trail_elev_threshold: float = 20.0) -> dict | None:
    """Map one Garmin activity to a run row, or None if it is not a usable run."""
    type_key = _g(act, "activityType", "typeKey")
    if type_key not in RUN_TYPE_KEYS:
        if isinstance(type_key, str) and "run" in type_key:
            log.warning("Unknown running typeKey %r skipped — add it to RUN_TYPE_KEYS", type_key)
        return None
    activity_id = _g(act, "activityId")
    distance = _g(act, "distance")
    if not activity_id or not distance:
        return None
    start = _g(act, "startTimeLocal")
    elev_gain = _g(act, "elevationGain")
    return {
        "activity_id": activity_id,
        "date": start[:10] if isinstance(start, str) else None,
        "start_time": start,
        "name": _g(act, "activityName"),
        "type_key": type_key,
        "run_category": classify_run(type_key, distance, elev_gain, trail_elev_threshold),
        "distance_m": distance,
        "duration_s": _g(act, "duration"),
        "moving_s": _g(act, "movingDuration"),
        "elev_gain_m": elev_gain,
        "elev_loss_m": _g(act, "elevationLoss"),
        "avg_hr": _g(act, "averageHR"),
        "max_hr": _g(act, "maxHR"),
        "avg_cadence": _g(act, "averageRunningCadenceInStepsPerMinute"),
        "training_load": _g(act, "activityTrainingLoad"),
        "aerobic_te": _g(act, "aerobicTrainingEffect"),
        "anaerobic_te": _g(act, "anaerobicTrainingEffect"),
    }


def fetch_runs(api, start: str, end: str, trail_elev_threshold: float = 20.0) -> list[dict]:
    """Running activities between start and end (inclusive); everything else is dropped (§6.2)."""
    activities = _safe(api.get_activities_by_date, start, end) or []
    runs = [map_activity(a, trail_elev_threshold) for a in activities if isinstance(a, dict)]
    runs = [r for r in runs if r and r["date"]]
    log.info("Fetched %d activities, kept %d runs", len(activities), len(runs))
    return runs
