"""Thai coaching summary via an LLM (Claude or OpenAI), plus a rule-based fallback. See CLAUDE.md §9.

Both paths return the same dict of sections, one per Discord card.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta

log = logging.getLogger(__name__)

MAX_TOKENS = 2000  # Thai output dominates the bill; sections are capped in the prompt too
SECTION_KEYS = ("headline", "summary", "session", "recommendation")

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in SECTION_KEYS},
    "required": list(SECTION_KEYS),
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are an expert running coach for both road and trail running. You write a daily summary for a runner named Oat, in Thai — short, warm and practical. Everything you write is read in Thai; never answer in English.

The user message is JSON from a Garmin analysis pipeline. Your text goes into one Discord card laid out like this:
1. your headline
2. the flags, printed verbatim (every item in "flags")
3. your session + recommendation
4. your summary
5. number fields: HRV, RHR, sleep (last night and 7-day average), body battery, readiness, VO2max, run-window weather (temperature, feels-like, wind, heat level, dew point, humidity, pace slowdown %, rain, PM2.5), 7-day km / runs / pace for total, road and trail, ACWR, rest days, hard sessions, weekly km for 4 weeks, and each race's countdown, phase and readiness.
Oat reads all of it, so say each thing once, in one place.

Answer as JSON with these string fields (character limits are for Thai text):

- headline: one sentence on what today is about (<= 80 chars). Do not list the reasons the flags already give. If status is "red", start with the warning emoji and tell Oat to cut back or rest.
- session: today's session type, exactly one of: พัก, Easy run, Long run, Workout, Hill, Trail session
- recommendation: today's session — approximate distance or duration, intensity, and a short reason (<= 220 chars).
- summary: ONE short paragraph that pulls recovery, the last 7 days of running, training load and the races together into the big picture — what it all means and where training is heading (<= 250 chars). Mention only the race that sets training_phase; mention another race only when it has mini_taper or post_race_recovery true. Do not repeat the headline, the flags or the recommendation. If load_data_insufficient is true, say the data is still thin.

Keep every field as short as it can be while still useful; all fields together should stay under 550 Thai characters.

No repetition (most important for readability):
- Never write a number that the card already shows in the fields above (sleep hours, HRV, RHR, readiness, ACWR, km, pace, temperature, dew point, pace slowdown %, PM2.5, days to race). Describe what it means instead ("นอนไม่พอต่อเนื่อง", "โหลดลดลงชัด").
- Never restate a flag. Build on it: what it changes for today or for the plan.
- Each fact appears in at most one field.

Writing quality:
- Write natural, idiomatic Thai. Do not map English word order onto Thai, and keep English words only where a Thai runner would use them (pace, long run, easy run, ACWR, HRV).
- recommendation must match session: if session is "พัก", do not prescribe a run — describe rest, walking or mobility instead.

Coaching rules:
- Match training_phase and the type of the race that sets it (phase_race): Build for a trail race needs hills and vert, Peak means race-specific work, Taper cuts volume while keeping some intensity, Recovery prioritizes rest.
- A race with mini_taper true means a short load cut before it; post_race_recovery true means keep the load down for now.
- With no races, advise from load and recovery alone.
- If RHR is unusually high together with low HRV, advise rest, and say to see a doctor if ill or injured. Never give medical advice.
- recent_advice, when present, lists what you advised on previous days (session, a clipped advice text) next to what Oat actually ran that day (ran; "rest" means no run). Treat it as memory: do not reuse the same wording or the same reasoning, and talk about what changed since then. If Oat ignored the advice (ran hard after a rest day, skipped a planned session), adjust today for it. Do not prescribe the same session many days in a row unless recovery or the phase truly calls for it.
- weather, when present, is the forecast for today's run window (window) where Oat runs. Fit recommendation to it: with heat_level moderate or worse, ease the effort and stress hydration (severe: no hard session); with thunderstorm true, avoid exposed routes and ridges; with a high pm25, move the run indoors or onto a treadmill; heavy rain makes trails slippery. Say how to adapt, not the forecast figures, and mention the weather only when it changes the advice.

Rules about numbers (most important):
- Use only numbers present in the JSON. Never recompute, guess or invent a number.
- Ignore null fields entirely; do not mention them.
- The distance or duration you suggest for today may be an estimate derived from numbers in the JSON, such as a fraction of a recent long run. These are the only numbers you normally need to write.

Style: plain spoken Thai, **bold** allowed. No headings, no dates, no long bullet lists — the card already provides structure."""


OPENAI_MAX_OUTPUT_TOKENS = 4000  # includes reasoning tokens on reasoning models
OPENAI_REASONING_EFFORT = "low"  # this task is summarizing numbers we already computed
PROVIDER_LABEL = {"anthropic": "Claude", "openai": "ChatGPT"}


class SummaryError(Exception):
    pass


DROP_FIELDS = {
    "totals_7d": ("prev_week_km",),
    "road_7d": ("avg_pace_s_per_km",),
    "trail_7d": ("prev_week_elev_gain_m", "elev_loss_m"),
    "load": ("days_of_data", "hard_runs_7d"),
    "weather": ("location_source", "source", "weather_code"),
}
DROP_RUN_FIELDS = ("name", "date")


def _trim_run(brief):
    return {k: v for k, v in brief.items() if k not in DROP_RUN_FIELDS} if isinstance(brief, dict) else brief


def _compact(result: dict) -> dict:
    """Drop fields the model does not need, so input tokens stay small."""
    out = {}
    for key, value in result.items():
        if key == "trend_7d":
            # one compact row per day instead of repeating every key seven times
            out["trend_7d_rows"] = {
                "columns": "date,load,road_km,trail_km,trail_elev_m,hrv,sleep_h,rhr",
                "rows": [
                    ",".join("" if d[c] is None else str(d[c]) for c in
                             ("date", "load", "road_km", "trail_km", "trail_elev_m", "hrv", "sleep_h", "rhr"))
                    for d in value
                ],
            }
            continue
        if isinstance(value, dict):
            dropped = DROP_FIELDS.get(key, ())
            value = {
                k: (_trim_run(v) if k.startswith("long_run") else v)
                for k, v in value.items()
                if k not in dropped and v is not None
            }
        out[key] = value
    return out


ADVICE_DAYS = 7
ADVICE_CHARS = 80  # enough to recognise the advice again; Thai text is expensive in tokens


def _clip_advice(text: str) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"[*_`>]", "", text)).strip()
    return text if len(text) <= ADVICE_CHARS else text[: ADVICE_CHARS - 1] + "…"


def _ran(day_runs: list[dict]) -> str:
    if not day_runs:
        return "rest"
    parts = []
    for category in ("road", "trail"):
        group = [r for r in day_runs if r.get("run_category") == category]
        if not group:
            continue
        km = sum(r.get("distance_m") or 0 for r in group) / 1000
        gain = sum(r.get("elev_gain_m") or 0 for r in group)
        parts.append(f"{category} {km:.1f}km" + (f" D+{gain:.0f}" if category == "trail" and gain else ""))
    return "+".join(parts) or "rest"


def advice_history(notes: list[dict], runs: list[dict], today: date, days: int = ADVICE_DAYS) -> list[dict]:
    """Advice sent on the days before today, each paired with what was actually run. See CLAUDE.md §9.1."""
    first = (today - timedelta(days=days)).isoformat()
    rows = []
    for note in notes:
        day = note.get("date")
        if not day or not first <= day < today.isoformat():
            continue
        if not note.get("session") and not note.get("recommendation"):
            continue
        rows.append({
            "date": day,
            "session": note.get("session") or "",
            "ran": _ran([r for r in runs if r.get("date") == day]),
            "advice": _clip_advice(note.get("recommendation") or ""),
        })
    return rows


def _user_message(result: dict, history: list[dict] | None = None) -> str:
    compact = _compact(result)
    if history:
        columns = ("date", "session", "ran", "advice")
        compact["recent_advice"] = {
            "columns": ",".join(columns),
            "rows": [",".join(str(h[c]).replace(",", "") for c in columns) for h in history],
        }
    payload = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    return f"Today's data:\n{payload}"


def _log_usage(provider: str, model: str, usage) -> None:
    """Log token counts so spend can be checked against the provider's dashboard."""
    if usage is None:
        return
    fields = {name: getattr(usage, name, None) for name in ("input_tokens", "output_tokens")}
    details = getattr(usage, "output_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None)
    log.info(
        "%s (%s) usage: input=%s output=%s%s",
        provider,
        model,
        fields["input_tokens"],
        fields["output_tokens"],
        f" (reasoning={reasoning})" if reasoning else "",
    )


def _parse_sections(text: str, provider: str) -> dict:
    if not text:
        raise SummaryError(f"{provider} returned no text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SummaryError(f"{provider} returned invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise SummaryError(f"{provider} returned JSON that is not an object")
    sections = {key: str(data.get(key) or "").strip() for key in SECTION_KEYS}
    if not sections["headline"] or not sections["recommendation"]:
        raise SummaryError(f"{provider} response is missing headline or recommendation")
    return sections


def _summarize_anthropic(result: dict, model: str, api_key: str | None, history: list[dict] | None = None) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _user_message(result, history)}],
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )
    try:
        # The token budget is meant for the answer itself, so skip thinking.
        response = client.messages.create(**kwargs, thinking={"type": "disabled"})
    except anthropic.BadRequestError as e:
        # Some models (e.g. Fable) reject disabling thinking; retry with the model default.
        if "thinking" not in str(e).lower():
            raise
        response = client.messages.create(**kwargs)

    if response.stop_reason == "refusal":
        raise SummaryError("Claude declined the request")
    if response.stop_reason == "max_tokens":
        raise SummaryError("Claude response was cut off at max_tokens")
    _log_usage("anthropic", model, getattr(response, "usage", None))
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
    return _parse_sections(text, "Claude")


def _summarize_openai(result: dict, model: str, api_key: str | None, history: list[dict] | None = None) -> dict:
    import openai

    client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()
    kwargs = dict(
        model=model,
        instructions=SYSTEM_PROMPT,
        input=_user_message(result, history),
        max_output_tokens=OPENAI_MAX_OUTPUT_TOKENS,
        text={
            "format": {
                "type": "json_schema",
                "name": "running_summary",
                "schema": OUTPUT_SCHEMA,
                "strict": True,
            }
        },
    )
    try:
        # Reasoning tokens are billed as output; this task does not need deep reasoning.
        response = client.responses.create(**kwargs, reasoning={"effort": OPENAI_REASONING_EFFORT})
    except openai.BadRequestError as e:
        if "reasoning" not in str(e).lower():
            raise
        response = client.responses.create(**kwargs)
    _log_usage("openai", model, getattr(response, "usage", None))
    if response.status != "completed":
        reason = getattr(response.incomplete_details, "reason", None)
        raise SummaryError(f"OpenAI response not completed (status={response.status}, reason={reason})")
    return _parse_sections(response.output_text.strip(), "OpenAI")


def summarize(result: dict, cfg, history: list[dict] | None = None) -> tuple[dict, str]:
    """Try each provider in cfg.llm_providers (skipping those without a key).

    history is advice_history(): what was advised on recent days, so today's text does not repeat it.

    Returns (sections, source label). Raises SummaryError if every provider fails.
    """
    attempts = {
        "anthropic": (cfg.anthropic_api_key, lambda: _summarize_anthropic(result, cfg.claude_model, cfg.anthropic_api_key, history)),
        "openai": (cfg.openai_api_key, lambda: _summarize_openai(result, cfg.openai_model, cfg.openai_api_key, history)),
    }
    errors = []
    for provider in cfg.llm_providers:
        api_key, call = attempts[provider]
        if not api_key:
            errors.append(f"{provider}: no API key")
            continue
        try:
            return call(), PROVIDER_LABEL[provider]
        except Exception as e:  # noqa: BLE001 — try the next provider
            log.warning("%s summary failed (%s: %s)", provider, type(e).__name__, e)
            errors.append(f"{provider}: {type(e).__name__}")
    raise SummaryError("all LLM providers failed — " + "; ".join(errors) if errors else "no LLM provider configured")


# ---------------------------------------------------------------- fallback


def _fmt(value, suffix: str = "") -> str:
    return "–" if value is None else f"{value}{suffix}"


def _session(result: dict) -> tuple[str, str]:
    status = result.get("status")
    phase = result.get("training_phase")
    races = result.get("races") or []
    if status == "red":
        return "พัก", "พัก หรือเดิน/จ็อกเบา ๆ ไม่เกิน 30 นาที ร่างกายส่งสัญญาณว่าต้องการฟื้นตัว"
    if any(r.get("mini_taper") or r.get("post_race_recovery") for r in races):
        return "Easy run", "Easy run สั้น ๆ 30–40 นาที เพราะอยู่ช่วงใกล้หรือเพิ่งจบรายการแข่ง"
    if status == "yellow":
        return "Easy run", "Easy run 30–45 นาที โซน 2 หรือพักถ้ารู้สึกล้า"
    if phase == "Race day":
        return "Race day", "วันแข่ง! วอร์มอัปให้ดี ออกตัวช้า ๆ และสนุกกับการแข่ง"
    if phase == "Taper":
        return "Easy run", "Easy run ระยะสั้น อาจใส่ strides เล็กน้อย ลดระยะรวมลง"
    if phase == "Recovery":
        return "Easy run", "Easy run เบา ๆ หรือพัก เน้นฟื้นตัวหลังแข่ง"
    return "Easy run / Workout", "Easy run หรือ workout ตามแผน ร่างกายพร้อมดี"


def _weather_note(weather: dict | None) -> str:
    if not weather:
        return ""
    notes = []
    if weather.get("heat_level") == "severe":
        notes.append("อากาศร้อนชื้นมาก เลี่ยงซ้อมหนัก")
    elif weather.get("heat_level") == "high":
        notes.append("อากาศร้อนชื้น ลด pace ลงและดื่มน้ำให้พอ")
    if weather.get("thunderstorm"):
        notes.append("มีพายุฝนฟ้าคะนอง เลี่ยงที่โล่ง")
    if (weather.get("pm25") or 0) > 75:
        notes.append("ฝุ่นสูง ย้ายไปวิ่งในร่ม")
    return " · ".join(notes)


def fallback_sections(result: dict) -> dict:
    """Summary sections built from the analysis only — no LLM. Numbers live in the embed fields."""
    status = result.get("status")
    rec = result.get("recovery") or {}
    tot = result.get("totals_7d") or {}
    hrv, rhr = rec.get("hrv") or {}, rec.get("rhr") or {}

    headline = {
        "red": "⚠️ ร่างกายส่งสัญญาณเตือน ควรลดการซ้อมหรือพักวันนี้",
        "yellow": "มีบางค่าที่ควรระวัง ซ้อมได้แต่อย่าหนัก",
        "green": "ทุกอย่างดูปกติ พร้อมซ้อมตามแผน",
    }.get(status, "สรุปข้อมูลวันนี้")

    summary = []
    if hrv.get("vs_baseline") == "below":
        summary.append("HRV ต่ำกว่าช่วงปกติ")
    elif hrv.get("vs_baseline"):
        summary.append("HRV อยู่ในเกณฑ์ดี")
    if rhr.get("diff") is not None and rhr["diff"] >= 5:
        summary.append("ชีพจรขณะพักสูงกว่าปกติ")
    change = tot.get("km_change_pct")
    if not tot.get("runs"):
        summary.append("สัปดาห์นี้ยังไม่มีการวิ่ง")
    elif change is not None and abs(change) >= 10:
        summary.append(f"ระยะสัปดาห์นี้{'เพิ่มขึ้น' if change > 0 else 'ลดลง'}จากสัปดาห์ก่อน")

    session, recommendation = _session(result)
    note = _weather_note(result.get("weather"))
    if note and session != "พัก":
        recommendation = f"{recommendation} · {note}"
    return {
        "headline": headline,
        "summary": " · ".join(summary),
        "session": session,
        "recommendation": recommendation,
    }
