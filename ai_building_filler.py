#!/usr/bin/env python3
"""
ai_building_filler.py — Fill missing building names using Claude AI.

Reads listings with building=NULL from listings table (via review_queue join),
sends original_text to Claude Haiku, extracts building/area/bedrooms/price,
updates listings table only when field is currently NULL/empty.

Usage:
    python3 ai_building_filler.py [--limit N] [--all] [--dry-run] [--verbose] [--workers N]

Env vars (auto-set by Replit AI Integration):
    AI_INTEGRATIONS_ANTHROPIC_BASE_URL
    AI_INTEGRATIONS_ANTHROPIC_API_KEY
Also works with plain ANTHROPIC_API_KEY.
"""
import os, sys, re, json, time, threading
import psycopg2
from psycopg2.extras import RealDictCursor
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Anthropic client setup ────────────────────────────────────────────────────
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
MAX_TOKENS = 256
WORKERS    = 5   # concurrent API calls

# ── DB ────────────────────────────────────────────────────────────────────────
DB_URL = os.environ.get("DATABASE_URL")
if not DB_URL:
    raise RuntimeError("DATABASE_URL required")

# ── CLI flags ─────────────────────────────────────────────────────────────────
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


PROMPT_TEMPLATE = """You are a UAE real estate data extractor.
Extract ONLY what is explicitly stated in this listing. Do NOT guess or invent.

Return JSON with exactly these keys:
  "building"  — specific building/tower/project name (string or null)
  "area"      — district/community name (string or null)
  "bedrooms"  — integer number of bedrooms, 0 = studio (integer or null)
  "price"     — price in AED as plain integer, no commas (integer or null)

Rules:
- building must be a proper name (e.g. "Binghatti Corner", "Golf Place 1"), NOT generic words like "villa" or "apartment"
- area must be a Dubai/UAE district name (e.g. "JVC", "Business Bay", "Dubai Hills Estate")
- Return null for any field you cannot find with certainty
- Return ONLY the JSON object, nothing else

Listing:
{text}"""

SKIP_BUILDINGS = {"villa", "apartment", "flat", "unit", "studio", "townhouse",
                  "penthouse", "duplex", "null", "n/a", "none", "unknown", "property"}

_print_lock = threading.Lock()


def safe_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs, flush=True)


def call_claude(text: str):
    """Call Claude Haiku and parse JSON. Returns dict (possibly empty) or None on API error."""
    prompt = PROMPT_TEMPLATE.format(text=text[:600])
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
        if data.get("building") and isinstance(data["building"], str):
            b = data["building"].strip()
            if b.lower() not in SKIP_BUILDINGS and len(b) >= 3:
                result["building"] = b
        if data.get("area") and isinstance(data["area"], str):
            a = data["area"].strip()
            if len(a) >= 2:
                result["area"] = a
        if data.get("bedrooms") is not None:
            try:
                result["bedrooms"] = int(data["bedrooms"])
            except (ValueError, TypeError):
                pass
        if data.get("price") is not None:
            try:
                p = int(str(data["price"]).replace(",", "").replace(" ", ""))
                if p > 10_000:
                    result["price"] = p
            except (ValueError, TypeError):
                pass
        return result
    except json.JSONDecodeError as e:
        safe_print(f"    [WARN] JSON parse error: {e}", flush=True)
        return None
    except Exception as e:
        safe_print(f"    [ERR] Claude API error ({type(e).__name__}): {e}", flush=True)
        time.sleep(2)
        return None


def _is_empty(val) -> bool:
    return val is None or (isinstance(val, str) and not val.strip())


def mark_ai_processed(lid: int, db_conn):
    """Mark review_queue row as ai_processed so it is skipped on future runs."""
    try:
        cur = db_conn.cursor()
        cur.execute(
            "UPDATE review_queue SET status = 'ai_processed' WHERE listing_id = %s AND status = 'pending'",
            (lid,),
        )
        cur.close()
    except Exception as e:
        safe_print(f"    [DB WARN] mark_ai_processed id={lid}: {e}")


def process_row(row, idx, total, db_conn):
    """Process a single listing row. Returns stats_dict."""
    lid  = row["listing_id"]
    text = row["original_text"] or ""

    result = call_claude(text)
    local_stats = dict(api_calls=1, errors=0, rows_updated=0,
                       ai_found_building=0, ai_found_area=0,
                       ai_found_bedrooms=0, ai_found_price=0)

    if result is None:
        # API error — do NOT mark as processed, retry next time
        local_stats["errors"] = 1
        return local_stats

    if result == {}:
        # Claude found nothing — mark as processed so we skip next time
        if VERBOSE:
            snippet = text[:70].replace("\n", " ")
            safe_print(f"  [{idx}/{total}] – id={lid}  (no data found, marked processed)")
            safe_print(f"      text: {snippet!r}")
        if not DRY_RUN:
            mark_ai_processed(lid, db_conn)
        return local_stats

    # Build updates (only fill NULL fields)
    updates = {}
    for field in ("building", "area"):
        if _is_empty(row[field]) and result.get(field):
            updates[field] = result[field]
            local_stats[f"ai_found_{field}"] = 1
    if _is_empty(row["bedrooms"]) and result.get("bedrooms") is not None:
        updates["bedrooms"] = result["bedrooms"]
        local_stats["ai_found_bedrooms"] = 1
    if _is_empty(row["price"]) and result.get("price"):
        updates["price"] = result["price"]
        local_stats["ai_found_price"] = 1

    if updates:
        found  = {k: v for k, v in result.items() if v is not None}
        saved  = list(updates.keys())
        safe_print(f"  [{idx}/{total}] ✓ id={lid}")
        safe_print(f"      {found}  → saved: {saved}")
    elif VERBOSE:
        found = {k: v for k, v in result.items() if v is not None}
        safe_print(f"  [{idx}/{total}] – id={lid}  found={found} (building not in text)")

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
        # Mark processed regardless — building field either filled or genuinely absent
        mark_ai_processed(lid, db_conn)
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
               l.building,
               l.area,
               l.bedrooms,
               l.price
        FROM   listings l
        LEFT JOIN review_queue rq
               ON rq.listing_id = l.id AND rq.status = 'ai_processed'
        WHERE  (l.building IS NULL OR l.building = '')
          AND  l.original_text IS NOT NULL
          AND  l.original_text != ''
          AND  rq.listing_id IS NULL    -- exclude already-processed ones
        ORDER  BY l.id ASC
        LIMIT  %s
    """, (LIMIT,))

    rows  = cur.fetchall()
    total = len(rows)
    safe_print(f"[ai_filler] Model: {MODEL}  |  Workers: {WORKERS}  |  Rows: {total}"
               + ("  [DRY RUN]" if DRY_RUN else ""))

    stats = dict(api_calls=0, errors=0, rows_updated=0,
                 ai_found_building=0, ai_found_area=0,
                 ai_found_bedrooms=0, ai_found_price=0)

    # One DB connection per worker (psycopg2 connections are not thread-safe)
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

    # ── Final report ──────────────────────────────────────────────────────────
    hit_rate = round(stats["rows_updated"] / total * 100, 1) if total else 0
    print()
    print("═" * 54)
    print("  AI BUILDING FILLER — ОТЧЁТ")
    print("═" * 54)
    print(f"  Обработано записей   : {total}")
    print(f"  API вызовов          : {stats['api_calls']}")
    print(f"  Ошибки API           : {stats['errors']}")
    print(f"  Строк обновлено      : {stats['rows_updated']}  ({hit_rate}%)")
    print()
    print("  По полям (Claude нашёл и записал):")
    print(f"    building   : {stats['ai_found_building']}")
    print(f"    area       : {stats['ai_found_area']}")
    print(f"    bedrooms   : {stats['ai_found_bedrooms']}")
    print(f"    price      : {stats['ai_found_price']}")
    print("═" * 54)
    if DRY_RUN:
        print("  *** DRY RUN — изменения НЕ записаны ***")
    elif total > 0 and stats["rows_updated"] == 0:
        print("  (0 updates — all remaining rows may lack building info)")


if __name__ == "__main__":
    if not (os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL") or os.environ.get("ANTHROPIC_API_KEY")):
        print("WARN: No Anthropic credentials found in env. Trying anyway...")
    run()
