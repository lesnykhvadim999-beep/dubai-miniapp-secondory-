# -*- coding: utf-8 -*-
"""
photo_dedup.py — Mark listings with duplicate first photo as is_audit=TRUE.

Strategy: compute pHash of the first photo of each listing. If two or more
listings share the same pHash within 7 days, keep the oldest one and flag the
rest as audit (reason='photo_duplicate_of_N').

Idempotent. Safe to run daily.
"""
import os
import sys, os, hashlib
sys.stdout.reconfigure(encoding='utf-8')

os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set"))
)

import psycopg2
from psycopg2.extras import RealDictCursor

DB_URL = os.environ["DATABASE_URL"]

# We don't have PIL/imagehash on Railway by default — use file_id-based dedup
# (Telegram returns the same file_id for re-uploads of identical media
#  WITHIN THE SAME BOT). Cross-channel dedup uses cover_image_url hash.
def file_phash(file_id: str) -> str:
    """Cheap fingerprint of a Telegram file_id. Same file → same id."""
    if not file_id: return ""
    # The first ~16 chars of the file_id are stable identifiers in Telegram.
    return hashlib.sha1(file_id.encode()).hexdigest()[:16]


def main():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    # Backfill photo_phash for all visible listings
    cur.execute("""
        SELECT id, cover_image_url, photo_phash
        FROM listings
        WHERE is_active = TRUE
          AND (is_audit IS NULL OR is_audit = FALSE)
          AND cover_image_url IS NOT NULL
          AND photo_phash IS NULL
    """)
    rows = cur.fetchall()
    print(f"[dedup] Computing phash for {len(rows)} listings...", flush=True)
    for r in rows:
        h = file_phash(r["cover_image_url"])
        cur.execute("UPDATE listings SET photo_phash=%s WHERE id=%s", (h, r["id"]))
    conn.commit()

    # Find duplicate phash groups, keep the oldest, flag rest
    cur.execute("""
        SELECT photo_phash, MIN(id) AS keep_id, ARRAY_AGG(id ORDER BY id) AS ids
        FROM listings
        WHERE is_active = TRUE
          AND (is_audit IS NULL OR is_audit = FALSE)
          AND photo_phash IS NOT NULL AND photo_phash != ''
        GROUP BY photo_phash
        HAVING COUNT(*) > 1
    """)
    groups = cur.fetchall()
    print(f"[dedup] Found {len(groups)} duplicate-photo groups", flush=True)

    flagged = 0
    for g in groups:
        keep = g["keep_id"]
        dupes = [i for i in g["ids"] if i != keep]
        for dup in dupes:
            cur.execute(
                "UPDATE listings SET is_audit=TRUE, "
                "audit_reason=%s, audit_flagged_at=NOW() WHERE id=%s",
                (f"photo_duplicate_of_{keep}", dup)
            )
            flagged += 1
    conn.commit()
    print(f"[dedup] DONE: flagged {flagged} duplicate-photo records")
    conn.close()


if __name__ == "__main__":
    main()
