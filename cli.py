"""`coach` command line.

    coach login | sync | report | ask | chat | note | schedule | doctor
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import logging
import sys

import config

log = logging.getLogger("coach")


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
    use_llm = not args.no_llm and s.has_api_key
    if not args.no_llm and not s.has_api_key:
        print("No ANTHROPIC_API_KEY in .env: rendering numbers only.")
    out_dir = s.reports_dir / "demo" if args.demo else s.reports_dir
    with _store(args.demo) as store:
        res = generate(store, goals, monday, today, out_dir, use_llm=use_llm, model=s.model)
    print(f"Report {res['week']}:\n  {res['md']}\n  {res['html']}")
    if not args.demo:
        from delivery import deliver
        deliver(res, s)
    return 0


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
    if not s.has_api_key:
        print("Chat needs ANTHROPIC_API_KEY in .env (see README).")
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
    check("ANTHROPIC_API_KEY set", s.has_api_key, "add your key to .env (never paste it in chat)")
    print(f"       model: {s.model}  chat model: {s.chat_model}")
    try:
        goals = config.load_goals()
        check("goals.yaml parses", True)
        todos = sum("TODO" in line for line in (config.ROOT / "goals.yaml").read_text(encoding="utf-8").splitlines())
        print(f"       {todos} TODO lines left in goals.yaml")
    except Exception as e:
        check(f"goals.yaml parses ({e})", False)
    check("Garmin login tokens", s.garmin_token_dir.exists(), "run `coach login`")
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
    rp.set_defaults(fn=cmd_report)

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
