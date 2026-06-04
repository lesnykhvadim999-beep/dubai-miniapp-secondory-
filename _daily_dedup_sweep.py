"""
B089: Daily dedup sweep — автоматически чистит broker reposts.

Запуск: ежедневно через cron (Railway / staging-processor).
Soft-delete (is_active=FALSE + status='dupe_*') — НЕ DELETE.

Стратегии:
  A) Same broker + same building + price + br + size + area → keep newest
  B) Same exact original_text → keep newest
  C) Same phone + building + price → keep newest
  D) Auto-detect new spam patterns (10+ копий one bld+price) → blocklist
  E) Auto-blocklist брокеров с >50 active

Логирует в parser_audit_log.
"""
import os
import psycopg2
import psycopg2.extras
import sys
from datetime import datetime

DSN = os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL")
if not DSN:
    print("[dedup-sweep] no DATABASE_URL — skipping")
    sys.exit(0)


def main():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    t0 = datetime.utcnow()

    stats = {"broker_reposts": 0, "text_reposts": 0, "phone_reposts": 0,
             "new_spam_patterns": 0, "auto_blocked_brokers": 0}

    # A) Same broker + all fields
    cur.execute("""
    WITH active AS (
      SELECT id, COALESCE(phone, agent_name, seller_username, '') AS broker_id,
             deal_type, property_type, area, building, bedrooms,
             size_sqft, price, currency
      FROM listings WHERE is_active=TRUE
    ),
    ranked AS (
      SELECT id, ROW_NUMBER() OVER (
        PARTITION BY broker_id, deal_type, property_type, area, building,
                     bedrooms, size_sqft, price, currency
        ORDER BY id DESC
      ) rn FROM active WHERE broker_id != ''
    )
    UPDATE listings SET is_active=FALSE, status='dupe_broker_repost',
                        updated_at=NOW()
    WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
    """)
    stats["broker_reposts"] = cur.rowcount

    # B) Same exact original_text
    cur.execute("""
    WITH d AS (
      SELECT id, ROW_NUMBER() OVER (
        PARTITION BY MD5(original_text) ORDER BY id DESC
      ) rn FROM listings
      WHERE is_active=TRUE AND original_text IS NOT NULL
        AND LENGTH(original_text) > 100
    )
    UPDATE listings SET is_active=FALSE, status='dupe_text', updated_at=NOW()
    WHERE id IN (SELECT id FROM d WHERE rn > 1)
    """)
    stats["text_reposts"] = cur.rowcount

    # C) Same phone + building + price
    cur.execute("""
    WITH d AS (
      SELECT id, ROW_NUMBER() OVER (
        PARTITION BY phone, building, price ORDER BY id DESC
      ) rn FROM listings
      WHERE is_active=TRUE AND phone IS NOT NULL
        AND building IS NOT NULL AND price IS NOT NULL
    )
    UPDATE listings SET is_active=FALSE, status='dupe_agent_repost',
                        updated_at=NOW()
    WHERE id IN (SELECT id FROM d WHERE rn > 1)
    """)
    stats["phone_reposts"] = cur.rowcount

    # D) Auto-detect spam patterns — smart: 10+ копий AND <=2 brokers AND <=7d
    cur.execute("""
    INSERT INTO parser_blocklist(kind, pattern, reason, added_by)
    SELECT 'spam_pattern',
           LOWER(building) || '|' || price::bigint::text,
           COUNT(*)::text || ' copies, ' ||
           COUNT(DISTINCT COALESCE(phone, agent_name, seller_username))::text || ' brokers, '
           || ROUND(EXTRACT(EPOCH FROM (MAX(created_at)-MIN(created_at)))/86400)::text || 'd',
           'daily-sweep-smart'
    FROM listings
    WHERE is_active=TRUE AND building IS NOT NULL AND price IS NOT NULL
    GROUP BY LOWER(building), price
    HAVING COUNT(*) >= 10
       AND COUNT(DISTINCT COALESCE(phone, agent_name, seller_username)) <= 2
       AND EXTRACT(EPOCH FROM (MAX(created_at)-MIN(created_at)))/86400 <= 7
    ON CONFLICT(kind, pattern) DO NOTHING
    """)
    stats["new_spam_patterns"] = cur.rowcount

    # E) Auto-blocklist brokers — smart: total>150 AND diversity<30%
    cur.execute("""
    INSERT INTO parser_blocklist(kind, pattern, reason, added_by)
    SELECT 'broker',
           LOWER(COALESCE(phone, agent_name, seller_username)),
           'autobot ' || COUNT(*)::text || ' total, ' ||
           COUNT(DISTINCT (building||price::text))::text || ' unique (' ||
           ROUND(100.0*COUNT(DISTINCT (building||price::text))/COUNT(*))::text || '%)',
           'daily-sweep-smart'
    FROM listings
    WHERE is_active=TRUE
      AND COALESCE(phone, agent_name, seller_username) IS NOT NULL
      AND LENGTH(COALESCE(phone, agent_name, seller_username)) > 3
      -- skip whitelisted
      AND LOWER(COALESCE(phone, agent_name, seller_username)) NOT IN
          (SELECT pattern FROM parser_whitelist WHERE kind='broker')
    GROUP BY LOWER(COALESCE(phone, agent_name, seller_username))
    HAVING COUNT(*) > 150
       AND 100.0 * COUNT(DISTINCT (building||price::text)) / COUNT(*) < 30
    ON CONFLICT(kind, pattern) DO NOTHING
    """)
    stats["auto_blocked_brokers"] = cur.rowcount

    dt = (datetime.utcnow() - t0).total_seconds()
    print(f"[dedup-sweep] {datetime.utcnow().isoformat()}Z done in {dt:.1f}s",
          flush=True)
    for k, v in stats.items():
        print(f"  {k}: {v}", flush=True)

    # Audit log
    try:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS parser_audit_log (
          id SERIAL PRIMARY KEY,
          ran_at TIMESTAMP DEFAULT NOW(),
          job TEXT,
          stats JSONB,
          duration_sec NUMERIC
        )
        """)
        import json
        cur.execute("INSERT INTO parser_audit_log(job, stats, duration_sec) VALUES(%s, %s, %s)",
                    ("daily_dedup_sweep", json.dumps(stats), dt))
    except Exception as e:
        print(f"[dedup-sweep] audit log fail: {e}")

    conn.close()


if __name__ == "__main__":
    main()
