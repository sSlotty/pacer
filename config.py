"""Configuration: every setting comes from environment variables (see CLAUDE.md §4)."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date

log = logging.getLogger(__name__)

LLM_PROVIDERS = ("anthropic", "openai")
RACE_TYPES = ("road", "trail", "mixed")
RACE_PRIORITIES = ("A", "B", "C")
_TIME_RE = re.compile(r"^\d{1,2}:[0-5]\d:[0-5]\d$")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s=%r is not an integer, using default %s", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("%s=%r is not a number, using default %s", name, raw, default)
        return default


def _env_coord(name: str, limit: float) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        value = None
    if value is None or not -limit <= value <= limit:
        log.warning("%s=%r is not a valid coordinate; ignoring", name, raw)
        return None
    return value


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _validate_race(item, index: int) -> dict | None:
    """Return a normalized race dict, or None (with a warning) if invalid."""

    def skip(reason: str) -> None:
        log.warning("RACES[%d] skipped: %s", index, reason)

    if not isinstance(item, dict):
        skip("not a JSON object")
        return None

    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        skip("missing 'name'")
        return None

    rtype = item.get("type")
    if rtype not in RACE_TYPES:
        skip(f"invalid 'type' {rtype!r} (expected road/trail/mixed)")
        return None

    raw_date = item.get("date")
    try:
        race_date = date.fromisoformat(raw_date) if isinstance(raw_date, str) else None
    except ValueError:
        race_date = None
    if race_date is None:
        skip(f"invalid 'date' {raw_date!r} (expected YYYY-MM-DD)")
        return None

    race = {"name": name.strip(), "type": rtype, "date": race_date.isoformat()}

    for key in ("distance_km", "elevation_m"):
        if item.get(key) is not None:
            val = _num(item[key])
            if val is None or val <= 0:
                skip(f"invalid '{key}' {item[key]!r}")
                return None
            race[key] = val

    target = item.get("target_time")
    if target is not None:
        if not isinstance(target, str) or not _TIME_RE.match(target):
            skip(f"invalid 'target_time' {target!r} (expected HH:MM:SS)")
            return None
        race["target_time"] = target

    priority = item.get("priority", "B")
    priority = priority.upper() if isinstance(priority, str) else priority
    if priority not in RACE_PRIORITIES:
        skip(f"invalid 'priority' {item.get('priority')!r} (expected A/B/C)")
        return None
    race["priority"] = priority
    return race


def parse_providers(raw: str | None) -> list[str]:
    """Parse LLM_PROVIDER ("openai,anthropic") into an ordered, de-duplicated list."""
    out = []
    for name in (raw or "anthropic").split(","):
        name = name.strip().lower()
        if not name:
            continue
        if name not in LLM_PROVIDERS:
            log.warning("LLM_PROVIDER %r is unknown (expected anthropic/openai); ignoring", name)
        elif name not in out:
            out.append(name)
    return out


def parse_races(raw: str | None) -> list[dict]:
    """Parse the RACES JSON. Bad entries are skipped; unparsable JSON means no races."""
    if raw is None or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("RACES is not valid JSON (%s); ignoring all races", e)
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        log.warning("RACES must be a JSON list; ignoring all races")
        return []
    races = [r for i, item in enumerate(data) if (r := _validate_race(item, i))]
    return sorted(races, key=lambda r: r["date"])


@dataclass
class Config:
    garmin_tokens_b64: str | None
    garmin_tokens_path: str
    anthropic_api_key: str | None
    discord_webhook_url: str | None
    claude_model: str
    llm_providers: list[str] = field(default_factory=lambda: ["anthropic"])
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    races: list[dict] = field(default_factory=list)
    trail_elev_threshold: float = 20.0
    tz_name: str = "Asia/Bangkok"
    send_deadline: str = "06:45"
    db_path: str = "data/garmin.db"
    backfill_days: int = 42
    refresh_days: int = 3
    weather_lat: float | None = None
    weather_lon: float | None = None
    run_time: str = "07:00"


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a local .env (git-ignored) without overriding real env vars."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_config() -> Config:
    load_dotenv()
    return Config(
        garmin_tokens_b64=os.getenv("GARMINTOKENS_BASE64") or None,
        garmin_tokens_path=os.getenv("GARMINTOKENS") or "~/.garminconnect",
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL") or None,
        claude_model=os.getenv("CLAUDE_MODEL") or "claude-sonnet-5",
        llm_providers=parse_providers(os.getenv("LLM_PROVIDER")),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL") or "gpt-5.4-mini",
        races=parse_races(os.getenv("RACES")),
        trail_elev_threshold=_env_float("TRAIL_ELEV_THRESHOLD", 20.0),
        tz_name=os.getenv("TZ_NAME") or "Asia/Bangkok",
        send_deadline=os.getenv("SEND_DEADLINE") or "06:45",
        db_path=os.getenv("DB_PATH") or "data/garmin.db",
        backfill_days=_env_int("BACKFILL_DAYS", 42),
        refresh_days=_env_int("REFRESH_DAYS", 3),
        weather_lat=_env_coord("WEATHER_LAT", 90),
        weather_lon=_env_coord("WEATHER_LON", 180),
        run_time=os.getenv("RUN_TIME") or "07:00",
    )
