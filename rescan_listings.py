#!/usr/bin/env python3
"""
rescan_listings.py — Re-run improved parser on ALL listings with original_text.
Updates building/area/emirate/bedrooms/size_sqft/price where previously NULL/empty.
Commits every BATCH_SIZE rows to avoid losing progress on timeout.

Usage:
    DATABASE_URL="postgresql://..." python3 rescan_listings.py [--dry-run] [--limit N] [--verbose]
"""
import os, sys, importlib
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

# ── Force fresh reload of parser so we always use the latest code ─────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parser_engine
importlib.reload(parser_engine)
from parser_engine import parse_message

# ── Disable Nominatim during rescan (1.1 sec/call would make 5k rows take hours)
parser_engine.nominatim_lookup = lambda q: None

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set"))
)
DRY_RUN  = "--dry-run" in sys.argv
VERBOSE  = "--verbose" in sys.argv
LIMIT    = None
OFFSET   = 0
for i, arg in enumerate(sys.argv):
    if arg == "--limit" and i + 1 < len(sys.argv):
        LIMIT = int(sys.argv[i + 1])
    if arg == "--offset" and i + 1 < len(sys.argv):
        OFFSET = int(sys.argv[i + 1])

BATCH_SIZE = 500   # commit every N rows

FAKE_DATE = datetime.utcnow()


def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def _better(old_val, new_val) -> bool:
    """True only when new_val is non-null and old is NULL/empty — never overwrite good data."""
    if new_val is None:
        return False
    if isinstance(new_val, str) and not new_val.strip():
        return False
    if old_val is None or (isinstance(old_val, str) and not old_val.strip()):
        return True
    return False


def rescan():
    conn = get_conn()
    cur  = conn.cursor()

    q = """
        SELECT id, original_text,
               building, area, emirate,
               bedrooms, size_sqft, price
        FROM   listings
        WHERE  original_text IS NOT NULL AND original_text != ''
        ORDER  BY id ASC
    """
    if LIMIT:
        q += f" LIMIT {LIMIT}"
    if OFFSET:
        q += f" OFFSET {OFFSET}"

    cur.execute(q)
    rows  = cur.fetchall()
    total = len(rows)
    print(f"[rescan] Parser version: reloaded  |  Total rows to process: {total}")
    if DRY_RUN:
        print("[rescan] *** DRY RUN — no DB writes ***")

    stats = dict(
        skipped=0,
        updated_rows=0,
        improved_building=0,
        improved_area=0,
        improved_emirate=0,
        improved_bedrooms=0,
        improved_size_sqft=0,
        improved_price=0,
    )

    batch_updates = 0

    for i, row in enumerate(rows):
        row_id = row["id"]
        text   = row["original_text"] or ""

        # ── Parse ────────────────────────────────────────────────────────────
        try:
            parsed = parse_message(
                text=text,
                message_id=0,
                message_date=FAKE_DATE,
                chat_id="rescan",
            )
        except Exception as e:
            print(f"  [ERR] id={row_id}: {e}")
            stats["skipped"] += 1
            continue

        if not parsed:
            stats["skipped"] += 1
            continue

        # ── Build update dict ─────────────────────────────────────────────
        updates = {}
        for field in ("building", "area", "emirate"):
            if _better(row[field], parsed.get(field)):
                updates[field] = parsed[field]
                stats[f"improved_{field}"] += 1

        for field, stat_key in [
            ("bedrooms",  "improved_bedrooms"),
            ("size_sqft", "improved_size_sqft"),
            ("price",     "improved_price"),
        ]:
            if _better(row[field], parsed.get(field)):
                updates[field] = parsed[field]
                stats[stat_key] += 1

        # ── Log ──────────────────────────────────────────────────────────────
        if VERBOSE and updates:
            snippet = text[:60].replace("\n", " ")
            print(f"  [FIX] id={row_id}  text={snippet!r}")
            for k, v in updates.items():
                print(f"        {k}: {row[k]!r}  →  {v!r}")

        # ── Write ─────────────────────────────────────────────────────────────
        if updates:
            stats["updated_rows"] += 1
            batch_updates += 1
            if not DRY_RUN:
                set_clause = ", ".join(f"{k} = %s" for k in updates)
                values     = list(updates.values()) + [row_id]
                cur.execute(f"UPDATE listings SET {set_clause} WHERE id = %s", values)

        # ── Batch commit ──────────────────────────────────────────────────────
        if not DRY_RUN and batch_updates >= BATCH_SIZE:
            conn.commit()
            batch_updates = 0

        # ── Progress ──────────────────────────────────────────────────────────
        if (i + 1) % 500 == 0 or (i + 1) == total:
            pct = round((i + 1) / total * 100, 1)
            print(
                f"  [{i+1}/{total}  {pct}%]  "
                f"updated={stats['updated_rows']}  "
                f"bld={stats['improved_building']}  "
                f"area={stats['improved_area']}  "
                f"price={stats['improved_price']}"
            )

    # ── Final commit ──────────────────────────────────────────────────────────
    if not DRY_RUN:
        conn.commit()
        print("[rescan] Final commit done.")

    conn.close()

    # ── Report ────────────────────────────────────────────────────────────────
    remaining = total - stats["updated_rows"] - stats["skipped"]
    print()
    print("═" * 52)
    print("  ИТОГОВЫЙ ОТЧЁТ РЕСКАНИРОВАНИЯ")
    print("═" * 52)
    print(f"  Всего записей в listings   : {total}")
    print(f"  Автоисправлено строк       : {stats['updated_rows']}")
    print(f"  Пропущено (парс провалился): {stats['skipped']}")
    print(f"  Без изменений              : {total - stats['updated_rows'] - stats['skipped']}")
    print()
    print("  По полям (заполнено заново):")
    print(f"    building   : {stats['improved_building']}")
    print(f"    area       : {stats['improved_area']}")
    print(f"    emirate    : {stats['improved_emirate']}")
    print(f"    bedrooms   : {stats['improved_bedrooms']}")
    print(f"    size_sqft  : {stats['improved_size_sqft']}")
    print(f"    price      : {stats['improved_price']}")
    print("═" * 52)

    if DRY_RUN:
        print("  *** DRY RUN — изменения НЕ записаны ***")


if __name__ == "__main__":
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)
    rescan()
