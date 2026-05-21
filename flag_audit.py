# -*- coding: utf-8 -*-
"""
Прогон всей базы и flagging записей в audit-категорию.

Критерии для is_audit=TRUE (записи скрыты от пользователей):
  1. Нет building AND нет price AND нет area (полный мусор)
  2. Price отличается от DLD building median >50% (price suspect)
  3. Price < 50k AED для sale (явный mis-parse)
  4. Price > 100M AED (кроме hotel/plot — legit cases)
  5. Sale residential без building И без area И без size

В audit_reason записывается человеко-читаемая причина.

Этот скрипт можно запускать ежедневно через cron / admin command /auditreview
для просмотра свежих audit-записей.
"""
import sys, time
sys.stdout.reconfigure(encoding='utf-8')
import psycopg2
from parser_engine import _lookup_benchmark

DB_URL = 'REDACTED_DSN_USE_DATABASE_URL_ENV'

def classify(row):
    """Returns (is_audit: bool, reason: str | None)."""
    rid, dt, pt, p, building, area, emirate, size, br = row

    reasons = []

    # 1. Полный мусор — нет ничего
    if not building and not price_set(p) and not area:
        reasons.append("no_building_price_area")

    # 2. Sale residential без building+area+size — невозможно найти / показать
    if dt == 'sale' and pt in ('apartment','studio','villa','townhouse','penthouse','duplex'):
        if not building and not area and not size:
            reasons.append("residential_no_basic_info")

    # 3. Price > 100M AED для residential (не hotel/plot)
    if p and p > 100_000_000 and pt not in ('hotel','plot'):
        reasons.append(f"price_too_high_{p}")

    # 4. Price < 50k AED для sale (mis-parse)
    if dt == 'sale' and p and p < 50_000 and pt != 'plot':
        reasons.append(f"sale_price_too_low_{p}")

    # 5. Rent > 5M / year (явный sale захвачен как rent)
    if dt == 'rent' and p and p > 5_000_000:
        reasons.append(f"rent_too_high_{p}")

    # 6. DLD comparison — building в benchmarks, diff > 50%
    if building and p and dt == 'sale' and p > 100_000:
        bench = _lookup_benchmark(building)
        if bench:
            med = bench.get('sale_median')
            count = bench.get('sale_count') or 0
            if med and count >= 5:
                diff_pct = abs(p - med) / med * 100
                if diff_pct > 50:
                    direction = "above" if p > med else "below"
                    reasons.append(f"dld_diff_{int(diff_pct)}%_{direction}_median_{med}")

    # 7. Rent DLD comparison
    if building and p and dt == 'rent' and p > 5000:
        bench = _lookup_benchmark(building)
        if bench:
            rent_med = bench.get('rent_median_yearly')
            if rent_med:
                diff_pct = abs(p - rent_med) / rent_med * 100
                if diff_pct > 50:
                    direction = "above" if p > rent_med else "below"
                    reasons.append(f"dld_rent_diff_{int(diff_pct)}%_{direction}_median_{rent_med}")

    return (len(reasons) > 0, "; ".join(reasons) if reasons else None)


def price_set(p):
    return p is not None and p > 0


def main():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # Reset previous audit flags (so re-run is idempotent)
    cur.execute("UPDATE listings SET is_audit=FALSE, audit_reason=NULL, audit_flagged_at=NULL WHERE is_active=TRUE")
    print(f"Reset {cur.rowcount} existing audit flags", flush=True)

    cur.execute("""
        SELECT id, deal_type, property_type, price, building, area, emirate, size_sqft, bedrooms
        FROM listings WHERE is_active=TRUE
    """)
    rows = cur.fetchall()
    print(f"Scanning {len(rows)} active records...", flush=True)

    flagged = 0
    reasons_count = {}
    start = time.time()
    for row in rows:
        is_audit, reason = classify(row)
        if is_audit:
            cur.execute(
                "UPDATE listings SET is_audit=TRUE, audit_reason=%s, audit_flagged_at=NOW() WHERE id=%s",
                (reason, row[0])
            )
            flagged += 1
            # Count main reason buckets
            for r in reason.split("; "):
                # Trim numeric suffixes for bucketing
                bucket = r.split("_")[0] + "_" + r.split("_")[1] if "_" in r else r
                key = "_".join(bucket.split("_")[:3])
                reasons_count[key] = reasons_count.get(key, 0) + 1

    conn.commit()
    print(f"\n[done] flagged {flagged}/{len(rows)} ({flagged*100/len(rows):.1f}%) in {time.time()-start:.0f}s")
    print()
    print("Top reasons:")
    for k, v in sorted(reasons_count.items(), key=lambda x: -x[1])[:15]:
        print(f"  {v:5d}  {k}")
    print()

    # Final visible (non-audit) count
    cur.execute("SELECT COUNT(*) FROM listings WHERE is_active=TRUE AND (is_audit IS NULL OR is_audit=FALSE)")
    visible = cur.fetchone()[0]
    print(f"Visible to users (non-audit): {visible}")
    print(f"Hidden in audit:             {flagged}")
    conn.close()


if __name__ == "__main__":
    main()
