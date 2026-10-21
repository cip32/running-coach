# Running coach

A personal running coach agent that:
- syncs **everything Garmin Connect exposes** into a local SQLite history,
- computes the training metrics in plain Python,
- uses Claude to turn those numbers into a weekly coaching report and a chat you can bounce ideas off.

It's built for one athlete with nothing hard-coded, so a copy of the folder with a different `goals.yaml`, `.env` and Garmin login works for anyone you coach.

```
Garmin ──▶ adapters/garmin.py ──▶ db/ (SQLite + raw cache) ──▶ analysis/ (numbers)
                                                                     │
          goals.yaml + coaching_principles.md ──▶ llm.py (Claude: interpretation only)
                                                                     │
                                         reports/ (Markdown + HTML + charts) ──▶ local file
                                                                     └──▶ email / Notion (off by default)
```

## Setup (Windows, once)

You need Python 3.11+ (3.14 is installed on this machine).

```bash
cd C:\home\claude\running-coach
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env
```

If you prefer [uv](https://docs.astral.sh/uv/), run `uv venv` and then `uv pip install -e ".[dev]"`.

Next, choose how the coach talks to Claude, with `COACH_BACKEND` in `.env`:

- **`claude-code` (default):** uses **your Claude subscription** through the Claude Code CLI that ships with the Claude desktop app. You don't need an API key or credits. Usage counts against your normal Claude limits. Claude Code is found automatically; set `CLAUDE_CODE_PATH` only if `coach doctor` can't find it. Your API key, if any, is never passed to Claude Code, so it can't bill the API by accident.
- **`api`:** uses `ANTHROPIC_API_KEY` from console.anthropic.com with prepaid API credits, billed per use and separately from a subscription.

Also in `.env`:
- `COACH_MODEL` defaults to `claude-opus-5-5`. Set `COACH_CHAT_MODEL` if you want a different model for chat.
- Everything else can stay on its defaults. `.env` is gitignored. **Never paste keys into chat.**

Then log in to Garmin once. You type your own email, password and code in the terminal. The password is used once and never stored; only an OAuth token is kept in `~/.garminconnect`, outside the repo.

```bash
.venv\Scripts\coach login
```

Finally, check the setup. `doctor` prints no secret values.

```bash
.venv\Scripts\coach doctor
```

**Try it without any accounts** (fake data, no API key needed). The output goes to `reports/out/demo/`.

```bash
.venv\Scripts\coach report --demo
```

## First sync

```bash
.venv\Scripts\coach sync --since 2026-07-27
.venv\Scripts\coach sync --full
```

1. **The first command** pulls the last ~12 weeks. That's enough for 8-week trends and takes about 30–60 minutes.
2. **The second command** backfills from `SYNC_START_DATE` (2025-01-01). `--full` is needed because the first sync moved the resume point to today. Already-cached data is skipped, but it takes a few hours the first time. If it stops (Garmin rate limit, PC asleep), run `coach sync --full` again and the cache makes the finished part instant.

After that, a plain `coach sync` (or the Sunday task) only fetches what's new.

For speed, `--core-only` skips the raw-only extra endpoints.

Syncing is **idempotent**:
- every row is upserted on a natural key;
- the last `SYNC_REFRESH_DAYS` (3) are always re-fetched, because Garmin finalises sleep and HRV late;
- every raw API response is kept compressed in `raw_cache`, so reruns are cheap and nothing Garmin returned is ever lost.

## Commands

| Command | What it does |
|---|---|
| `coach sync` | Pull new Garmin data (activities, laps, HR/speed streams, strength sets, sleep, HRV, readiness, body battery, stress, training status, VO2max, LT, race predictions, calendar, and ~20 more raw endpoints) |
| `coach report [--week 2026-W43] [--no-llm] [--demo]` | Build the weekly report. Default week: the last complete Monday–Sunday (on Sunday, the current week) |
| `coach note "left calf tight after strides"` | Log feelings, niggles, life stress. Used in the next report and in chat. `--date` to backdate |
| `coach ask "Should I move the long run to Saturday?"` | One question to the coach, using goals, principles, 8 weeks of data, the latest report and the last 14 days of notes |
| `coach chat` | Multi-turn version of `ask`. `note: ...` inside chat logs a note |
| `coach schedule install` / `remove` | Windows Task Scheduler job: `sync` + `report` every Sunday 19:00 |
| `coach doctor` | Check config, login, timezone, database counts |
| `coach login` | Redo the Garmin login if tokens expire |

## How to use this each week

1. **During the week, log anything the watch can't know:**
   ```
   coach note "slept badly, work deadline"
   coach note "right achilles stiff first 2 km, gone after"
   ```
   Pain words (pain, sore, tight, niggle…) become red flags in the report, and the coach will suggest a physio or doctor.

2. **Sunday 19:00:** the scheduled task syncs Garmin and writes `reports/out/<week>.md` and `.html`. Open the HTML; it's self-contained with embedded charts. To run it by hand at any time:
   ```
   coach sync
   coach report
   ```

3. **Read it in this order:**
   1. Red flags under Recovery
   2. Section 8 (next week)
   3. The 5 bullets and 3 actions

   Next week is a *suggestion with reasoning* that adjusts what's already on your Garmin calendar. You decide.

4. **Bounce ideas off it:**
   ```
   coach chat
   ```
   For example: "HRV is low again, should Thursday's LT1 become easy?" or "Can I race the Herbstlauf hard on 8 Nov?"

5. **Adjust the plan in Garmin Connect** as usual. Planned workouts sync back and are compared against what you ran in the next report (verdicts: on target, too hard, too easy, missed).

6. **When something changes** (new race, new zones, injury), edit `goals.yaml`. To change *how* the coach thinks, edit `coaching_principles.md`. Both are read on every run.

## What the report contains

1. **Week headline:** km, runs, time, elevation vs last week and the 4-week average, plus sleep-score and readiness charts.
2. **Sessions:** each run with type (easy / strides / LT1 / LT / hard / long / double), pace, HR, minutes per intensity bucket, strides count, grey-zone flag, and HR drift on long runs.
3. **Key workouts:** each Garmin calendar workout vs what was executed, with a plain verdict and the reason.
4. **Intensity distribution:** easy / steady / threshold / hard share, with grey-zone drift flagged on easy days.
5. **Load trend:** 8-week volume against the ramp to 100 km, computed ACWR (7-day vs 42-day EWMA) next to Garmin's acute/chronic, long-run progression, VO2max, LT and race prediction.
6. **Recovery:** sleep average and consistency, HRV against Garmin's baseline band (including nights-in-a-row below it), resting HR (last 3 days vs 30-day norm), body battery, readiness and **red flags**.
7. **Long-term context:** phase and week, progress toward 100 km/week, weeks to Vienna.
8. **Next week:** suggested volume and day-by-day sessions with reasons, what to protect, and what to cut first if recovery is poor.
9. **Summary:** five bullets and three small actions.

**How the work is split:**
- All numbers are computed deterministically in `analysis/` and covered by unit tests. Claude only interprets them: it's told to use only numbers present in the data and to say when data is missing.
- Missing data (e.g. a night without HRV) is counted and listed, never filled in.
- With `--no-llm`, or when Claude isn't reachable (Claude Code missing, usage limit hit, API out of credits), you still get the full numbers report with facts-only bullets, and the reason is listed under data gaps.

## Coaching logic

`coaching_principles.md` encodes:
- Steve Magness-style periodization: volume first, general → support → specific, never drop a quality, threshold before marathon pace, the long run as the key session, about +1 mile/day beyond previous volume, fueling practice, and sharpening over tapering;
- your phase rules;
- the safety rules: reduce load when HRV is below baseline 3+ days, sleep is poor, resting HR is up 5+ bpm, or you report pain; for pain, suggest a physio or doctor and never prescribe treatment.

The coach is told this file is authoritative and to name the principle behind each recommendation.

Session labels and recovery thresholds are tunable in `goals.yaml` under `classification` and `recovery_thresholds`.

## Project layout

```
cli.py                     `coach` command
config.py                  .env + goals.yaml loading, secret-redacting log filter
goals.yaml                 your goals, phases, zones (single source of truth)
coaching_principles.md     how the coach coaches (read every time)
adapters/models.py         common data model (Activity, Lap, Stream, DailyWellness, ...)
adapters/base.py           SourceAdapter interface
adapters/garmin.py         Garmin Connect adapter (python-garminconnect)
adapters/demo.py           fake athlete for --demo
adapters/sync.py           idempotent, resumable sync engine
db/schema.sql, db/store.py SQLite schema and upserts
analysis/                  zones, classification, load, weeks, recovery, plan vs executed, context
reports/                   charts (matplotlib), Jinja templates, render + pipeline
prompts/                   weekly report and chat instructions for Claude
llm.py                     Claude calls: Claude Code (subscription) or API backend; structured output, caching
delivery/                  optional email / Notion (off by default)
scheduler/                 Windows task installer, launchd/cron example
tests/                     unit tests with fixture data (`pytest`)
```

### Adding another source (TrainingPeaks, Notion, Strava…)

Subclass `adapters.base.SourceAdapter` and implement the methods that source can provide (`fetch_activities`, `fetch_wellness`, `fetch_planned`, …), returning the types in `adapters/models.py`. Everything downstream (DB, metrics, reports) works unchanged. The demo adapter is a compact example.

## Scheduling and delivery

- **Windows:** run `coach schedule install`. It registers *RunningCoachWeeklyReport*, which runs Sundays at 19:00 local time and runs late if the PC was off. `coach doctor` checks that Windows is on Berlin time. The log goes to `data/scheduled.log`.
- **macOS / Linux:** see `scheduler/launchd.plist.example`, which also has the cron line with `CRON_TZ=Europe/Berlin`.
- **GitHub Actions** isn't recommended: your Garmin tokens and database live on this machine.
- **Delivery:**
  - The report is always written locally first.
  - `DELIVERY_EMAIL=true` (SMTP settings in `.env`) or `DELIVERY_NOTION=true` (integration token + parent page) also send it out.
  - **Both are off by default, and nothing is sent anywhere unless you set them.**

## Development

```bash
.venv\Scripts\python -m pytest -q
```

The tests cover:
- zone distribution, classification and HR drift;
- ACWR and EWMA, week comparisons, recovery flags and missing data;
- planned vs executed, the ramp and phase context;
- idempotent and resumable sync;
- the LLM request shape and refusal handling (no real API calls);
- the CLI and delivery opt-in.

## Your TODOs

- [x] Claude access: done. The default `COACH_BACKEND=claude-code` uses your Claude subscription. An API key is only needed if you switch to `COACH_BACKEND=api`.
- [ ] In `goals.yaml` → `zones`:
  - [ ] Confirm `max_hr`, `lt1_hr`, `lt2_hr` and the `hr_buckets` floors. They're pre-filled from Garmin: max 185, LTHR 177, zone floors 91/114/152/166/175.
  - [ ] Set your pace zones.
- [ ] In `goals.yaml` → `definitions`: write what **you** mean by **LT1** and by a **hard session**.
- [ ] In `goals.yaml` → `races`: add your B/C races, or put them on the Garmin calendar.
- [ ] In `goals.yaml` → `notes`: add injuries, niggles and history.
- [ ] Check `phases` dates and `volume` (ramp from 60–70 to 100 km by 28 Dec).
- [ ] Run `coach schedule install` when you're happy with a manual report.
- [ ] Optional: SMTP or Notion settings in `.env`, plus `DELIVERY_EMAIL` / `DELIVERY_NOTION=true`.
