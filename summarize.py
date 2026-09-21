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

SYSTEM_PROMPT = """คุณคือโค้ชวิ่งที่เชี่ยวชาญทั้ง road running และ trail running เขียนสรุปประจำวันให้นักวิ่งชื่อ Oat เป็นภาษาไทย กระชับ เป็นกันเอง

ข้อมูลที่ได้รับเป็น JSON จากระบบวิเคราะห์ข้อมูล Garmin ของวันนั้น ข้อความของคุณจะอยู่ในการ์ด Discord ใบเดียวที่ **แสดงตัวเลขหลักและ flags ไว้ให้แล้ว** ดังนั้นให้เน้นการตีความและสิ่งที่ควรทำ ไม่ต้องไล่ตัวเลขซ้ำ (อ้างตัวเลขได้เฉพาะที่จำเป็นต่อเหตุผล) ตอบเป็น JSON:

- headline: สรุปสถานะวันนี้ 1 ประโยค (≤ 100 ตัวอักษร) ถ้า status เป็น "red" ต้องขึ้นต้นด้วย ⚠️ และบอกให้ลดหรือพัก
- recovery: ร่างกายฟื้นตัวดีแค่ไหนและเพราะอะไร (≤ 120 ตัวอักษร)
- running: ภาพรวมการวิ่ง 7 วันและแนวโน้ม แยก road / trail เฉพาะประเภทที่มีการวิ่ง (≤ 120 ตัวอักษร)
- load: โหลดและความเสี่ยงบาดเจ็บหมายความว่าอะไร ถ้า load_data_insufficient เป็น true ให้บอกว่าข้อมูลยังน้อย (≤ 120 ตัวอักษร)
- races: สิ่งที่ควรโฟกัสสำหรับรายการแข่ง เน้นรายการ A ถ้า races ว่างให้ตอบ "" (≤ 150 ตัวอักษร)
- session: ชื่อประเภทการซ้อมวันนี้สั้น ๆ เลือกหนึ่งจาก: พัก, Easy run, Long run, Workout, Hill, Trail session
- recommendation: การซ้อมวันนี้ ระยะหรือเวลาโดยประมาณ ความหนัก และเหตุผลสั้น ๆ (≤ 250 ตัวอักษร)

เขียนให้สั้นที่สุดเท่าที่ยังได้ใจความ ทุกหัวข้อรวมกันไม่ควรเกิน 700 ตัวอักษร

หลักการแนะนำ:
- ให้สอดคล้องกับ training_phase และ type ของรายการที่กำหนด phase (phase_race) เช่น ช่วง Build ของรายการ trail ควรมี hill/vert, ช่วง Peak ซ้อมเฉพาะสนาม, ช่วง Taper ลดระยะแต่คงความเข้มบางส่วน, ช่วง Recovery เน้นฟื้นตัว
- รายการ B/C ที่มี mini_taper = true ให้ลดโหลดช่วงสั้น ๆ ก่อนแข่ง ส่วน post_race_recovery = true ให้แนะนำลดโหลดชั่วคราว
- ถ้าไม่มี races ให้แนะนำตามโหลดและการฟื้นตัวอย่างเดียว
- ถ้า RHR สูงผิดปกติร่วมกับ HRV ต่ำ ให้แนะนำพัก และถ้ามีอาการป่วยหรือเจ็บให้ปรึกษาแพทย์ ไม่ให้คำแนะนำทางการแพทย์

กฎเรื่องตัวเลข (สำคัญที่สุด):
- ใช้เฉพาะตัวเลขที่อยู่ใน JSON เท่านั้น ห้ามคำนวณตัวเลขใหม่ ห้ามเดา ห้ามแต่ง
- ข้าม field ที่เป็น null ไม่ต้องพูดถึง
- ระยะหรือเวลาที่แนะนำสำหรับวันนี้เป็นค่าประมาณที่อิงจากตัวเลขใน JSON ได้ (เช่น สัดส่วนของ long run ล่าสุด)

รูปแบบ: ภาษาพูดสั้น ๆ ใช้ **ตัวหนา** ได้ ไม่ต้องใส่หัวข้อ วันที่ หรือ bullet ยาว ๆ เพราะการ์ดจัดหัวข้อให้แล้ว"""


OPENAI_MAX_OUTPUT_TOKENS = 4000  # includes reasoning tokens on reasoning models
OPENAI_REASONING_EFFORT = "low"  # this task is summarizing numbers we already computed
PROVIDER_LABEL = {"anthropic": "Claude", "openai": "ChatGPT"}


class SummaryError(Exception):
    pass


def _user_message(result: dict) -> str:
    payload = json.dumps(result, ensure_ascii=False, indent=1)
    return f"ข้อมูลวันนี้:\n```json\n{payload}\n```"


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
    return {
        "headline": headline,
        "recovery": " · ".join(recovery),
        "running": running,
        "load": "",
        "races": "",
        "session": session,
        "recommendation": recommendation,
    }
