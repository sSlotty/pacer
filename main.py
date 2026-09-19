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
from config import load_config
from storage import Storage

log = logging.getLogger("garmin-daily")

ANALYSIS_WINDOW_DAYS = 42


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Garmin daily running coach")
    p.add_argument("--dry-run", action="store_true", help="print JSON and summary; do not post to Discord")
    p.add_argument("--backfill", type=int, metavar="N", help="fetch the last N days")
    p.add_argument("--date", type=date.fromisoformat, metavar="YYYY-MM-DD", help="treat this date as today")
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


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    cfg = load_config()
    today = args.date or datetime.now(ZoneInfo(cfg.tz_name)).date()
    log.info("Today is %s (%s); %d race(s) configured", today, cfg.tz_name, len(cfg.races))

    if not args.dry_run and not cfg.discord_webhook_url:
        log.error("DISCORD_WEBHOOK_URL is not set (use --dry-run to test without Discord)")
        return 1

    db = Storage(cfg.db_path)
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

        if api is not None:
            if args.backfill:
                days = args.backfill
            elif db.is_empty():
                days = cfg.backfill_days
            else:
                days = cfg.refresh_days
            fetch_and_store(api, db, today, max(days, 1), cfg.trail_elev_threshold)

        since = (today - timedelta(days=ANALYSIS_WINDOW_DAYS - 1)).isoformat()
        result = analysis.analyze(
            db.daily_since(since), db.runs_since(since), today, cfg.races, cfg.trail_elev_threshold
        )
    finally:
        db.close()

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
    log.info("Summary sent to Discord (status=%s)", result["status"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
