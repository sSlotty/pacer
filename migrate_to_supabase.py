"""Copy the local SQLite history into Supabase. Safe to re-run: rows are upserted.

Usage: DATABASE_URL=postgresql://... python migrate_to_supabase.py [--sqlite data/garmin.db]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from config import load_config
from storage import DAILY_COLUMNS, NOTIFICATION_COLUMNS, PostgresStorage, Storage

log = logging.getLogger("migrate")


def migrate(src: Storage, dst: Storage) -> dict[str, int]:
    daily = src.daily_since("0000-00-00")
    runs = src.runs_since("0000-00-00")
    notes = [dict(r) for r in src._exec("SELECT * FROM notifications ORDER BY date")]
    for row in daily:
        dst._upsert("daily_metrics", DAILY_COLUMNS, "date", row)
    dst.conn.commit()
    dst.upsert_runs(runs)
    for row in notes:
        dst._upsert("notifications", NOTIFICATION_COLUMNS, "date", row)
    dst.conn.commit()
    return {"daily_metrics": len(daily), "runs": len(runs), "notifications": len(notes)}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sqlite", default=None, help="SQLite file to copy (default: DB_PATH)")
    args = p.parse_args(argv)
    cfg = load_config()
    if not cfg.database_url:
        log.error("DATABASE_URL is not set (put the Supabase Session pooler connection string in .env)")
        return 1
    path = args.sqlite or cfg.db_path
    if not os.path.exists(path):
        log.error("SQLite file %s not found", path)
        return 1
    src = Storage(path)
    dst = PostgresStorage(cfg.database_url)
    try:
        counts = migrate(src, dst)
    finally:
        src.close()
        dst.close()
    log.info("Copied to Supabase: %s", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
