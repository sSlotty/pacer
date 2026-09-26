"""SQLite storage (stdlib only). Upserts never let a later None overwrite a stored value."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from analysis import INDOOR_TYPE_KEYS

DAILY_COLUMNS = {
    "date": "TEXT PRIMARY KEY",
    "resting_hr": "REAL",
    "stress_avg": "REAL",
    "body_battery_high": "REAL",
    "body_battery_low": "REAL",
    "body_battery_wake": "REAL",
    "sleep_seconds": "REAL",
    "deep_sleep_seconds": "REAL",
    "rem_sleep_seconds": "REAL",
    "awake_seconds": "REAL",
    "sleep_score": "REAL",
    "hrv_last_night": "REAL",
    "hrv_weekly_avg": "REAL",
    "hrv_status": "TEXT",
    "hrv_baseline_low": "REAL",
    "hrv_baseline_high": "REAL",
    "readiness_score": "REAL",
    "readiness_level": "TEXT",
    "vo2max_running": "REAL",
}

RUN_COLUMNS = {
    "activity_id": "INTEGER PRIMARY KEY",
    "date": "TEXT NOT NULL",
    "start_time": "TEXT",
    "name": "TEXT",
    "type_key": "TEXT",
    "run_category": "TEXT",
    "distance_m": "REAL",
    "duration_s": "REAL",
    "moving_s": "REAL",
    "elev_gain_m": "REAL",
    "elev_loss_m": "REAL",
    "avg_hr": "REAL",
    "max_hr": "REAL",
    "avg_cadence": "REAL",
    "training_load": "REAL",
    "aerobic_te": "REAL",
    "anaerobic_te": "REAL",
    "start_lat": "REAL",
    "start_lon": "REAL",
}


NOTIFICATION_COLUMNS = {
    "date": "TEXT PRIMARY KEY",
    "sent_at": "TEXT",
    "source": "TEXT",
    "status": "TEXT",
}


class Storage:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._create("daily_metrics", DAILY_COLUMNS)
        self._create("runs", RUN_COLUMNS)
        self._create("notifications", NOTIFICATION_COLUMNS)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(date)")
        self.conn.commit()

    def _create(self, table: str, columns: dict[str, str]) -> None:
        cols = ", ".join(f"{name} {decl}" for name, decl in columns.items())
        self.conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({cols})")
        # Add columns introduced after the table was first created.
        existing = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl.split()[0]}")

    def _upsert(self, table: str, columns: dict[str, str], key: str, row: dict) -> None:
        names = list(columns)
        placeholders = ", ".join("?" for _ in names)
        updates = ", ".join(
            f"{c} = COALESCE(excluded.{c}, {table}.{c})" for c in names if c != key
        )
        sql = (
            f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders}) "
            f"ON CONFLICT({key}) DO UPDATE SET {updates}"
        )
        self.conn.execute(sql, [row.get(c) for c in names])

    def upsert_daily(self, row: dict) -> None:
        self._upsert("daily_metrics", DAILY_COLUMNS, "date", row)
        self.conn.commit()

    def upsert_runs(self, rows: list[dict]) -> None:
        for row in rows:
            self._upsert("runs", RUN_COLUMNS, "activity_id", row)
        self.conn.commit()

    def is_empty(self) -> bool:
        daily = self.conn.execute("SELECT 1 FROM daily_metrics LIMIT 1").fetchone()
        runs = self.conn.execute("SELECT 1 FROM runs LIMIT 1").fetchone()
        return daily is None and runs is None

    def daily_since(self, since: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM daily_metrics WHERE date >= ? ORDER BY date", (since,)
        )
        return [dict(r) for r in cur]

    def runs_since(self, since: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM runs WHERE date >= ? ORDER BY date, start_time", (since,)
        )
        return [dict(r) for r in cur]

    def last_run_location(self) -> tuple[float, float] | None:
        """(lat, lon) where the latest outdoor run with GPS started, or None."""
        placeholders = ", ".join("?" for _ in INDOOR_TYPE_KEYS)
        row = self.conn.execute(
            "SELECT start_lat, start_lon FROM runs "
            "WHERE start_lat IS NOT NULL AND start_lon IS NOT NULL "
            f"AND type_key NOT IN ({placeholders}) ORDER BY date DESC, start_time DESC LIMIT 1",
            INDOOR_TYPE_KEYS,
        ).fetchone()
        return (row["start_lat"], row["start_lon"]) if row else None

    def was_sent(self, day: str) -> bool:
        """True if a summary for this date was already posted to Discord."""
        row = self.conn.execute("SELECT 1 FROM notifications WHERE date = ?", (day,)).fetchone()
        return row is not None

    def mark_sent(self, day: str, source: str | None, status: str | None) -> None:
        self.conn.execute(
            "INSERT INTO notifications (date, sent_at, source, status) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET sent_at = excluded.sent_at, "
            "source = excluded.source, status = excluded.status",
            (day, datetime.now(timezone.utc).isoformat(timespec="seconds"), source or "fallback", status),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
