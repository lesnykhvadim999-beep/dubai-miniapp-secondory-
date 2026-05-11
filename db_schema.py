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
CREATE TABLE IF NOT EXISTS leads (
    id              SERIAL PRIMARY KEY,
    telegram_id     BIGINT,
    username        VARCHAR(200),
    listing_id      INT REFERENCES listings(id),
    action          VARCHAR(50),   -- view/book/contact/save
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    notes           TEXT
);

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
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Check by telegram_message_id first (exact dedup)
            if data.get("telegram_message_id"):
                cur.execute(
                    "SELECT id FROM listings WHERE telegram_message_id=%s AND telegram_chat_id=%s",
                    (data["telegram_message_id"], data.get("telegram_chat_id", ""))
                )
                row = cur.fetchone()
                if row:
                    return row["id"], False

            # Check by listing_key (property dedup)
            key = data.get("listing_key")
            if key:
                cur.execute("SELECT id, price FROM listings WHERE listing_key=%s", (key,))
                row = cur.fetchone()
                if row:
                    existing_id = row["id"]
                    old_price = row["price"]
                    new_price = data.get("price")
                    # Update price history if price changed
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

            # Insert new listing
            cols = [
                "listing_key","source","telegram_chat_id","telegram_message_id","message_date",
                "original_text","seller_username","deal_type","property_type",
                "emirate","emirate_confidence","area","area_confidence",
                "building","building_confidence","location_confidence",
                "needs_manual_review","review_reason",
                "bedrooms","bathrooms","size_sqft","bua_sqft","plot_sqft",
                "floor","unit_number","view","furnishing","status",
                "price","currency","original_price","selling_price","price_per_sqft",
                "discount_amount","discount_percent",
                "is_hot_deal","deal_quality","deal_reason","is_below_market","price_vs_market_percent",
                "market_avg_sqft","market_rent_1br","market_growth_pct","roi_estimate",
                "airbnb_estimate_low","airbnb_estimate_high","investment_score",
                "agent_name","phone","whatsapp",
                "has_images","cover_image_url","confidence_score",
            ]
            vals = [data.get(c) for c in cols]
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


def save_images(listing_id: int, urls: list[str]):
    if not urls:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
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
        print(f"[db] save_images error: {e}")
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
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as total FROM listings WHERE is_active=TRUE")
            total = cur.fetchone()["total"]

            cur.execute("SELECT COUNT(*) as hot FROM listings WHERE is_hot_deal=TRUE AND is_active=TRUE")
            hot = cur.fetchone()["hot"]

            cur.execute("SELECT COUNT(*) as review FROM listings WHERE needs_manual_review=TRUE AND is_active=TRUE")
            review = cur.fetchone()["review"]

            cur.execute("""
                SELECT emirate, COUNT(*) as cnt FROM listings
                WHERE is_active=TRUE GROUP BY emirate ORDER BY cnt DESC
            """)
            by_emirate = {r["emirate"]: r["cnt"] for r in cur.fetchall()}

            cur.execute("""
                SELECT deal_quality, COUNT(*) as cnt FROM listings
                WHERE is_active=TRUE AND deal_quality IS NOT NULL
                GROUP BY deal_quality ORDER BY cnt DESC
            """)
            by_quality = {r["deal_quality"]: r["cnt"] for r in cur.fetchall()}

            # Today's sync
            cur.execute("""
                SELECT COALESCE(SUM(new_listings),0) as new,
                       COALESCE(SUM(duplicates),0) as dupes,
                       COALESCE(SUM(hot_deals),0) as hot,
                       COUNT(*) as syncs,
                       MAX(synced_at) as last_sync
                FROM sync_log
                WHERE synced_at >= NOW() - INTERVAL '24 hours'
            """)
            today = cur.fetchone()

            cur.execute("SELECT COUNT(*) as cnt FROM users")
            users_total = cur.fetchone()["cnt"]

            cur.execute("SELECT COUNT(*) as cnt FROM leads WHERE created_at >= NOW() - INTERVAL '24 hours'")
            leads_today = cur.fetchone()["cnt"]

            return {
                "total": total,
                "hot_deals": hot,
                "needs_review": review,
                "by_emirate": by_emirate,
                "by_quality": by_quality,
                "today_new": today["new"],
                "today_dupes": today["dupes"],
                "today_hot": today["hot"],
                "syncs_today": today["syncs"],
                "last_sync": today["last_sync"],
                "users_total": users_total,
                "leads_today": leads_today,
            }
    finally:
        conn.close()


def search_listings(filters: dict, limit: int = 10, offset: int = 0) -> tuple[list, int]:
    """Search listings with filters. Returns (results, total_count)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            where = ["is_active = TRUE"]
            params = []

            if filters.get("emirate"):
                where.append("emirate = %s")
                params.append(filters["emirate"])

            if filters.get("area"):
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

            if filters.get("bedrooms") is not None:
                br = filters["bedrooms"]
                if br == 0:
                    where.append("bedrooms = 0 OR property_type='studio'")
                elif br == 99:  # 4BR+
                    where.append("bedrooms >= 4")
                else:
                    where.append("bedrooms = %s")
                    params.append(br)

            if filters.get("min_price"):
                where.append("price >= %s")
                params.append(int(filters["min_price"] * 1_000_000))

            if filters.get("max_price"):
                where.append("price <= %s")
                params.append(int(filters["max_price"] * 1_000_000))

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
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(last_message_id) as mid FROM sync_log WHERE channel=%s",
                (channel,)
            )
            row = cur.fetchone()
            return row["mid"] if row and row["mid"] else None
    finally:
        conn.close()
