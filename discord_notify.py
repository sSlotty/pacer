"""Discord webhook: one dashboard-style embed per day. See CLAUDE.md §10."""

from __future__ import annotations

from datetime import date, datetime, timezone

COLORS = {"green": 0x2ECC71, "yellow": 0xF1C40F, "red": 0xE74C3C}
STATUS_BADGE = {"green": "🟢 พร้อมซ้อม", "yellow": "🟡 ซ้อมได้แต่ระวัง", "red": "🔴 ควรพัก"}
FLAG_ICON = {"red": "🔴", "yellow": "🟡", "info": "ℹ️"}
PRIORITY_ICON = {"A": "🅰️", "B": "🅱️", "C": "©️"}
TYPE_LABEL = {"road": "🛣️ Road", "trail": "⛰️ Trail", "mixed": "🛣️⛰️ Mixed"}
PHASE_LABEL = {
    "Base": "🧱 Base", "Build": "🏗️ Build", "Peak": "🔥 Peak",
    "Taper": "🪶 Taper", "Race day": "🏁 Race day", "Recovery": "🛌 Recovery",
}
TH_DAYS = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")
TH_MONTHS = ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")

TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_NAME_LIMIT = 256
FIELD_LIMIT = 1024
EMBED_LIMIT = 6000
MAX_FIELDS = 25
BLANK = "​"
NO_RUNS = "ไม่มีการวิ่ง"


# ---------------------------------------------------------------- formatting helpers


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


def _num(value, decimals: int = 1) -> str:
    """53.0 → '53', 21.01 → '21.0', 2500 → '2,500'."""
    if value is None:
        return "–"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.{decimals}f}"


def _change(value, suffix: str = "") -> str:
    """Signed change with an arrow: ▲5.5 / ▼30.6%."""
    if value is None:
        return ""
    if value == 0:
        return f"±0{suffix}"
    return f"{'▲' if value > 0 else '▼'}{_num(abs(value))}{suffix}"


def _thai_date(value: str, weekday: bool = False) -> str:
    d = date.fromisoformat(value)
    text = f"{d.day} {TH_MONTHS[d.month - 1]} {d.year}"
    return f"{TH_DAYS[d.weekday()]} {text}" if weekday else text


def _bar(pct, width: int = 10) -> str:
    """▰▰▰▱▱ progress bar for 0–100 %."""
    filled = 0 if pct is None else max(0, min(width, round(pct / 100 * width)))
    return "▰" * filled + "▱" * (width - filled)


def _field(name: str, value: str, inline: bool = True) -> dict:
    return {"name": _clip(name, FIELD_NAME_LIMIT), "value": _clip(value or BLANK, FIELD_LIMIT), "inline": inline}


def _row(fields: list[dict]) -> list[dict]:
    """Pad an inline group to a multiple of 3 so the next group starts on a new row."""
    while fields and len(fields) % 3:
        fields.append(_field(BLANK, BLANK))
    return fields


def _acwr_zone(acwr):
    if acwr is None:
        return "⚪", "ยังคำนวณไม่ได้"
    if acwr > 1.5:
        return "🔴", "เสี่ยงบาดเจ็บ"
    if acwr > 1.3:
        return "🟡", "เพิ่มเร็ว"
    if acwr >= 0.8:
        return "🟢", "เหมาะสม"
    return "🔵", "โหลดต่ำ"


# ---------------------------------------------------------------- description


def _description(result: dict, sections: dict) -> str:
    parts = []
    if sections.get("headline"):
        parts.append(sections["headline"])

    flags = result.get("flags") or []
    if flags:
        parts.append("\n".join(f"> {FLAG_ICON.get(f['level'], '•')} {f['message']}" for f in flags))

    if sections.get("recommendation"):
        session = _clip(sections.get("session") or "แผนวันนี้", 60)
        parts.append(f"**🎯 แนะนำวันนี้ · {session}**\n{sections['recommendation']}")

    for key, heading in (
        ("recovery", "🛌 การฟื้นตัว"),
        ("running", "🏃 การวิ่ง 7 วัน"),
        ("load", "📈 โหลด"),
        ("races", "🏁 รายการแข่ง"),
    ):
        if sections.get(key):
            parts.append(f"**{heading}**\n{sections[key]}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- fields


def _recovery_fields(rec: dict) -> list[dict]:
    hrv, rhr, sleep = rec.get("hrv") or {}, rec.get("rhr") or {}, rec.get("sleep") or {}
    ready = rec.get("readiness") or {}
    vo2 = rec.get("vo2max_running") or {}
    fields = []

    if hrv.get("last_night") is not None:
        value = f"**{_num(hrv['last_night'])}** ms"
        if hrv.get("baseline_low") is not None and hrv.get("baseline_high") is not None:
            value += f"\nปกติ {_num(hrv['baseline_low'])}–{_num(hrv['baseline_high'])}"
        fields.append(_field("💓 HRV", value))
    elif hrv.get("avg_7d") is not None:
        fields.append(_field("💓 HRV", f"**{_num(hrv['avg_7d'])}** ms\nเฉลี่ย 7 วัน"))

    if rhr.get("yesterday") is not None:
        value = f"**{_num(rhr['yesterday'])}** bpm"
        if rhr.get("diff") is not None:
            value += f" {_change(rhr['diff'])}"
        if rhr.get("avg_14d") is not None:
            value += f"\nเฉลี่ย {_num(rhr['avg_14d'])}"
        fields.append(_field("❤️ RHR", value))

    if sleep.get("last_night_h") is not None:
        value = f"**{_num(sleep['last_night_h'])}** ชม."
        if sleep.get("score") is not None:
            value += f" · {_num(sleep['score'])}"
        if sleep.get("avg_7d_h") is not None:
            value += f"\nเฉลี่ย {_num(sleep['avg_7d_h'])} ชม."
        fields.append(_field("😴 การนอน", value))
    elif sleep.get("avg_7d_h") is not None:
        fields.append(_field("😴 การนอน", f"**{_num(sleep['avg_7d_h'])}** ชม.\nเฉลี่ย 7 วัน"))

    if rec.get("body_battery_wake") is not None:
        fields.append(_field("🔋 Body battery", f"**{_num(rec['body_battery_wake'])}**\nตอนตื่น"))
    if ready.get("score") is not None:
        level = f"\n{ready['level'].title()}" if ready.get("level") else ""
        fields.append(_field("🎯 Readiness", f"**{_num(ready['score'])}**{level}"))
    if vo2.get("value") is not None:
        fields.append(_field("🫁 VO2max", f"**{_num(vo2['value'])}**"))
    return _row(fields)


def _running_fields(result: dict) -> list[dict]:
    tot = result.get("totals_7d") or {}
    road = result.get("road_7d") or {}
    trail = result.get("trail_7d") or {}

    if tot.get("runs"):
        total = f"**{_num(tot.get('km'))}** km · {tot['runs']} ครั้ง"
        if tot.get("km_change_pct") is not None:
            total += f"\n{_change(tot['km_change_pct'], '%')} จากสัปดาห์ก่อน"
    else:
        total = NO_RUNS
    fields = [_field("📊 รวม 7 วัน", total)]

    if road.get("runs"):
        value = f"**{_num(road.get('km'))}** km · {road['runs']} ครั้ง"
        if road.get("avg_pace"):
            value += f"\n{road['avg_pace']} /km"
        long7 = road.get("long_run_7d") or {}
        if long7.get("km"):
            value += f"\nlong {_num(long7['km'])} km"
        fields.append(_field("🛣️ Road", value))

    if trail.get("runs"):
        value = f"**{_num(trail.get('km'))}** km · {trail['runs']} ครั้ง\nD+ {_num(trail.get('elev_gain_m'))} m"
        if trail.get("vertical_rate_m_per_km") is not None:
            value += f" · {_num(trail['vertical_rate_m_per_km'])} m/km"
        fields.append(_field("⛰️ Trail", value))
    return _row(fields)


def _load_fields(load: dict) -> list[dict]:
    icon, label = _acwr_zone(load.get("acwr"))
    acwr = f"**{_num(load.get('acwr'), 2)}** {icon}\n{label}"
    if load.get("load_data_insufficient"):
        acwr += " (ข้อมูล < 21 วัน)"
    # ACWR means nothing without its definition; spell it out on the card.
    acwr += "\nโหลด 7 วัน ÷ 28 วัน\nปกติ 0.8–1.3"
    fields = [_field("⚖️ ACWR", acwr), _field("🛋️ วันพัก", f"**{_num(load.get('rest_days_7d'))}** / 7 วัน")]
    if load.get("hard_pct_7d") is not None:
        fields.append(_field("💥 ซ้อมหนัก", f"**{_num(load['hard_pct_7d'])}%**\n{_num(load.get('hard_runs_7d'))} ครั้ง"))
    return _row(fields)


def _race_field(race: dict, result: dict) -> dict:
    days = race["days_to_race"]
    when = f"อีก {days} วัน" if days > 0 else "วันนี้! 🎉" if days == 0 else f"ผ่านมา {-days} วัน"
    name = f"{PRIORITY_ICON.get(race['priority'], race['priority'])} {race['name']} · {when}"

    spec = [TYPE_LABEL.get(race["type"], race["type"])]
    if race.get("distance_km"):
        spec.append(f"{_num(race['distance_km'])} km")
    if race.get("elevation_m"):
        spec.append(f"D+ {_num(race['elevation_m'])} m")
    if race.get("target_time"):
        spec.append(f"เป้า {race['target_time']}")
    spec.append(_thai_date(race["date"]))
    lines = [" · ".join(spec)]

    tags = []
    if race.get("sets_training_phase") and result.get("training_phase"):
        tags.append(f"Phase **{PHASE_LABEL.get(result['training_phase'], result['training_phase'])}**")
    if race.get("mini_taper"):
        tags.append("🪶 mini taper")
    if race.get("post_race_recovery"):
        tags.append("🛌 ฟื้นตัวหลังแข่ง")
    if tags:
        lines.append(" · ".join(tags))

    rd = race.get("readiness") or {}
    if rd.get("long_run_pct_of_distance") is not None:
        lines.append(f"`{_bar(rd['long_run_pct_of_distance'])}` long run {_num(rd['long_run_pct_of_distance'])}%")
    if rd.get("max_single_run_elev_gain_pct") is not None:
        lines.append(f"`{_bar(rd['max_single_run_elev_gain_pct'])}` D+ สูงสุด {_num(rd['max_single_run_elev_gain_pct'])}%")
    if rd.get("target_pace"):
        line = f"target pace {rd['target_pace']} /km"
        diff = rd.get("long_run_vs_target_s_per_km")
        if diff:
            line += f" · long run {'ช้ากว่า' if diff > 0 else 'เร็วกว่า'} {_num(abs(diff))} วิ/km"
        lines.append(line)
    return _field(name, "\n".join(lines), inline=False)


def _weekly_field(totals: dict) -> dict | None:
    weeks = totals.get("weekly_km_4w") or []
    if not weeks:
        return None
    peak = max((w["km"] or 0 for w in weeks), default=0) or 1
    rows = []
    for i, w in enumerate(weeks):
        km = w["km"] or 0
        filled = round(km / peak * 10)
        start, end = date.fromisoformat(w["start"]), date.fromisoformat(w["end"])
        # Label the full range: a start-date-only label reads as stale on the last row,
        # which always ends today.
        label = f"{start:%d/%m}-{end:%d/%m}"
        marker = " <- สัปดาห์นี้" if i == len(weeks) - 1 else ""
        rows.append(f"{label} {'█' * filled}{'░' * (10 - filled)} {km:5.1f}{marker}")
    return _field("📅 ระยะรายสัปดาห์ (km)", "```\n" + "\n".join(rows) + "\n```", inline=False)


# ---------------------------------------------------------------- message


def embed_length(embed: dict) -> int:
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    total += len((embed.get("footer") or {}).get("text", ""))
    for f in embed.get("fields", []):
        total += len(f["name"]) + len(f["value"])
    return total


def build_embed(result: dict, sections: dict, source: str | None = "Claude") -> dict:
    status = result.get("status")
    fields = _recovery_fields(result.get("recovery") or {})
    fields += _running_fields(result)
    fields += _load_fields(result.get("load") or {})
    fields += [_race_field(r, result) for r in (result.get("races") or [])[:3]]
    weekly = _weekly_field(result.get("totals_7d") or {})
    if weekly:
        fields.append(weekly)

    footer = f"Pacer · ข้อมูลจาก Garmin Connect · {f'สรุปโดย {source}' if source else 'ข้อความอัตโนมัติ (ไม่ได้ใช้ AI)'}"
    embed = {
        "title": _clip(f"🏃 {_thai_date(result['date'], weekday=True)} · {STATUS_BADGE.get(status, '⚪')}", TITLE_LIMIT),
        "description": _clip(_description(result, sections), DESCRIPTION_LIMIT),
        "color": COLORS.get(status, COLORS["yellow"]),
        "fields": fields[:MAX_FIELDS],
        "footer": {"text": footer},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    overflow = embed_length(embed) - EMBED_LIMIT
    if overflow > 0:
        embed["description"] = _clip(embed["description"], max(len(embed["description"]) - overflow, 0))
    if not embed["description"]:
        del embed["description"]
    return embed


def build_payload(result: dict, sections: dict, source: str | None = "Claude") -> dict:
    return {"username": "Pacer", "embeds": [build_embed(result, sections, source)]}


def send(webhook_url: str, payload: dict) -> None:
    import requests

    resp = requests.post(webhook_url, json=payload, timeout=20)
    if not 200 <= resp.status_code < 300:
        # Never include the webhook URL in the error.
        raise RuntimeError(f"Discord webhook returned HTTP {resp.status_code}: {resp.text[:200]}")


def send_summary(webhook_url: str, result: dict, sections: dict, source: str | None = "Claude") -> None:
    """`source` is the LLM label for the footer, or None for the rule-based fallback."""
    send(webhook_url, build_payload(result, sections, source))


def send_auth_alert(webhook_url: str, detail: str = "") -> None:
    embed = {
        "title": "⚠️ Garmin login ล้มเหลว",
        "description": _clip(
            "Token ของ Garmin ใช้ไม่ได้แล้ว ต้องรัน `python setup_tokens.py` ใหม่บนเครื่อง "
            "แล้วอัปเดต Secret `GARMINTOKENS_BASE64`" + (f"\n\n`{detail}`" if detail else ""),
            DESCRIPTION_LIMIT,
        ),
        "color": COLORS["red"],
    }
    send(webhook_url, {"username": "Pacer", "embeds": [embed]})
