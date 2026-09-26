# 🏃 Pacer — Garmin Daily Running Coach

ระบบที่รันวันละครั้งบน GitHub Actions ดึงข้อมูล **การวิ่ง** (road + trail) และข้อมูลการฟื้นตัว (การนอน, HRV, RHR, readiness) จาก Garmin Connect วิเคราะห์โหลดการซ้อม ความเสี่ยงบาดเจ็บ ความพร้อมสำหรับรายการแข่ง และสภาพอากาศช่วงเวลาที่จะออกวิ่ง (Open-Meteo) แล้วให้ AI (Claude หรือ ChatGPT) เขียนสรุปพร้อมคำแนะนำการวิ่งวันนี้เป็นภาษาไทย ส่งเข้า Discord ทุกเช้า 08:00 (เวลาไทย)

> ⚠️ ไม่ใช่คำแนะนำทางการแพทย์ ถ้ามีอาการป่วยหรือบาดเจ็บให้ปรึกษาแพทย์

## ภาพรวม

```
Garmin Connect ──► garmin_fetch.py ──► Supabase Postgres (หรือ SQLite data/garmin.db ถ้ารันบนเครื่องโดยไม่ตั้ง DATABASE_URL)
                                            │
                                            ▼
                     analysis.py (สถิติ road/trail, ACWR, การฟื้นตัว, flags, race phase)
                                            │
                                            ▼
                     summarize.py (Claude / OpenAI → ข้อความภาษาไทยแยกหัวข้อ, ถ้าล้มเหลวใช้ข้อความ rule-based)
                                            │
                                            ▼
                     discord_notify.py (การ์ดแยกหัวข้อ + webhook)
```

| ไฟล์ | หน้าที่ |
|---|---|
| `config.py` | อ่าน environment variable และตรวจรูปแบบ `RACES` |
| `garmin_fetch.py` | login ด้วย token, ดึงข้อมูล, กรองเฉพาะกิจกรรมวิ่ง |
| `weather.py` | พยากรณ์อากาศช่วงเวลาวิ่งจาก Open-Meteo: อุณหภูมิ, ความร้อนชื้น, ฝน/พายุ, PM2.5 |
| `storage.py` | บันทึก/อ่าน SQLite |
| `analysis.py` | คำนวณ metrics และ flags ทั้งหมด |
| `summarize.py` | เรียก Claude / OpenAI และข้อความสำรอง |
| `discord_notify.py` | สร้าง embed และส่ง webhook |
| `main.py` | ตัวควบคุมหลัก + CLI |
| `setup_tokens.py` | สร้าง token ของ Garmin (รันบนเครื่องตัวเองครั้งเดียว) |

## สิ่งที่ต้องมี

- Python 3.12 ขึ้นไป
- บัญชี Garmin Connect ที่ซิงก์ข้อมูลจากนาฬิกา
- API key อย่างน้อยหนึ่งเจ้า (ต้องเติมเครดิต API แยกจากแพ็กเกจแชต):
  - Anthropic ([console.anthropic.com](https://console.anthropic.com)) หรือ
  - OpenAI ([platform.openai.com](https://platform.openai.com)) — สมาชิก ChatGPT Plus ใช้กับ API ไม่ได้
- Discord webhook (Server Settings → Integrations → Webhooks → New Webhook → Copy Webhook URL)
- โปรเจกต์ [Supabase](https://supabase.com) (แพ็กเกจฟรีพอ) สำหรับเก็บข้อมูลย้อนหลัง
- GitHub repository แบบ **private** (ประวัติ git มีไฟล์ฐานข้อมูลสุขภาพเก่า `data/garmin.db`)

## ขั้นตอนติดตั้ง

### 1. ติดตั้งบนเครื่อง

```bash
git clone https://github.com/<you>/pacer.git
cd pacer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. สร้าง token ของ Garmin

```bash
python setup_tokens.py
```

- ใส่ email และรหัสผ่าน Garmin (รหัสผ่านไม่ถูกบันทึกที่ไหน)
- ถ้าเปิด MFA ไว้ ระบบจะถามรหัส MFA
- ผลลัพธ์:
  - `~/.garminconnect/garmin_tokens.json` สำหรับรันบนเครื่อง
  - `garmin_tokens.b64` สำหรับใส่ใน GitHub Secret

คัดลอกเนื้อหาไฟล์ `garmin_tokens.b64` (macOS: `pbcopy < garmin_tokens.b64`) แล้ว **ลบไฟล์ทิ้งทันที**

```bash
rm garmin_tokens.b64
```

> ไฟล์ token อยู่ใน `.gitignore` แล้ว แต่ห้าม commit เด็ดขาด token นี้เข้าถึงบัญชี Garmin ได้

### 3. ตั้งค่า Supabase

1. สร้างโปรเจกต์ใน Supabase แล้วกด **Connect** ด้านบน → เลือก **Session pooler** → คัดลอก connection string (`postgresql://postgres.<ref>:<password>@aws-...pooler.supabase.com:5432/postgres`) แล้วแทน `[YOUR-PASSWORD]` ด้วยรหัสผ่านฐานข้อมูล
   - ต้องใช้ **Session pooler** ไม่ใช่ Direct connection เพราะ Direct connection ของ Supabase ใช้ IPv6 อย่างเดียว ซึ่ง GitHub Actions ไม่รองรับ
2. ใส่ใน `.env` เป็น `DATABASE_URL=...`
3. ย้ายข้อมูลเก่าจาก `data/garmin.db` ขึ้น Supabase (รันซ้ำได้ ไม่ซ้ำข้อมูล):

```bash
python migrate_to_supabase.py
```

ระบบสร้างตารางให้เองและเปิด **Row Level Security** ทุกตาราง ข้อมูลจึงอ่านผ่าน API key สาธารณะของ Supabase (anon key) ไม่ได้ อ่านได้เฉพาะผ่าน connection string เท่านั้น ห้ามเผยแพร่ `DATABASE_URL`

### 4. ทดสอบบนเครื่อง (ต้องผ่านก่อนเปิด cron)

คัดลอกไฟล์ตัวอย่างแล้วใส่ค่าในไฟล์ `.env` (ไฟล์นี้อยู่ใน `.gitignore` ไม่ถูก commit และระบบจะอ่านให้อัตโนมัติ):

```bash
cp .env.example .env
# แก้ .env ใส่ DISCORD_WEBHOOK_URL, ANTHROPIC_API_KEY และ/หรือ OPENAI_API_KEY, RACES (ถ้ามี)

python main.py --dry-run
```

- ครั้งแรกจะดึงย้อนหลัง 42 วัน (ประมาณ 2–3 นาที เพราะหน่วงเวลาระหว่าง request)
- `--dry-run` จะพิมพ์ JSON ผลวิเคราะห์และข้อความสรุป **ไม่ส่ง Discord**
- รันครั้งถัดไปจะดึงแค่ 3 วันล่าสุด

ถ้าต้องการลองส่ง Discord จริงจากเครื่อง:

```bash
python main.py   # ต้องใส่ DISCORD_WEBHOOK_URL ใน .env แล้ว
```

### 5. ตั้งค่า GitHub

ไปที่ repo → **Settings → Secrets and variables → Actions**

**Secrets** (แท็บ Secrets):

| ชื่อ | ค่า |
|---|---|
| `GARMINTOKENS_BASE64` | เนื้อหาจาก `garmin_tokens.b64` |
| `ANTHROPIC_API_KEY` | API key ของ Anthropic (ถ้าใช้ Claude) |
| `OPENAI_API_KEY` | API key ของ OpenAI (ถ้าใช้ ChatGPT) |
| `DISCORD_WEBHOOK_URL` | URL ของ Discord webhook |
| `DATABASE_URL` | connection string ของ Supabase (Session pooler) — ถ้าไม่ใส่ workflow จะ fail |

**Variables** (แท็บ Variables, ไม่บังคับ):

| ชื่อ | ค่า |
|---|---|
| `RACES` | JSON รายการแข่ง (ดูด้านล่าง) |
| `LLM_PROVIDER` | ลำดับ AI ที่จะลอง เช่น `openai,anthropic` ค่าเริ่มต้น `anthropic` |
| `CLAUDE_MODEL` | ค่าเริ่มต้น `claude-sonnet-5` |
| `OPENAI_MODEL` | ค่าเริ่มต้น `gpt-5.4-mini` |
| `TRAIL_ELEV_THRESHOLD` | ค่าเริ่มต้น `20` (m/km) |
| `SEND_DEADLINE` | ค่าเริ่มต้น `06:45` เวลาไทย |
| `WEATHER_LAT` / `WEATHER_LON` | พิกัดที่ใช้ดูพยากรณ์อากาศ ถ้าไม่ตั้ง ใช้จุดเริ่มของการวิ่งกลางแจ้งครั้งล่าสุด |
| `RUN_TIME` | เวลาออกวิ่ง ค่าเริ่มต้น `07:00` |


### 6. ทดสอบ workflow

ไปที่แท็บ **Actions → Garmin daily running coach → Run workflow** ถ้าผ่าน จะมีข้อความใน Discord และข้อมูลใหม่ในตารางของ Supabase (การกดรันเองจะใส่ `--force` ให้อัตโนมัติ จึงส่งเสมอแม้วันนั้นส่งไปแล้ว)

### เวลาส่งข้อความ

cron ของ GitHub **ไม่รับประกันเวลา** ของจริงที่เจอคือตั้ง 08:00 แล้วรัน 12:53 และอีกวันข้ามรอบเช้าทิ้งไป 3 รอบ ระบบจึงตั้งให้รันหลายรอบทุก 30 นาทีช่วง **02:00–06:45 (เวลาไทย)** แล้วให้โปรแกรมตัดสินใจเองว่ารอบไหนควรส่ง

1. ถ้าวันนั้นส่งไปแล้ว จบทันที ไม่ส่งซ้ำและไม่เรียก Garmin
2. ถ้าข้อมูลการนอน/HRV ของคืนนั้นยังไม่ซิงก์เข้า Garmin (นาฬิกามักซิงก์ตอนคุณตื่น) จะรอรอบถัดไป รอบที่มาเช็กแบบนี้ดึงข้อมูลแค่วันเดียว
3. ถ้าข้อมูลมาแล้ว หรือถึงเวลา `SEND_DEADLINE` (ค่าเริ่มต้น 06:45) จะส่งทันทีด้วยข้อมูลเท่าที่มี

ผลคือข้อความจะถึงเร็วที่สุดเท่าที่ข้อมูลพร้อม และอย่างช้าที่สุดคือก่อน 07:00 ถ้าคุณเปลี่ยนเวลาออกวิ่ง ให้แก้ `SEND_DEADLINE` และช่วงเวลา cron ใน `.github/workflows/daily.yml` (cron ใช้เวลา UTC = เวลาไทย − 7 ชั่วโมง)

## รายการแข่ง (`RACES`)

```json
[
  {"name": "Uthai Trail 2026", "type": "trail", "date": "2026-12-05", "distance_km": 50, "elevation_m": 2500, "priority": "A"},
  {"name": "Bangkok Marathon", "type": "road", "date": "2026-11-15", "distance_km": 42.195, "target_time": "04:00:00", "priority": "B"}
]
```

| key | จำเป็น | ค่า |
|---|---|---|
| `name` | ✅ | ชื่อรายการ |
| `type` | ✅ | `road`, `trail` หรือ `mixed` |
| `date` | ✅ | `YYYY-MM-DD` |
| `distance_km` | | ระยะ (km) |
| `elevation_m` | | D+ รวม (m) สำหรับ trail/mixed |
| `target_time` | | `HH:MM:SS` สำหรับ road/mixed |
| `priority` | | `A` (เป้าหมายหลัก), `B`, `C` ค่าเริ่มต้น `B` |

- รายการที่เขียนผิดจะถูกข้ามพร้อม warning ใน log ไม่ทำให้ระบบล่ม
- Phase การซ้อม (Base → Build → Peak → Taper → Race day → Recovery) คิดจากรายการ **A ที่ใกล้ที่สุด**
- รายการ B/C ที่เหลือ ≤ 7 วันจะได้คำแนะนำ mini taper และหลังแข่ง 7 วันจะแนะนำลดโหลด

## สภาพอากาศ

ทุกเช้าระบบดูพยากรณ์รายชั่วโมงจาก [Open-Meteo](https://open-meteo.com) (ฟรี ไม่ต้องสมัคร ไม่ต้องใช้ API key) ช่วง `RUN_TIME` ถึง 2 ชั่วโมงถัดไป (ค่าเริ่มต้น 07:00–09:00) แล้วแสดงในการ์ด:

- อุณหภูมิ, อุณหภูมิที่รู้สึก, สภาพอากาศ, ลม
- **ความร้อนชื้น**: คิดจากอุณหภูมิ + จุดน้ำค้าง (°F) แล้วบอกว่า pace จะช้าลงประมาณกี่ % เพื่อไม่ให้ไล่ pace ปกติในวันที่ร้อนชื้น
- โอกาสฝน / ปริมาณฝน และ **PM2.5** ตามเกณฑ์กรมควบคุมมลพิษ

ถ้าร้อนชื้นมาก, มีพายุฝนฟ้าคะนอง หรือ PM2.5 เกิน 75 µg/m³ จะขึ้นคำเตือนสีเหลือง และ AI จะปรับคำแนะนำการซ้อม (ลดความหนัก, เลี่ยงเส้นทางโล่ง, ย้ายเข้าลู่) — อากาศไม่เคยทำให้สถานะเป็นสีแดง เพราะสีแดงหมายถึงร่างกายต้องพัก

ตำแหน่ง: ตั้ง `WEATHER_LAT` / `WEATHER_LON` ใน Variables (แนะนำ) ถ้าไม่ตั้ง ระบบใช้จุดเริ่มของการวิ่งกลางแจ้งครั้งล่าสุด (เก็บพิกัดแบบปัดเหลือ ~1 km) ถ้าไม่มีทั้งสองอย่างจะข้ามส่วนอากาศ และถ้า Open-Meteo ล่ม การ์ดจะส่งตามปกติโดยไม่มีส่วนนี้

## คำสั่ง CLI

```bash
python main.py                    # รันปกติ ส่ง Discord
python main.py --dry-run          # พิมพ์ผล ไม่ส่ง Discord
python main.py --backfill 30      # ดึงย้อนหลัง 30 วัน
python main.py --date 2026-09-01  # วิเคราะห์เสมือนวันนั้นเป็นวันนี้
python main.py --force            # ส่งทันที ข้ามเงื่อนไขส่งวันละครั้ง
```

## Environment variables ทั้งหมด

| ตัวแปร | ค่าเริ่มต้น | หมายเหตุ |
|---|---|---|
| `GARMINTOKENS_BASE64` | – | token สำหรับ CI |
| `GARMINTOKENS` | `~/.garminconnect` | โฟลเดอร์ token สำหรับรันบนเครื่อง |
| `LLM_PROVIDER` | `anthropic` | ลำดับ AI ที่จะลอง คั่นด้วย comma (ข้ามเจ้าที่ไม่มี key) |
| `ANTHROPIC_API_KEY` | – | |
| `OPENAI_API_KEY` | – | |
| `DISCORD_WEBHOOK_URL` | – | ไม่ต้องใช้ตอน `--dry-run` |
| `CLAUDE_MODEL` | `claude-sonnet-5` | |
| `OPENAI_MODEL` | `gpt-5.4-mini` | |
| `RACES` | `[]` | |
| `TRAIL_ELEV_THRESHOLD` | `20` | m/km ที่ทำให้ `ultra_run` ถูกนับเป็น trail |
| `TZ_NAME` | `Asia/Bangkok` | |
| `SEND_DEADLINE` | `06:45` | เวลาท้องถิ่นที่ต้องส่งให้ได้ แม้ข้อมูลการนอนยังไม่เข้า |
| `DATABASE_URL` | – | Supabase Postgres (Session pooler) ถ้าไม่ตั้งจะใช้ SQLite |
| `DB_PATH` | `data/garmin.db` | ใช้เมื่อไม่มี `DATABASE_URL` |
| `BACKFILL_DAYS` | `42` | ใช้เมื่อฐานข้อมูลว่าง |
| `REFRESH_DAYS` | `3` | ดึงซ้ำย้อนหลังเผื่อซิงก์ช้า |
| `WEATHER_LAT` / `WEATHER_LON` | – | พิกัดพยากรณ์อากาศ (ไม่ตั้ง = จุดเริ่มการวิ่งกลางแจ้งล่าสุด) |
| `RUN_TIME` | `07:00` | เวลาออกวิ่ง ใช้ดูอากาศชั่วโมงนั้น + 2 ชั่วโมงถัดไป |

## รันเทสต์

เทสต์ใช้ข้อมูลจำลอง ไม่ต้องต่อเน็ตและไม่ต้องติดตั้ง dependency

```bash
python -m unittest discover -s tests -t .
```

เทสต์ฝั่ง Postgres จะข้ามไปถ้าไม่ตั้ง `TEST_DATABASE_URL` ใช้ Postgres ชั่วคราวใน Docker (**ห้ามชี้ไป Supabase จริง เพราะเทสต์ลบตาราง**):

```bash
docker run -d --rm --name pacer-pg-test -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:17-alpine
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres python -m unittest discover -s tests -t .
docker stop pacer-pg-test
```

## แก้ปัญหา

| อาการ | วิธีแก้ |
|---|---|
| Discord แจ้ง "Garmin login ล้มเหลว" | token หมดอายุหรือถูกยกเลิก รัน `python setup_tokens.py` ใหม่ แล้วอัปเดต Secret `GARMINTOKENS_BASE64` |
| ข้อความใน Discord เขียนว่า "ไม่ได้ใช้ AI" | AI ทุกเจ้าใน `LLM_PROVIDER` ล้มเหลว (key ผิด, เครดิตหมด ฯลฯ) ดู log ใน Actions |
| ข้อความมาช้ากว่าที่ตั้งไว้ | cron ของ GitHub เลื่อนได้หลายชั่วโมง ระบบจึงรันหลายรอบและส่งทันทีที่ข้อมูลพร้อม ถ้าต้องการตรงเวลาจริง ๆ ต้องย้ายไปรันบนเครื่องตัวเองด้วย `launchd` หรือ cron |
| ข้อมูลบางค่าเป็น `–` | Garmin ยังไม่ซิงก์หรือนาฬิการุ่นนั้นไม่มีข้อมูล ระบบดึงซ้ำย้อนหลัง 3 วันให้อัตโนมัติ |
| Garmin endpoint error | `garminconnect` เป็น API ไม่เป็นทางการ ลองอัปเดต `pip install -U garminconnect` ก่อน |
| workflow ขึ้น `DATABASE_URL is not set` | เพิ่ม secret `DATABASE_URL` |
| ต่อ Supabase ไม่ได้ / `Network is unreachable` | ใช้ connection string แบบ **Session pooler** (host `...pooler.supabase.com`) ไม่ใช่ `db.<ref>.supabase.co` |
| ต้องรัน `setup_tokens.py` ใหม่บ่อยผิดปกติ (เช่น ทุก 1–2 วัน) | ใน CI token ที่ refresh แล้วไม่ถูกบันทึกกลับเข้า Secret ถ้า Garmin ยกเลิก refresh token เก่าหลังหมุนใหม่ จะเกิดอาการนี้ ต้องเพิ่มขั้นตอนอัปเดต Secret อัตโนมัติ |

## ความปลอดภัย

- ห้าม commit token, API key หรือ webhook URL — ทุกอย่างอยู่ใน GitHub Secrets
- repo ต้องเป็น **private** เสมอ
- ระบบไม่เก็บรหัสผ่าน Garmin ใช้เฉพาะ token
- ระบบดึงข้อมูลวันละครั้ง หน่วง 0.4 วินาทีระหว่าง request และจำกัดไม่เกิน 250 request ต่อรอบ
