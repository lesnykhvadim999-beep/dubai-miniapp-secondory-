"""Phase 8 worker — обрабатывает listings_staging, promotes валидные в listings.

Каждый цикл:
  1. Берёт 30 oldest-not-tried-recently из staging
  2. Прогоняет AI quality-gate (LLM extracts missing fields)
  3. Применяет corrections + проверяет valid+complete
  4. Если все 5 critical полей OK → INSERT в listings + DELETE из staging
  5. Иначе increment attempts; после 8 попыток → переносит в listings с
     is_audit=TRUE (ручной review админом)

Pacing: 1.5 sec/LLM call. Cycle every 5 min.

Env:
  STAGING_BATCH_SIZE  (default 30)
  STAGING_CYCLE_SEC   (default 300)
  STAGING_MAX_ATTEMPTS (default 8)
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
BATCH = int(os.environ.get("STAGING_BATCH_SIZE", "8"))
CYCLE_SEC = int(os.environ.get("STAGING_CYCLE_SEC", "900"))  # 15 min
MAX_ATTEMPTS = int(os.environ.get("STAGING_MAX_ATTEMPTS", "8"))

import psycopg2
from psycopg2.extras import RealDictCursor
from llm_chain import llm_call
from parser_engine import (
    _is_building_stopword, _clean_building_candidate,
    is_spam, _validate_listing_strict,
)


def emit(msg):
    print(f"[{datetime.utcnow().strftime('%H:%M')}] {msg}", flush=True)


def fetch_batch(limit):
    """Oldest not-tried-in-last-30-min, attempts < MAX."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM listings_staging
                 WHERE staging_attempts < %s
                   AND (staging_last_try IS NULL
                        OR staging_last_try < NOW() - INTERVAL '30 minutes')
                 ORDER BY staging_attempts ASC, created_at ASC
                 LIMIT %s
            """, (MAX_ATTEMPTS, limit))
            return cur.fetchall()
    finally:
        conn.close()


def llm_extract(row):
    text = (row.get("original_text") or "")[:1500]
    if len(text) < 80:
        return None
    summary = (
        f"building={row.get('building')!r}, area={row.get('area')!r}, "
        f"price={row.get('price')!r}, bedrooms={row.get('bedrooms')!r}, "
        f"size_sqft={row.get('size_sqft')!r}, deal_type={row.get('deal_type')!r}"
    )
    prompt = (
        "Dubai real estate parser. Extract missing fields from text.\n"
        f"PARSED:\n{summary}\n\n"
        f"TEXT:\n```\n{text}\n```\n\n"
        "Output JSON keys: building, area, price (int AED), bedrooms (int, 0=studio), "
        "size_sqft (int sqft), deal_type ('sale'|'rent'), confidence (0-100), "
        "should_reject (true if spam/non-Dubai/car).\n"
        "Rules: NO marketing as building (Below OP/Iconic Views are NOT names). "
        "Palm Jumeirah vs Palm Jebel Ali — different islands. "
        "Multi-listing → first only. ONLY JSON, no commentary."
    )
    r = llm_call(prompt, max_tokens=250, timeout=20)
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
    """Returns dict of fields to update."""
    if not corr:
        return {}
    try:
        conf = int(corr.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0

    updates = {}
    if corr.get("should_reject") and conf >= 80:
        updates["__reject__"] = True
        return updates

    if conf < 70:
        return updates

    # building
    nb = corr.get("building")
    if nb and isinstance(nb, str) and not row.get("building"):
        cleaned = _clean_building_candidate(nb)
        if cleaned and not _is_building_stopword(cleaned):
            updates["building"] = cleaned

    # area
    na = corr.get("area")
    if na and isinstance(na, str) and not row.get("area"):
        updates["area"] = na.strip()

    # price
    np_ = corr.get("price")
    if np_ and isinstance(np_, (int, float)) and not row.get("price"):
        p = int(np_)
        if 50_000 <= p <= 5_000_000_000:
            updates["price"] = p

    # bedrooms
    nbed = corr.get("bedrooms")
    if (nbed is not None and isinstance(nbed, (int, float))
            and row.get("bedrooms") is None):
        b = int(nbed)
        if 0 <= b <= 20:
            updates["bedrooms"] = b

    # size_sqft
    ns = corr.get("size_sqft")
    if (ns is not None and isinstance(ns, (int, float))
            and row.get("size_sqft") is None):
        s = int(ns)
        if 200 <= s <= 100_000:
            updates["size_sqft"] = s

    return updates


def is_ready_for_promotion(row, updates):
    """Объединённые поля (existing + updates). Готовы к promotion если все
    critical поля заполнены и нет flag."""
    merged = dict(row)
    merged.update({k: v for k, v in updates.items() if not k.startswith("__")})
    crit_ok = (
        merged.get("building") and
        merged.get("area") and
        merged.get("price") and
        merged.get("bedrooms") is not None and
        merged.get("deal_type")
    )
    # size_sqft optional (not always in source text)
    # Run strict validator
    audit_reasons = _validate_listing_strict(merged)
    if audit_reasons:
        return False, audit_reasons
    return crit_ok, []


def promote(row, updates):
    """Insert merged row to listings, delete from staging."""
    merged = dict(row)
    merged.update(updates)
    # Strip staging-specific columns
    for k in ("staging_attempts", "staging_last_try", "staging_blocked_reason",
              "staging_priority", "__reject__"):
        merged.pop(k, None)

    cols = [k for k in merged.keys() if k != "id"]
    vals = [merged[k] for k in cols]
    placeholders = ", ".join(["%s"] * len(cols))
    columns_str = ", ".join(cols)

    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    f"INSERT INTO listings ({columns_str}) VALUES ({placeholders}) "
                    f"ON CONFLICT (listing_key) DO NOTHING RETURNING id",
                    vals)
                new_id = cur.fetchone()
                cur.execute("DELETE FROM listings_staging WHERE id = %s", (row["id"],))
                conn.commit()
                return new_id[0] if new_id else None
            except Exception as e:
                conn.rollback()
                raise


def increment_attempt(lid, updates, reason=None):
    """Save partial updates + bump attempts counter."""
    set_clauses = ["staging_attempts = staging_attempts + 1",
                   "staging_last_try = NOW()"]
    args = []
    for col, val in updates.items():
        if col.startswith("__"):
            continue
        set_clauses.append(f"{col} = %s")
        args.append(val)
    if reason:
        set_clauses.append("staging_blocked_reason = %s")
        args.append(reason[:300])
    args.append(lid)
    with psycopg2.connect(DATABASE_URL) as c, c.cursor() as cur:
        cur.execute(
            f"UPDATE listings_staging SET {', '.join(set_clauses)} WHERE id = %s",
            args)
        c.commit()


def reject(lid, reason):
    """Permanent reject — move to audit table or just delete."""
    with psycopg2.connect(DATABASE_URL) as c, c.cursor() as cur:
        cur.execute("DELETE FROM listings_staging WHERE id = %s", (lid,))
        c.commit()
    emit(f"🚫 rejected id={lid} · {reason[:80]}")


def cycle_once():
    rows = fetch_batch(BATCH)
    if not rows:
        return 0, 0, 0, 0
    promoted = 0
    retried = 0
    rejected = 0
    failed = 0
    for row in rows:
        time.sleep(1.5)  # pacing
        try:
            corr = llm_extract(row)
            updates = apply_corrections(row, corr) if corr else {}

            # Reject path
            if updates.get("__reject__"):
                reject(row["id"], "LLM marked as spam/non-Dubai/car")
                rejected += 1
                continue

            # Promote check
            ready, audit_reasons = is_ready_for_promotion(row, updates)
            if ready:
                try:
                    new_id = promote(row, updates)
                    if new_id:
                        promoted += 1
                    else:
                        # Duplicate by listing_key — drop from staging
                        reject(row["id"], "duplicate listing_key")
                except Exception as e:
                    emit(f"promote err id={row['id']}: {e}")
                    increment_attempt(row["id"], updates, str(e)[:200])
                    failed += 1
                continue

            # Not ready — increment + persist partial updates
            reason = "; ".join(audit_reasons) if audit_reasons else "incomplete"
            # If hit max attempts — promote with audit flag
            if row["staging_attempts"] >= MAX_ATTEMPTS - 1:
                updates["needs_manual_review"] = True
                updates["is_audit"] = True
                updates["review_reason"] = reason[:300]
                try:
                    new_id = promote(row, updates)
                    if new_id:
                        emit(f"⚠️ id={row['id']} max_attempts → moved to listings"
                             f" with is_audit=TRUE ({reason[:60]})")
                        promoted += 1
                except Exception as e:
                    emit(f"audit-promote err id={row['id']}: {e}")
                    failed += 1
            else:
                increment_attempt(row["id"], updates, reason)
                retried += 1
        except Exception as e:
            emit(f"cycle err id={row.get('id')}: {e}")
            failed += 1
    return promoted, retried, rejected, failed


def main():
    emit(f"🔄 staging-processor started · BATCH={BATCH} · CYCLE={CYCLE_SEC}s")
    while True:
        started = time.time()
        try:
            p, r, rej, f = cycle_once()
        except Exception as e:
            emit(f"cycle exception: {e}")
            p = r = rej = f = 0

        # Get total staging size
        try:
            with psycopg2.connect(DATABASE_URL) as conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM listings_staging")
                total = cur.fetchone()[0]
        except Exception:
            total = "?"

        emit(f"📊 cycle done · promoted={p} · retried={r} · rejected={rej} "
             f"· failed={f} · staging_size={total}")

        elapsed = time.time() - started
        sleep_for = max(10, CYCLE_SEC - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
