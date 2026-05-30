#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chaos_monkey.py
===============
Gentle chaos engineering for Vadim's bot ecosystem.

CANONICAL LOCATION: ``C:\\Users\\Вадим\\.claude\\chaos_monkey.py`` — Windows
Task Scheduler should point at that path. This copy lives inside the
resale-bot repo so it travels with the codebase and is version-controlled.
Copy / symlink to .claude on each host:

    copy "C:\\Projects\\resale-bot\\tools\\chaos_monkey.py" "C:\\Users\\Вадим\\.claude\\chaos_monkey.py"

Once per week (Sunday 03:00 UTC by default — schedule via Windows Task
Scheduler) this script:

    1. Picks one random bot from BOT_SERVICES below.
    2. Calls ``railway restart --service <bot>``.
    3. Polls the bot's /health endpoint for up to 5 minutes.
    4. If the bot recovers — logs success.
    5. If it doesn't — notifies @vadim_admin_bot via Telegram with full
       diagnostics (last 200 log lines, latest deploy status).

OPT-IN:
    Disabled by default. Will only act if the environment variable
    CHAOS_MONKEY_ENABLED is set to "1" (or "true"/"yes").

CONFIG via env:
    CHAOS_MONKEY_ENABLED       — gate flag.  Default 0 (dry-run).
    CHAOS_MONKEY_DRY_RUN       — if 1, picks a bot and logs the plan
                                  but does NOT call railway restart.
                                  Default 0 (i.e. real restart when gated on).
    CHAOS_MONKEY_BOTS          — comma-separated override of bot list.
    CHAOS_MONKEY_HEALTH_TIMEOUT_S — recovery poll window. Default 300.
    CHAOS_MONKEY_POLL_INTERVAL_S  — poll interval. Default 10.
    ADMIN_BOT_TOKEN            — Telegram bot used for alerts.
    ADMIN_CHAT_ID              — chat to ping on failure (Vadim's).

LOG:
    Appends one JSON line per run to chaos_monkey.log in the same dir.

Usage:
    py chaos_monkey.py             # honours CHAOS_MONKEY_ENABLED
    py chaos_monkey.py --force     # ignore the gate (for manual testing)
    py chaos_monkey.py --dry-run   # pick & log but never restart
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import random
import shutil
import subprocess
import sys
import time
from typing import Optional
from urllib import request as urlreq, parse as urlparse, error as urlerr

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HERE = pathlib.Path(__file__).resolve().parent
LOG_FILE = HERE / "chaos_monkey.log"
STATE_FILE = HERE / "chaos_monkey_state.json"

# Each entry: (railway_service_name, health_url).
# Health URL must respond 200 within HEALTH_TIMEOUT_S after restart.
BOT_SERVICES: list[tuple[str, str]] = [
    ("resale-bot",       "https://resale-bot-production.up.railway.app/health"),
    ("lead-bot",         "https://lead-bot-production.up.railway.app/health"),
    ("roi-bot",          "https://roi-bot-production.up.railway.app/health"),
    ("channel-bot-new",  "https://channel-bot-new-production.up.railway.app/health"),
    ("hub-bot",          "https://hub-bot-production.up.railway.app/health"),
    ("currency-bot",     "https://currency-bot-production.up.railway.app/health"),
    ("dubai-dld-analytics-bot",
                         "https://dubai-dld-analytics-bot-production.up.railway.app/health"),
]


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


HEALTH_TIMEOUT_S = int(os.environ.get("CHAOS_MONKEY_HEALTH_TIMEOUT_S", "300"))
POLL_INTERVAL_S = int(os.environ.get("CHAOS_MONKEY_POLL_INTERVAL_S", "10"))

ADMIN_BOT_TOKEN = os.environ.get("ADMIN_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID") or os.environ.get("VADIM_CHAT_ID")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def log_line(level: str, **fields) -> None:
    """Append one JSON log line and echo to stderr."""
    payload = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "level": level,
        **fields,
    }
    line = json.dumps(payload, ensure_ascii=False)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, file=sys.stderr)


def notify_admin(text: str) -> None:
    """Send Telegram alert if ADMIN_BOT_TOKEN + ADMIN_CHAT_ID are configured."""
    if not ADMIN_BOT_TOKEN or not ADMIN_CHAT_ID:
        log_line("WARNING", msg="admin_notify_skipped",
                 reason="no admin bot token / chat id in env")
        return
    url = f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage"
    body = urlparse.urlencode({
        "chat_id": ADMIN_CHAT_ID,
        "text": text[:4000],
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urlreq.Request(url, data=body, method="POST")
    try:
        with urlreq.urlopen(req, timeout=15) as r:
            r.read()
    except (urlerr.URLError, OSError) as e:
        log_line("ERROR", msg="admin_notify_failed", err=str(e))


def http_get_ok(url: str, timeout: float = 5.0) -> tuple[bool, Optional[int]]:
    try:
        with urlreq.urlopen(url, timeout=timeout) as r:
            return (200 <= r.status < 300), r.status
    except urlerr.HTTPError as e:
        return False, e.code
    except (urlerr.URLError, OSError):
        return False, None


def railway_cli_available() -> bool:
    return shutil.which("railway") is not None


def railway_restart(service: str) -> tuple[bool, str]:
    """Returns (ok, combined_stdout_stderr)."""
    try:
        proc = subprocess.run(
            ["railway", "restart", "--service", service],
            capture_output=True, text=True, timeout=60,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, out
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return False, f"{type(e).__name__}: {e}"


def railway_logs_tail(service: str, lines: int = 200) -> str:
    try:
        proc = subprocess.run(
            ["railway", "logs", "--service", service, "--lines", str(lines)],
            capture_output=True, text=True, timeout=30,
        )
        return (proc.stdout or "")[-4000:]
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return f"<logs unavailable: {e}>"


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2),
                              encoding="utf-8")
    except OSError as e:
        log_line("WARNING", msg="state_save_failed", err=str(e))


def pick_bot(state: dict) -> tuple[str, str]:
    """Pick a random bot, avoiding the one restarted last week if possible."""
    bots = BOT_SERVICES
    override = os.environ.get("CHAOS_MONKEY_BOTS")
    if override:
        wanted = {s.strip() for s in override.split(",") if s.strip()}
        bots = [b for b in BOT_SERVICES if b[0] in wanted] or BOT_SERVICES
    last = state.get("last_target")
    pool = [b for b in bots if b[0] != last] or bots
    return random.choice(pool)


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def run(force: bool = False, dry_run_override: bool = False) -> int:
    enabled = _env_bool("CHAOS_MONKEY_ENABLED", False)
    dry_run = dry_run_override or _env_bool("CHAOS_MONKEY_DRY_RUN", False)

    if not enabled and not force:
        log_line("INFO", msg="chaos_monkey_disabled",
                 reason="CHAOS_MONKEY_ENABLED is not set to 1",
                 hint="set CHAOS_MONKEY_ENABLED=1 or pass --force")
        return 0

    if not railway_cli_available() and not dry_run:
        log_line("ERROR", msg="railway_cli_missing")
        notify_admin("Chaos Monkey: railway CLI not found on host. Cannot restart anything.")
        return 2

    state = load_state()
    service, health_url = pick_bot(state)
    log_line("INFO", msg="chaos_monkey_target_picked",
             service=service, health_url=health_url, dry_run=dry_run)

    # Pre-restart sanity check — if already unhealthy, skip (we'd blame the wrong thing).
    pre_ok, pre_status = http_get_ok(health_url, timeout=5.0)
    if not pre_ok and not dry_run:
        log_line("WARNING", msg="pre_restart_unhealthy",
                 service=service, status=pre_status,
                 action="skipping_to_avoid_false_alert")
        return 0

    if dry_run:
        log_line("INFO", msg="dry_run_complete", service=service)
        state["last_target"] = service
        state["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat()
        state["last_result"] = "dry_run"
        save_state(state)
        return 0

    ok, out = railway_restart(service)
    log_line("INFO" if ok else "ERROR",
             msg="railway_restart_invoked",
             service=service, ok=ok, output=out[-500:])
    if not ok:
        notify_admin(
            f"Chaos Monkey: railway restart FAILED for {service}.\n\n{out[-1000:]}"
        )
        state["last_target"] = service
        state["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat()
        state["last_result"] = "restart_cmd_failed"
        save_state(state)
        return 3

    # Poll health for up to HEALTH_TIMEOUT_S.
    started = time.time()
    recovered = False
    last_status: Optional[int] = None
    while time.time() - started < HEALTH_TIMEOUT_S:
        time.sleep(POLL_INTERVAL_S)
        good, status = http_get_ok(health_url, timeout=5.0)
        last_status = status
        log_line("INFO", msg="health_poll",
                 service=service, good=good, status=status,
                 elapsed_s=int(time.time() - started))
        if good:
            recovered = True
            break

    state["last_target"] = service
    state["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat()

    if recovered:
        log_line("INFO", msg="chaos_monkey_success", service=service)
        state["last_result"] = "recovered"
        save_state(state)
        return 0

    # Failure: snapshot diagnostics and ping the admin bot.
    logs_tail = railway_logs_tail(service, lines=200)
    state["last_result"] = "recovery_failed"
    save_state(state)
    msg = (
        f"Chaos Monkey: {service} did NOT recover within {HEALTH_TIMEOUT_S}s.\n"
        f"Last /health status: {last_status}\n\n"
        f"Logs tail:\n{logs_tail}"
    )
    log_line("ERROR", msg="chaos_monkey_recovery_failed",
             service=service, last_status=last_status)
    notify_admin(msg)
    return 4


def main() -> None:
    ap = argparse.ArgumentParser(description="Chaos Monkey for Vadim's bots")
    ap.add_argument("--force", action="store_true",
                    help="Ignore CHAOS_MONKEY_ENABLED gate")
    ap.add_argument("--dry-run", action="store_true",
                    help="Pick a bot and log the plan but do NOT restart")
    args = ap.parse_args()
    rc = run(force=args.force, dry_run_override=args.dry_run)
    sys.exit(rc)


if __name__ == "__main__":
    main()
