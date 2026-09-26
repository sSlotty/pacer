"""Storage: Supabase Postgres when DATABASE_URL is set, else SQLite. See CLAUDE.md §7.

Both backends run the same SQL; upserts never let a later None overwrite a stored value.
This module is stdlib-only — psycopg is imported inside PostgresStorage.
"""

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


TABLES = {"daily_metrics": DAILY_COLUMNS, "runs": RUN_COLUMNS, "notifications": NOTIFICATION_COLUMNS}


class Storage:
    """SQLite backend; PostgresStorage overrides only the dialect hooks."""

    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        for table, columns in TABLES.items():
            self._create(table, columns)
        self._exec("CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(date)")
        self.conn.commit()

    # ---- dialect hooks

    def _exec(self, sql: str, params=()):
        return self.conn.execute(sql, params)

    def _decl(self, decl: str) -> str:
        return decl

    def _existing_columns(self, table: str) -> set[str]:
        return {row["name"] for row in self._exec(f"PRAGMA table_info({table})")}

    def _secure(self, table: str) -> None:
        pass

    # ----

    def _create(self, table: str, columns: dict[str, str]) -> None:
        cols = ", ".join(f"{name} {self._decl(decl)}" for name, decl in columns.items())
        self._exec(f"CREATE TABLE IF NOT EXISTS {table} ({cols})")
        # Add columns introduced after the table was first created.
        existing = self._existing_columns(table)
        for name, decl in columns.items():
            if name not in existing:
                self._exec(f"ALTER TABLE {table} ADD COLUMN {name} {self._decl(decl.split()[0])}")
        self._secure(table)

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
        self._exec(sql, [row.get(c) for c in names])

    def upsert_daily(self, row: dict) -> None:
        self._upsert("daily_metrics", DAILY_COLUMNS, "date", row)
        self.conn.commit()

    def upsert_runs(self, rows: list[dict]) -> None:
        for row in rows:
            self._upsert("runs", RUN_COLUMNS, "activity_id", row)
        self.conn.commit()

    def is_empty(self) -> bool:
        daily = self._exec("SELECT 1 FROM daily_metrics LIMIT 1").fetchone()
        runs = self._exec("SELECT 1 FROM runs LIMIT 1").fetchone()
        return daily is None and runs is None

    def daily_since(self, since: str) -> list[dict]:
        cur = self._exec(
            "SELECT * FROM daily_metrics WHERE date >= ? ORDER BY date", (since,)
        )
        return [dict(r) for r in cur]

    def runs_since(self, since: str) -> list[dict]:
        cur = self._exec(
            "SELECT * FROM runs WHERE date >= ? ORDER BY date, start_time", (since,)
        )
        return [dict(r) for r in cur]

    def last_run_location(self) -> tuple[float, float] | None:
        """(lat, lon) where the latest outdoor run with GPS started, or None."""
        placeholders = ", ".join("?" for _ in INDOOR_TYPE_KEYS)
        row = self._exec(
            "SELECT start_lat, start_lon FROM runs "
            "WHERE start_lat IS NOT NULL AND start_lon IS NOT NULL "
            f"AND type_key NOT IN ({placeholders}) ORDER BY date DESC, start_time DESC NULLS LAST LIMIT 1",
            INDOOR_TYPE_KEYS,
        ).fetchone()
        return (row["start_lat"], row["start_lon"]) if row else None

    def was_sent(self, day: str) -> bool:
        """True if a summary for this date was already posted to Discord."""
        row = self._exec("SELECT 1 FROM notifications WHERE date = ?", (day,)).fetchone()
        return row is not None

    def mark_sent(self, day: str, source: str | None, status: str | None) -> None:
        self._exec(
            "INSERT INTO notifications (date, sent_at, source, status) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET sent_at = excluded.sent_at, "
            "source = excluded.source, status = excluded.status",
            (day, datetime.now(timezone.utc).isoformat(timespec="seconds"), source or "fallback", status),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


POSTGRES_TYPES = {"REAL": "DOUBLE PRECISION", "INTEGER": "BIGINT"}  # float4 loses precision; Garmin ids exceed int32


class PostgresStorage(Storage):
    """Supabase (or any) Postgres via a connection string. Same tables and SQL as SQLite."""

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row

        # prepare_threshold=None: Supabase's transaction pooler cannot hold prepared statements.
        self.conn = psycopg.connect(url, row_factory=dict_row, prepare_threshold=None, connect_timeout=20)
        self._init_schema()

    def _exec(self, sql: str, params=()):
        return self.conn.execute(sql.replace("?", "%s"), params)

    def _decl(self, decl: str) -> str:
        head, _, rest = decl.partition(" ")
        return " ".join(filter(None, (POSTGRES_TYPES.get(head, head), rest)))

    def _existing_columns(self, table: str) -> set[str]:
        rows = self._exec(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ?",
            (table,),
        )
        return {row["column_name"] for row in rows}

    def _secure(self, table: str) -> None:
        # With RLS on and no policies, Supabase's public API keys cannot read health data;
        # the table owner connecting through DATABASE_URL is unaffected.
        self._exec(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def open_storage(cfg) -> Storage:
    """Supabase when DATABASE_URL is set, otherwise the local SQLite file."""
    if getattr(cfg, "database_url", None):
        return PostgresStorage(cfg.database_url)
    return Storage(cfg.db_path)
