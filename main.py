"""Daily orchestrator. Usage: python main.py [--dry-run] [--backfill N] [--date YYYY-MM-DD]"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import analysis
import discord_notify
import garmin_fetch
import summarize
import weather
from config import load_config
from storage import Storage, open_storage

log = logging.getLogger("garmin-daily")

ANALYSIS_WINDOW_DAYS = 42


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Garmin daily running coach")
    p.add_argument("--dry-run", action="store_true", help="print JSON and summary; do not post to Discord")
    p.add_argument("--backfill", type=int, metavar="N", help="fetch the last N days")
    p.add_argument("--date", type=date.fromisoformat, metavar="YYYY-MM-DD", help="treat this date as today")
    p.add_argument("--force", action="store_true", help="send even if already sent today or data is incomplete")
    return p.parse_args(argv)


def fetch_and_store(api, db: Storage, today: date, days: int, trail_elev_threshold: float) -> None:
    garmin_fetch.reset_request_budget()
    start = today - timedelta(days=days - 1)
    log.info("Fetching %d day(s): %s → %s", days, start, today)
    # Runs first: one request, so a long --backfill that exhausts the request budget
    # still stores every run.
    runs = garmin_fetch.fetch_runs(api, start.isoformat(), today.isoformat(), trail_elev_threshold)
    db.upsert_runs(runs)
    for i in range(days):  # newest first, for the same reason
        day = (today - timedelta(days=i)).isoformat()
        db.upsert_daily(garmin_fetch.fetch_day(api, day))


# Garmin publishes a readiness score hours before the watch uploads the night's sleep,
# so readiness alone does not mean the data worth waiting for has arrived.
RECOVERY_KEYS = ("sleep_seconds", "hrv_last_night")


def has_recovery_data(daily: list[dict], today: date) -> bool:
    """True once Garmin has synced last night's sleep or HRV for `today`."""
    for row in daily:
        if str(row.get("date"))[:10] == today.isoformat():
            return any(row.get(key) is not None for key in RECOVERY_KEYS)
    return False


def past_deadline(now: datetime, deadline: str) -> bool:
    try:
        hour, minute = (int(x) for x in deadline.split(":"))
    except ValueError:
        log.warning("SEND_DEADLINE=%r is not HH:MM; treating this run as past the deadline", deadline)
        return True
    return (now.hour, now.minute) >= (hour, minute)


def fetch_weather(cfg, db: Storage, today: date) -> dict | None:
    """Forecast for today's run window at WEATHER_LAT/LON, else where the last outdoor run started."""
    if cfg.weather_lat is not None and cfg.weather_lon is not None:
        lat, lon, source = cfg.weather_lat, cfg.weather_lon, "config"
    elif (loc := db.last_run_location()) is not None:
        (lat, lon), source = loc, "last_run"
    else:
        log.info("No weather location (set WEATHER_LAT/WEATHER_LON); skipping weather")
        return None
    return weather.get_weather(lat, lon, today.isoformat(), cfg.tz_name, cfg.run_time, source)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    cfg = load_config()
    today = args.date or datetime.now(ZoneInfo(cfg.tz_name)).date()
    log.info("Today is %s (%s); %d race(s) configured", today, cfg.tz_name, len(cfg.races))

    if not args.dry_run and not cfg.discord_webhook_url:
        log.error("DISCORD_WEBHOOK_URL is not set (use --dry-run to test without Discord)")
        return 1

    db = open_storage(cfg)
    log.info("Storage: %s", "Supabase Postgres" if cfg.database_url else f"SQLite {cfg.db_path}")
    try:
        try:
            api = garmin_fetch.login(cfg.garmin_tokens_b64, cfg.garmin_tokens_path)
        except garmin_fetch.GarminAuthError as e:
            log.error("Garmin auth failed: %s — re-run setup_tokens.py", e)
            if not args.dry_run:
                try:
                    discord_notify.send_auth_alert(cfg.discord_webhook_url, str(e))
                except Exception as send_err:  # noqa: BLE001
                    log.error("Could not send auth alert to Discord: %s", send_err)
            return 1
        except Exception as e:  # noqa: BLE001 — network trouble: still report from stored data
            log.error("Garmin login failed (%s: %s); using data already in DB", type(e).__name__, e)
            api = None

        gated = not (args.dry_run or args.force)
        if gated and db.was_sent(today.isoformat()):
            # Keep collecting: today's sleep and runs usually land after the summary
            # goes out, and without this they would not reach the DB until tomorrow.
            log.info("Summary for %s was already sent; refreshing today's data only", today)
            if api is not None:
                garmin_fetch.reset_request_budget()
                db.upsert_daily(garmin_fetch.fetch_day(api, today.isoformat()))
                db.upsert_runs(
                    garmin_fetch.fetch_runs(api, today.isoformat(), today.isoformat(), cfg.trail_elev_threshold)
                )
            return 0

        # A gated run may only be polling for Garmin's overnight sync, so fetch today
        # alone first and pull the full window only once we know we will post.
        if api is not None and gated and not args.backfill and not db.is_empty():
            garmin_fetch.reset_request_budget()
            db.upsert_daily(garmin_fetch.fetch_day(api, today.isoformat()))
            if args.date is None and not has_recovery_data(db.daily_since(today.isoformat()), today):
                now = datetime.now(ZoneInfo(cfg.tz_name))
                if not past_deadline(now, cfg.send_deadline):
                    log.info(
                        "Garmin has not synced last night's recovery data yet (now %s, deadline %s); "
                        "waiting for a later run",
                        now.strftime("%H:%M"),
                        cfg.send_deadline,
                    )
                    return 0
                log.info("Recovery data still missing but past %s; sending anyway", cfg.send_deadline)

        if api is not None:
            if args.backfill:
                days = args.backfill
            elif db.is_empty():
                days = cfg.backfill_days
            else:
                days = cfg.refresh_days
            fetch_and_store(api, db, today, max(days, 1), cfg.trail_elev_threshold)

        since = (today - timedelta(days=ANALYSIS_WINDOW_DAYS - 1)).isoformat()
        daily = db.daily_since(since)

        # Login failed, so the poll above never ran: decide from what the DB already holds.
        if gated and api is None and args.date is None and not has_recovery_data(daily, today):
            if not past_deadline(datetime.now(ZoneInfo(cfg.tz_name)), cfg.send_deadline):
                log.info("No recovery data for %s and Garmin is unreachable; waiting for a later run", today)
                return 0

        # Only runs that will post get this far, so polling runs never call the weather API.
        forecast = fetch_weather(cfg, db, today)
        result = analysis.analyze(
            daily, db.runs_since(since), today, cfg.races, cfg.trail_elev_threshold, weather=forecast
        )

        try:
            sections, source = summarize.summarize(result, cfg)
            log.info("Summary written by %s", source)
        except Exception as e:  # noqa: BLE001 — any LLM failure falls back to rule-based text
            log.error("LLM summary failed (%s); using fallback text", e)
            sections, source = summarize.fallback_sections(result), None

        if args.dry_run:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print("\n" + "=" * 60 + "\n")
            print(json.dumps(sections, ensure_ascii=False, indent=2))
            return 0

        try:
            discord_notify.send_summary(cfg.discord_webhook_url, result, sections, source)
        except Exception as e:  # noqa: BLE001
            log.error("Discord send failed: %s", e)
            return 1
        db.mark_sent(today.isoformat(), source, result["status"])
        log.info("Summary sent to Discord (status=%s)", result["status"])
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
