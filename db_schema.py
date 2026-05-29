"""
db_schema.py — New database schema for Dubai Resale Intelligence System
Tables: emirates, areas, buildings, listings, listing_images,
        price_history, sync_log, users, leads
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


SCHEMA_SQL = """
-- ── Emirates ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS emirates (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) UNIQUE NOT NULL,
    aliases     TEXT[],
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Areas ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS areas (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    emirate_id  INT REFERENCES emirates(id),
    emirate     VARCHAR(100),
    aliases     TEXT[],
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(name, emirate)
);

-- ── Buildings ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS buildings (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(300) NOT NULL,
    area_id     INT REFERENCES areas(id),
    area        VARCHAR(200),
    emirate     VARCHAR(100),
    developer   VARCHAR(200),
    aliases     TEXT[],
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(name, area)
);

-- ── Listings ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS listings (
    id                      SERIAL PRIMARY KEY,
    listing_key             VARCHAR(500) UNIQUE,

    -- Source
    source                  VARCHAR(50) DEFAULT 'telegram',
    telegram_chat_id        VARCHAR(100),
    telegram_message_id     BIGINT,
    message_date            TIMESTAMPTZ,
    original_text           TEXT,
    seller_username         VARCHAR(200),

    -- Deal
    deal_type               VARCHAR(20),        -- sale / rent
    property_type           VARCHAR(50),        -- apartment/villa/townhouse/penthouse/studio/plot

    -- Location
    emirate                 VARCHAR(100),
    emirate_confidence      FLOAT DEFAULT 0,
    area                    VARCHAR(200),
    area_confidence         FLOAT DEFAULT 0,
    building                VARCHAR(300),
    building_confidence     FLOAT DEFAULT 0,
    location_confidence     FLOAT DEFAULT 0,
    needs_manual_review     BOOLEAN DEFAULT FALSE,
    review_reason           TEXT,

    -- Property details
    bedrooms                INT,
    bathrooms               INT,
    size_sqft               FLOAT,
    bua_sqft                FLOAT,
    plot_sqft               FLOAT,
    floor                   INT,
    unit_number             VARCHAR(50),
    view                    VARCHAR(200),
    furnishing              VARCHAR(50),        -- furnished/unfurnished/semi-furnished
    status                  VARCHAR(50),        -- vacant/rented/offplan/ready

    -- Pricing
    price                   BIGINT,
    currency                VARCHAR(10) DEFAULT 'AED',
    original_price          BIGINT,
    selling_price           BIGINT,
    price_per_sqft          FLOAT,
    discount_amount         BIGINT,
    discount_percent        FLOAT,

    -- Deal quality
    is_hot_deal             BOOLEAN DEFAULT FALSE,
    deal_quality            VARCHAR(20),        -- normal/interesting/good/very_good
    deal_reason             TEXT,
    is_below_market         BOOLEAN DEFAULT FALSE,
    price_vs_market_percent FLOAT,

    -- Market analysis
    market_avg_sqft         FLOAT,
    market_rent_1br         BIGINT,
    market_growth_pct       FLOAT,
    roi_estimate            FLOAT,
    airbnb_estimate_low     BIGINT,
    airbnb_estimate_high    BIGINT,
    investment_score        FLOAT,

    -- Contact (ADMIN ONLY)
    agent_name              VARCHAR(200),
    phone                   VARCHAR(100),
    whatsapp                VARCHAR(100),

    -- Images
    has_images              BOOLEAN DEFAULT FALSE,
    cover_image_url         TEXT,

    -- Dedup
    confidence_score        FLOAT DEFAULT 0,
    is_duplicate_message    BOOLEAN DEFAULT FALSE,
    possible_duplicate_id   INT,
    price_drop_detected     BOOLEAN DEFAULT FALSE,

    -- Meta
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    is_active               BOOLEAN DEFAULT TRUE,
    alerted                 BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_listings_emirate ON listings(emirate);
CREATE INDEX IF NOT EXISTS idx_listings_area ON listings(area);
CREATE INDEX IF NOT EXISTS idx_listings_building ON listings(building);
CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price);
CREATE INDEX IF NOT EXISTS idx_listings_bedrooms ON listings(bedrooms);
CREATE INDEX IF NOT EXISTS idx_listings_deal_type ON listings(deal_type);
CREATE INDEX IF NOT EXISTS idx_listings_is_hot ON listings(is_hot_deal);
CREATE INDEX IF NOT EXISTS idx_listings_message ON listings(telegram_message_id);
CREATE INDEX IF NOT EXISTS idx_listings_active ON listings(is_active);

-- ── Listing Images ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS listing_images (
    id          SERIAL PRIMARY KEY,
    listing_id  INT REFERENCES listings(id) ON DELETE CASCADE,
    url         TEXT NOT NULL,
    sort_order  INT DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_images_listing ON listing_images(listing_id);

-- ── Pending image uploads (retry queue) ──────────────────────────────────────
-- Когда save_images вызывается до того, как listing promoted из staging в listings,
-- FK на listings(id) ещё нет. Раньше мы просто молча выходили, картинки терялись.
-- Теперь складываем такие urls сюда, а cron pending_images_retry.py забирает
-- их позже и пытается вставить.
CREATE TABLE IF NOT EXISTS pending_image_uploads (
    id              BIGSERIAL PRIMARY KEY,
    listing_id      BIGINT      NOT NULL,
    image_url       TEXT        NOT NULL,
    sort_order      INT         DEFAULT 0,
    attempts        INT         DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    last_error      TEXT,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(listing_id, image_url)
);
CREATE INDEX IF NOT EXISTS idx_piu_listing
    ON pending_image_uploads(listing_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_piu_pending
    ON pending_image_uploads(created_at) WHERE resolved_at IS NULL;

-- ── Price History ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS price_history (
    id          SERIAL PRIMARY KEY,
    listing_id  INT REFERENCES listings(id) ON DELETE CASCADE,
    price       BIGINT,
    price_date  TIMESTAMPTZ DEFAULT NOW(),
    source_msg  BIGINT
);

CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id);

-- ── Sync Log ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sync_log (
    id              SERIAL PRIMARY KEY,
    synced_at       TIMESTAMPTZ DEFAULT NOW(),
    channel         VARCHAR(100),
    messages_parsed INT DEFAULT 0,
    new_listings    INT DEFAULT 0,
    duplicates      INT DEFAULT 0,
    hot_deals       INT DEFAULT 0,
    errors          INT DEFAULT 0,
    last_message_id BIGINT
);

-- ── Users ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    telegram_id     BIGINT UNIQUE NOT NULL,
    username        VARCHAR(200),
    first_name      VARCHAR(200),
    language        VARCHAR(10) DEFAULT 'en',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_seen       TIMESTAMPTZ DEFAULT NOW(),
    searches_count  INT DEFAULT 0,
    is_vip          BOOLEAN DEFAULT FALSE
);

-- ── Leads ─────────────────────────────────────────────────────────────────────
-- ON DELETE CASCADE: лиды без listing не имеют смысла (контекст утерян).
CREATE TABLE IF NOT EXISTS leads (
    id              SERIAL PRIMARY KEY,
    telegram_id     BIGINT,
    username        VARCHAR(200),
    listing_id      INT REFERENCES listings(id) ON DELETE CASCADE,
    action          VARCHAR(50),   -- view/book/contact/save
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    notes           TEXT
);

-- Migration: ensure leads.listing_id has ON DELETE CASCADE on existing DBs.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.referential_constraints
        WHERE constraint_name='leads_listing_id_fkey'
          AND delete_rule <> 'CASCADE'
    ) THEN
        ALTER TABLE leads DROP CONSTRAINT leads_listing_id_fkey;
        ALTER TABLE leads ADD CONSTRAINT leads_listing_id_fkey
            FOREIGN KEY (listing_id) REFERENCES listings(id) ON DELETE CASCADE;
    END IF;
END $$;

-- ── Pending Listings (user-submitted, awaiting moderation) ────────────────────
CREATE TABLE IF NOT EXISTS pending_listings (
    id          SERIAL PRIMARY KEY,
    uid         BIGINT,
    data        JSONB,
    status      VARCHAR(20) DEFAULT 'pending',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Review Queue (admin moderation of scraped listings) ───────────────────────
CREATE TABLE IF NOT EXISTS review_queue (
    id          SERIAL PRIMARY KEY,
    listing_id  INTEGER REFERENCES listings(id) ON DELETE CASCADE,
    reason      TEXT,
    status      VARCHAR(20) DEFAULT 'pending',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_rq_listing ON review_queue(listing_id);
CREATE INDEX IF NOT EXISTS idx_rq_status  ON review_queue(status);

-- ── Favorites ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS favorites (
    id           SERIAL PRIMARY KEY,
    uid          BIGINT NOT NULL,
    listing_id   INT REFERENCES listings(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(uid, listing_id)
);
CREATE INDEX IF NOT EXISTS idx_fav_uid ON favorites(uid);

-- ── Price Alerts ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS price_alerts (
    id           SERIAL PRIMARY KEY,
    uid          BIGINT NOT NULL,
    deal_type    VARCHAR(20),
    property_type VARCHAR(50),
    emirate      VARCHAR(100),
    area         VARCHAR(200),
    bedrooms     INT,
    min_price    BIGINT,
    max_price    BIGINT,
    last_notified TIMESTAMPTZ,
    last_listing_id INT,
    is_active    BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_alerts_uid ON price_alerts(uid);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON price_alerts(is_active);

-- ── Audit Queue (LLM background bug-fix worker) ──────────────────────────────
-- Cron worker (db_cleanup_cron + audit_worker) складывает сюда подозрительные
-- листинги (mismatched price, missing fields, junk building). LLM решает что
-- делать. После починки status='resolved'.
CREATE TABLE IF NOT EXISTS audit_queue (
    id                    BIGSERIAL PRIMARY KEY,
    listing_id            BIGINT REFERENCES listings(id) ON DELETE CASCADE,
    bug_category          TEXT,
    bug_description       TEXT,
    original_text         TEXT,
    current_db_state      JSONB,
    expected_state        JSONB,
    source                TEXT,
    reference_screenshot  TEXT,
    priority              INT DEFAULT 5,
    status                TEXT DEFAULT 'pending',
    llm_response          JSONB,
    attempts              INT DEFAULT 0,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    processed_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_aq_listing ON audit_queue(listing_id);
CREATE INDEX IF NOT EXISTS idx_aq_category ON audit_queue(bug_category);
CREATE INDEX IF NOT EXISTS idx_aq_source ON audit_queue(source);
CREATE INDEX IF NOT EXISTS idx_aq_status_priority ON audit_queue(status, priority, created_at);

-- ── Media Uploads (Telegram file_id → CDN upload queue) ──────────────────────
-- Когда листинг promoted в основную таблицу, media uploader забирает file_id
-- из Telegram и кладёт картинку на CDN. status: pending/uploaded/failed.
CREATE TABLE IF NOT EXISTS media_uploads (
    id          SERIAL PRIMARY KEY,
    listing_id  INT REFERENCES listings(id) ON DELETE CASCADE,
    file_id     TEXT UNIQUE NOT NULL,
    cdn_url     TEXT,
    size_bytes  BIGINT,
    status      TEXT NOT NULL,
    error       TEXT,
    uploaded_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_media_uploads_listing ON media_uploads(listing_id);
CREATE INDEX IF NOT EXISTS idx_media_uploads_status ON media_uploads(status);

-- Migration: ensure media_uploads.listing_id has ON DELETE CASCADE
-- (раньше было SET NULL → orphans копились).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.referential_constraints
        WHERE constraint_name='media_uploads_listing_id_fkey'
          AND delete_rule <> 'CASCADE'
    ) THEN
        ALTER TABLE media_uploads DROP CONSTRAINT media_uploads_listing_id_fkey;
        ALTER TABLE media_uploads ADD CONSTRAINT media_uploads_listing_id_fkey
            FOREIGN KEY (listing_id) REFERENCES listings(id) ON DELETE CASCADE;
    END IF;
END $$;

-- NB: pending_image_uploads.listing_id НЕ имеет FK намеренно — туда кладутся
-- ID из listings_staging (BIGSERIAL) до promotion. FK сломал бы retry-cron.

-- ── New listing columns (additive — safe re-run) ─────────────────────────────
ALTER TABLE listings ADD COLUMN IF NOT EXISTS is_off_plan BOOLEAN DEFAULT FALSE;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS handover_date DATE;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS photo_phash VARCHAR(64);
ALTER TABLE listings ADD COLUMN IF NOT EXISTS broker_dedup_key VARCHAR(200);
ALTER TABLE listings ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS extra_info JSONB;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS is_audit BOOLEAN DEFAULT FALSE;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS audit_reason TEXT;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS audit_flagged_at TIMESTAMPTZ;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS is_frozen BOOLEAN DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_listings_frozen ON listings(is_frozen) WHERE is_frozen = TRUE;

CREATE INDEX IF NOT EXISTS idx_listings_phash ON listings(photo_phash);
CREATE INDEX IF NOT EXISTS idx_listings_offplan ON listings(is_off_plan);

-- ── Watchlists (v55) ─────────────────────────────────────────────────────────
-- Универсальные подписки: на район/здание/девелопера/ценовой диапазон/listing.
-- Cron worker раз в день шлёт дайджест matches, раз в неделю — AI digest.
CREATE TABLE IF NOT EXISTS watchlists (
    id                BIGSERIAL PRIMARY KEY,
    user_id           BIGINT NOT NULL,
    watch_type        TEXT,
    watch_value       TEXT,
    filters           JSONB,
    notification_freq TEXT DEFAULT 'daily',
    is_active         BOOLEAN DEFAULT TRUE,
    last_notified_at  TIMESTAMPTZ,
    last_weekly_at    TIMESTAMPTZ,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_w_user_active ON watchlists(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_w_active ON watchlists(is_active) WHERE is_active = TRUE;

-- ── Cross-bot UTM tracking (v55) ─────────────────────────────────────────────
-- Логирует каждый /start <payload> переход между ботами экосистемы.
-- Источник правды для analytics конверсий hub→resale→roi→lead и т.п.
CREATE TABLE IF NOT EXISTS cross_bot_jumps (
    id           BIGSERIAL PRIMARY KEY,
    from_bot     TEXT,
    to_bot       TEXT,
    user_id      BIGINT,
    payload      TEXT,
    utm_source   TEXT,
    utm_campaign TEXT,
    utm_content  TEXT,
    jumped_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cbj_to_bot ON cross_bot_jumps(to_bot, jumped_at DESC);
CREATE INDEX IF NOT EXISTS idx_cbj_from_bot ON cross_bot_jumps(from_bot, jumped_at DESC);
CREATE INDEX IF NOT EXISTS idx_cbj_user ON cross_bot_jumps(user_id, jumped_at DESC);
"""

SEED_SQL = """
-- Seed Emirates
INSERT INTO emirates (name, aliases) VALUES
    ('Dubai',           ARRAY['DXB', 'دبي', 'Dubay', 'DBX']),
    ('Abu Dhabi',       ARRAY['AUH', 'AD', 'أبوظبي']),
    ('Sharjah',         ARRAY['SHJ', 'الشارقة']),
    ('Ras Al Khaimah',  ARRAY['RAK', 'رأس الخيمة']),
    ('Ajman',           ARRAY['AJM', 'عجمان']),
    ('Fujairah',        ARRAY['FUJ', 'الفجيرة']),
    ('Umm Al Quwain',   ARRAY['UAQ', 'أم القيوين'])
ON CONFLICT (name) DO NOTHING;

-- Seed Dubai Areas
INSERT INTO areas (name, emirate, aliases) VALUES
    ('Downtown Dubai',           'Dubai', ARRAY['Downtown', 'DT', 'DTDXB']),
    ('Business Bay',             'Dubai', ARRAY['BB', 'Biz Bay']),
    ('Dubai Marina',             'Dubai', ARRAY['Marina', 'DM', 'The Marina']),
    ('Palm Jumeirah',            'Dubai', ARRAY['Palm', 'PJ', 'The Palm']),
    ('Jumeirah Village Circle',  'Dubai', ARRAY['JVC', 'Jumeirah Village']),
    ('Jumeirah Village Triangle','Dubai', ARRAY['JVT']),
    ('Jumeirah Beach Residence', 'Dubai', ARRAY['JBR', 'The Walk']),
    ('Dubai Hills Estate',       'Dubai', ARRAY['Dubai Hills', 'DHE', 'DH']),
    ('Dubai Creek Harbour',      'Dubai', ARRAY['Creek Harbour', 'DCH', 'Creek']),
    ('MBR City',                 'Dubai', ARRAY['Mohammed Bin Rashid City', 'MBR']),
    ('Meydan',                   'Dubai', ARRAY['Meydan City', 'Nad Al Sheba']),
    ('Emaar South',              'Dubai', ARRAY['ES']),
    ('Al Furjan',                'Dubai', ARRAY['Furjan', 'Al-Furjan']),
    ('Arjan',                    'Dubai', ARRAY['Arjan Dubailand']),
    ('DAMAC Hills',              'Dubai', ARRAY['Damac Hills', 'Akoya']),
    ('Bluewaters Island',        'Dubai', ARRAY['Bluewaters', 'Blue Waters']),
    ('Dubai South',              'Dubai', ARRAY['Dubai World Central', 'DWC', 'DS']),
    ('Jumeirah',                 'Dubai', ARRAY['Jumeira', 'JBR Area']),
    ('Sports City',              'Dubai', ARRAY['DSC', 'Dubai Sports City', 'SC']),
    ('Silicon Oasis',            'Dubai', ARRAY['DSO', 'Dubai Silicon Oasis']),
    ('International City',       'Dubai', ARRAY['IC', 'Intl City']),
    ('Dubai Harbour',            'Dubai', ARRAY['DH', 'Harbour']),
    ('City Walk',                'Dubai', ARRAY['CW', 'Citywalk']),
    ('DIFC',                     'Dubai', ARRAY['Dubai International Financial Centre']),
    ('Barsha Heights',           'Dubai', ARRAY['TECOM', 'Barsha']),
    ('Sobha Hartland',           'Dubai', ARRAY['Sobha', 'Hartland']),
    ('Motor City',               'Dubai', ARRAY['Motorcity']),
    ('La Mer',                   'Dubai', ARRAY['La Mer Jumeirah']),
    ('Discovery Gardens',        'Dubai', ARRAY['DG']),
    ('The Valley',               'Dubai', ARRAY['Emaar Valley']),
    ('Dubailand',                'Dubai', ARRAY['Dubai Land']),
    -- Abu Dhabi
    ('Yas Island',               'Abu Dhabi', ARRAY['Yas']),
    ('Saadiyat Island',          'Abu Dhabi', ARRAY['Saadiyat']),
    ('Al Reem Island',           'Abu Dhabi', ARRAY['Reem Island', 'Al Reem']),
    ('Al Raha',                  'Abu Dhabi', ARRAY['Al Raha Beach']),
    ('Al Maryah Island',         'Abu Dhabi', ARRAY['Maryah Island']),
    -- RAK
    ('Al Marjan Island',         'Ras Al Khaimah', ARRAY['Marjan Island']),
    ('Mina Al Arab',             'Ras Al Khaimah', ARRAY['Mina Arab']),
    ('Al Hamra Village',         'Ras Al Khaimah', ARRAY['Al Hamra'])
ON CONFLICT (name, emirate) DO NOTHING;
"""


def init_db():
    """Create all tables and seed initial data."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(SEED_SQL)
        conn.commit()
        print("[db] Schema initialized OK")
    except Exception as e:
        conn.rollback()
        print(f"[db] Schema error: {e}")
        raise
    finally:
        conn.close()


def get_emirate_id(name: str) -> int | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM emirates WHERE name = %s", (name,))
            row = cur.fetchone()
            return row["id"] if row else None
    finally:
        conn.close()


def get_area_by_name(name: str, emirate: str = None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if emirate:
                cur.execute(
                    "SELECT * FROM areas WHERE LOWER(name)=LOWER(%s) AND emirate=%s",
                    (name, emirate)
                )
            else:
                cur.execute("SELECT * FROM areas WHERE LOWER(name)=LOWER(%s)", (name,))
            return cur.fetchone()
    finally:
        conn.close()


def get_building_by_name(name: str, area: str = None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if area:
                cur.execute(
                    "SELECT * FROM buildings WHERE LOWER(name)=LOWER(%s) AND LOWER(area)=LOWER(%s)",
                    (name, area)
                )
            else:
                cur.execute("SELECT * FROM buildings WHERE LOWER(name)=LOWER(%s)", (name,))
            return cur.fetchone()
    finally:
        conn.close()


def upsert_listing(data: dict) -> tuple[int, bool]:
    """
    Insert or update listing. Returns (id, is_new).
    Uses listing_key for dedup.

    FREEZE LOGIC:
      - Если найден existing record с is_frozen=TRUE → возвращаем id
        без каких-либо UPDATE (даже price). Это сохраняет ручную
        очистку базы (audit flags, building/area corrections и т.д.).
      - Новые записи (INSERT) — is_frozen=FALSE по умолчанию.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Check by telegram_message_id first (exact dedup)
            if data.get("telegram_message_id"):
                cur.execute(
                    "SELECT id, is_frozen FROM listings WHERE telegram_message_id=%s AND telegram_chat_id=%s",
                    (data["telegram_message_id"], data.get("telegram_chat_id", ""))
                )
                row = cur.fetchone()
                if row:
                    return row["id"], False

            # Check by content signature — same first 250 chars of text = dup
            # (брокер форвардит одно и то же объявление в разные каналы)
            import re as _re
            if data.get("original_text"):
                sig = _re.sub(r'\s+', ' ', (data["original_text"] or "")[:250]).strip()
                if len(sig) > 50:
                    cur.execute("""SELECT id FROM listings
                        WHERE regexp_replace(LEFT(original_text, 250), '\\s+', ' ', 'g') = %s
                        AND (telegram_message_id IS DISTINCT FROM %s OR telegram_chat_id IS DISTINCT FROM %s)
                        LIMIT 1""",
                        (sig, data.get("telegram_message_id"), data.get("telegram_chat_id", "")))
                    dup = cur.fetchone()
                    if dup:
                        # Already have this content from another channel/post — skip
                        return dup["id"], False

            # Check by listing_key (property dedup)
            key = data.get("listing_key")
            if key:
                cur.execute("SELECT id, price, is_frozen FROM listings WHERE listing_key=%s", (key,))
                row = cur.fetchone()
                if row:
                    existing_id = row["id"]
                    # FROZEN — ничего не трогаем, защищаем cleanup
                    if row.get("is_frozen"):
                        return existing_id, False

                    old_price = row["price"]
                    new_price = data.get("price")
                    # Update price history if price changed (для нефрозен записей)
                    if new_price and old_price and new_price != old_price:
                        cur.execute(
                            "INSERT INTO price_history(listing_id, price, source_msg) VALUES(%s,%s,%s)",
                            (existing_id, new_price, data.get("telegram_message_id"))
                        )
                        drop = old_price - new_price
                        drop_pct = round(drop / old_price * 100, 1) if old_price else 0
                        cur.execute(
                            """UPDATE listings SET price=%s, updated_at=NOW(),
                               price_drop_detected=%s, discount_amount=%s, discount_percent=%s
                               WHERE id=%s""",
                            (new_price, drop > 0, abs(drop), abs(drop_pct), existing_id)
                        )
                    conn.commit()
                    return existing_id, False

            # Auto-audit для записей без building name (quality gate).
            # Договорённость с владельцем: лучше меньше но качественных.
            if data.get("auto_audit"):
                data["is_audit"] = True

            # v106: STAGING ROUTING — incomplete listings идут в listings_staging,
            # не в основную listings. Worker (_staging_processor.py) дотягивает
            # их через AI и promotes когда complete. Это гарантирует что в
            # listings нет мусора.
            is_complete = (
                data.get("building") and
                data.get("area") and
                data.get("price") and
                data.get("bedrooms") is not None and
                data.get("deal_type") and
                not data.get("needs_manual_review") and
                not data.get("is_audit")
            )
            if not is_complete:
                # v134: dedup staging — same listing_key or same TG message already queued
                key = data.get("listing_key")
                if key:
                    cur.execute(
                        "SELECT id FROM listings_staging WHERE listing_key=%s LIMIT 1",
                        (key,)
                    )
                    if cur.fetchone():
                        return None, False  # already queued — skip
                chat_id = data.get("telegram_chat_id")
                msg_id = data.get("telegram_message_id")
                if chat_id and msg_id:
                    cur.execute(
                        "SELECT id FROM listings_staging WHERE telegram_chat_id=%s AND telegram_message_id=%s LIMIT 1",
                        (chat_id, msg_id)
                    )
                    if cur.fetchone():
                        return None, False  # same message already in staging
                # Insert into staging instead
                return _insert_to_staging(cur, conn, data), True

            # Insert new listing (complete & valid)
            cols = [
                "listing_key","source","telegram_chat_id","telegram_message_id","message_date",
                "original_text","seller_username","deal_type","property_type",
                "emirate","emirate_confidence","area","area_confidence",
                "building","building_confidence","location_confidence",
                "needs_manual_review","review_reason","is_audit",
                "bedrooms","bathrooms","size_sqft","bua_sqft","plot_sqft",
                "floor","unit_number","view","furnishing","status",
                "price","currency","original_price","selling_price","price_per_sqft",
                "discount_amount","discount_percent",
                "is_hot_deal","deal_quality","deal_reason","is_below_market","price_vs_market_percent",
                "market_avg_sqft","market_rent_1br","market_growth_pct","roi_estimate",
                "airbnb_estimate_low","airbnb_estimate_high","investment_score",
                "agent_name","phone","whatsapp",
                "has_images","cover_image_url","confidence_score",
                "extra_info","is_off_plan","handover_date","photo_phash",
                "broker_dedup_key","description",
            ]
            vals = []
            for c in cols:
                v = data.get(c)
                # extra_info is a Python dict — serialize to JSON for JSONB column
                if c == "extra_info" and v is not None:
                    import json as _json
                    v = _json.dumps(v, ensure_ascii=False)
                vals.append(v)
            placeholders = ",".join(["%s"] * len(cols))
            col_str = ",".join(cols)
            cur.execute(
                f"INSERT INTO listings ({col_str}) VALUES ({placeholders}) RETURNING id",
                vals
            )
            new_id = cur.fetchone()["id"]

            # Initial price history
            if data.get("price"):
                cur.execute(
                    "INSERT INTO price_history(listing_id, price, source_msg) VALUES(%s,%s,%s)",
                    (new_id, data["price"], data.get("telegram_message_id"))
                )

            conn.commit()
            return new_id, True

    except Exception as e:
        conn.rollback()
        print(f"[db] upsert_listing error: {e}")
        raise
    finally:
        conn.close()


def _insert_to_staging(cur, conn, data: dict) -> int:
    """v106: записывает incomplete listing в listings_staging.
    Background worker _staging_processor.py дотягивает поля через LLM
    и promotes в listings когда complete.
    """
    # Same column set as listings, plus staging columns
    cols = [
        "listing_key","source","telegram_chat_id","telegram_message_id","message_date",
        "original_text","seller_username","deal_type","property_type",
        "emirate","emirate_confidence","area","area_confidence",
        "building","building_confidence","location_confidence",
        "needs_manual_review","review_reason","is_audit",
        "bedrooms","bathrooms","size_sqft","bua_sqft","plot_sqft",
        "floor","unit_number","view","furnishing","status",
        "price","currency","original_price","selling_price","price_per_sqft",
        "discount_amount","discount_percent",
        "is_hot_deal","deal_quality","deal_reason","is_below_market","price_vs_market_percent",
        "market_avg_sqft","market_rent_1br","market_growth_pct","roi_estimate",
        "airbnb_estimate_low","airbnb_estimate_high","investment_score",
        "agent_name","phone","whatsapp",
        "has_images","cover_image_url","confidence_score",
        "extra_info","is_off_plan","handover_date","photo_phash",
        "broker_dedup_key","description",
    ]
    vals = []
    for c in cols:
        v = data.get(c)
        if c == "extra_info" and v is not None:
            import json as _json
            v = _json.dumps(v, ensure_ascii=False)
        vals.append(v)
    placeholders = ",".join(["%s"] * len(cols))
    col_str = ",".join(cols)
    cur.execute(
        f"INSERT INTO listings_staging ({col_str}) VALUES ({placeholders}) RETURNING id",
        vals
    )
    new_id = cur.fetchone()["id"]
    conn.commit()
    return new_id


def save_images(listing_id: int, urls: list[str]):
    if not urls or not listing_id:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # FK guard: upsert_listing может вернуть id из listings_staging
            # (incomplete listings), а listing_images.listing_id ссылается на
            # listings.id. Без этой проверки получаем FK violation для каждого
            # incomplete объявления. Если listing ещё нет в listings — кладём
            # urls в pending_image_uploads, retry-cron вставит их позже когда
            # listing будет promoted.
            cur.execute("SELECT 1 FROM listings WHERE id=%s", (listing_id,))
            if not cur.fetchone():
                for i, url in enumerate(urls):
                    try:
                        cur.execute(
                            """
                            INSERT INTO pending_image_uploads
                                (listing_id, image_url, sort_order)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (listing_id, image_url) DO NOTHING
                            """,
                            (listing_id, url, i),
                        )
                    except Exception as e:
                        # pending_image_uploads ещё может не существовать на
                        # стороне БД при первом деплое — не падаем, просто лог.
                        print(f"[db] pending_image_uploads insert skipped: {e}")
                        break
                conn.commit()
                return
            for i, url in enumerate(urls):
                cur.execute(
                    "INSERT INTO listing_images(listing_id, url, sort_order) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (listing_id, url, i)
                )
            # Set cover image and has_images flag
            cur.execute(
                "UPDATE listings SET has_images=TRUE, cover_image_url=%s WHERE id=%s",
                (urls[0], listing_id)
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[db] save_images error (listing_id={listing_id}): {e}")
    finally:
        conn.close()


def get_listing_images(listing_id: int) -> list[str]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT url FROM listing_images WHERE listing_id=%s ORDER BY sort_order",
                (listing_id,)
            )
            return [r["url"] for r in cur.fetchall()]
    finally:
        conn.close()


def save_user(telegram_id: int, username: str, first_name: str, language: str = "en"):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users(telegram_id, username, first_name, language)
                VALUES(%s,%s,%s,%s)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username=EXCLUDED.username,
                    first_name=EXCLUDED.first_name,
                    last_seen=NOW()
            """, (telegram_id, username, first_name, language))
        conn.commit()
    except Exception as e:
        conn.rollback()
    finally:
        conn.close()


def save_lead(telegram_id: int, username: str, listing_id: int, action: str, notes: str = None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO leads(telegram_id, username, listing_id, action, notes) VALUES(%s,%s,%s,%s,%s)",
                (telegram_id, username, listing_id, action, notes)
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
    finally:
        conn.close()


def log_sync(channel: str, parsed: int, new: int, dupes: int, hot: int, errors: int, last_msg_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sync_log(channel, messages_parsed, new_listings, duplicates, hot_deals, errors, last_message_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s)
            """, (channel, parsed, new, dupes, hot, errors, last_msg_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
    finally:
        conn.close()


def get_full_stats() -> dict:
    """
    ИСПРАВЛЕНО:
    - avg_sale_price теперь с фильтром price BETWEEN 300000 AND 50000000
      (убираем мусорные цены: телефоны спарсились как цены)
    - avg_rent_price фильтр 10000 AND 1500000
    - today/yesterday/week/month считаются по created_at AT TIME ZONE 'Asia/Dubai'
    - sale/rent counts с теми же price-фильтрами
    - pending_listings count добавлен
    - corrupt_prices count — сколько объектов с явно битой ценой
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # ── Totals ──────────────────────────────────────────────────────
            cur.execute("""
                SELECT
                  COUNT(*) as total,
                  COUNT(*) FILTER (WHERE deal_type='sale') as sale_total,
                  COUNT(*) FILTER (WHERE deal_type='rent') as rent_total,
                  COUNT(*) FILTER (WHERE is_hot_deal=TRUE) as hot,
                  COUNT(*) FILTER (WHERE needs_manual_review=TRUE) as review,
                  COUNT(*) FILTER (WHERE is_below_market=TRUE) as below_market,
                  COUNT(DISTINCT building) FILTER (WHERE building IS NOT NULL) as buildings,
                  COUNT(DISTINCT area) FILTER (WHERE area IS NOT NULL) as areas,
                  COUNT(DISTINCT telegram_chat_id) as groups
                FROM listings WHERE is_active=TRUE
            """)
            totals = cur.fetchone()

            # ── Avg prices (с фильтром мусорных цен) ───────────────────────
            # БАГ: телефоны типа +380685841342 попадали в price → avg = 951M
            # Фикс: ограничиваем реальным диапазоном цен UAE
            cur.execute("""
                SELECT
                  ROUND(AVG(price)) as avg_sale
                FROM listings
                WHERE is_active=TRUE AND deal_type='sale'
                  AND price BETWEEN 200000 AND 50000000
            """)
            avg_sale = cur.fetchone()["avg_sale"] or 0

            cur.execute("""
                SELECT
                  ROUND(AVG(price)) as avg_rent
                FROM listings
                WHERE is_active=TRUE AND deal_type='rent'
                  AND price BETWEEN 10000 AND 1500000
            """)
            avg_rent = cur.fetchone()["avg_rent"] or 0

            cur.execute("""
                SELECT ROUND(AVG(price_per_sqft)) as avg_sqft
                FROM listings
                WHERE is_active=TRUE AND price_per_sqft BETWEEN 200 AND 15000
            """)
            avg_sqft = cur.fetchone()["avg_sqft"] or 0

            cur.execute("""
                SELECT ROUND(AVG(roi_estimate)::numeric, 1) as avg_roi
                FROM listings
                WHERE is_active=TRUE AND roi_estimate BETWEEN 1 AND 25
            """)
            avg_roi = cur.fetchone()["avg_roi"] or 0

            # ── Corrupt price count (для инфо) ──────────────────────────────
            cur.execute("""
                SELECT COUNT(*) as cnt FROM listings
                WHERE is_active=TRUE AND price > 50000000 AND deal_type='sale'
            """)
            corrupt_prices = cur.fetchone()["cnt"]

            # ── By emirate ──────────────────────────────────────────────────
            cur.execute("""
                SELECT COALESCE(emirate,'Unknown') as emirate, COUNT(*) as cnt
                FROM listings WHERE is_active=TRUE
                GROUP BY emirate ORDER BY cnt DESC LIMIT 8
            """)
            by_emirate = {r["emirate"]: r["cnt"] for r in cur.fetchall()}

            # ── By quality ──────────────────────────────────────────────────
            cur.execute("""
                SELECT deal_quality, COUNT(*) as cnt FROM listings
                WHERE is_active=TRUE AND deal_quality IS NOT NULL
                GROUP BY deal_quality ORDER BY cnt DESC
            """)
            by_quality = {r["deal_quality"]: r["cnt"] for r in cur.fetchall()}

            # ── By deal type (чистые, без мусора) ───────────────────────────
            cur.execute("""
                SELECT
                  COUNT(*) FILTER (WHERE deal_type='sale' AND price BETWEEN 200000 AND 50000000) as sale_clean,
                  COUNT(*) FILTER (WHERE deal_type='rent' AND price BETWEEN 10000 AND 1500000) as rent_clean
                FROM listings WHERE is_active=TRUE
            """)
            clean = cur.fetchone()

            # ── Time-based (Dubai timezone UTC+4) ───────────────────────────
            cur.execute("""
                SELECT
                  COUNT(*) FILTER (
                    WHERE created_at AT TIME ZONE 'Asia/Dubai' >= CURRENT_DATE
                  ) as today,
                  COUNT(*) FILTER (
                    WHERE created_at AT TIME ZONE 'Asia/Dubai' >= CURRENT_DATE - 1
                    AND created_at AT TIME ZONE 'Asia/Dubai' < CURRENT_DATE
                  ) as yesterday,
                  COUNT(*) FILTER (
                    WHERE created_at >= date_trunc('week', NOW() AT TIME ZONE 'Asia/Dubai') AT TIME ZONE 'Asia/Dubai'
                  ) as this_week,
                  COUNT(*) FILTER (
                    WHERE created_at >= date_trunc('month', NOW() AT TIME ZONE 'Asia/Dubai') AT TIME ZONE 'Asia/Dubai'
                  ) as this_month
                FROM listings WHERE is_active=TRUE
            """)
            time_counts = cur.fetchone()

            # ── Sync log ────────────────────────────────────────────────────
            cur.execute("""
                SELECT
                  COALESCE(SUM(new_listings),0) as new,
                  COALESCE(SUM(duplicates),0) as dupes,
                  COALESCE(SUM(hot_deals),0) as hot,
                  COALESCE(SUM(errors),0) as errors,
                  COUNT(*) as syncs,
                  MAX(synced_at) as last_sync
                FROM sync_log
                WHERE synced_at >= NOW() - INTERVAL '24 hours'
            """)
            today_sync = cur.fetchone()

            # Last sync per channel
            cur.execute("""
                SELECT channel, MAX(synced_at) as last, MAX(last_message_id) as last_id
                FROM sync_log GROUP BY channel ORDER BY last DESC
            """)
            by_channel = {r["channel"]: {"last": r["last"], "last_id": r["last_id"]}
                          for r in cur.fetchall()}

            # ── Users & leads ───────────────────────────────────────────────
            cur.execute("SELECT COUNT(*) as cnt FROM users")
            users_total = cur.fetchone()["cnt"]

            cur.execute("""
                SELECT
                  COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') as today,
                  COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') as week
                FROM leads
            """)
            leads = cur.fetchone()

            # ── Pending moderation ──────────────────────────────────────────
            try:
                cur.execute("SELECT COUNT(*) as cnt FROM pending_listings WHERE approved IS NULL")
                pending = cur.fetchone()["cnt"]
            except Exception:
                pending = 0

            # ── Review queue ────────────────────────────────────────────────
            try:
                cur.execute("SELECT COUNT(*) as cnt FROM review_queue WHERE resolved=FALSE")
                review_queue = cur.fetchone()["cnt"]
            except Exception:
                review_queue = totals["review"]

            return {
                # Totals
                "total":           totals["total"],
                "sale_total":      totals["sale_total"],
                "rent_total":      totals["rent_total"],
                "sale_clean":      clean["sale_clean"],
                "rent_clean":      clean["rent_clean"],
                "hot_deals":       totals["hot"],
                "needs_review":    totals["review"],
                "below_market":    totals["below_market"],
                "buildings_count": totals["buildings"],
                "areas_count":     totals["areas"],
                "groups_count":    totals["groups"],
                "corrupt_prices":  corrupt_prices,
                # Averages
                "avg_sale_price":  avg_sale,
                "avg_rent_price":  avg_rent,
                "avg_price_sqft":  avg_sqft,
                "avg_roi":         float(avg_roi) if avg_roi else 0,
                # Time
                "today_listings":  time_counts["today"],
                "yesterday_listings": time_counts["yesterday"],
                "week_listings":   time_counts["this_week"],
                "month_listings":  time_counts["this_month"],
                # Breakdowns
                "by_emirate":      by_emirate,
                "by_quality":      by_quality,
                "by_channel":      by_channel,
                # Sync
                "today_new":       today_sync["new"],
                "today_dupes":     today_sync["dupes"],
                "today_hot":       today_sync["hot"],
                "today_errors":    today_sync["errors"],
                "syncs_today":     today_sync["syncs"],
                "last_sync":       today_sync["last_sync"],
                # Users
                "users_total":     users_total,
                "leads_today":     leads["today"],
                "leads_week":      leads["week"],
                "pending":         pending,
                "review_queue":    review_queue,
            }
    finally:
        conn.close()


def search_listings(filters: dict, limit: int = 10, offset: int = 0) -> tuple[list, int]:
    """Search listings with filters. Returns (results, total_count)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Audit-flagged records are hidden from regular searches (low quality,
            # price mismatch with DLD, missing key fields). Admin can still see
            # them via /auditreview command.
            where = ["is_active = TRUE", "(is_audit IS NULL OR is_audit = FALSE)"]
            params = []

            if filters.get("emirate"):
                where.append("emirate = %s")
                params.append(filters["emirate"])

            if filters.get("area"):
                # Alias expansion: пользователи говорят «JVC», в DB может быть
                # «Jumeirah Village Circle» или «Al Barsha South Fourth» (DLD official).
                # Раскрываем запрос на ВСЕ варианты — пусть юзер всегда находит.
                from area_aliases import expand_area_query
                aliases = expand_area_query(filters["area"])
                if len(aliases) > 1:
                    placeholders = " OR ".join(["LOWER(area) LIKE LOWER(%s)"] * len(aliases))
                    where.append(f"({placeholders})")
                    for a in aliases:
                        params.append(f"%{a}%")
                else:
                    where.append("LOWER(area) LIKE LOWER(%s)")
                    params.append(f"%{filters['area']}%")

            if filters.get("building"):
                where.append("LOWER(building) LIKE LOWER(%s)")
                params.append(f"%{filters['building']}%")

            if filters.get("deal_type"):
                where.append("deal_type = %s")
                params.append(filters["deal_type"])

            if filters.get("property_type"):
                where.append("property_type = %s")
                params.append(filters["property_type"])

            # property_type_in: list of types for category filter
            # (e.g. commercial = [office, retail, warehouse, hotel, hotel_apartment, serviced_apartment])
            if filters.get("property_type_in"):
                types = filters["property_type_in"]
                placeholders = ",".join(["%s"] * len(types))
                where.append(f"property_type IN ({placeholders})")
                params.extend(types)

            # property_type_not_in: exclude commercial when searching residential
            if filters.get("property_type_not_in"):
                types = filters["property_type_not_in"]
                placeholders = ",".join(["%s"] * len(types))
                where.append(f"(property_type IS NULL OR property_type NOT IN ({placeholders}))")
                params.extend(types)

            if filters.get("bedrooms") is not None:
                br = filters["bedrooms"]
                if br == 0:
                    where.append("(bedrooms = 0 OR property_type='studio')")
                elif br == 99:  # 4BR+
                    where.append("bedrooms >= 4")
                else:
                    where.append("bedrooms = %s")
                    params.append(br)

            if filters.get("min_price"):
                where.append("price >= %s")
                params.append(int(filters["min_price"]))

            if filters.get("max_price"):
                where.append("price <= %s")
                params.append(int(filters["max_price"]))

            if filters.get("view"):
                where.append("LOWER(view) LIKE LOWER(%s)")
                params.append(f"%{filters['view']}%")

            if filters.get("status"):
                where.append("status = %s")
                params.append(filters["status"])

            if filters.get("furnishing"):
                where.append("furnishing = %s")
                params.append(filters["furnishing"])

            if filters.get("hot_only"):
                where.append("is_hot_deal = TRUE")

            if filters.get("has_images"):
                where.append("has_images = TRUE")

            # Off-plan / ready filter
            if filters.get("is_off_plan") is True:
                where.append("is_off_plan = TRUE")
            elif filters.get("is_off_plan") is False:
                where.append("(is_off_plan IS NULL OR is_off_plan = FALSE)")

            # High-confidence filter (UI toggle "🎯 Verified only").
            # confidence_min in [0..1]. Listings без confidence_score не проходят.
            if filters.get("confidence_min") is not None:
                where.append("(confidence_score IS NOT NULL AND confidence_score >= %s)")
                params.append(float(filters["confidence_min"]))

            where_sql = " AND ".join(where)

            # Count
            cur.execute(f"SELECT COUNT(*) as cnt FROM listings WHERE {where_sql}", params)
            total = cur.fetchone()["cnt"]

            # Sort
            sort = filters.get("sort", "best_deals")
            order = {
                "best_deals":    "deal_quality DESC NULLS LAST, price ASC",
                "price_asc":     "price ASC",
                "price_desc":    "price DESC",
                "newest":        "created_at DESC",
                "biggest_drop":  "discount_percent DESC NULLS LAST",
            }.get(sort, "created_at DESC")

            cur.execute(
                f"SELECT * FROM listings WHERE {where_sql} ORDER BY {order} LIMIT %s OFFSET %s",
                params + [limit, offset]
            )
            results = cur.fetchall()
            return list(results), total

    finally:
        conn.close()


def get_listing_by_id(listing_id: int) -> dict | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM listings WHERE id=%s", (listing_id,))
            return cur.fetchone()
    finally:
        conn.close()


def get_price_history(listing_id: int) -> list:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT price, price_date FROM price_history WHERE listing_id=%s ORDER BY price_date",
                (listing_id,)
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def get_last_parsed_message_id(channel: str) -> int | None:
    """B053 v3: берём последнюю строку по id (не MAX) — иначе старые аномальные
    значения от retro-parser перебивают актуальные."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_message_id as mid FROM sync_log WHERE channel=%s ORDER BY id DESC LIMIT 1",
                (channel,)
            )
            row = cur.fetchone()
            return row["mid"] if row and row["mid"] else None
    finally:
        conn.close()


# ── Favorites ────────────────────────────────────────────────────────────────
def add_favorite(uid: int, listing_id: int) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO favorites (uid, listing_id) VALUES (%s, %s) "
                "ON CONFLICT (uid, listing_id) DO NOTHING",
                (uid, listing_id)
            )
        conn.commit()
        return True
    finally:
        conn.close()


def remove_favorite(uid: int, listing_id: int) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM favorites WHERE uid=%s AND listing_id=%s",
                        (uid, listing_id))
        conn.commit()
        return True
    finally:
        conn.close()


def is_favorited(uid: int, listing_id: int) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM favorites WHERE uid=%s AND listing_id=%s",
                        (uid, listing_id))
            return cur.fetchone() is not None
    finally:
        conn.close()


def is_favorited_batch(uid: int, listing_ids: list) -> set:
    """v52: batch fetch for N listings — 1 SQL roundtrip instead of N."""
    if not listing_ids:
        return set()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT listing_id FROM favorites WHERE uid=%s AND listing_id = ANY(%s)",
                (uid, list(listing_ids)),
            )
            rows = cur.fetchall()
            # rows[0] could be tuple or RealDictRow
            return {r[0] if not hasattr(r, 'get') else r.get('listing_id') for r in rows}
    finally:
        conn.close()


def get_user_favorites(uid: int, limit: int = 50) -> list:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT l.* FROM listings l
                JOIN favorites f ON f.listing_id = l.id
                WHERE f.uid = %s AND l.is_active = TRUE
                  AND (l.is_audit IS NULL OR l.is_audit = FALSE)
                ORDER BY f.created_at DESC
                LIMIT %s
            """, (uid, limit))
            return list(cur.fetchall())
    finally:
        conn.close()


# ── Price Alerts ─────────────────────────────────────────────────────────────
def add_price_alert(uid: int, filters: dict) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO price_alerts (uid, deal_type, property_type, emirate, area,
                                          bedrooms, min_price, max_price)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                uid,
                filters.get("deal_type"),
                filters.get("property_type"),
                filters.get("emirate"),
                filters.get("area"),
                filters.get("bedrooms"),
                filters.get("min_price"),
                filters.get("max_price"),
            ))
            return cur.fetchone()["id"]
    finally:
        conn.commit()
        conn.close()


def get_user_alerts(uid: int) -> list:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM price_alerts WHERE uid=%s AND is_active=TRUE "
                "ORDER BY created_at DESC", (uid,))
            return list(cur.fetchall())
    finally:
        conn.close()


def delete_alert(uid: int, alert_id: int) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE price_alerts SET is_active=FALSE WHERE id=%s AND uid=%s",
                        (alert_id, uid))
        conn.commit()
        return True
    finally:
        conn.close()


def get_all_active_alerts() -> list:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM price_alerts WHERE is_active=TRUE")
            return list(cur.fetchall())
    finally:
        conn.close()


def update_alert_last_notified(alert_id: int, listing_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE price_alerts SET last_notified=NOW(), last_listing_id=%s WHERE id=%s",
                (listing_id, alert_id))
        conn.commit()
    finally:
        conn.close()


# ── Watchlists (v55) ─────────────────────────────────────────────────────────
def add_watchlist(user_id: int, watch_type: str, watch_value: str,
                  filters: dict = None, freq: str = "daily") -> int:
    """INSERT новой подписки или re-activate существующей.
    Возвращает id. Уникальность по (user_id, watch_type, watch_value)."""
    import json as _json
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM watchlists
                WHERE user_id=%s AND watch_type=%s AND watch_value=%s
                LIMIT 1
            """, (user_id, watch_type, watch_value or ""))
            row = cur.fetchone()
            if row:
                cur.execute("""
                    UPDATE watchlists
                       SET is_active=TRUE, filters=%s, notification_freq=%s
                     WHERE id=%s
                """, (_json.dumps(filters or {}), freq, row["id"]))
                conn.commit()
                return row["id"]
            cur.execute("""
                INSERT INTO watchlists
                    (user_id, watch_type, watch_value, filters, notification_freq)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (user_id, watch_type, watch_value or "",
                  _json.dumps(filters or {}), freq))
            new_id = cur.fetchone()["id"]
        conn.commit()
        return new_id
    finally:
        conn.close()


def remove_watchlist(user_id: int, watch_id: int) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE watchlists SET is_active=FALSE "
                        "WHERE id=%s AND user_id=%s", (watch_id, user_id))
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


def get_user_watchlists(user_id: int) -> list:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM watchlists
                 WHERE user_id=%s AND is_active=TRUE
                 ORDER BY created_at DESC
            """, (user_id,))
            return list(cur.fetchall())
    finally:
        conn.close()


def is_watching(user_id: int, watch_type: str, watch_value: str) -> bool:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM watchlists
                 WHERE user_id=%s AND watch_type=%s AND watch_value=%s
                   AND is_active=TRUE LIMIT 1
            """, (user_id, watch_type, watch_value or ""))
            return cur.fetchone() is not None
    finally:
        conn.close()


def get_active_watchlists(freq: str = None) -> list:
    """Все активные watchlists. Если freq задан — только указанной частоты."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if freq:
                cur.execute("SELECT * FROM watchlists WHERE is_active=TRUE "
                            "AND notification_freq=%s", (freq,))
            else:
                cur.execute("SELECT * FROM watchlists WHERE is_active=TRUE")
            return list(cur.fetchall())
    finally:
        conn.close()


def update_watchlist_notified(watch_id: int, weekly: bool = False):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if weekly:
                cur.execute("UPDATE watchlists SET last_weekly_at=NOW() "
                            "WHERE id=%s", (watch_id,))
            else:
                cur.execute("UPDATE watchlists SET last_notified_at=NOW() "
                            "WHERE id=%s", (watch_id,))
        conn.commit()
    finally:
        conn.close()


# ── Cross-bot UTM tracking (v55) ─────────────────────────────────────────────
def log_cross_bot_jump(from_bot: str, to_bot: str, user_id: int,
                        payload: str = "", utm_source: str = None,
                        utm_campaign: str = None, utm_content: str = None) -> None:
    """Логирует /start <payload> переход между ботами экосистемы.
    Никогда не бросает (silent fail) — analytics не должна ломать UX."""
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cross_bot_jumps
                        (from_bot, to_bot, user_id, payload,
                         utm_source, utm_campaign, utm_content)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (from_bot or "unknown", to_bot, user_id,
                      payload or "", utm_source, utm_campaign, utm_content))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"[cbj] log fail: {e}", flush=True)


def get_cross_bot_stats(days: int = 7) -> list:
    """Возвращает агрегированную таблицу from_bot → to_bot за N дней."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # NB: интервал нельзя параметризовать как INTERVAL '%s days' —
            # подставляем как f-string (days приходит из кода, не от юзера)
            cur.execute(f"""
                SELECT from_bot, to_bot, COUNT(*) AS n,
                       COUNT(DISTINCT user_id) AS users
                  FROM cross_bot_jumps
                 WHERE jumped_at > NOW() - INTERVAL '{int(days)} days'
                 GROUP BY from_bot, to_bot
                 ORDER BY n DESC
            """)
            return list(cur.fetchall())
    finally:
        conn.close()
