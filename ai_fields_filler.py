#!/usr/bin/env python3
"""
ai_fields_filler.py — Fill missing price and bedrooms using Claude AI.

Reads listings where price IS NULL or bedrooms IS NULL,
calls Claude Haiku once per listing, updates only NULL fields.
Tracks processed rows via review_queue status='fields_ai_processed'.

Usage:
    python3 ai_fields_filler.py [--limit N] [--all] [--dry-run] [--verbose] [--workers N]

Env vars (auto-set by Replit AI Integration):
    AI_INTEGRATIONS_ANTHROPIC_BASE_URL
    AI_INTEGRATIONS_ANTHROPIC_API_KEY
Also works with plain ANTHROPIC_API_KEY.
"""
import os, sys, re, json, time, threading
import psycopg2
from psycopg2.extras import RealDictCursor
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

BASE_URL = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
API_KEY  = (
    os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
    or os.environ.get("ANTHROPIC_API_KEY")
    or "dummy"
)

client = anthropic.Anthropic(
    api_key=API_KEY,
    **({"base_url": BASE_URL} if BASE_URL else {}),
)

MODEL      = "claude-haiku-4-5"
MAX_TOKENS = 128
WORKERS    = 5

DB_URL = os.environ.get(
    "DATABASE_URL",
    "REDACTED_DSN_USE_DATABASE_URL_ENV",
)

DRY_RUN = "--dry-run" in sys.argv
VERBOSE = "--verbose" in sys.argv
LIMIT   = 100
for i, arg in enumerate(sys.argv):
    if arg == "--limit" and i + 1 < len(sys.argv):
        LIMIT = int(sys.argv[i + 1])
    if arg == "--all":
        LIMIT = 9999
    if arg == "--workers" and i + 1 < len(sys.argv):
        WORKERS = int(sys.argv[i + 1])

# Status key used to track records already processed by this script
AI_STATUS = "fields_ai_processed"

PROMPT_TEMPLATE = """Extract price and bedroom count from this UAE real estate listing.
Return ONLY JSON with these keys:
  "price"    — sale price in AED as plain integer (e.g. 1500000), or null
  "bedrooms" — integer bedroom count (studio=0, 1BR=1, 2BR=2 etc.), or null

Rules:
- 1.5M = 1500000, 750k = 750000
- If two prices (Mortgage/Cash) → take Cash price
- Rental price per year (160k/year) → still record as 160000
- Studio = 0 bedrooms
- 1BHK / 1BR / "1 bedroom" = 1
- Return null for any field you cannot find with certainty
- Return ONLY the JSON object, nothing else

Listing:
{text}"""


_print_lock = threading.Lock()


def safe_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs, flush=True)


def call_claude(text: str):
    """
    Returns dict with found fields, {} if nothing found, None on API error.
    """
    prompt = PROMPT_TEMPLATE.format(text=text[:500])
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
        if not m:
            return {}
        data = json.loads(m.group())
        result = {}
        if data.get("price") is not None:
            try:
                p = int(str(data["price"]).replace(",", "").replace(" ", ""))
                if 10_000 < p < 500_000_000:
                    result["price"] = p
            except (ValueError, TypeError):
                pass
        if data.get("bedrooms") is not None:
            try:
                b = int(data["bedrooms"])
                if 0 <= b <= 20:
                    result["bedrooms"] = b
            except (ValueError, TypeError):
                pass
        return result
    except json.JSONDecodeError as e:
        safe_print(f"    [WARN] JSON parse error: {e}")
        return None
    except Exception as e:
        safe_print(f"    [ERR] Claude API error ({type(e).__name__}): {e}")
        time.sleep(2)
        return None


def mark_done(lid: int, db_conn):
    """Insert/update review_queue row so this listing is skipped next run."""
    try:
        cur = db_conn.cursor()
        # Upsert: if row with this listing_id already exists in pending state, update it.
        # Otherwise insert a new row directly as fields_ai_processed.
        cur.execute("""
            UPDATE review_queue
               SET status = %s, reviewed_at = NOW()
             WHERE listing_id = %s AND status = 'pending'
        """, (AI_STATUS, lid))
        if cur.rowcount == 0:
            # No pending row — insert a fresh marker
            cur.execute("""
                INSERT INTO review_queue (listing_id, reason, status, reviewed_at)
                VALUES (%s, 'ai_fields_filler', %s, NOW())
                ON CONFLICT DO NOTHING
            """, (lid, AI_STATUS))
        cur.close()
    except Exception as e:
        safe_print(f"    [DB WARN] mark_done id={lid}: {e}")


def process_row(row, idx, total, db_conn):
    lid  = row["listing_id"]
    text = row["original_text"] or ""

    result = call_claude(text)
    local_stats = dict(api_calls=1, errors=0, rows_updated=0,
                       ai_found_price=0, ai_found_bedrooms=0)

    if result is None:
        local_stats["errors"] = 1
        return local_stats

    if result == {}:
        if VERBOSE:
            safe_print(f"  [{idx}/{total}] – id={lid}  (no data, marked done)")
        if not DRY_RUN:
            mark_done(lid, db_conn)
        return local_stats

    # Only write fields that are currently NULL
    updates = {}
    if row["price"] is None and result.get("price") is not None:
        updates["price"] = result["price"]
        local_stats["ai_found_price"] = 1
    if row["bedrooms"] is None and result.get("bedrooms") is not None:
        updates["bedrooms"] = result["bedrooms"]
        local_stats["ai_found_bedrooms"] = 1

    if updates:
        safe_print(f"  [{idx}/{total}] ✓ id={lid}")
        safe_print(f"      {result}  → saved: {list(updates.keys())}")
    elif VERBOSE:
        safe_print(f"  [{idx}/{total}] – id={lid}  found={result} (already filled)")

    if not DRY_RUN:
        if updates:
            set_clause = ", ".join(f"{k} = %s" for k in updates)
            values     = list(updates.values()) + [lid]
            try:
                cur = db_conn.cursor()
                cur.execute(f"UPDATE listings SET {set_clause} WHERE id = %s", values)
                cur.close()
                local_stats["rows_updated"] = 1
            except Exception as e:
                safe_print(f"    [DB ERR] id={lid}: {e}")
        mark_done(lid, db_conn)
    elif updates:
        local_stats["rows_updated"] = 1

    return local_stats


def run():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    conn.autocommit = True
    cur  = conn.cursor()

    # Select listings missing price OR bedrooms that haven't been processed yet
    cur.execute("""
        SELECT l.id      AS listing_id,
               l.original_text,
               l.price,
               l.bedrooms
        FROM   listings l
        LEFT JOIN review_queue rq
               ON rq.listing_id = l.id AND rq.status = %s
        WHERE  (l.price IS NULL OR l.bedrooms IS NULL)
          AND  l.original_text IS NOT NULL
          AND  l.original_text != ''
          AND  rq.listing_id IS NULL
        ORDER  BY l.id ASC
        LIMIT  %s
    """, (AI_STATUS, LIMIT))

    rows  = cur.fetchall()
    total = len(rows)
    safe_print(f"[ai_fields_filler] Model: {MODEL}  |  Workers: {WORKERS}  |  Rows: {total}"
               + ("  [DRY RUN]" if DRY_RUN else ""))

    if total == 0:
        print("\n  Нет записей для обработки — всё заполнено или помечено.")
        conn.close()
        return

    stats = dict(api_calls=0, errors=0, rows_updated=0,
                 ai_found_price=0, ai_found_bedrooms=0)

    worker_conns = []
    for _ in range(WORKERS):
        wconn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
        wconn.autocommit = True
        worker_conns.append(wconn)

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {}
            for i, row in enumerate(rows):
                wconn = worker_conns[i % WORKERS]
                f = executor.submit(process_row, row, i + 1, total, wconn)
                futures[f] = i

            for f in as_completed(futures):
                local_stats = f.result()
                for k in stats:
                    stats[k] += local_stats.get(k, 0)
    finally:
        for wconn in worker_conns:
            try:
                wconn.close()
            except Exception:
                pass
        conn.close()

    hit_rate = round(stats["rows_updated"] / total * 100, 1) if total else 0
    print()
    print("═" * 54)
    print("  AI FIELDS FILLER — ОТЧЁТ")
    print("═" * 54)
    print(f"  Обработано записей   : {total}")
    print(f"  API вызовов          : {stats['api_calls']}")
    print(f"  Ошибки API           : {stats['errors']}")
    print(f"  Строк обновлено      : {stats['rows_updated']}  ({hit_rate}%)")
    print()
    print("  По полям (Claude нашёл и записал):")
    print(f"    price    : {stats['ai_found_price']}")
    print(f"    bedrooms : {stats['ai_found_bedrooms']}")
    print("═" * 54)
    if DRY_RUN:
        print("  *** DRY RUN — изменения НЕ записаны ***")


if __name__ == "__main__":
    if not (os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL") or os.environ.get("ANTHROPIC_API_KEY")):
        print("WARN: No Anthropic credentials found in env. Trying anyway...")
    run()
