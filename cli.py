"""`coach` command line.

    coach login | sync | report | ask | chat | note | schedule | doctor
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import logging
import os
import sys

import config

log = logging.getLogger("coach")


def _llm_hint(s: config.Settings) -> str:
    if s.backend == "claude-code":
        return "Claude Code not found; install the Claude desktop app or set CLAUDE_CODE_PATH in .env"
    return "ANTHROPIC_API_KEY missing in .env (or set COACH_BACKEND=claude-code to use your Claude subscription)"


def _today(goals: dict) -> dt.date:
    return dt.datetime.now(config.tz(goals)).date()


def _store(demo: bool = False):
    from db.store import Store
    s = config.settings()
    return Store(s.db_path.with_name("demo.db") if demo else s.db_path)


# --- login / sync ----------------------------------------------------------

def cmd_login(args: argparse.Namespace) -> int:
    from garminconnect import Garmin
    s = config.settings()
    print("Garmin Connect login. Your password is used once and never stored.")
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (hidden as you type): ")
    s.garmin_token_dir.mkdir(parents=True, exist_ok=True)
    g = Garmin(email=email, password=password,
               prompt_mfa=lambda: input("Code Garmin sent you (email/SMS/app): ").strip())
    try:
        g.login(str(s.garmin_token_dir))
        g.client.dump(str(s.garmin_token_dir))
    except Exception as e:
        print(f"Login failed: {e}")
        return 1
    print(f"Logged in as {g.get_full_name()}. Tokens saved in {s.garmin_token_dir}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from adapters.sync import sync, sync_goal_races
    s, goals = config.settings(), config.load_goals()
    today = _today(goals)
    with _store(args.demo) as store:
        if args.demo:
            from adapters.demo import DemoAdapter
            # demo history ends on the last complete Sunday so the demo report shows a full week
            today = today - dt.timedelta(days=(today.weekday() + 1) % 7)
            adapter = DemoAdapter(today=today)
            start = adapter.first
        else:
            from adapters.garmin import GarminAdapter
            adapter = GarminAdapter(s.garmin_token_dir, store, today=today,
                                    refresh_days=s.sync_refresh_days, throttle_s=s.sync_throttle_s,
                                    extras=not args.core_only,
                                    fit_dir=s.db_path.parent / "fit" if s.sync_fit else None)
            start = dt.date.fromisoformat(args.since) if args.since else s.sync_start
        res = sync(adapter, store, start=start, today=today, refresh_days=s.sync_refresh_days,
                   progress=lambda m: print(f"  {m}", flush=True), full=args.full)
        sync_goal_races(goals, store)
        print(f"Synced {res.activities} activities, {res.days} days, {res.planned} planned workouts.")
        if res.errors:
            print("Stopped early (run `coach sync` again to resume):")
            for e in res.errors:
                print(f"  - {e}")
            return 1
    return 0


# --- report ------------------------------------------------------------------

def cmd_report(args: argparse.Namespace) -> int:
    from analysis.weeks import last_complete_monday, parse_week
    from reports.pipeline import generate
    s, goals = config.settings(), config.load_goals()
    today = _today(goals)
    if args.demo:
        args.since, args.full, args.core_only = None, False, True
        cmd_sync(args)                              # demo.db is always regenerated up to today
    monday = parse_week(args.week) if args.week else last_complete_monday(today)
    use_llm = not args.no_llm and s.llm_available
    if not args.no_llm and not s.llm_available:
        print(f"Coaching step unavailable ({_llm_hint(s)}): rendering numbers only.")
    out_dir = s.reports_dir / "demo" if args.demo else s.reports_dir
    override = None
    if getattr(args, "narrative_file", None):
        import json
        from pathlib import Path
        override = json.loads(Path(args.narrative_file).read_text(encoding="utf-8"))
    with _store(args.demo) as store:
        res = generate(store, goals, monday, today, out_dir, use_llm=use_llm and override is None,
                       model=s.model, narrative_override=override)
    print(f"Report {res['week']}:\n  {res['md']}\n  {res['html']}")
    if not args.demo:
        from delivery import deliver
        deliver(res, s)
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    """Write everything needed to author the weekly narrative (instructions,
    principles, goals, this week's data, output schema) to one file. The cloud
    routine reads it, writes the narrative JSON, and passes it to
    `coach report --narrative-file`."""
    import json
    from pathlib import Path
    from analysis.metrics import build_week
    from analysis.weeks import last_complete_monday, parse_week
    from llm import NARRATIVE_SCHEMA, weekly_prompt
    from reports.pipeline import previous_reports
    goals = config.load_goals()
    today = _today(goals)
    monday = parse_week(args.week) if args.week else last_complete_monday(today)
    with _store() as store:
        m = build_week(store, goals, monday, today=today)
        system, user = weekly_prompt(m, previous_reports(store, m["week"]))
    out = Path(args.out or config.settings().db_path.parent / f"brief-{m['week']}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"# Weekly coaching brief {m['week']}\n\n"
        "Write the narrative as a single JSON object that matches the schema at the end, "
        "following the instructions, coaching principles and goals below. Use only numbers from the week data.\n\n"
        f"## Instructions, principles and goals\n\n{system}\n\n## This week\n\n{user}\n\n"
        f"## Output JSON schema\n\n```json\n{json.dumps(NARRATIVE_SCHEMA, indent=1)}\n```\n",
        encoding="utf-8")
    print(f"Brief for {m['week']}: {out}")
    return 0


def cmd_export_garmin_token(args: argparse.Namespace) -> int:
    """Save the Garmin login as one token string (for the cloud environment's
    GARMIN_TOKENS secret) to a gitignored file. The token is never printed."""
    from garminconnect import Garmin
    s = config.settings()
    if not s.garmin_token_dir.exists():
        print("No Garmin login on this PC yet. Run `coach login` first.")
        return 1
    g = Garmin()
    g.login(str(s.garmin_token_dir))
    out = s.db_path.parent / "garmin_token.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(g.client.dumps(), encoding="utf-8")
    print(f"Garmin token saved to {out}\n"
          "Copy the whole file content into your cloud environment as GARMIN_TOKENS, then delete the file.\n"
          "Never paste it into chat or commit it.")
    return 0


def cmd_slim_db(args: argparse.Namespace) -> int:
    """Drop the raw API cache and compact the database so it fits in git."""
    with _store() as store:
        before = store.counts()["raw_cache"]
        store.db.execute("DELETE FROM raw_cache")
        store.db.commit()
        store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store.db.execute("VACUUM")
        path = store.path
    print(f"Removed {before} cached raw responses; {path} is now {path.stat().st_size / 1e6:.1f} MB")
    return 0


RETRY_AFTER_H = 6   # a numbers-only report is retried at most this often


def cmd_weekly(args: argparse.Namespace) -> int:
    """What the scheduled task runs (Sunday 19:00, at logon and on unlock):
    sync + report for the due week, unless that report already exists. Cheap to
    call often; it exits immediately when there is nothing to do."""
    from analysis.load import iso_week
    from analysis.weeks import due_monday
    goals = config.load_goals()
    now = dt.datetime.now(config.tz(goals)).replace(tzinfo=None)
    monday = due_monday(now)
    week = iso_week(monday)
    with _store() as store:
        existing = store.report(week)
    if existing and not args.force:
        generated = dt.datetime.fromisoformat(existing["generated_at"]).astimezone().replace(tzinfo=None)
        if existing["narrative"] or now - generated < dt.timedelta(hours=RETRY_AFTER_H):
            print(f"{now:%Y-%m-%d %H:%M} {week}: report already done, nothing to do.")
            return 0
    print(f"{now:%Y-%m-%d %H:%M} {week}: syncing and building the weekly report.")
    sync_rc = cmd_sync(argparse.Namespace(since=None, full=False, core_only=False, demo=False))
    if sync_rc:
        print("Sync stopped early; building the report from the data already saved.")
    return cmd_report(argparse.Namespace(week=week, no_llm=False, demo=False))


# --- note / ask / chat ---------------------------------------------------------

def cmd_note(args: argparse.Namespace) -> int:
    goals = config.load_goals()
    now = dt.datetime.now(config.tz(goals)).replace(tzinfo=None)
    if args.date:
        now = dt.datetime.combine(dt.date.fromisoformat(args.date), now.time())
    text = " ".join(args.text).strip()
    if not text:
        print("Nothing to log.")
        return 1
    with _store() as store:
        store.add_note(text, now)
    print(f"Noted for {now.date()}. It will be used in the next report and in chat.")
    words = [w.lower() for w in (goals.get("recovery_thresholds") or {}).get("pain_keywords", [])]
    if any(w in text.lower() for w in words):
        print("If this is pain rather than normal soreness, consider getting it checked by a physio or doctor.")
    return 0


def _chat_session():
    from analysis.metrics import digest
    from llm import Chat, chat_context
    s, goals = config.settings(), config.load_goals()
    if not s.llm_available:
        print(f"Chat unavailable: {_llm_hint(s)}.")
        return None
    today = _today(goals)
    with _store() as store:
        d = digest(store, goals, today)
        latest = store.recent_reports(1)
        notes = store.notes((today - dt.timedelta(days=14)).isoformat(), today.isoformat())
    return Chat(chat_context(d, latest[0] if latest else None, notes))


def _stream_answer(chat, question: str) -> None:
    for chunk in chat.ask(question):
        print(chunk, end="", flush=True)
    print()


def cmd_ask(args: argparse.Namespace) -> int:
    chat = _chat_session()
    if chat is None:
        return 1
    _stream_answer(chat, " ".join(args.question))
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    chat = _chat_session()
    if chat is None:
        return 1
    print("Coach chat. Empty line or Ctrl+C to quit. Prefix with 'note:' to log a note.")
    while True:
        try:
            q = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            return 0
        if q.lower().startswith("note:"):
            cmd_note(argparse.Namespace(text=[q[5:]], date=None))
            continue
        print("coach> ", end="")
        try:
            _stream_answer(chat, q)
        except Exception as e:
            print(f"\n[error: {e}]")


# --- schedule ----------------------------------------------------------------

def cmd_schedule(args: argparse.Namespace) -> int:
    import platform
    import subprocess
    if platform.system() != "Windows":
        print("On macOS/Linux use scheduler/launchd.plist.example (it also has the cron line).")
        return 1
    script = config.ROOT / "scheduler" / "install_windows_task.ps1"
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    if args.action == "remove":
        cmd.append("-Remove")
    return subprocess.call(cmd)


# --- doctor ------------------------------------------------------------------

def cmd_doctor(args: argparse.Namespace) -> int:
    import time
    s, ok = config.settings(), True

    def check(label: str, good: bool, hint: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'ok' if good else '!!'}] {label}" + (f"  → {hint}" if hint and not good else ""))

    print("Config")
    check(".env present", (config.ROOT / ".env").exists(), "copy .env.example to .env")
    if s.backend == "claude-code":
        exe = config.find_claude_code()
        check(f"Claude backend: Claude Code on your subscription ({exe if exe else 'not found'})",
              exe is not None, "install the Claude desktop app or set CLAUDE_CODE_PATH in .env")
        check("CLAUDE_CODE_OAUTH_TOKEN set (long-lived login for scheduled runs)",
              bool(os.getenv("CLAUDE_CODE_OAUTH_TOKEN")),
              "run `claude setup-token` yourself and put the token in .env (see README)")
    else:
        check("Claude backend: API, ANTHROPIC_API_KEY set", s.has_api_key,
              "add your key to .env (never paste it in chat)")
    print(f"       model: {s.model}  chat model: {s.chat_model}")
    try:
        goals = config.load_goals()
        check("goals.yaml parses", True)
        todos = sum("TODO" in line for line in (config.ROOT / "goals.yaml").read_text(encoding="utf-8").splitlines())
        print(f"       {todos} TODO lines left in goals.yaml")
    except Exception as e:
        check(f"goals.yaml parses ({e})", False)
    check("Garmin login", s.garmin_token_dir.exists() or bool(os.getenv("GARMIN_TOKENS")), "run `coach login`")
    local = time.strftime("%z")
    berlin = dt.datetime.now(config.tz()).strftime("%z")
    check(f"computer timezone matches Europe/Berlin ({local})", local == berlin,
          "the scheduler uses local time; set Windows to Berlin time or adjust the task")
    print(f"Delivery: email={'ON' if s.delivery_email else 'off'}  notion={'ON' if s.delivery_notion else 'off'}")
    if s.db_path.exists():
        from db.store import Store
        with Store(s.db_path) as st:
            print("Database", s.db_path)
            for k, v in st.counts().items():
                print(f"       {k:18} {v}")
            print(f"       last sync: {st.state_get('garmin:last_sync')}  "
                  f"daily cursor: {st.state_get('garmin:daily_cursor')}")
    else:
        print("Database: not created yet (run `coach sync`)")
    return 0 if ok else 1


# --- main --------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coach", description="Personal running coach")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="one-time Garmin login (you type your credentials)").set_defaults(fn=cmd_login)

    sp = sub.add_parser("sync", help="pull Garmin data into the local database (idempotent)")
    sp.add_argument("--since", help="first day for the backfill (default SYNC_START_DATE)")
    sp.add_argument("--full", action="store_true", help="ignore the resume cursor and re-walk every day")
    sp.add_argument("--core-only", action="store_true", help="skip raw-only extra endpoints (faster)")
    sp.add_argument("--demo", action="store_true", help="fill demo.db with fake data")
    sp.set_defaults(fn=cmd_sync)

    rp = sub.add_parser("report", help="build the weekly report (Markdown + HTML)")
    rp.add_argument("--week", help="ISO week, e.g. 2026-W43 (default: last complete week)")
    rp.add_argument("--no-llm", action="store_true", help="numbers only, no Claude commentary")
    rp.add_argument("--demo", action="store_true", help="use fake demo data (no accounts needed)")
    rp.add_argument("--narrative-file", help="use a narrative JSON written from `coach brief` instead of calling Claude")
    rp.set_defaults(fn=cmd_report)

    bp = sub.add_parser("brief", help="write the coaching brief for a week (used by the cloud routine)")
    bp.add_argument("--week", help="ISO week (default: last complete week)")
    bp.add_argument("--out", help="output file (default data/brief-<week>.md)")
    bp.set_defaults(fn=cmd_brief)

    sub.add_parser("export-garmin-token", help="save the Garmin login as a token string for the cloud (never printed)") \
        .set_defaults(fn=cmd_export_garmin_token)
    sub.add_parser("slim-db", help="drop the raw API cache and compact the database (for git)").set_defaults(fn=cmd_slim_db)

    wp = sub.add_parser("weekly", help="scheduled job: sync + report for the due week if not done yet")
    wp.add_argument("--force", action="store_true", help="rebuild even if this week's report exists")
    wp.set_defaults(fn=cmd_weekly)

    np_ = sub.add_parser("note", help="log how you felt, niggles, life stress")
    np_.add_argument("text", nargs="+")
    np_.add_argument("--date", help="YYYY-MM-DD (default today)")
    np_.set_defaults(fn=cmd_note)

    ap = sub.add_parser("ask", help="ask the coach one question")
    ap.add_argument("question", nargs="+")
    ap.set_defaults(fn=cmd_ask)

    sub.add_parser("chat", help="multi-turn conversation with the coach").set_defaults(fn=cmd_chat)

    sc = sub.add_parser("schedule", help="install/remove the Sunday 19:00 Windows task")
    sc.add_argument("action", choices=["install", "remove"])
    sc.set_defaults(fn=cmd_schedule)

    sub.add_parser("doctor", help="check config, login, database (prints no secrets)").set_defaults(fn=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    config.setup_logging(args.verbose)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
