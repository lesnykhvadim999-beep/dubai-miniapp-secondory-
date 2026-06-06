# -*- coding: utf-8 -*-
"""Real-time accuracy monitor (#56).

Background daemon: every 60s scans listings created in the last 5 minutes
and not yet verified_realtime. For each one re-runs a second LLM via
parser_v2.verify_extraction. If >=2 fields come back as "wrong" the
listing is flagged for manual review and confidence is dropped by 0.2.
Otherwise it is marked verified_realtime=TRUE.

Run modes:
    python _realtime_accuracy_daemon.py            # forever loop, sleep 60s
    python _realtime_accuracy_daemon.py --once     # single cycle (smoke test)
    python _realtime_accuracy_daemon.py --sample 3 # single cycle, only N listings

Deploy: as a separate Railway worker in the resale-bot project, or import
run_cycle() from cron_worker.py.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
import traceback
from datetime import datetime, timezone

# Audit 2026-06-06: REMOVED sys.stdout re-wrap.
# When this module is imported INSIDE a long-running process (cron_worker),
# replacing sys.stdout invalidates cached references in other threads →
# ValueError: I/O operation on closed file. Railway already sets
# PYTHONIOENCODING=utf-8 in the container env so the original wrap is moot.

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2  # type: ignore
import psycopg2.extras  # type: ignore

from parser_v2 import verify_extraction  # type: ignore

try:
    from admin_notify import admin_notify  # type: ignore
except Exception:  # pragma: no cover
    def admin_notify(text: str, **_kw) -> bool:  # type: ignore
        print(f"[admin_notify-stub] {text[:200]}")
        return False

DSN = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set")),
)

SLEEP_SEC = 60
WINDOW_MIN = 5            # how far back to look for new listings
WRONG_THRESHOLD = 2       # >= this many "wrong" verdicts → flag
CONFIDENCE_PENALTY = 0.2  # subtract from confidence_score on flag

VERIFY_FIELDS = ("deal_type", "property_type", "area", "building",
                 "bedrooms", "size_sqft", "price")


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [rt-accuracy] {msg}", flush=True)


def _get_conn():
    return psycopg2.connect(DSN)


def ensure_schema() -> None:
    """Idempotent migration — safe to call at startup."""
    conn = _get_conn()
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            "ALTER TABLE listings "
            "ADD COLUMN IF NOT EXISTS verified_realtime BOOL DEFAULT FALSE"
        )
    finally:
        conn.close()


def _fetch_candidates(limit: int | None = None) -> list[dict]:
    sql = (
        "SELECT id, original_text, deal_type, property_type, area, building, "
        "       bedrooms, size_sqft, price, confidence_score "
        "FROM listings "
        "WHERE created_at > NOW() - INTERVAL '%s min' "
        "  AND COALESCE(verified_realtime, FALSE) = FALSE "
        "  AND original_text IS NOT NULL "
        "ORDER BY created_at DESC"
    ) % WINDOW_MIN
    if limit:
        sql += f" LIMIT {int(limit)}"
    conn = _get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _mark_verified(lid: int) -> None:
    conn = _get_conn()
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE listings SET verified_realtime = TRUE WHERE id = %s",
            (lid,),
        )
    finally:
        conn.close()


def _flag_for_review(lid: int, wrong_fields: list[str]) -> None:
    conn = _get_conn()
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE listings "
            "SET needs_manual_review = TRUE, "
            "    review_reason       = 'realtime:accuracy_check_failed', "
            "    confidence_score    = GREATEST(COALESCE(confidence_score,0) - %s, 0.0), "
            "    verified_realtime   = TRUE "
            "WHERE id = %s",
            (CONFIDENCE_PENALTY, lid),
        )
    finally:
        conn.close()


def _verify_one(row: dict) -> tuple[bool, list[str]]:
    """Returns (flagged, wrong_fields)."""
    fields = {k: row.get(k) for k in VERIFY_FIELDS}
    text = row.get("original_text") or ""
    try:
        verdict = verify_extraction(text, fields) or {}
    except Exception as e:
        log(f"verify_extraction error on #{row.get('id')}: {e}")
        return False, []
    wrong = [k for k, v in verdict.items()
             if str(v or "").lower() == "wrong"]
    return (len(wrong) >= WRONG_THRESHOLD), wrong


def run_cycle(limit: int | None = None) -> dict:
    """One pass. Returns stats dict."""
    stats = {"scanned": 0, "verified": 0, "flagged": 0, "errors": 0}
    try:
        rows = _fetch_candidates(limit=limit)
    except Exception as e:
        log(f"fetch failed: {e}")
        stats["errors"] += 1
        return stats

    stats["scanned"] = len(rows)
    if not rows:
        return stats

    log(f"cycle start — {len(rows)} candidate(s)")

    for row in rows:
        lid = row["id"]
        try:
            flagged, wrong = _verify_one(row)
            if flagged:
                _flag_for_review(lid, wrong)
                stats["flagged"] += 1
                snippet = (row.get("original_text") or "")[:200]
                msg = (
                    f"⚠️ Realtime accuracy fail: listing #{lid}\n"
                    f"Original: {snippet}\n"
                    f"Issues: {', '.join(wrong)}"
                )
                try:
                    admin_notify(msg, priority="info")
                except Exception as e:
                    log(f"admin_notify fail for #{lid}: {e}")
                log(f"flag #{lid} wrong={wrong}")
            else:
                _mark_verified(lid)
                stats["verified"] += 1
        except Exception as e:
            stats["errors"] += 1
            log(f"row #{lid} fatal: {e}\n{traceback.format_exc()}")

    log(
        f"cycle done — scanned={stats['scanned']} "
        f"verified={stats['verified']} flagged={stats['flagged']} "
        f"errors={stats['errors']}"
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="Run a single cycle and exit (smoke test).")
    parser.add_argument("--sample", type=int, default=0,
                        help="Limit candidates to N rows (smoke test).")
    args = parser.parse_args()

    try:
        ensure_schema()
    except Exception as e:
        log(f"migration warn: {e}")

    if args.once or args.sample:
        limit = args.sample if args.sample > 0 else None
        run_cycle(limit=limit)
        return 0

    log(f"daemon start — interval={SLEEP_SEC}s window={WINDOW_MIN}min")
    while True:
        try:
            run_cycle()
        except Exception as e:
            log(f"cycle crashed: {e}\n{traceback.format_exc()}")
        time.sleep(SLEEP_SEC)


if __name__ == "__main__":
    sys.exit(main())
