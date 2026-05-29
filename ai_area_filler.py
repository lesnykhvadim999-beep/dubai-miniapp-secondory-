#!/usr/bin/env python3
"""
ai_area_filler.py — Fill missing area/district using Claude AI.

Reads listings where area IS NULL, calls Claude Haiku, updates only NULL fields.
Tracks processed rows via review_queue status='area_ai_processed'.

Usage:
    python3 ai_area_filler.py [--limit N] [--all] [--dry-run] [--verbose] [--workers N]
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

DB_URL = os.environ.get("DATABASE_URL")
if not DB_URL:
    raise RuntimeError("DATABASE_URL required")

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

AI_STATUS = "area_ai_processed"

PROMPT_TEMPLATE = """Extract area/district from this UAE real estate listing.
Return ONLY JSON with these keys:
  "area"    — district/community name (string or null)
  "emirate" — one of: Dubai, Abu Dhabi, Sharjah, RAK, Ajman, or null

Known Dubai districts (use exact spelling):
Downtown Dubai, Business Bay, Dubai Marina, Palm Jumeirah, JVC, JVT, JLT,
Dubai Hills Estate, Creek Harbour, MBR City, DIFC, Al Furjan, DAMAC Hills,
DAMAC Hills 2, Sobha Hartland, Sports City, Motor City, Arabian Ranches,
Meydan, Dubai South, Bur Dubai, Deira, Al Barsha, Discovery Gardens,
International City, Silicon Oasis, Production City, Town Square, Al Quoz,
Jumeirah, Umm Suqeim, The Springs, The Meadows, The Lakes, Emirates Hills,
Al Nahda, Muhaisnah, Al Rashidiya, Oud Metha, Jumeirah Islands,
Jumeirah Golf Estates, Dubai Investment Park, Warsan

Abbreviations: JVC=Jumeirah Village Circle, JLT=Jumeirah Lake Towers,
JVT=Jumeirah Village Triangle, DCH=Dubai Creek Harbour, MBR=MBR City,
DIP=Dubai Investment Park, DH=Dubai Hills Estate

Rules:
- Return the district/community, not the building name
- If only a building name is mentioned and you know its area, return the area
- Return null if genuinely not found — do NOT guess
- Return ONLY the JSON object, nothing else

Listing:
{text}"""

SKIP_AREAS = {"null", "n/a", "none", "unknown", "uae", "dubai", "abu dhabi",
              "sharjah", "rak", "ajman", "emirates"}

_print_lock = threading.Lock()


def safe_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs, flush=True)


def call_claude(text: str):
    """Returns dict with found fields, {} if nothing, None on API error."""
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
        if data.get("area") and isinstance(data["area"], str):
            a = data["area"].strip()
            if a.lower() not in SKIP_AREAS and len(a) >= 2:
                result["area"] = a
        if data.get("emirate") and isinstance(data["emirate"], str):
            e = data["emirate"].strip()
            if e.lower() not in SKIP_AREAS and len(e) >= 2:
                result["emirate"] = e
        return result
    except json.JSONDecodeError as e:
        safe_print(f"    [WARN] JSON parse error: {e}")
        return None
    except Exception as e:
        safe_print(f"    [ERR] Claude API error ({type(e).__name__}): {e}")
        time.sleep(2)
        return None


def mark_done(lid: int, db_conn):
    try:
        cur = db_conn.cursor()
        cur.execute("""
            UPDATE review_queue
               SET status = %s, reviewed_at = NOW()
             WHERE listing_id = %s AND status = 'pending'
        """, (AI_STATUS, lid))
        if cur.rowcount == 0:
            cur.execute("""
                INSERT INTO review_queue (listing_id, reason, status, reviewed_at)
                VALUES (%s, 'ai_area_filler', %s, NOW())
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
                       ai_found_area=0, ai_found_emirate=0)

    if result is None:
        local_stats["errors"] = 1
        return local_stats

    if result == {}:
        if VERBOSE:
            safe_print(f"  [{idx}/{total}] – id={lid}  (no area found, marked done)")
        if not DRY_RUN:
            mark_done(lid, db_conn)
        return local_stats

    updates = {}
    if (row["area"] is None or row["area"] == "") and result.get("area"):
        updates["area"] = result["area"]
        local_stats["ai_found_area"] = 1
    if (row["emirate"] is None or row["emirate"] == "") and result.get("emirate"):
        updates["emirate"] = result["emirate"]
        local_stats["ai_found_emirate"] = 1

    if updates:
        safe_print(f"  [{idx}/{total}] ✓ id={lid}")
        safe_print(f"      {result}  → saved: {list(updates.keys())}")
    elif VERBOSE:
        safe_print(f"  [{idx}/{total}] – id={lid}  found={result} (fields already filled)")

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

    cur.execute("""
        SELECT l.id      AS listing_id,
               l.original_text,
               l.area,
               l.emirate
        FROM   listings l
        LEFT JOIN review_queue rq
               ON rq.listing_id = l.id AND rq.status = %s
        WHERE  (l.area IS NULL OR l.area = '')
          AND  l.original_text IS NOT NULL
          AND  l.original_text != ''
          AND  rq.listing_id IS NULL
        ORDER  BY l.id ASC
        LIMIT  %s
    """, (AI_STATUS, LIMIT))

    rows  = cur.fetchall()
    total = len(rows)
    safe_print(f"[ai_area_filler] Model: {MODEL}  |  Workers: {WORKERS}  |  Rows: {total}"
               + ("  [DRY RUN]" if DRY_RUN else ""))

    if total == 0:
        print("\n  Нет записей для обработки — всё заполнено или помечено.")
        conn.close()
        return

    stats = dict(api_calls=0, errors=0, rows_updated=0,
                 ai_found_area=0, ai_found_emirate=0)

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
    print("  AI AREA FILLER — ОТЧЁТ")
    print("═" * 54)
    print(f"  Обработано записей   : {total}")
    print(f"  API вызовов          : {stats['api_calls']}")
    print(f"  Ошибки API           : {stats['errors']}")
    print(f"  Строк обновлено      : {stats['rows_updated']}  ({hit_rate}%)")
    print()
    print("  По полям (Claude нашёл и записал):")
    print(f"    area    : {stats['ai_found_area']}")
    print(f"    emirate : {stats['ai_found_emirate']}")
    print("═" * 54)
    if DRY_RUN:
        print("  *** DRY RUN — изменения НЕ записаны ***")


if __name__ == "__main__":
    if not (os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL") or os.environ.get("ANTHROPIC_API_KEY")):
        print("WARN: No Anthropic credentials found in env. Trying anyway...")
    run()
