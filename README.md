<div align="center">

<img src="assets/logo.svg" alt="Pacer" width="112" height="112">

# Pacer

**โค้ชวิ่งส่วนตัวที่อ่านข้อมูลจาก Garmin แล้วส่งแผนซ้อมของวันนี้เข้า Discord ทุกเช้า**

รองรับทั้ง road และ trail · วิเคราะห์การฟื้นตัว โหลดการซ้อม สภาพอากาศ และการเตรียมตัวแข่ง · สรุปเป็นภาษาไทยด้วย AI

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![GitHub Actions](https://img.shields.io/badge/Runs%20on-GitHub%20Actions-2088FF?logo=githubactions&logoColor=white)](.github/workflows/daily.yml)
[![AI](https://img.shields.io/badge/AI-Claude%20%7C%20ChatGPT-D97757?logo=anthropic&logoColor=white)](#configuration)
[![Supabase](https://img.shields.io/badge/Data-Supabase-3FCF8E?logo=supabase&logoColor=white)](https://supabase.com)
[![Discord](https://img.shields.io/badge/Notify-Discord-5865F2?logo=discord&logoColor=white)](https://discord.com)

[Features](#features) · [How It Works](#how-it-works) · [Getting Started](#getting-started) · [Configuration](#configuration) · [Troubleshooting](#troubleshooting)

**ภาษาไทย** · [English](README.en.md)

</div>

---

> [!NOTE]
> Pacer ไม่ใช่คำแนะนำทางการแพทย์ ถ้ามีอาการป่วยหรือบาดเจ็บ ควรปรึกษาแพทย์

## Features

| | |
|---|---|
| 🛌 **การฟื้นตัว** | HRV เทียบ baseline, RHR เทียบค่าเฉลี่ย 14 วัน, การนอน, Body Battery, Training Readiness และ VO2max |
| 🏃 **สถิติการวิ่ง** | ระยะ เวลา และ pace ย้อนหลัง 7 วัน แยก road / trail พร้อม D+, effort km และ long run ที่ยาวที่สุด |
| 📈 **โหลดและความเสี่ยงบาดเจ็บ** | ACWR (โหลด 7 วัน ÷ 28 วัน), สัดส่วนการซ้อมหนัก, จำนวนวันพัก และระยะที่เพิ่มเร็วเกินไป |
| 🌦️ **สภาพอากาศช่วงเวลาวิ่ง** | อุณหภูมิ ความร้อนชื้น (พร้อม % ที่ pace ควรช้าลง) ฝน พายุฝนฟ้าคะนอง และ PM2.5 จาก [Open-Meteo](https://open-meteo.com) |
| 🏁 **เตรียมตัวแข่ง** | นับถอยหลังได้หลายรายการ, phase การซ้อม (Base → Build → Peak → Taper), mini taper ของรายการรอง และตัวชี้วัดความพร้อม |
| 🤖 **คำแนะนำโดย AI** | Claude หรือ ChatGPT เขียนสรุปเป็นภาษาไทยจากตัวเลขจริงเท่านั้น ถ้า AI ใช้ไม่ได้จะส่งข้อความแบบ rule-based แทน |

### Discord Card

การ์ดแต่ละวันเป็น embed เดียวแบบ dashboard สีของการ์ดบอกสถานะวันนั้น

| ส่วน | เนื้อหา |
|---|---|
| หัวการ์ด | วันที่ภาษาไทย และสถานะ 🟢 พร้อมซ้อม · 🟡 ซ้อมได้แต่ระวัง · 🔴 ควรพัก |
| คำเตือน | ทุกเงื่อนไขที่เข้าเกณฑ์ เช่น ACWR สูง, นอนน้อย, พายุฝนฟ้าคะนองช่วงวิ่ง |
| 🎯 แนะนำวันนี้ | ประเภทการซ้อม ระยะหรือเวลาโดยประมาณ และเหตุผล |
| ตัวเลขหลัก | การฟื้นตัว → อากาศ → การวิ่ง → โหลด เรียงเป็นกลุ่มละ 3 ช่อง |
| รายการแข่ง | วันที่เหลือ, phase และแถบความพร้อม `▰▰▰▱▱` |
| กราฟรายสัปดาห์ | ระยะ 4 สัปดาห์ล่าสุด |

## How It Works

```mermaid
flowchart LR
    G[Garmin Connect] -->|runs + recovery| F[garmin_fetch]
    W[Open-Meteo] -->|forecast + PM2.5| A
    F --> DB[(Supabase<br/>Postgres)]
    DB --> A[analysis<br/>metrics + flags]
    A --> S[summarize<br/>Claude / ChatGPT]
    S -. AI ใช้ไม่ได้ .-> R[rule-based<br/>fallback]
    S --> D[Discord card]
    R --> D
```

GitHub Actions รันทุก 30 นาทีช่วง **02:00–06:45** (เวลาไทย) และส่งการ์ด **วันละครั้ง** ในรอบแรกที่ข้อมูลการนอนเมื่อคืนซิงก์เข้า Garmin แล้ว

1. ถ้าวันนี้ส่งไปแล้ว → เก็บข้อมูลที่เพิ่งซิงก์เข้าฐานข้อมูล แล้วจบ
2. ถ้าข้อมูลการนอน / HRV ยังไม่เข้า → รอรอบถัดไป (รอบนี้ดึงข้อมูลแค่วันเดียว)
3. ถ้าข้อมูลพร้อมแล้ว หรือถึงเวลา `SEND_DEADLINE` (06:45) → วิเคราะห์และส่งทันทีด้วยข้อมูลที่มี

> [!TIP]
> ใช้หลายรอบเพราะ cron ของ GitHub เลื่อนเวลาได้หลายชั่วโมงและบางครั้งข้ามรอบไปเลย ถ้าคุณออกวิ่งคนละเวลา ให้แก้ `SEND_DEADLINE`, `RUN_TIME` และช่วงเวลา cron ใน [`daily.yml`](.github/workflows/daily.yml) (cron ใช้เวลา UTC = เวลาไทย − 7 ชม.)

<details>
<summary><b>Project Structure</b></summary>

| ไฟล์ | หน้าที่ |
|---|---|
| [`main.py`](main.py) | ตัวควบคุมหลักและ CLI |
| [`garmin_fetch.py`](garmin_fetch.py) | login ด้วย token, ดึงข้อมูล, เก็บเฉพาะกิจกรรมวิ่ง |
| [`weather.py`](weather.py) | พยากรณ์อากาศช่วงเวลาวิ่ง และประเมินความร้อนชื้น ฝน และฝุ่น |
| [`analysis.py`](analysis.py) | คำนวณ metrics, flags และ race phase (pure function, stdlib เท่านั้น) |
| [`summarize.py`](summarize.py) | เรียก Claude / OpenAI และสร้างข้อความสำรอง |
| [`discord_notify.py`](discord_notify.py) | สร้าง embed และส่ง webhook |
| [`storage.py`](storage.py) | Supabase Postgres หรือ SQLite สำหรับรันบนเครื่อง |
| [`config.py`](config.py) | อ่าน environment variables และตรวจรูปแบบ `RACES` |
| [`setup_tokens.py`](setup_tokens.py) | สร้าง token ของ Garmin (รันบนเครื่องครั้งเดียว) |
| [`migrate_to_supabase.py`](migrate_to_supabase.py) | ย้ายข้อมูลจาก SQLite ขึ้น Supabase |
| [`CLAUDE.md`](CLAUDE.md) | สเปกฉบับเต็มของระบบ |

</details>

## Getting Started

### Prerequisites

- Python 3.12 ขึ้นไป
- บัญชี Garmin Connect ที่ซิงก์ข้อมูลจากนาฬิกา
- API key ของ [Anthropic](https://console.anthropic.com) หรือ [OpenAI](https://platform.openai.com) อย่างน้อยหนึ่งเจ้า (เครดิต API แยกจากแพ็กเกจแชต สมาชิก ChatGPT Plus ใช้กับ API ไม่ได้)
- Discord webhook (Server Settings → Integrations → Webhooks → New Webhook)
- โปรเจกต์ [Supabase](https://supabase.com) (แพ็กเกจฟรีเพียงพอ)

### 1. Install

```bash
git clone https://github.com/<you>/pacer.git
cd pacer
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 2. Create Garmin Tokens

```bash
python setup_tokens.py
```

ใส่ email และรหัสผ่าน Garmin (รองรับ MFA) รหัสผ่านไม่ถูกบันทึกไว้ที่ไหน ผลลัพธ์มีสองอย่าง:

- `~/.garminconnect/garmin_tokens.json` สำหรับรันบนเครื่อง
- `garmin_tokens.b64` สำหรับใส่ใน GitHub Secret → คัดลอกเนื้อหา (`pbcopy < garmin_tokens.b64`) แล้ว **ลบไฟล์ทิ้งทันที**

> [!WARNING]
> token ใช้เข้าถึงบัญชี Garmin ได้ ห้าม commit หรือส่งต่อ (ไฟล์อยู่ใน `.gitignore` แล้ว)

### 3. Set Up Supabase

1. ในโปรเจกต์ Supabase กด **Connect** → เลือก **Session pooler** → คัดลอก connection string แล้วแทน `[YOUR-PASSWORD]` ด้วยรหัสผ่านฐานข้อมูล
2. ใส่ใน `.env` เป็น `DATABASE_URL=...`

ระบบสร้างตารางให้เองในการรันครั้งแรก และเปิด **Row Level Security** ทุกตาราง ข้อมูลจึงอ่านผ่าน anon key สาธารณะของ Supabase ไม่ได้

> [!IMPORTANT]
> ต้องใช้ **Session pooler** (host `…pooler.supabase.com`) ไม่ใช่ Direct connection เพราะ Direct connection ใช้ IPv6 อย่างเดียว ซึ่ง GitHub Actions ไม่รองรับ ถ้ารหัสผ่านมีอักขระพิเศษ ให้ percent-encode ก่อน (เช่น `@` → `%40`)

ถ้าเคยใช้ Pacer แบบ SQLite มาก่อน ย้ายข้อมูลเดิมขึ้นไปได้ด้วย `python migrate_to_supabase.py` (รันซ้ำได้ ข้อมูลไม่ซ้ำ)

### 4. Test Locally

ใส่ API key และ webhook ใน `.env` แล้วรัน:

```bash
python main.py --dry-run
```

ครั้งแรกจะดึงข้อมูลย้อนหลัง 42 วัน (ประมาณ 2–3 นาที) แล้วพิมพ์ผลวิเคราะห์และข้อความสรุปออกมา **โดยไม่ส่ง Discord** รอบถัดไปดึงแค่ 3 วันล่าสุด

### 5. Configure GitHub Actions

ที่ repo → **Settings → Secrets and variables → Actions** ใส่ secrets ต่อไปนี้:

| Secret | ค่า |
|---|---|
| `GARMINTOKENS_BASE64` | เนื้อหาจาก `garmin_tokens.b64` |
| `DATABASE_URL` | connection string แบบ Session pooler |
| `DISCORD_WEBHOOK_URL` | URL ของ Discord webhook |
| `ANTHROPIC_API_KEY` | ถ้าใช้ Claude |
| `OPENAI_API_KEY` | ถ้าใช้ ChatGPT |

จากนั้นไปที่ **Actions → Garmin daily running coach → Run workflow** เพื่อทดสอบ การกดรันเองจะส่งการ์ดทันทีเสมอ ถ้าผ่าน จะเห็นการ์ดใน Discord และข้อมูลใหม่ใน Supabase

## Configuration

ตั้งเป็น **Variables** ใน GitHub (หรือใส่ใน `.env` ตอนรันบนเครื่อง) ทุกตัวไม่บังคับ

| Variable | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `RACES` | `[]` | รายการแข่ง ดู[รูปแบบด้านล่าง](#races) |
| `LLM_PROVIDER` | `anthropic` | ลำดับ AI ที่จะลอง เช่น `openai,anthropic` (ข้ามเจ้าที่ไม่มี key) |
| `CLAUDE_MODEL` | `claude-sonnet-5` | |
| `OPENAI_MODEL` | `gpt-5.4-mini` | |
| `RUN_TIME` | `07:00` | เวลาออกวิ่ง ใช้ดูอากาศชั่วโมงนั้นและอีก 2 ชั่วโมงถัดไป |
| `SEND_DEADLINE` | `06:45` | เวลาที่ต้องส่งให้ได้ แม้ข้อมูลการนอนยังไม่เข้า |
| `WEATHER_LAT` / `WEATHER_LON` | – | พิกัดพยากรณ์อากาศ ถ้าไม่ตั้ง ใช้จุดเริ่มของการวิ่งกลางแจ้งครั้งล่าสุด |
| `TRAIL_ELEV_THRESHOLD` | `20` | m/km ที่ทำให้ `ultra_run` ถูกนับเป็น trail |

<details>
<summary><b>Other Environment Variables</b></summary>

| ตัวแปร | ค่าเริ่มต้น | หมายเหตุ |
|---|---|---|
| `GARMINTOKENS_BASE64` | – | token สำหรับ CI |
| `GARMINTOKENS` | `~/.garminconnect` | โฟลเดอร์ token สำหรับรันบนเครื่อง |
| `DATABASE_URL` | – | Supabase Postgres ถ้าไม่ตั้งจะใช้ SQLite ที่ `DB_PATH` |
| `DB_PATH` | `data/garmin.db` | SQLite สำหรับรันบนเครื่อง |
| `DISCORD_WEBHOOK_URL` | – | ไม่ต้องใช้ตอน `--dry-run` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | – | |
| `TZ_NAME` | `Asia/Bangkok` | ใช้กำหนดว่า "วันนี้" คือวันไหน |
| `BACKFILL_DAYS` | `42` | จำนวนวันที่ดึงเมื่อฐานข้อมูลว่าง |
| `REFRESH_DAYS` | `3` | ดึงซ้ำย้อนหลังเผื่อ Garmin ซิงก์ช้า |

</details>

### Races

```json
[
  {"name": "Uthai Trail", "type": "trail", "date": "2026-12-05", "distance_km": 50, "elevation_m": 2500, "priority": "A"},
  {"name": "Bangkok Marathon", "type": "road", "date": "2026-11-15", "distance_km": 42.195, "target_time": "04:00:00", "priority": "B"}
]
```

| Key | จำเป็น | ค่า |
|---|:---:|---|
| `name` | ✓ | ชื่อรายการ |
| `type` | ✓ | `road`, `trail` หรือ `mixed` |
| `date` | ✓ | `YYYY-MM-DD` |
| `distance_km` | | ระยะ (km) |
| `elevation_m` | | D+ รวม (m) สำหรับ trail / mixed |
| `target_time` | | `HH:MM:SS` สำหรับ road / mixed |
| `priority` | | `A` เป้าหมายหลัก, `B`, `C` (ค่าเริ่มต้น `B`) |

- **Phase การซ้อม** คิดจากรายการ A ที่ใกล้ที่สุด: Base (> 70 วัน) → Build → Peak → Taper (≤ 14 วัน) → Race day → Recovery
- **รายการ B / C** ไม่เปลี่ยน phase หลัก แต่ได้ mini taper 7 วันก่อนแข่ง และคำแนะนำให้ลดโหลด 7 วันหลังแข่ง
- รายการที่เขียนผิดจะถูกข้ามพร้อม warning ใน log โดยระบบไม่ล่ม

### Weather

Pacer ดูพยากรณ์รายชั่วโมงช่วง `RUN_TIME` จาก Open-Meteo ซึ่งฟรีและไม่ต้องใช้ API key

- **ความร้อนชื้น** คิดจากอุณหภูมิ + จุดน้ำค้าง (°F) แล้วบอกว่า pace ควรช้าลงกี่ % จะได้ไม่ไล่ pace ปกติในวันที่ร้อนชื้น
- **PM2.5** แบ่งระดับตามเกณฑ์ของกรมควบคุมมลพิษ
- ถ้าร้อนชื้นมาก มีพายุฝนฟ้าคะนอง หรือ PM2.5 เกิน 75 µg/m³ จะขึ้นคำเตือนสีเหลือง และ AI จะปรับคำแนะนำ เช่น ลดความหนัก เลี่ยงเส้นทางโล่ง หรือย้ายไปวิ่งบนลู่
- อากาศไม่เคยทำให้สถานะเป็นสีแดง เพราะสีแดงหมายถึงร่างกายต้องพัก

## CLI Usage

```bash
python main.py                    # รันตามปกติและส่ง Discord (วันละครั้ง)
python main.py --dry-run          # พิมพ์ผลวิเคราะห์และข้อความ ไม่ส่ง Discord
python main.py --force            # ส่งทันที ข้ามเงื่อนไขวันละครั้ง
python main.py --backfill 30      # ดึงข้อมูลย้อนหลัง 30 วัน
python main.py --date 2026-09-01  # วิเคราะห์โดยถือว่าวันนั้นเป็นวันนี้
```

## Development

เทสต์ใช้ข้อมูลจำลองทั้งหมด ไม่ต้องต่ออินเทอร์เน็ต

```bash
python -m unittest discover -s tests -t .
```

เทสต์ฝั่ง Postgres จะรันเมื่อตั้ง `TEST_DATABASE_URL` ใช้ Postgres ชั่วคราวใน Docker เท่านั้น

```bash
docker run -d --rm --name pacer-pg-test -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:17-alpine
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres python -m unittest discover -s tests -t .
docker stop pacer-pg-test
```

> [!CAUTION]
> ห้ามชี้ `TEST_DATABASE_URL` ไปที่ Supabase จริง เพราะเทสต์จะลบตาราง

ก่อนเพิ่มฟีเจอร์ ให้อัปเดตสเปกใน [`CLAUDE.md`](CLAUDE.md) ก่อนเขียนโค้ด

## Troubleshooting

| อาการ | วิธีแก้ |
|---|---|
| Discord แจ้ง "Garmin login ล้มเหลว" | token หมดอายุหรือถูกยกเลิก รัน `python setup_tokens.py` ใหม่ แล้วอัปเดต secret `GARMINTOKENS_BASE64` |
| การ์ดเขียนว่า "ไม่ได้ใช้ AI" | AI ทุกเจ้าใน `LLM_PROVIDER` ล้มเหลว (key ผิด, เครดิตหมด ฯลฯ) ดูรายละเอียดใน log ของ Actions |
| workflow ขึ้น `DATABASE_URL is not set` | เพิ่ม secret `DATABASE_URL` |
| ต่อ Supabase ไม่ได้ / `Network is unreachable` | ใช้ connection string แบบ Session pooler ไม่ใช่ `db.<ref>.supabase.co` |
| การ์ดมาช้ากว่าที่ตั้งไว้ | cron ของ GitHub เลื่อนได้หลายชั่วโมง ถ้าต้องการให้ตรงเวลาจริง ๆ ให้รันบนเครื่องตัวเองด้วย `launchd` หรือ cron |
| บางช่องในการ์ดหายไป | Garmin ยังไม่ซิงก์ หรือนาฬิการุ่นนั้นไม่มีข้อมูลนั้น ระบบดึงซ้ำย้อนหลัง 3 วันให้อัตโนมัติ |
| Garmin endpoint error | `garminconnect` เป็น API ไม่เป็นทางการ ลอง `pip install -U garminconnect` ก่อน |
| ต้องรัน `setup_tokens.py` ใหม่ทุก 1–2 วัน | token ที่ refresh ใน CI ไม่ถูกบันทึกกลับเข้า secret ถ้า Garmin ยกเลิก refresh token เก่า จะเกิดอาการนี้ |

## Security & Privacy

- **ไม่มี credential ใน repo**: token, API key, webhook และ connection string อยู่ใน GitHub Secrets หรือ `.env` ซึ่งอยู่ใน `.gitignore` เท่านั้น
- **ไม่เก็บรหัสผ่าน Garmin**: ระบบใช้ token เท่านั้น
- **ข้อมูลสุขภาพอยู่ใน Supabase** ซึ่งเปิด Row Level Security ไม่อยู่ใน repo และไฟล์ `.db` ถูก ignore ไว้
- **ไม่เก็บพิกัดละเอียด**: จุดเริ่มวิ่งถูกปัดเหลือประมาณ 1 km ก่อนบันทึก
- **log ของ Actions ไม่มีตัวเลขสุขภาพ** เพราะ log ของ repo public ทุกคนเปิดดูได้
- **ไม่เรียก Garmin ถี่**: หน่วง 0.4 วินาทีระหว่าง request และจำกัดไม่เกิน 250 request ต่อรอบ

## Credits

- [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) · Garmin Connect API แบบไม่เป็นทางการ
- [Open-Meteo](https://open-meteo.com) · ข้อมูลพยากรณ์อากาศและคุณภาพอากาศ (CC BY 4.0)
- [Anthropic Claude](https://www.anthropic.com) และ [OpenAI](https://openai.com) · สรุปและคำแนะนำ
- [Supabase](https://supabase.com) · ฐานข้อมูล

<div align="center">
<sub>Pacer ไม่มีความเกี่ยวข้องกับ Garmin Ltd. · ใช้กับบัญชีของตัวเองเท่านั้น</sub>
</div>
