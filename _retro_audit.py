"""Retro-audit worker — verifies parsed listings against original_text via LLM.

Берёт случайный сэмпл из каждого ценового сегмента, проверяет:
  - price корректно извлечён?
  - building/area соответствует original_text?
  - deal_type правильный (sale/rent)?
  - bedrooms / size_sqft не противоречат?

При несоответствии — flags as audit_flagged.
Каждый час — progress report через stdout (для Monitor → push notif).

Запуск:
    PYTHONIOENCODING=utf-8 DATABASE_URL=... python -u _retro_audit.py

Env:
  AUDIT_BATCH_SIZE       (default 20)
  AUDIT_REPORT_EVERY_SEC (default 3600)
  AUDIT_SAMPLE_LIMIT     (default 500 — сколько проверить всего)
"""
import os, sys, io, time, json, random
from datetime import datetime

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
except Exception:
    pass

sys.path.insert(0, '.')

DATABASE_URL = os.environ.get("DATABASE_URL")
BATCH_SIZE = int(os.environ.get("AUDIT_BATCH_SIZE", "20"))
REPORT_EVERY = int(os.environ.get("AUDIT_REPORT_EVERY_SEC", "3600"))
SAMPLE_LIMIT = int(os.environ.get("AUDIT_SAMPLE_LIMIT", "500"))

assert DATABASE_URL, "DATABASE_URL not set"

import psycopg2
from psycopg2.extras import RealDictCursor

try:
    from llm_chain import llm_call
except ImportError:
    print("[audit] llm_chain unavailable — abort", flush=True)
    sys.exit(1)


def emit(msg):
    print(f"[{datetime.utcnow().strftime('%H:%M')}] {msg}", flush=True)


def fetch_sample(limit, exclude_ids=None):
    """Случайный сэмпл из каждого ценового сегмента, исключая уже проверенные."""
    exclude_ids = list(exclude_ids or [])
    tiers = [
        (0, 500_000),
        (500_000, 1_500_000),
        (1_500_000, 3_000_000),
        (3_000_000, 6_000_000),
        (6_000_000, 15_000_000),
        (15_000_000, 1_000_000_000),
    ]
    per_tier = max(1, limit // len(tiers))
    rows = []
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            for low, high in tiers:
                query = """
                    SELECT id, building, area, price, deal_type, bedrooms,
                           size_sqft, original_text, status
                      FROM listings
                     WHERE price > %s AND price <= %s
                       AND original_text IS NOT NULL AND length(original_text) > 50
                       AND (deal_type = 'sale' OR deal_type IS NULL)
                       AND COALESCE(status, '') NOT IN ('rejected', 'spam', 'duplicate', 'audit_flagged')
                       AND created_at > NOW() - INTERVAL '7 days'
                """
                args = [low, high]
                if exclude_ids:
                    query += " AND id NOT IN %s"
                    args.append(tuple(exclude_ids))
                query += " ORDER BY random() LIMIT %s"
                args.append(per_tier)
                cur.execute(query, args)
                rows.extend(cur.fetchall())
    finally:
        conn.close()
    random.shuffle(rows)
    return rows[:limit]


def verify_listing(row):
    """LLM проверка vs original_text.

    Returns dict: {ok: bool, reason: str, suggested_price/building/area: ...}
    """
    text = (row.get("original_text") or "")[:1200]
    parsed = {
        "building": row.get("building"),
        "area": row.get("area"),
        "price_aed": int(row["price"]) if row.get("price") else None,
        "bedrooms": row.get("bedrooms"),
        "size_sqft": row.get("size_sqft"),
        "deal_type": row.get("deal_type"),
    }

    prompt = f"""You are a Dubai real estate listing auditor. Verify if the PARSED data matches the ORIGINAL listing text.

ORIGINAL TEXT:
{text}

PARSED DATA:
- Building: {parsed['building']}
- Area: {parsed['area']}
- Price (AED): {('{:,}'.format(parsed['price_aed']) if parsed['price_aed'] else 'None')}
- Bedrooms: {parsed['bedrooms']}
- Size: {parsed['size_sqft']} sqft
- Deal type: {parsed['deal_type']}

Task: Output a JSON object with these fields:
- "verdict": "ok" | "wrong_price" | "wrong_building" | "wrong_area" | "wrong_deal_type" | "wrong_bedrooms" | "multiple_issues" | "spam"
- "issues": [list of brief issue descriptions, e.g., "price 6.6M is service charge, not sale price"]
- "confidence": 0-100 integer
- "real_price_aed": int if you can extract correct price, else null
- "real_building": string if wrong, else null

ONLY output valid JSON. No prose."""

    result = llm_call(prompt, max_tokens=180, timeout=20)
    if not result:
        return None
    # Extract JSON
    import re
    m = re.search(r"\{.*\}", result, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def flag_listing(lid, reason):
    """Помечает запись как audit_flagged."""
    try:
        with psycopg2.connect(DATABASE_URL) as c, c.cursor() as cur:
            cur.execute(
                "UPDATE listings SET status = 'audit_flagged', audit_reason = %s, "
                "audit_flagged_at = NOW() WHERE id = %s",
                (reason[:300], lid),
            )
            c.commit()
    except Exception as e:
        emit(f"flag err id={lid}: {e}")


def main():
    emit(f"🔍 retro-audit started · BATCH={BATCH_SIZE} · LIMIT={SAMPLE_LIMIT} · REPORT={REPORT_EVERY}s")
    started = time.time()
    last_report = started
    total_checked = 0
    total_flagged = 0
    total_ok = 0
    total_unable = 0
    by_verdict = {}
    flagged_ids = []

    while total_checked < SAMPLE_LIMIT:
        batch = fetch_sample(BATCH_SIZE, exclude_ids=flagged_ids[-500:])
        if not batch:
            emit("⏸️ no more records to audit — stopping")
            break

        for row in batch:
            if total_checked >= SAMPLE_LIMIT:
                break
            # Throttle: SambaNova free-tier ~1 RPS, Groq ~30 RPM.
            # Pause 1.2s — keeps both providers happy and stretches daily quota.
            time.sleep(1.2)
            verdict_data = verify_listing(row)
            total_checked += 1
            if not verdict_data:
                total_unable += 1
                # If we hit many consecutive failures — long pause (5min) to
                # let provider cooldowns expire.
                if total_unable >= 10 and total_unable % 10 == 0:
                    emit(f"⚠️ {total_unable} unable in a row — pausing 300s for cooldowns")
                    time.sleep(300)
                continue
            verdict = verdict_data.get("verdict", "?")
            by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
            if verdict == "ok":
                total_ok += 1
            elif verdict in ("wrong_price", "wrong_building", "wrong_area",
                              "wrong_deal_type", "wrong_bedrooms",
                              "multiple_issues", "spam"):
                issues = verdict_data.get("issues") or []
                reason = f"{verdict}: " + " · ".join(issues[:3])
                flag_listing(row["id"], reason)
                flagged_ids.append(row["id"])
                total_flagged += 1
                # Emit bug alert immediately if confidence > 75
                conf = verdict_data.get("confidence", 0)
                if conf > 75:
                    bld = (row.get("building") or "?")[:25]
                    emit(f"🐛 id={row['id']} {verdict} (conf={conf}%) bld={bld} "
                         f"price={row.get('price'):,} → {reason[:100]}")

        # Hourly progress report
        now = time.time()
        if (now - last_report) >= REPORT_EVERY:
            elapsed = (now - started) / 60
            acc = total_ok * 100 / max(total_checked, 1)
            emit(f"📊 progress · checked={total_checked}/{SAMPLE_LIMIT} · "
                 f"OK={total_ok} ({acc:.0f}%) · flagged={total_flagged} · "
                 f"unable={total_unable} · elapsed={elapsed:.1f}min")
            emit(f"   breakdown: {dict(sorted(by_verdict.items(), key=lambda x: -x[1])[:6])}")
            last_report = now

    elapsed_min = (time.time() - started) / 60
    acc = total_ok * 100 / max(total_checked, 1)
    emit(f"✅ retro-audit COMPLETE · checked={total_checked} · OK={total_ok} ({acc:.0f}%) "
         f"· flagged={total_flagged} · elapsed={elapsed_min:.1f}min")
    emit(f"   final breakdown: {dict(sorted(by_verdict.items(), key=lambda x: -x[1]))}")


if __name__ == "__main__":
    main()
