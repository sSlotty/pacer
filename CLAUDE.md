# Garmin Daily Running Coach — Agent Instructions

ไฟล์นี้คือสเปกและกฎการทำงานสำหรับ agent ที่พัฒนาและดูแลโปรเจกต์นี้ อ่านให้จบก่อนเริ่มงานทุกครั้ง และปฏิบัติตามอย่างเคร่งครัด ถ้าสเปกขัดกับสิ่งที่พบจริง (เช่น field ของ Garmin เปลี่ยน) ให้แก้โค้ดตามความจริงแล้วอัปเดตไฟล์นี้ให้ตรงกัน

## 1. เป้าหมายและขอบเขต

ระบบ Python ที่รันวันละครั้งอัตโนมัติ สำหรับ **การวิ่งเท่านั้น ทั้ง road run และ trail run** เพื่อ

1. ดึงข้อมูลการวิ่งและข้อมูลการฟื้นตัวจาก Garmin Connect ของเจ้าของ (Oat)
2. เก็บข้อมูลย้อนหลังไว้ดูแนวโน้ม
3. วิเคราะห์ 4 ด้าน: สถิติการวิ่ง (แยก road / trail), การฟื้นตัว (นอน/HRV/RHR), training load และความเสี่ยงบาดเจ็บ, การเตรียมตัวแข่ง
4. ให้ LLM (Claude API เป็นค่าเริ่มต้น หรือ OpenAI) เขียนสรุปและคำแนะนำการวิ่งเป็นภาษาไทย
5. ส่งสรุปเข้า Discord ผ่าน webhook

**นอกขอบเขต**: กิจกรรมที่ไม่ใช่การวิ่ง (ปั่น ว่าย เวท เดิน เดินป่า ฯลฯ) ไม่ต้องดึง ไม่ต้องเก็บ ไม่ต้องวิเคราะห์ ห้ามเพิ่มการรองรับกีฬาอื่นเว้นแต่เจ้าของสั่ง

สแต็กต้องสอดคล้องกับระบบแจ้งเตือนข่าวที่ Oat มีอยู่แล้ว: Python + GitHub Actions + Claude API + Discord (รองรับ OpenAI เป็นทางเลือกเสริมตามที่เจ้าของสั่ง ดูหัวข้อ 9)

## 2. กฎที่ห้ามละเมิด

- **ห้าม commit credential ใด ๆ** (รหัสผ่าน Garmin, token, API key, webhook URL) ทุกอย่างต้องมาจาก environment variable / GitHub Secrets เท่านั้น เพิ่ม `garmin_tokens.b64`, `.env`, `~/.garminconnect` ใน `.gitignore`
- **repo ต้องเป็น private** เพราะมีการ commit ไฟล์ฐานข้อมูลสุขภาพ (`data/garmin.db`) ถ้าตรวจพบว่า repo เป็น public ให้หยุดและแจ้งเจ้าของ
- **ห้ามเก็บรหัสผ่าน Garmin ใน CI** ใช้ token แบบ base64 (`GARMINTOKENS_BASE64`) ที่สร้างจากเครื่องเจ้าของเท่านั้น
- **ห้ามเรียก Garmin ถี่** รันได้ไม่เกินวันละ ~6 รอบ (ดูหัวข้อ 11.1) แต่ละรอบดึงไม่กี่วัน, ใส่ `time.sleep(0.3–0.5)` ระหว่าง request, ห้ามเกิน ~250 request ต่อรอบ และรวมทั้งวันไม่ควรเกิน ~150 request ดังนั้น `REFRESH_DAYS` ต้องน้อย (ค่าเริ่มต้น 3)
- **การ fetch ต้องทนความล้มเหลว**: endpoint ใดพังให้ log warning แล้วใช้ `None` ห้ามให้ทั้งระบบล่ม
- **LLM ห้ามแต่งตัวเลข**: ทุกตัวเลขในข้อความสรุปต้องมาจาก JSON ที่ส่งไปเท่านั้น
- **ถ้า LLM ทุกเจ้าล้มเหลว ต้องยังส่ง Discord ได้** ด้วยข้อความสรุปแบบ rule-based (fallback)
- ไม่ใช่คำแนะนำทางการแพทย์: ถ้า RHR สูงผิดปกติร่วมกับ HRV ต่ำ ให้แนะนำพัก และถ้ามีอาการป่วยหรือเจ็บให้ปรึกษาแพทย์

## 3. โครงสร้างไฟล์

```
garmin-daily/
├── CLAUDE.md                  # ไฟล์นี้
├── README.md                  # คู่มือติดตั้งภาษาไทยสำหรับคน
├── requirements.txt
├── .gitignore
├── config.py                  # อ่าน env ทั้งหมด
├── garmin_fetch.py            # login + ดึงข้อมูล + map field + กรองเฉพาะการวิ่ง
├── storage.py                 # SQLite upsert/query
├── analysis.py                # คำนวณ metrics และ flags (stdlib only)
├── summarize.py               # เรียก LLM (Claude / OpenAI) + fallback
├── discord_notify.py          # สร้าง embed + ส่ง webhook
├── main.py                    # orchestrator + CLI
├── setup_tokens.py            # รันบนเครื่องเจ้าของครั้งเดียว (รองรับ MFA)
├── tests/test_analysis.py     # ทดสอบ analysis + RACES ด้วยข้อมูลจำลอง ไม่ต้องใช้เน็ต
├── tests/test_pipeline.py     # ทดสอบ fetch (fake API), storage, embed, fallback, main
├── data/garmin.db             # สร้างอัตโนมัติ, commit โดย workflow
└── .github/workflows/daily.yml
```

`analysis.py` และ `storage.py` ต้องใช้ stdlib เท่านั้น เพื่อให้ทดสอบได้โดยไม่ต้องติดตั้ง dependency ส่วน `requests`, `anthropic`, `garminconnect` ให้ import ภายในฟังก์ชันหรือเฉพาะในโมดูลที่ใช้

## 4. Configuration (environment variables)

| ตัวแปร | จำเป็น | ค่าเริ่มต้น | หมายเหตุ |
|---|---|---|---|
| `GARMINTOKENS_BASE64` | ใน CI | – | ผลจาก `setup_tokens.py` เก็บใน Secrets |
| `GARMINTOKENS` | ไม่ | `~/.garminconnect` | โฟลเดอร์ token สำหรับรันบนเครื่อง |
| `LLM_PROVIDER` | ไม่ | `anthropic` | ลำดับผู้ให้บริการที่จะลอง คั่นด้วย comma เช่น `openai,anthropic` ข้ามเจ้าที่ไม่มี API key |
| `ANTHROPIC_API_KEY` | ถ้าใช้ Claude | – | Secrets |
| `OPENAI_API_KEY` | ถ้าใช้ OpenAI | – | Secrets |
| `DISCORD_WEBHOOK_URL` | ใช่ | – | Secrets |
| `CLAUDE_MODEL` | ไม่ | `claude-sonnet-5` | |
| `OPENAI_MODEL` | ไม่ | `gpt-5.4-mini` | |
| `RACES` | ไม่ | `[]` | JSON list ของรายการแข่ง (ดูหัวข้อ 4.1) เก็บใน Variables ถ้าว่างให้ข้ามส่วน race |
| `TRAIL_ELEV_THRESHOLD` | ไม่ | `20` | m/km สำหรับจัดประเภท ultra_run (ดูหัวข้อ 8) |
| `TZ_NAME` | ไม่ | `Asia/Bangkok` | ใช้กำหนด "วันนี้" |
| `SEND_DEADLINE` | ไม่ | `06:45` | เวลาท้องถิ่นที่ต้องส่งให้ได้ แม้ข้อมูลการนอนยังไม่เข้า (ดูหัวข้อ 11.1) |
| `DB_PATH` | ไม่ | `data/garmin.db` | |
| `BACKFILL_DAYS` | ไม่ | `42` | ใช้เมื่อ DB ว่าง |
| `REFRESH_DAYS` | ไม่ | `3` | ดึงซ้ำย้อนหลังเผื่อ sync ช้า |

ตอนรันบนเครื่อง `config.py` อ่านไฟล์ `.env` (git-ignored, ตัวอย่างใน `.env.example`) ด้วย loader แบบ stdlib โดยไม่ทับ env ที่ตั้งไว้แล้ว ใน CI ไม่มีไฟล์นี้

### 4.1 รูปแบบ `RACES`

```json
[
  {
    "name": "Uthai Trail 2026",
    "type": "trail",
    "date": "YYYY-MM-DD",
    "distance_km": 50,
    "elevation_m": 2500,
    "priority": "A"
  },
  {
    "name": "Bangkok Marathon",
    "type": "road",
    "date": "YYYY-MM-DD",
    "distance_km": 42.195,
    "target_time": "04:00:00",
    "priority": "B"
  }
]
```

| key | จำเป็น | ค่าที่รับ |
|---|---|---|
| `name` | ใช่ | ข้อความ |
| `type` | ใช่ | `road`, `trail` หรือ `mixed` (สนามผสมถนนกับเทรล) |
| `date` | ใช่ | `YYYY-MM-DD` |
| `distance_km` | ไม่ | ตัวเลข |
| `elevation_m` | ไม่ | ตัวเลข ใช้กับ `trail` และ `mixed` |
| `target_time` | ไม่ | `HH:MM:SS` ใช้กับ `road` และ `mixed` |
| `priority` | ไม่ | `A` (เป้าหมายหลัก), `B`, `C` ค่าเริ่มต้น `B` |

`config.py` ต้อง validate ทีละรายการ: รายการที่ JSON ผิด, ขาด key จำเป็น หรือค่าไม่ถูกต้อง ให้ log warning แล้วข้ามเฉพาะรายการนั้น ห้ามทำให้ระบบล่ม ถ้า JSON ทั้งก้อน parse ไม่ได้ให้ถือว่าไม่มีรายการแข่ง เรียงรายการตาม `date`

## 5. การยืนยันตัวตนกับ Garmin

ใช้ไลบรารี unofficial `garminconnect` เพราะ Garmin ไม่มี public API สำหรับผู้ใช้ทั่วไป

> ตั้งแต่ `garminconnect` 0.3.x ไลบรารี **ไม่ได้ใช้ `garth` แล้ว** token เป็น JSON (`di_token`, `di_refresh_token`, `di_client_id`) จัดการผ่าน `api.client.dump(path)` / `api.client.dumps()` / `api.client.loads(json)` และ `api.login(tokenstore)` แยก inline token จาก path ด้วยการดูว่าขึ้นต้นด้วย `{` หรือไม่ (ไม่ใช่ความยาว > 512 แบบเดิม)

**`setup_tokens.py`** (รันบนเครื่องเจ้าของ):
1. รับ email ด้วย `input()` และรหัสผ่านด้วย `getpass`
2. `api = Garmin(email, password, return_on_mfa=True)` แล้ว `r1, r2 = api.login()`
3. ถ้า `r1 == "needs_mfa"` ให้ถามรหัส MFA แล้ว `api.resume_login(r2, code)`
4. บันทึก `api.client.dump("~/.garminconnect")` สำหรับรัน local (ได้ไฟล์ `~/.garminconnect/garmin_tokens.json`)
5. เขียน `base64(api.client.dumps())` ลง `garmin_tokens.b64` และบอกให้ผู้ใช้คัดลอกไปใส่ Secret `GARMINTOKENS_BASE64` แล้วลบไฟล์ทิ้ง (เข้ารหัส base64 เพื่อให้เป็นบรรทัดเดียว วางใน Secret ได้ไม่เพี้ยน)

**Login ในระบบ**: `api = Garmin()` แล้ว `api.login(tokenstore)` โดย tokenstore เป็น JSON ที่ decode จาก `GARMINTOKENS_BASE64` ถ้ามี ไม่เช่นนั้นใช้ path โฟลเดอร์ (`GARMINTOKENS` หรือ `~/.garminconnect`) ไลบรารี refresh access token เองด้วย refresh token ถ้า login ล้มเหลวด้วย auth error (หรือ token ใช้ไม่ได้) ให้ส่งข้อความเตือนเข้า Discord ว่า "ต้องรัน setup_tokens.py ใหม่" แล้ว exit code 1

## 6. ข้อมูลที่ดึง (garmin_fetch.py)

ทุกการเรียกผ่าน `_safe(fn, *args)` ที่จับ exception แล้วคืน `None` และใช้ helper `_g(obj, *keys)` สำหรับอ่าน nested dict อย่างปลอดภัย

### 6.1 ข้อมูลฟื้นตัวรายวัน `fetch_day(api, date) -> dict`

ดึงเฉพาะค่าที่เกี่ยวกับความพร้อมในการวิ่ง ไม่ดึง steps

| field ที่เก็บ | แหล่ง |
|---|---|
| `resting_hr` | `get_stats(d)["restingHeartRate"]` |
| `stress_avg` | `get_stats(d)["averageStressLevel"]` |
| `body_battery_high` / `_low` / `_wake` | `get_stats(d)` → `bodyBatteryHighestValue`, `bodyBatteryLowestValue`, `bodyBatteryAtWakeTime` |
| `sleep_seconds`, `deep_sleep_seconds`, `rem_sleep_seconds`, `awake_seconds` | `get_sleep_data(d)["dailySleepDTO"]` → `sleepTimeSeconds`, `deepSleepSeconds`, `remSleepSeconds`, `awakeSleepSeconds` |
| `sleep_score` | `dailySleepDTO.sleepScores.overall.value` |
| `hrv_last_night`, `hrv_weekly_avg`, `hrv_status` | `get_hrv_data(d)["hrvSummary"]` → `lastNightAvg`, `weeklyAvg`, `status` |
| `hrv_baseline_low` / `_high` | `hrvSummary.baseline` → `balancedLow`, `balancedUpper` |
| `readiness_score`, `readiness_level` | `get_training_readiness(d)` (อาจเป็น list ให้ใช้ตัวแรก) → `score`, `level` |
| `vo2max_running` | `get_max_metrics(d)[0].generic.vo2MaxPreciseValue` หรือ `vo2MaxValue` |

### 6.2 กิจกรรมวิ่ง `fetch_runs(api, start, end) -> list[dict]`

ดึงจาก `get_activities_by_date(start, end)` แล้ว **เก็บเฉพาะ typeKey ต่อไปนี้** ที่เหลือทิ้งทั้งหมด:

`running, street_running, track_running, treadmill_running, indoor_running, virtual_run, trail_running, ultra_run`

map เป็น: `activity_id` (`activityId`), `date` (10 ตัวแรกของ `startTimeLocal`), `start_time`, `name`, `type_key` (`activityType.typeKey`), `run_category` (`road` / `trail` ตามหัวข้อ 8.1), `distance_m`, `duration_s`, `moving_s` (`movingDuration`), `elev_gain_m` (`elevationGain`), `elev_loss_m` (`elevationLoss`), `avg_hr`, `max_hr`, `avg_cadence` (`averageRunningCadenceInStepsPerMinute`), `training_load` (`activityTrainingLoad`), `aerobic_te`, `anaerobic_te` ตัดรายการที่ไม่มี `activity_id` หรือ `distance_m` เป็น 0 ทิ้ง

## 7. Storage (storage.py)

SQLite สามตาราง: `daily_metrics` (PK `date`), `runs` (PK `activity_id`) คอลัมน์ตาม field ในหัวข้อ 6 และ `notifications` (PK `date`, คอลัมน์ `sent_at`, `source`, `status`) สำหรับกันส่งซ้ำตามหัวข้อ 11.1 พร้อมเมธอด `was_sent(date)` และ `mark_sent(date, source, status)`

Upsert ด้วย `INSERT ... ON CONFLICT DO UPDATE SET col = COALESCE(excluded.col, col)` เพื่อไม่ให้ค่า `None` จากการดึงรอบหลังทับค่าที่มีอยู่ มีเมธอด `is_empty()`, `daily_since(date)`, `runs_since(date)` คืนค่าเป็น list ของ dict

## 8. การวิเคราะห์ (analysis.py)

`analyze(daily, runs, today, races=None, trail_elev_threshold=20.0) -> dict` ต้องเป็น pure function (`races` คือ list ที่ผ่าน `config.parse_races()` แล้ว, `run_category` ถูกคำนวณใหม่ตาม threshold ทุกครั้ง) ผลลัพธ์ต้อง serialize เป็น JSON ได้ และทุกค่าที่คำนวณไม่ได้ให้เป็น `None`

**หลักการเรื่องวันที่**: ข้อมูลฟื้นตัว (sleep, HRV, readiness, body battery ตอนตื่น) ใช้ของ **วันนี้** เพราะเป็นคืนที่เพิ่งผ่านไป ส่วนข้อมูลที่สะสมทั้งวัน (stress, RHR) ใช้ของ **เมื่อวาน** เพราะของวันนี้ยังไม่ครบ

### 8.1 การจัดประเภท road / trail

- `trail_running` → `trail`
- `ultra_run` → `trail` ถ้า `elev_gain_m / km ≥ TRAIL_ELEV_THRESHOLD` ไม่เช่นนั้น `road`
- ประเภทอื่นทั้งหมดในรายการ 6.2 → `road`
- `treadmill_running`, `indoor_running`, `virtual_run` นับเป็น road แต่ **ไม่นำไปคำนวณ pace เฉลี่ย** (ระยะจากลู่มักคลาดเคลื่อน)

### 8.2 สถิติการวิ่ง

หน้าต่าง 7 วันล่าสุด (today−6 ถึง today) เทียบกับ 7 วันก่อนหน้า คำนวณทั้ง **รวม**, **road** และ **trail**

**รวม**: ระยะ km, เวลา (ชม.), จำนวนครั้ง, % เปลี่ยนแปลงระยะเทียบสัปดาห์ก่อน, ระยะรวม 4 สัปดาห์ล่าสุดแยกรายสัปดาห์ (ใช้ดูความสม่ำเสมอ)

**Road**
- ระยะ km, จำนวนครั้ง
- pace เฉลี่ย (min/km จาก `moving_s` ถ้ามี ไม่เช่นนั้น `duration_s`) แสดงเป็น `m:ss`
- long run ที่ยาวที่สุดใน 7 และ 28 วัน พร้อม pace
- cadence เฉลี่ย (ถ้ามีข้อมูล)

**Trail**
- ระยะ km, D+ (m), D− (m), จำนวนครั้ง
- `effort_km = km + elev_gain_m / 100`
- vertical rate = D+ ต่อ km
- long run ที่ยาวที่สุดใน 7 และ 28 วัน (ระยะ + D+)

### 8.3 Training load (เฉพาะการวิ่ง)

- load รายวัน = ผลรวม `training_load` ของการวิ่งในวันนั้น ถ้ารายการไหนไม่มี ให้ใช้ `duration_s / 60` แทน
- `acute` = ค่าเฉลี่ย 7 วัน, `chronic` = ค่าเฉลี่ย 28 วัน, `acwr = acute / chronic` (ถ้า chronic = 0 ให้เป็น `None`)
- ถ้ามีข้อมูลไม่ถึง 21 วัน ให้ใส่ `load_data_insufficient: true`
- สัดส่วนความหนัก: % ของการวิ่งใน 7 วันที่ `anaerobic_te ≥ 2.0` (ใช้ดูว่าซ้อมหนักเกินไปหรือไม่)
- จำนวนวันพักใน 7 วันล่าสุด (วันที่ไม่มีการวิ่ง)

### 8.4 การฟื้นตัว

- HRV คืนล่าสุดเทียบ baseline ของ Garmin และค่าเฉลี่ย 7 วันจากข้อมูลตัวเอง
- RHR เมื่อวานเทียบค่าเฉลี่ย 14 วันก่อนหน้า
- การนอนคืนล่าสุด (ชม., deep, REM, score) และค่าเฉลี่ย 7 วัน
- Body battery ตอนตื่น, readiness score/level, VO2max การวิ่งล่าสุด

### 8.5 Flags

list ของ `{level: "red"|"yellow"|"info", message: <ภาษาไทย>}`

| เงื่อนไข | ระดับ |
|---|---|
| ACWR > 1.5 | red — เสี่ยงบาดเจ็บ |
| 1.3 < ACWR ≤ 1.5 | yellow |
| ACWR < 0.8 และไม่อยู่ในช่วง Taper / Recovery ของรายการใด ๆ | yellow — โหลดลดลงมาก |
| HRV คืนล่าสุด < `hrv_baseline_low` | yellow |
| RHR เมื่อวาน ≥ ค่าเฉลี่ย 14 วัน + 5 bpm | yellow (red ถ้าเกิดร่วมกับ HRV ต่ำ) |
| นอน < 6 ชม. | yellow |
| ค่าเฉลี่ยการนอน 7 วัน < 6.5 ชม. | yellow |
| ระยะวิ่งรวมสัปดาห์นี้เพิ่ม > 30% จากสัปดาห์ก่อน | yellow |
| D+ trail สัปดาห์นี้เพิ่ม > 30% จากสัปดาห์ก่อน (และสัปดาห์ก่อน > 0) | yellow — ระวังเข่าและน่อง |
| ไม่มีวันพักเลยใน 7 วัน | yellow |
| > 50% ของการวิ่งใน 7 วันมี `anaerobic_te ≥ 2.0` | yellow — ซ้อมหนักถี่เกินไป |
| readiness level เป็น `LOW` หรือ `POOR` | yellow |

สถานะรวม: มี red → `red`, มี yellow → `yellow`, ไม่มีเลย → `green`

### 8.6 การเตรียมตัวแข่ง (เมื่อ `RACES` ไม่ว่าง)

**รายการที่ใช้งาน** = รายการที่ `date ≥ today − 14` แสดงสูงสุด 3 รายการที่ใกล้ที่สุด

**Phase การซ้อมหลัก** คำนวณจาก **รายการ A ที่ใกล้ที่สุด** ถ้าไม่มีรายการ A ให้ใช้รายการที่ใกล้ที่สุด

| วันที่เหลือ | phase |
|---|---|
| > 70 | Base |
| 29–70 | Build |
| 15–28 | Peak |
| 1–14 | Taper |
| 0 | Race day |
| −1 ถึง −14 | Recovery |

**รายการ B / C** ไม่เปลี่ยน phase หลัก แต่ถ้าเหลือ ≤ 7 วัน ให้ใส่ `mini_taper: true` ในรายการนั้น และถ้าจบไปไม่เกิน 7 วันให้ใส่ `post_race_recovery: true` เพื่อให้ LLM แนะนำลดโหลดชั่วคราว

**กรณีรายการชนกัน**: ถ้ารายการ A สองรายการห่างกัน < 42 วัน ให้เพิ่ม flag `info` เตือนว่าเวลาฟื้นตัวระหว่างรายการน้อย

**ตัวชี้วัดความพร้อมรายรายการ** ตาม `type`

- **trail**: long run สูงสุดใน 28 วัน (ทุกประเภท) เป็น % ของ `distance_km`, D+ trail สัปดาห์นี้และ D+ สูงสุดในการวิ่งครั้งเดียวใน 28 วัน เป็น % ของ `elevation_m`
- **road**: long run สูงสุดใน 28 วันเป็น % ของ `distance_km`, target pace จาก `target_time / distance_km` เทียบกับ pace ของ road long run ล่าสุด (นิยาม: road run กลางแจ้งที่ยาวที่สุดใน 28 วัน ไม่รวมลู่วิ่ง ค่าบวก = ช้ากว่า target)
- **mixed**: คำนวณทั้งชุด trail และ road ตามค่าที่มี

ผลลัพธ์ใส่ใน `races: [...]` แต่ละรายการมี name, type, priority, date, days_to_race, readiness metrics, mini_taper / post_race_recovery และใส่ `training_phase` กับ `phase_race` ไว้ระดับบนสุด

### 8.7 Trend

แนบ list 7 วันล่าสุด (date, load, road_km, trail_km, trail_elev_m, hrv, sleep_h, rhr) ให้ LLM ใช้เป็นบริบท

## 9. สรุปด้วย LLM (summarize.py)

`summarize(result, providers) -> (sections, source)` ลองผู้ให้บริการตามลำดับใน `LLM_PROVIDER` (ข้ามเจ้าที่ไม่มี key) คืน dict หัวข้อตามตารางด้านล่าง และ `source` = ชื่อที่แสดงใน footer (`Claude` / `ChatGPT`) ถ้าล้มเหลวทุกเจ้าให้ raise แล้ว main ใช้ `fallback_sections()` ทั้งสองเจ้าใช้ system prompt และ JSON schema เดียวกัน

**OpenAI**: `openai.OpenAI().responses.create(model=OPENAI_MODEL, instructions=SYSTEM_PROMPT, input=..., max_output_tokens=4000, reasoning={"effort": "low"} (ถ้าโมเดลไม่รับให้ส่งซ้ำโดยไม่ใส่ เพราะ reasoning token คิดเงินเป็น output), text={"format": {"type": "json_schema", "name": "running_summary", "schema": ..., "strict": True}})` อ่านผลจาก `response.output_text` ถ้า `status` ไม่ใช่ `completed` ให้ถือว่าล้มเหลว (max_output_tokens สูงกว่า Claude เพราะรวม reasoning token)

**Claude**:

- ใช้ `anthropic.Anthropic().messages.create(model=CLAUDE_MODEL, max_tokens=2000, system=..., messages=[...], output_config={"format": {"type": "json_schema", ...}})` ส่งผล `analyze()` เป็น JSON (`ensure_ascii=False`)
- **System prompt เขียนเป็นภาษาอังกฤษ แต่สั่งให้ตอบเป็นภาษาไทย** เพราะภาษาไทยกิน token มากกว่า 2–4 เท่า (วัดจริง: prompt ไทยใช้ input ~3,868 token, อังกฤษ ~1,841 token ต่อครั้ง)
- **ย่อ payload ก่อนส่ง** ด้วย `_compact()`: ตัด field ที่ซ้ำหรือไม่ได้ใช้ (`prev_week_km`, `avg_pace_s_per_km`, `days_of_data`, ชื่อ/วันที่ของ long run ฯลฯ), ตัด key ที่เป็น None, ย่อ `trend_7d` เป็นตาราง CSV บรรทัดเดียวต่อวัน และ dump แบบไม่มีช่องว่าง (ลดขนาดลง ~47%)
  - ค่าใช้จ่ายเกือบทั้งหมดคือ **โทเคนขาออกภาษาไทย** (ภาษาไทยกิน token มากกว่าอังกฤษ 2–4 เท่าต่อตัวอักษร) จึงคุมความยาวแต่ละหัวข้อใน prompt และตั้ง max_tokens ไว้ที่ 2000
  - ทุกครั้งที่เรียกสำเร็จ ให้ log จำนวนโทเคนจาก `response.usage` (input / output / reasoning) เพื่อตรวจสอบกับ dashboard ของผู้ให้บริการได้
- ส่ง `thinking={"type": "disabled"}` (โมเดลรุ่นใหม่เปิด thinking เป็นค่าเริ่มต้น) ถ้าโมเดลตอบ 400 ที่เกี่ยวกับ thinking ให้ส่งซ้ำโดยไม่ใส่ `thinking`
- ถ้า `stop_reason` เป็น `refusal` หรือ `max_tokens`, JSON ไม่ถูกต้อง หรือไม่มีข้อความ ให้ถือว่าล้มเหลวและใช้ fallback
- **คำตอบเป็น JSON แยกตามหัวข้อ** (ทุก key เป็น string, `""` ถ้าไม่มีเนื้อหา) เพื่อจัดวางเป็นส่วน ๆ ใน embed เดียว ตัวเลขหลักแสดงใน fields แล้ว ข้อความจึงเน้นการตีความ ไม่ต้องซ้ำตัวเลขหรือ flags:

| key | เนื้อหา | ความยาวแนะนำ |
|---|---|---|
| `headline` | สรุปสถานะวันนี้ 1–2 ประโยค (ถ้า red ขึ้นต้นด้วย ⚠️) | ≤ 150 ตัวอักษร |
| `recovery` | การฟื้นตัว | ≤ 200 |
| `running` | การวิ่ง 7 วัน แยก road / trail เฉพาะประเภทที่มีการวิ่ง | ≤ 200 |
| `load` | โหลดและความเสี่ยง | ≤ 200 |
| `races` | race countdown ทุกรายการที่ใช้งาน (`""` ถ้าไม่มี) | ≤ 250 |
| `session` | ชื่อประเภทการซ้อมวันนี้สั้น ๆ: พัก / Easy run / Long run / Workout / Hill / Trail session | ≤ 40 |
| `recommendation` | รายละเอียดการซ้อมวันนี้ ระยะหรือเวลาโดยประมาณ และเหตุผล | ≤ 350 |

- System prompt ต้องกำหนดว่า:
  - เป็นโค้ชวิ่งที่เชี่ยวชาญทั้ง road และ trail เขียนภาษาไทย กระชับ เป็นกันเอง
  - คำแนะนำต้องสอดคล้องกับ `training_phase` และ `type` ของรายการที่กำหนด phase เช่น ช่วง Build ของรายการ trail ควรมี hill/vert, ช่วง Taper ลดระยะ และต้องคำนึงถึงรายการ B/C ที่อยู่ในช่วง mini taper หรือเพิ่งจบ
  - ถ้ามีหลายรายการ ให้เน้นรายการ A และพูดถึงรายการอื่นสั้น ๆ
  - ใช้เฉพาะตัวเลขใน JSON, ข้าม field ที่เป็น null, ห้ามเดา
  - ใช้ Discord markdown ได้ (ตัวหนา, bullet) ไม่ต้องใส่หัวข้อ เพราะการ์ดมีหัวข้ออยู่แล้ว
  - ถ้าสถานะ red ต้องขึ้นต้นด้วยคำเตือนและแนะนำลดหรือพัก
- `fallback_sections(result)` สร้าง dict รูปแบบเดียวกันจาก flags และตัวเลขหลักโดยไม่ใช้ LLM

## 10. Discord (discord_notify.py)

ส่ง **1 ข้อความ 1 embed แบบ dashboard** ผ่าน `requests.post(webhook, json={"username": "Pacer", "embeds": [embed]}, timeout=20)` สีของ embed ตามสถานะ: green `0x2ECC71`, yellow `0xF1C40F`, red `0xE74C3C`

**title**: `🏃 <วัน วันที่ เดือน ปี ภาษาไทย> · <ป้ายสถานะ>` (🟢 พร้อมซ้อม / 🟡 ซ้อมได้แต่ระวัง / 🔴 ควรพัก)

**description** (เรียงตามนี้ ข้ามส่วนที่ว่าง):
1. `headline`
2. flags เป็น block quote บรรทัดละรายการ (`> 🔴 ...`)
3. `**🎯 แนะนำวันนี้ · <session>**` + `recommendation`
4. `**🛌 การฟื้นตัว**` + `recovery`, `**🏃 การวิ่ง 7 วัน**` + `running`, `**📈 โหลด**` + `load`, `**🏁 รายการแข่ง**` + `races`

**fields** (ตัวเลขหลัก ไม่ซ้ำกับข้อความ):
- inline 3 คอลัมน์ แบ่งเป็นกลุ่ม: ฟื้นตัว (HRV, RHR, การนอน, Body battery, Readiness, VO2max) → วิ่ง (รวม 7 วัน, Road, Trail) → โหลด (ACWR พร้อมโซน, วันพัก, ซ้อมหนัก)
- **ซ่อนช่องที่ไม่มีข้อมูล** แทนการแสดง `–` (เช่น การนอนของคืนนี้ยังไม่ซิงก์ ให้แสดงค่าเฉลี่ย 7 วันแทนพร้อมบอกว่าเป็นค่าเฉลี่ย) และเติมช่องว่าง (`\u200b`) ให้แต่ละกลุ่มครบแถวละ 3 ช่อง เพื่อไม่ให้กลุ่มปนกัน
- Road / Trail ที่ไม่มีการวิ่งใน 7 วันให้ข้าม ถ้าไม่มีการวิ่งเลยให้ช่องรวมแสดง `ไม่มีการวิ่ง`
- ถ้ามีรายการแข่ง: field ต่อรายการ (สูงสุด 3) ชื่อ `<priority> <ชื่อ> · อีก N วัน` ค่า: type, ระยะ/D+, phase (เฉพาะรายการที่กำหนด phase), mini taper / ฟื้นตัวหลังแข่ง และแถบความพร้อม `▰▰▱▱`
- field สุดท้าย: ระยะรายสัปดาห์ 4 สัปดาห์เป็นแถบกราฟใน code block
- รูปแบบตัวเลข: km ทศนิยม 1 ตำแหน่ง, bpm ไม่มีทศนิยมถ้าเป็นจำนวนเต็ม, การเปลี่ยนแปลงใช้ลูกศร ▲/▼

**footer**: `Pacer · ข้อมูลจาก Garmin Connect · สรุปโดย <Claude|ChatGPT>` หรือ `ข้อความอัตโนมัติ (ไม่ได้ใช้ AI)` + timestamp

- ข้อจำกัด Discord: title ≤ 256, description ≤ 4,096, ≤ 25 fields, field name ≤ 256, value ≤ 1,024 และรวมทั้ง embed ≤ 6,000 ตัวอักษร (ถ้าเกินให้ตัด description)
- ตรวจ HTTP status ถ้าไม่ใช่ 2xx ให้ raise

## 11. Orchestrator (main.py)

CLI: `python main.py [--dry-run] [--backfill N] [--date YYYY-MM-DD] [--force]`

1. `today` = `--date` หรือวันนี้ตาม `TZ_NAME`
2. เปิด DB, login Garmin
3. จำนวนวันที่ดึง = `--backfill` ถ้าระบุ, ไม่เช่นนั้น `BACKFILL_DAYS` ถ้า DB ว่าง, ไม่เช่นนั้น `REFRESH_DAYS`
4. ดึง `fetch_runs` ทั้งช่วงแล้ว upsert ก่อน จากนั้นวนดึง `fetch_day` จากวันล่าสุดย้อนหลัง (sleep ระหว่างรอบ) แล้ว upsert — ลำดับนี้ทำให้ถ้าชนเพดาน 250 request ข้อมูลที่สำคัญที่สุดยังได้ครบ
   - ถ้า login ล้มเหลวด้วยเหตุอื่นที่ไม่ใช่ auth (เช่น เน็ต) ให้ log error แล้ววิเคราะห์จากข้อมูลที่มีใน DB และส่งสรุปตามปกติ
5. อ่านข้อมูล 42 วันจาก DB → `analyze()`
6. `summarize()` ถ้าล้มเหลวใช้ `fallback_sections()` และ log error
7. `--dry-run` ให้ print JSON และข้อความ ไม่ส่ง Discord, ไม่เช่นนั้นตรวจเงื่อนไขในหัวข้อ 11.1 แล้วส่ง Discord และบันทึกว่าส่งแล้ว

### 11.1 ส่งวันละครั้ง เมื่อข้อมูลพร้อม

เจ้าของออกวิ่งตอน 07:00 และ cron ของ GitHub เลื่อนเวลาได้หลายชั่วโมง จึงตั้ง workflow ให้รันหลายรอบในช่วงเช้าแล้วให้ `main.py` ตัดสินใจเองว่ารอบไหนควรส่ง:

1. ถ้าวันนี้ส่งไปแล้ว (ตาราง `notifications`) → จบทันที exit 0 ไม่ส่งซ้ำ ไม่ต้องเรียก Garmin
2. ดึงข้อมูล **เฉพาะวันนี้ 1 วัน** ก่อน (ประหยัด request เพราะรอบส่วนใหญ่เป็นแค่การมาเช็ก) ถ้ายังไม่มีข้อมูลการฟื้นตัวของวันนี้ (`sleep_seconds`, `hrv_last_night` และ `readiness_score` เป็น None ทั้งหมด) และเวลาท้องถิ่นยังไม่ถึง `SEND_DEADLINE` → ยังไม่ส่ง รอรอบถัดไป exit 0
3. ถ้าจะส่ง ให้ดึงเต็มช่วง `REFRESH_DAYS` (พร้อมกิจกรรมวิ่ง) แล้ววิเคราะห์ ส่ง Discord และบันทึกลง `notifications`
4. ถ้า login Garmin ไม่ได้ ให้ใช้ข้อมูลใน DB ตัดสินตามเงื่อนไขข้อ 2 เหมือนกัน

`--force` ข้ามเงื่อนไขทั้งหมด (ใช้กับ `workflow_dispatch` และการทดสอบ) ส่วน `--date` ที่ระบุเองถือว่าเป็นการสั่งด้วยมือ ให้ข้ามเงื่อนไขข้อ 2 แต่ยังกันส่งซ้ำ
8. ใช้ `logging` ระดับ INFO, ห้าม log token หรือ webhook URL

## 12. GitHub Actions (.github/workflows/daily.yml)

- trigger: `schedule: cron "0,30 19-23 * * *"` และ `"45 23 * * *"` (02:00–06:45 เวลาไทย รวม 11 รอบ) และ `workflow_dispatch` (ใส่ `--force`)
  - **cron ของ GitHub ไม่ตรงเวลาและรับประกันไม่ได้** วัดจริง: ตั้ง 08:00 รันจริง 12:53 และอีกวันตั้ง 5 รอบช่วง 05:00–06:45 GitHub ข้าม 3 รอบแรกแล้วรัน 07:39 กับ 08:53
  - วิธีรับมือคือรันหลายรอบกระจายทั้งเช้า + เงื่อนไขในหัวข้อ 11.1 (ส่งครั้งเดียว รอบแรกที่ข้อมูลพร้อม) ไม่ใช่การตั้งเวลาให้แม่นขึ้น
  - รอบที่ไม่ได้ส่งใช้เวลา ~1 นาทีและยิง Garmin แค่ 5 request รวมทั้งวันประมาณ 86 request และ ~15 นาทีของโควตา Actions
- `permissions: contents: write`, `concurrency: garmin-daily`
- ขั้นตอน: `actions/checkout@v7` → `actions/setup-python@v7` (3.12, cache pip) — ใช้ major เวอร์ชันที่รันบน Node 24 เพื่อไม่ให้เจอ deprecation warning → `pip install -r requirements.txt` → `python main.py` → commit `data/garmin.db` กลับ repo ด้วยชื่อ `github-actions[bot]` เฉพาะเมื่อมีการเปลี่ยนแปลง (`git diff --cached --quiet || git commit`)
- secrets: `GARMINTOKENS_BASE64`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (ถ้าใช้), `DISCORD_WEBHOOK_URL`
- variables: `RACES` (JSON ตามหัวข้อ 4.1), และไม่บังคับ `LLM_PROVIDER`, `CLAUDE_MODEL`, `OPENAI_MODEL`, `TRAIL_ELEV_THRESHOLD`
- ขั้น commit DB ใช้ `if: always()` เพื่อเก็บข้อมูลที่ดึงมาแล้วแม้ส่ง Discord ไม่สำเร็จ และ push เฉพาะเมื่อมี commit ใหม่

## 13. Dependencies

```
garminconnect>=0.3.16   # 0.3.x ไม่ใช้ garth แล้ว ดูหัวข้อ 5
anthropic>=0.40
openai>=2.0
requests>=2.31
```

## 14. การทดสอบ

- `tests/test_analysis.py` สร้างข้อมูลจำลอง 42 วันที่มีทั้ง road, trail, treadmill และ ultra_run แล้วตรวจ:
  - การจัดประเภท road / trail ถูกต้อง รวมถึง ultra_run ทั้งสองกรณี
  - treadmill ไม่ถูกนำไปคำนวณ pace เฉลี่ย
  - ACWR และ effort_km คำนวณถูก
  - flags ออกตามเงื่อนไขในหัวข้อ 8.5
  - race phase ถูกทุกช่วง และคิดจากรายการ A ที่ใกล้ที่สุด (หรือรายการใกล้สุดถ้าไม่มี A)
  - รายการ B/C ได้ `mini_taper` และ `post_race_recovery` ถูกต้อง
  - ตัวชี้วัดความพร้อมถูกต้องทั้ง `road`, `trail` และ `mixed`
  - `RACES` ที่ JSON ผิด หรือมีบางรายการไม่ครบ ไม่ทำให้ crash และข้ามเฉพาะรายการที่ผิด
  - สัปดาห์ที่ไม่มีการวิ่งเลย และข้อมูลที่เป็น None ไม่ทำให้ crash
  - ผลลัพธ์ `json.dumps` ได้
- ทดสอบว่า `fetch_runs` กรองกิจกรรมที่ไม่ใช่การวิ่งทิ้ง (ใช้ fake response)
- ทดสอบ embed builder ว่าไม่เกินข้อจำกัดของ Discord
- ก่อนเปิดใช้ cron ต้องรัน `python main.py --dry-run` บนเครื่องให้ผ่านก่อน

## 15. Definition of Done

- [ ] `setup_tokens.py` ทำงานได้ทั้งแบบมีและไม่มี MFA
- [ ] `python main.py --dry-run` ครั้งแรก backfill 42 วันได้ และรอบถัดไปดึงแค่ 3 วัน
- [ ] DB มีเฉพาะกิจกรรมวิ่ง ไม่มีกีฬาอื่น
- [ ] สรุปแยก road และ trail ถูกต้อง
- [ ] endpoint ใดพังแล้วระบบยังส่งสรุปได้
- [ ] LLM พังแล้วยังได้ fallback message ใน Discord
- [ ] ไม่มี secret ใด ๆ อยู่ใน git history
- [ ] workflow รันผ่านด้วย `workflow_dispatch` และ commit DB กลับได้
- [ ] tests ผ่านทั้งหมด
- [ ] README.md ภาษาไทยอธิบายขั้นตอนติดตั้งครบ

## 16. ข้อควรรู้

- `garminconnect` เป็น unofficial API อาจพังเมื่อ Garmin เปลี่ยนระบบ ถ้าเจอ error ให้เช็กเวอร์ชันล่าสุดของไลบรารีก่อนแก้โค้ด
- ชื่อ field และ typeKey ใน response ของ Garmin อาจต่างกันตามรุ่นนาฬิกา ให้ใช้ `_g()` เสมอ ถ้าพบ typeKey การวิ่งที่ไม่อยู่ในรายการ 6.2 ให้ log warning และเพิ่มเข้าไปพร้อมอัปเดตไฟล์นี้
- ใน CI token ที่ไลบรารี refresh ระหว่างรัน (ได้ `di_refresh_token` ใหม่) **ไม่ถูกเขียนกลับ** เข้า Secret `GARMINTOKENS_BASE64` ถ้าพบว่า auth ล้มเหลวหลังรันได้ไม่กี่วัน แปลว่า Garmin ยกเลิก refresh token เก่า ต้องเพิ่มขั้นอัปเดต Secret อัตโนมัติ (ต้องใช้ PAT) — ยังไม่ได้ทำ
- ถ้าจะเพิ่มฟีเจอร์ใหม่ ให้อัปเดตไฟล์นี้ก่อนเขียนโค้ด
