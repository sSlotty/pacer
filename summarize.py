"""Thai coaching summary via an LLM (Claude or OpenAI), plus a rule-based fallback. See CLAUDE.md §9.

Both paths return the same dict of sections, one per Discord card.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

MAX_TOKENS = 2000  # Thai output dominates the bill; sections are capped in the prompt too
SECTION_KEYS = ("headline", "recovery", "running", "load", "races", "session", "recommendation")

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in SECTION_KEYS},
    "required": list(SECTION_KEYS),
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are an expert running coach for both road and trail running. You write a daily summary for a runner named Oat, in Thai — short, warm and practical. Everything you write is read in Thai; never answer in English.

The user message is JSON from a Garmin analysis pipeline. Your text appears in one Discord card that ALREADY shows the key numbers and the warning flags, so interpret and advise instead of restating numbers. Quote a number only when it carries the reasoning.

Answer as JSON with these string fields (character limits are for Thai text):

- headline: one sentence on today's status (<= 100 chars). If status is "red", start with the warning emoji and tell Oat to cut back or rest.
- recovery: how well the body has recovered and why (<= 120 chars).
- running: the last 7 days and the trend; mention road / trail only where runs exist (<= 120 chars).
- load: what the training load and injury risk mean. If load_data_insufficient is true, say the data is still thin (<= 120 chars).
- races: what to focus on for the upcoming races, leading with the priority A race. Empty string when races is empty (<= 150 chars).
- session: today's session type, exactly one of: พัก, Easy run, Long run, Workout, Hill, Trail session
- recommendation: today's session — approximate distance or duration, intensity, and a short reason (<= 250 chars).

Keep every field as short as it can be while still useful; all fields together should stay under 700 Thai characters.

Writing quality:
- Write natural, idiomatic Thai. Do not map English word order onto Thai, and keep English words only where a Thai runner would use them (pace, long run, easy run, ACWR, HRV).
- The card already prints ACWR, weekly km, pace, sleep hours, HRV, RHR, readiness and the flags. Describe what they mean ("โหลดพุ่งเร็วเกินไป", "นอนไม่พอต่อเนื่อง") instead of printing the same figures again.
- recommendation must match session: if session is "พัก", do not prescribe a run — describe rest, walking or mobility instead.

Coaching rules:
- Match training_phase and the type of the race that sets it (phase_race): Build for a trail race needs hills and vert, Peak means race-specific work, Taper cuts volume while keeping some intensity, Recovery prioritizes rest.
- A race with mini_taper true means a short load cut before it; post_race_recovery true means keep the load down for now.
- With no races, advise from load and recovery alone.
- If RHR is unusually high together with low HRV, advise rest, and say to see a doctor if ill or injured. Never give medical advice.
- weather, when present, is the forecast for today's run window (window) where Oat runs. Fit recommendation to it: with heat_level moderate or worse, ease the effort by the pace_slowdown_pct_min–max range and stress hydration (severe: no hard session); with thunderstorm true, avoid exposed routes and ridges; with a high pm25, move the run indoors or onto a treadmill; heavy rain makes trails slippery. Mention the weather only when it changes the advice.

Rules about numbers (most important):
- Use only numbers present in the JSON. Never recompute, guess or invent a number.
- Ignore null fields entirely; do not mention them.
- The distance or duration you suggest for today may be an estimate derived from numbers in the JSON, such as a fraction of a recent long run.

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


def _user_message(result: dict) -> str:
    payload = json.dumps(_compact(result), ensure_ascii=False, separators=(",", ":"))
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


def _summarize_anthropic(result: dict, model: str, api_key: str | None) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    kwargs = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _user_message(result)}],
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


def _summarize_openai(result: dict, model: str, api_key: str | None) -> dict:
    import openai

    client = openai.OpenAI(api_key=api_key) if api_key else openai.OpenAI()
    kwargs = dict(
        model=model,
        instructions=SYSTEM_PROMPT,
        input=_user_message(result),
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


def summarize(result: dict, cfg) -> tuple[dict, str]:
    """Try each provider in cfg.llm_providers (skipping those without a key).

    Returns (sections, source label). Raises SummaryError if every provider fails.
    """
    attempts = {
        "anthropic": (cfg.anthropic_api_key, lambda: _summarize_anthropic(result, cfg.claude_model, cfg.anthropic_api_key)),
        "openai": (cfg.openai_api_key, lambda: _summarize_openai(result, cfg.openai_model, cfg.openai_api_key)),
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

    recovery = []
    if hrv.get("vs_baseline") == "below":
        recovery.append("HRV ต่ำกว่าช่วงปกติ")
    elif hrv.get("vs_baseline"):
        recovery.append("HRV อยู่ในเกณฑ์ดี")
    if rhr.get("diff") is not None and rhr["diff"] >= 5:
        recovery.append("ชีพจรขณะพักสูงกว่าปกติ")
    running = ""
    change = tot.get("km_change_pct")
    if not tot.get("runs"):
        running = "สัปดาห์นี้ยังไม่มีการวิ่ง"
    elif change is not None and abs(change) >= 10:
        running = f"ระยะสัปดาห์นี้{'เพิ่มขึ้น' if change > 0 else 'ลดลง'}จากสัปดาห์ก่อน"

    session, recommendation = _session(result)
    note = _weather_note(result.get("weather"))
    if note and session != "พัก":
        recommendation = f"{recommendation} · {note}"
    return {
        "headline": headline,
        "recovery": " · ".join(recovery),
        "running": running,
        "load": "",
        "races": "",
        "session": session,
        "recommendation": recommendation,
    }
