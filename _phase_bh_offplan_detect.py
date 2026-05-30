"""PHASE BH — detect is_off_plan via rule-based signals.

Strong off-plan signals:
  - 'Payment Plan: 80/20' / '50% paid'
  - 'Handover Q1 2027' / 'Completion 2027'
  - 'Off-plan' / 'Pre-launch' / 'Launch'
  - 'Developer price' / 'Original Price (OP)'
  - 'Booking amount'

Strong ready signals:
  - 'Ready to move' / 'Move-in ready' / 'Ready now'
  - 'Vacant' / 'Tenanted' / 'Available now'
  - 'Title Deed'
"""
from __future__ import annotations
import os
import re
import psycopg2
from datetime import datetime, timezone

DB = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:REDACTED_DSN_PASSWORD"
    "@tramway.proxy.rlwy.net:23228/railway",
)


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


_OFFPLAN_SIGNALS = re.compile(
    r"\b(off[\s\-]?plan|pre[\s\-]?launch|launch\s+price|"
    r"payment\s+plan\s*:?\s*\d|"
    r"\d+\s*[\/%]\s*\d+\s*payment|"
    r"\d+%\s+(paid|down|booking)|"
    r"booking\s+amount|"
    r"developer[\s']*s?\s+price|"
    r"original\s+price|\bop\b\s*[:+/\-]|"
    r"handover\s*[:\-]?\s*(?:q[1-4]|20\d{2})|"
    r"completion\s*[:\-]?\s*(?:q[1-4]|20\d{2})|"
    r"50\s*[%]?\s+(paid|на\s+handover|on\s+handover|до\s+сдачи))",
    re.IGNORECASE,
)

_READY_SIGNALS = re.compile(
    r"\b(ready\s+to\s+move|ready\s+now|move[\s\-]?in\s+ready|"
    r"vacant\s+on\s+transfer|tenanted|available\s+now|"
    r"title\s+deed|готов(?:ый|о)\s+к\s+(?:заселению|въезду)|"
    r"jaehez|جاهز\s+للسكن)",
    re.IGNORECASE,
)


def detect_off_plan(text):
    """Returns True (off-plan), False (ready), None (no signal)."""
    if not text:
        return None
    has_op = bool(_OFFPLAN_SIGNALS.search(text))
    has_ready = bool(_READY_SIGNALS.search(text))
    if has_op and not has_ready:
        return True
    if has_ready and not has_op:
        return False
    if has_op and has_ready:
        # Mixed signals → off-plan wins (handover date often mentioned even on ready)
        return True
    return None


def main():
    c = psycopg2.connect(DB, connect_timeout=10)
    cur = c.cursor()

    cur.execute("""
        SELECT id, COALESCE(v2_source_message_text, original_text, description) AS txt,
               is_off_plan, status
        FROM listings
        WHERE v2_extracted_at IS NOT NULL AND is_active = TRUE
          AND COALESCE(v2_source_message_text, original_text, description) IS NOT NULL
    """)
    rows = cur.fetchall()
    log(f"Scanning {len(rows):,} listings")

    set_offplan = 0
    set_ready = 0
    status_updated = 0
    for lid, txt, is_op, status in rows:
        result = detect_off_plan(txt)
        upd = {}
        if is_op is None and result is not None:
            upd["is_off_plan"] = result
            if result:
                set_offplan += 1
            else:
                set_ready += 1
        # Auto-set status if missing
        if not status and result is not None:
            upd["status"] = "off_plan" if result else "ready"
            status_updated += 1
        if upd:
            set_parts = ", ".join(f"{k}=%s" for k in upd.keys())
            params = list(upd.values()) + [lid]
            cur.execute(f"UPDATE listings SET {set_parts} WHERE id=%s", params)
    c.commit()
    log(f"\n=== STATS ===")
    log(f"  is_off_plan = TRUE:  +{set_offplan}")
    log(f"  is_off_plan = FALSE: +{set_ready}")
    log(f"  status set:          +{status_updated}")
    c.close()


if __name__ == "__main__":
    main()
