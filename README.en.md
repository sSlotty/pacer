<div align="center">

<img src="assets/logo.svg" alt="Pacer" width="112" height="112">

# Pacer

**A personal running coach that reads your Garmin data and posts today's training plan to Discord every morning**

Road and trail · Recovery, training load, weather and race preparation · AI-written summary in Thai

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![GitHub Actions](https://img.shields.io/badge/Runs%20on-GitHub%20Actions-2088FF?logo=githubactions&logoColor=white)](.github/workflows/daily.yml)
[![AI](https://img.shields.io/badge/AI-Claude%20%7C%20ChatGPT-D97757?logo=anthropic&logoColor=white)](#configuration)
[![Supabase](https://img.shields.io/badge/Data-Supabase-3FCF8E?logo=supabase&logoColor=white)](https://supabase.com)
[![Discord](https://img.shields.io/badge/Notify-Discord-5865F2?logo=discord&logoColor=white)](https://discord.com)

[Features](#features) · [How It Works](#how-it-works) · [Getting Started](#getting-started) · [Configuration](#configuration) · [Troubleshooting](#troubleshooting)

[ภาษาไทย](README.md) · **English**

</div>

---

> [!NOTE]
> Pacer is not medical advice. If you are ill or injured, see a doctor.

## Features

| | |
|---|---|
| 🛌 **Recovery** | HRV against your baseline, resting HR against its 14-day average, sleep, Body Battery, Training Readiness and VO2max |
| 🏃 **Running stats** | Distance, time and pace over the last 7 days, split into road and trail, with D+, effort km and your longest run |
| 📈 **Load and injury risk** | ACWR (7-day load ÷ 28-day load), share of hard sessions, rest days and week-over-week jumps in volume |
| 🌦️ **Weather at run time** | Temperature, heat and humidity (with the expected pace slowdown), rain, thunderstorms and PM2.5 from [Open-Meteo](https://open-meteo.com) |
| 🏁 **Race preparation** | Countdown to several races, training phase (Base → Build → Peak → Taper), mini tapers for secondary races and readiness indicators |
| 🤖 **AI coaching** | Claude or ChatGPT writes the summary in Thai using only the computed numbers; if every AI provider fails, a rule-based message goes out instead |

### Discord Card

Each day is a single dashboard-style embed whose colour shows the day's status.

| Section | Content |
|---|---|
| Header | Date in Thai and status: 🟢 ready to train · 🟡 train with care · 🔴 rest |
| Warnings | Every condition that triggered, e.g. high ACWR, short sleep, a thunderstorm during your run |
| 🎯 Today's plan | Session type, approximate distance or duration, and the reason |
| Key numbers | Recovery → weather → running → load, in groups of three |
| Races | Days to go, training phase and a readiness bar `▰▰▰▱▱` |
| Weekly chart | Distance over the last 4 weeks |

## How It Works

```mermaid
flowchart LR
    G[Garmin Connect] -->|runs + recovery| F[garmin_fetch]
    W[Open-Meteo] -->|forecast + PM2.5| A
    F --> DB[(Supabase<br/>Postgres)]
    DB --> A[analysis<br/>metrics + flags]
    A --> S[summarize<br/>Claude / ChatGPT]
    S -. AI unavailable .-> R[rule-based<br/>fallback]
    S --> D[Discord card]
    R --> D
```

GitHub Actions runs every 30 minutes from **02:00 to 06:45** (Bangkok time) and posts the card **once a day**, on the first run after last night's sleep has synced to Garmin.

1. Already posted today → store any newly synced data, then stop
2. Sleep / HRV not synced yet → wait for the next run (this run fetches a single day only)
3. Data is ready, or `SEND_DEADLINE` (06:45) has passed → analyse and post with whatever is available

> [!TIP]
> Pacer runs many times because GitHub's cron can start hours late and sometimes skips runs entirely. If you run at a different time, change `SEND_DEADLINE`, `RUN_TIME` and the cron window in [`daily.yml`](.github/workflows/daily.yml) (cron uses UTC = Bangkok time − 7 h).

<details>
<summary><b>Project Structure</b></summary>

| File | Purpose |
|---|---|
| [`main.py`](main.py) | Orchestrator and CLI |
| [`garmin_fetch.py`](garmin_fetch.py) | Token login, data fetching, keeps running activities only |
| [`weather.py`](weather.py) | Forecast for the run window; assesses heat, rain and air quality |
| [`analysis.py`](analysis.py) | Metrics, flags and race phase (pure function, stdlib only) |
| [`summarize.py`](summarize.py) | Calls Claude / OpenAI and builds the fallback text |
| [`discord_notify.py`](discord_notify.py) | Builds the embed and posts the webhook |
| [`storage.py`](storage.py) | Supabase Postgres, or SQLite for local runs |
| [`config.py`](config.py) | Reads environment variables and validates `RACES` |
| [`setup_tokens.py`](setup_tokens.py) | Creates Garmin tokens (run once on your machine) |
| [`migrate_to_supabase.py`](migrate_to_supabase.py) | Copies SQLite history to Supabase |
| [`CLAUDE.md`](CLAUDE.md) | Full system specification (Thai) |

</details>

## Getting Started

### Prerequisites

- Python 3.12 or newer
- A Garmin Connect account synced from your watch
- An API key from [Anthropic](https://console.anthropic.com) or [OpenAI](https://platform.openai.com), or both (API credit is separate from chat subscriptions; ChatGPT Plus does not include API access)
- A Discord webhook (Server Settings → Integrations → Webhooks → New Webhook)
- A [Supabase](https://supabase.com) project (the free tier is enough)

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

Enter your Garmin email and password (MFA is supported). The password is never saved. You get two files:

- `~/.garminconnect/garmin_tokens.json` for local runs
- `garmin_tokens.b64` for the GitHub secret → copy its contents (`pbcopy < garmin_tokens.b64`) and **delete the file right away**

> [!WARNING]
> The token grants access to your Garmin account. Never commit or share it (the files are already in `.gitignore`).

### 3. Set Up Supabase

1. In your Supabase project, click **Connect** → choose **Session pooler** → copy the connection string and replace `[YOUR-PASSWORD]` with your database password
2. Add it to `.env` as `DATABASE_URL=...`

Pacer creates its tables on the first run and enables **Row Level Security** on each of them, so Supabase's public anon key cannot read your data.

> [!IMPORTANT]
> Use the **Session pooler** (host `…pooler.supabase.com`), not the direct connection: the direct connection is IPv6-only, and GitHub Actions has no IPv6. Percent-encode special characters in the password (e.g. `@` → `%40`).

If you used Pacer with SQLite before, copy your history up with `python migrate_to_supabase.py` (safe to re-run; rows are not duplicated).

### 4. Test Locally

Put your API key and webhook in `.env`, then run:

```bash
python main.py --dry-run
```

The first run fetches the last 42 days (about 2–3 minutes) and prints the analysis and summary **without posting to Discord**. Later runs fetch only the last 3 days.

### 5. Configure GitHub Actions

In your repo → **Settings → Secrets and variables → Actions**, add these secrets:

| Secret | Value |
|---|---|
| `GARMINTOKENS_BASE64` | Contents of `garmin_tokens.b64` |
| `DATABASE_URL` | Session pooler connection string |
| `DISCORD_WEBHOOK_URL` | Your Discord webhook URL |
| `ANTHROPIC_API_KEY` | If you use Claude |
| `OPENAI_API_KEY` | If you use ChatGPT |

Then open **Actions → Garmin daily running coach → Run workflow** to test it. A manual run always posts a card. If it succeeds, you will see the card in Discord and new rows in Supabase.

## Configuration

Set these as GitHub **Variables** (or in `.env` for local runs). All are optional.

| Variable | Default | Meaning |
|---|---|---|
| `RACES` | `[]` | Your races, see [format below](#races) |
| `LLM_PROVIDER` | `anthropic` | Order of AI providers to try, e.g. `openai,anthropic` (providers without a key are skipped) |
| `CLAUDE_MODEL` | `claude-sonnet-5` | |
| `OPENAI_MODEL` | `gpt-5.4-mini` | |
| `RUN_TIME` | `07:00` | When you start running; the forecast covers that hour and the next two |
| `SEND_DEADLINE` | `06:45` | Latest time to post, even if sleep data has not synced |
| `WEATHER_LAT` / `WEATHER_LON` | – | Forecast location; defaults to where your last outdoor run started |
| `TRAIL_ELEV_THRESHOLD` | `20` | m/km above which an `ultra_run` counts as trail |

<details>
<summary><b>Other Environment Variables</b></summary>

| Variable | Default | Notes |
|---|---|---|
| `GARMINTOKENS_BASE64` | – | Token for CI |
| `GARMINTOKENS` | `~/.garminconnect` | Token folder for local runs |
| `DATABASE_URL` | – | Supabase Postgres; falls back to SQLite at `DB_PATH` when unset |
| `DB_PATH` | `data/garmin.db` | SQLite file for local runs |
| `DISCORD_WEBHOOK_URL` | – | Not needed with `--dry-run` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | – | |
| `TZ_NAME` | `Asia/Bangkok` | Decides which date counts as "today" |
| `BACKFILL_DAYS` | `42` | Days fetched when the database is empty |
| `REFRESH_DAYS` | `3` | Days re-fetched each run in case Garmin syncs late |

</details>

### Races

```json
[
  {"name": "Uthai Trail", "type": "trail", "date": "2026-12-05", "distance_km": 50, "elevation_m": 2500, "priority": "A"},
  {"name": "Bangkok Marathon", "type": "road", "date": "2026-11-15", "distance_km": 42.195, "target_time": "04:00:00", "priority": "B"}
]
```

| Key | Required | Value |
|---|:---:|---|
| `name` | ✓ | Race name |
| `type` | ✓ | `road`, `trail` or `mixed` |
| `date` | ✓ | `YYYY-MM-DD` |
| `distance_km` | | Distance (km) |
| `elevation_m` | | Total D+ (m), for trail / mixed |
| `target_time` | | `HH:MM:SS`, for road / mixed |
| `priority` | | `A` main goal, `B`, `C` (default `B`) |

- **Training phase** follows the nearest A race: Base (> 70 days) → Build → Peak → Taper (≤ 14 days) → Race day → Recovery
- **B / C races** don't change the main phase, but get a mini taper in the 7 days before and a reduced-load suggestion for 7 days after
- Invalid entries are skipped with a warning in the log; the run carries on

### Weather

Pacer reads the hourly Open-Meteo forecast for your `RUN_TIME`. It is free and needs no API key.

- **Heat and humidity** combine temperature and dew point (°F) into the expected pace slowdown, so you don't chase your normal pace on a hot, humid day
- **PM2.5** is graded on Thailand's Pollution Control Department scale
- Strong heat, a thunderstorm or PM2.5 above 75 µg/m³ raises a yellow warning, and the AI adapts the session, e.g. easing off, avoiding exposed routes or moving to a treadmill
- Weather never turns the status red, because red means your body needs rest

## CLI Usage

```bash
python main.py                    # normal run; posts to Discord once a day
python main.py --dry-run          # print the analysis and summary; post nothing
python main.py --force            # post now, ignoring the once-a-day rule
python main.py --backfill 30      # fetch the last 30 days
python main.py --date 2026-09-01  # analyse as if that date were today
```

## Development

Tests use simulated data only and need no internet connection.

```bash
python -m unittest discover -s tests -t .
```

Postgres tests run when `TEST_DATABASE_URL` is set. Point it at a throwaway Postgres in Docker only:

```bash
docker run -d --rm --name pacer-pg-test -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:17-alpine
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres python -m unittest discover -s tests -t .
docker stop pacer-pg-test
```

> [!CAUTION]
> Never point `TEST_DATABASE_URL` at your real Supabase project. The tests drop the tables.

Before adding a feature, update the spec in [`CLAUDE.md`](CLAUDE.md) first.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Discord says "Garmin login ล้มเหลว" (Garmin login failed) | The token expired or was revoked. Run `python setup_tokens.py` again and update the `GARMINTOKENS_BASE64` secret |
| The card says "ไม่ได้ใช้ AI" (no AI used) | Every provider in `LLM_PROVIDER` failed (wrong key, no credit, …). Check the Actions log |
| Workflow fails with `DATABASE_URL is not set` | Add the `DATABASE_URL` secret |
| Can't reach Supabase / `Network is unreachable` | Use the Session pooler connection string, not `db.<ref>.supabase.co` |
| The card arrives later than expected | GitHub's cron can run hours late. For exact timing, run it on your own machine with `launchd` or cron |
| Some fields are missing from the card | Garmin hasn't synced yet, or your watch doesn't record that metric. The last 3 days are re-fetched automatically |
| Garmin endpoint errors | `garminconnect` is an unofficial API. Try `pip install -U garminconnect` first |
| You need to re-run `setup_tokens.py` every 1–2 days | Tokens refreshed in CI are not written back to the secret; if Garmin revokes the old refresh token, this happens |

## Security & Privacy

- **No credentials in the repo**: tokens, API keys, the webhook and the connection string live only in GitHub Secrets or the git-ignored `.env`
- **No Garmin password stored**: Pacer uses tokens only
- **Health data stays in Supabase**, protected by Row Level Security, never in the repo; `.db` files are git-ignored
- **No precise locations**: run start points are rounded to about 1 km before they are stored
- **No health numbers in Actions logs**, since anyone can read the logs of a public repo
- **Gentle on Garmin**: 0.4 s between requests and at most 250 requests per run

## Credits

- [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) · unofficial Garmin Connect API
- [Open-Meteo](https://open-meteo.com) · weather and air-quality data (CC BY 4.0)
- [Anthropic Claude](https://www.anthropic.com) and [OpenAI](https://openai.com) · summaries and coaching
- [Supabase](https://supabase.com) · database

<div align="center">
<sub>Pacer is not affiliated with Garmin Ltd. · Use it with your own account only</sub>
</div>
