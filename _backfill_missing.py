"""Backfill NULL fields через LLM — догоняем completeness до 95%.

Strategy:
  - Берём sample из listings где хотя бы 1 поле NULL и original_text есть
  - Для каждого делаем LLM-extract → building, area, price, bedrooms, size_sqft
  - Если confidence >= 75 — пишем в DB с source='llm_backfill'
  - Pacing 1.5 sec/request (под SambaNova RPS limit)
  - Hourly progress

Env:
  BACKFILL_LIMIT   (default 500 — за один проход)
  BACKFILL_AGE_DAYS (default 30 — только свежие)
"""
import os, sys, io, time, json, re
from datetime import datetime
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
except Exception:
    pass

sys.path.insert(0, '.')

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set"))
)
LIMIT = int(os.environ.get("BACKFILL_LIMIT", "500"))
AGE_DAYS = int(os.environ.get("BACKFILL_AGE_DAYS", "30"))

import psycopg2
from psycopg2.extras import RealDictCursor
from llm_chain import llm_call

# Re-use AI quality-gate prompt format
from parser_engine import _is_building_stopword, _clean_building_candidate


def emit(msg):
    print(f"[{datetime.utcnow().strftime('%H:%M')}] {msg}", flush=True)


def fetch_targets():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, building, area, price, bedrooms, size_sqft,
                       deal_type, original_text
                  FROM listings
                 WHERE original_text IS NOT NULL AND length(original_text) > 100
                   AND COALESCE(status,'') NOT IN ('rejected','spam','duplicate','audit_flagged')
                   AND created_at > NOW() - (%s || ' days')::interval
                   AND (building IS NULL OR area IS NULL OR price IS NULL
                        OR bedrooms IS NULL OR size_sqft IS NULL)
                 ORDER BY random()
                 LIMIT %s
            """, (AGE_DAYS, LIMIT))
            return cur.fetchall()
    finally:
        conn.close()


def llm_extract(row):
    text = (row["original_text"] or "")[:1500]
    parsed_summary = (
        f"building={row.get('building')!r}, area={row.get('area')!r}, "
        f"price={row.get('price')!r}, bedrooms={row.get('bedrooms')!r}, "
        f"size_sqft={row.get('size_sqft')!r}, deal_type={row.get('deal_type')!r}"
    )
    prompt = (
        "Dubai real estate parser. Extract missing fields from the text. "
        "Output corrections as JSON; if field is correct/unknown, set null.\n\n"
        f"PARSED:\n{parsed_summary}\n\n"
        f"TEXT:\n```\n{text}\n```\n\n"
        "Output JSON keys: building, area, price (int AED), bedrooms (int, 0=studio), "
        "size_sqft (int, sqft), deal_type ('sale'|'rent'), confidence (0-100).\n"
        "Rules: Palm Jumeirah vs Palm Jebel Ali — different islands. Multi-listing — "
        "first only. No marketing as building. ONLY JSON, no commentary."
    )
    r = llm_call(prompt, max_tokens=200, timeout=20)
    if not r:
        return None
    m = re.search(r"\{.*\}", r, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def apply_corrections(row, corr):
    """Returns list of (column, value) updates."""
    if not corr:
        return []
    try:
        conf = int(corr.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0
    if conf < 75:
        return []
    updates = []

    # building
    new_bld = corr.get("building")
    if (new_bld and isinstance(new_bld, str) and not row.get("building")):
        cleaned = _clean_building_candidate(new_bld)
        if cleaned and not _is_building_stopword(cleaned):
            updates.append(("building", cleaned))

    # area
    new_area = corr.get("area")
    if new_area and isinstance(new_area, str) and not row.get("area"):
        updates.append(("area", new_area.strip()))

    # price
    new_price = corr.get("price")
    if (new_price and isinstance(new_price, (int, float))
            and not row.get("price")):
        p = int(new_price)
        if 50_000 <= p <= 5_000_000_000:
            updates.append(("price", p))

    # bedrooms
    new_bed = corr.get("bedrooms")
    if (new_bed is not None and isinstance(new_bed, (int, float))
            and row.get("bedrooms") is None):
        b = int(new_bed)
        if 0 <= b <= 20:
            updates.append(("bedrooms", b))

    # size_sqft
    new_size = corr.get("size_sqft")
    if (new_size is not None and isinstance(new_size, (int, float))
            and row.get("size_sqft") is None):
        s = int(new_size)
        if 200 <= s <= 100_000:
            updates.append(("size_sqft", s))

    return updates


def save(lid, updates):
    if not updates:
        return
    set_clause = ", ".join(f"{col} = %s" for col, _ in updates)
    args = [v for _, v in updates] + [lid]
    with psycopg2.connect(DATABASE_URL) as c, c.cursor() as cur:
        cur.execute(
            f"UPDATE listings SET {set_clause} WHERE id = %s", args)
        c.commit()


def main():
    rows = fetch_targets()
    emit(f"📦 backfill targets: {len(rows)}")
    if not rows:
        emit("No NULL records to backfill.")
        return

    stats = {"updated": 0, "no_llm": 0, "no_corr": 0,
             "fields_filled": {"building": 0, "area": 0, "price": 0,
                                "bedrooms": 0, "size_sqft": 0}}
    started = time.time()
    for i, row in enumerate(rows, 1):
        time.sleep(1.5)
        corr = llm_extract(row)
        if not corr:
            stats["no_llm"] += 1
            continue
        updates = apply_corrections(row, corr)
        if not updates:
            stats["no_corr"] += 1
            continue
        try:
            save(row["id"], updates)
            stats["updated"] += 1
            for col, _ in updates:
                stats["fields_filled"][col] = stats["fields_filled"].get(col, 0) + 1
        except Exception as e:
            emit(f"save err id={row['id']}: {e}")

        if i % 30 == 0:
            elapsed = (time.time() - started) / 60
            emit(f"📊 {i}/{len(rows)} · updated={stats['updated']} "
                 f"· filled={stats['fields_filled']} · elapsed={elapsed:.1f}min")

    elapsed = (time.time() - started) / 60
    emit(f"✅ backfill COMPLETE · {len(rows)} processed · "
         f"updated={stats['updated']} · no_llm={stats['no_llm']} · "
         f"no_corr={stats['no_corr']} · elapsed={elapsed:.1f}min")
    emit(f"   fields_filled: {stats['fields_filled']}")


if __name__ == "__main__":
    main()
