"""Phase 8 — создаёт listings_staging как зеркало listings.

Архитектура:
  parser → если все critical fields OK → listings (видна юзерам)
         → если что-то NULL/suspicious → listings_staging (фоновая доводка)
  worker: каждые 5 мин — берёт oldest staging, прогоняет AI quality-gate,
          если стал valid → promote в listings + delete из staging.
"""
import os, sys, io
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
except Exception:
    pass

import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set"))
)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS listings_staging (
    -- Mirror of listings columns (LIKE doesn't work cleanly with PK conflicts)
    id            BIGSERIAL PRIMARY KEY,
    listing_key   VARCHAR,
    source        VARCHAR,
    telegram_chat_id    VARCHAR,
    telegram_message_id BIGINT,
    message_date  TIMESTAMP WITH TIME ZONE,
    original_text TEXT,
    seller_username VARCHAR,
    deal_type     VARCHAR,
    property_type VARCHAR,
    emirate       VARCHAR,
    emirate_confidence DOUBLE PRECISION,
    area          TEXT,
    area_confidence    DOUBLE PRECISION,
    building      TEXT,
    building_confidence DOUBLE PRECISION,
    location_confidence DOUBLE PRECISION,
    needs_manual_review BOOLEAN,
    review_reason TEXT,
    bedrooms      INTEGER,
    bathrooms     INTEGER,
    size_sqft     DOUBLE PRECISION,
    bua_sqft      DOUBLE PRECISION,
    plot_sqft     DOUBLE PRECISION,
    floor         INTEGER,
    unit_number   VARCHAR,
    view          VARCHAR,
    furnishing    VARCHAR,
    status        VARCHAR,
    price         BIGINT,
    currency      VARCHAR,
    original_price BIGINT,
    selling_price  BIGINT,
    price_per_sqft DOUBLE PRECISION,
    discount_amount BIGINT,
    discount_percent DOUBLE PRECISION,
    is_hot_deal   BOOLEAN,
    deal_quality  VARCHAR,
    deal_reason   TEXT,
    is_below_market BOOLEAN,
    price_vs_market_percent DOUBLE PRECISION,
    market_avg_sqft DOUBLE PRECISION,
    market_rent_1br BIGINT,
    market_growth_pct DOUBLE PRECISION,
    roi_estimate  DOUBLE PRECISION,
    airbnb_estimate_low  BIGINT,
    airbnb_estimate_high BIGINT,
    investment_score DOUBLE PRECISION,
    agent_name    VARCHAR,
    phone         VARCHAR,
    whatsapp      VARCHAR,
    has_images    BOOLEAN,
    cover_image_url TEXT,
    confidence_score DOUBLE PRECISION,
    is_duplicate_message BOOLEAN,
    possible_duplicate_id INTEGER,
    price_drop_detected BOOLEAN,
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_active     BOOLEAN DEFAULT TRUE,
    alerted       BOOLEAN DEFAULT FALSE,
    is_audit      BOOLEAN DEFAULT FALSE,
    audit_reason  TEXT,
    audit_flagged_at TIMESTAMP WITH TIME ZONE,
    description   TEXT,
    extra_info    JSONB,
    is_off_plan   BOOLEAN,
    handover_date DATE,
    photo_phash   VARCHAR,
    broker_dedup_key VARCHAR,
    is_frozen     BOOLEAN DEFAULT FALSE,
    -- Staging-specific columns
    staging_attempts INTEGER DEFAULT 0,
    staging_last_try TIMESTAMP WITH TIME ZONE,
    staging_blocked_reason TEXT,
    staging_priority INTEGER DEFAULT 5  -- 1=urgent, 5=normal, 9=last-resort
);

CREATE INDEX IF NOT EXISTS idx_staging_attempts ON listings_staging(staging_attempts, staging_last_try);
CREATE INDEX IF NOT EXISTS idx_staging_created  ON listings_staging(created_at);
CREATE INDEX IF NOT EXISTS idx_staging_listing_key ON listings_staging(listing_key);
"""

with psycopg2.connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
        cur.execute(CREATE_SQL)
        # Stats
        cur.execute("""SELECT
            (SELECT COUNT(*) FROM listings) AS listings_total,
            (SELECT COUNT(*) FROM listings_staging) AS staging_total
        """)
        row = cur.fetchone()
        print(f"OK. listings={row[0]}  staging={row[1]}")
    conn.commit()
print("Done.")
