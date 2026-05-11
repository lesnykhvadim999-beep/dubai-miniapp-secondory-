"""
market_updater.py — Monthly market data updater
Sources:
- DXBInteract (Dubai Land Department) — real transaction data
- DARI (Abu Dhabi Real Estate) — Abu Dhabi transactions
- Dubai REST API — open data
Updates market_data table once per month.
"""
import os
import time
import requests
from datetime import datetime, timedelta
from db_schema import get_conn

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Schema ─────────────────────────────────────────────────────────────────────
MARKET_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_data (
    id              SERIAL PRIMARY KEY,
    area            VARCHAR(200) NOT NULL,
    emirate         VARCHAR(100) NOT NULL,

    -- Sale prices
    avg_price_sqft  FLOAT,          -- AED per sqft
    avg_price_1br   BIGINT,         -- avg sale price 1BR
    avg_price_2br   BIGINT,
    avg_price_3br   BIGINT,
    median_price    BIGINT,         -- median transaction price

    -- Rental
    avg_rent_studio BIGINT,         -- yearly AED
    avg_rent_1br    BIGINT,
    avg_rent_2br    BIGINT,
    avg_rent_3br    BIGINT,

    -- Market health
    transactions_count  INT,        -- number of transactions this month
    avg_days_on_market  INT,        -- liquidity indicator
    price_change_pct    FLOAT,      -- vs previous month %
    yoy_change_pct      FLOAT,      -- year-over-year %

    -- Investment metrics
    avg_roi             FLOAT,      -- rental yield %
    demand_score        FLOAT,      -- 0-10 demand indicator
    liquidity_score     FLOAT,      -- 0-10 how fast properties sell

    -- Source info
    source          VARCHAR(100),
    data_month      VARCHAR(10),    -- "2026-05"
    updated_at      TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(area, emirate, data_month)
);

CREATE INDEX IF NOT EXISTS idx_market_area ON market_data(area);
CREATE INDEX IF NOT EXISTS idx_market_emirate ON market_data(emirate);
CREATE INDEX IF NOT EXISTS idx_market_month ON market_data(data_month);
"""


def init_market_db():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(MARKET_SCHEMA)
        conn.commit()
        print("[market] Schema OK")
    finally:
        conn.close()


# ── DXBInteract Parser (Dubai Land Department) ─────────────────────────────────
def fetch_dxb_transactions(area: str = None) -> list[dict]:
    """
    Fetch real transaction data from DXBInteract.
    Returns list of transaction records.
    """
    results = []
    try:
        # DXBInteract open API endpoint
        url = "https://www.dxbinteract.com/api/transactions"
        params = {
            "type": "sales",
            "period": "last_month",
            "format": "json",
        }
        if area:
            params["area"] = area

        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("data") or data.get("transactions") or []
            print(f"[dxb] Fetched {len(results)} transactions for {area or 'all'}")
    except Exception as e:
        print(f"[dxb] Error: {e}")

    # Fallback: try alternative endpoint
    if not results:
        try:
            url2 = "https://dubailand.gov.ae/api/v1/transactions"
            resp2 = requests.get(url2, headers=HEADERS, timeout=15)
            if resp2.status_code == 200:
                data2 = resp2.json()
                results = data2.get("result") or data2.get("data") or []
                print(f"[dxb] DLD fallback: {len(results)} records")
        except Exception as e2:
            print(f"[dxb] DLD fallback error: {e2}")

    return results


def fetch_dxb_rental(area: str = None) -> list[dict]:
    """Fetch rental transaction data from DXBInteract."""
    results = []
    try:
        url = "https://www.dxbinteract.com/api/transactions"
        params = {"type": "rentals", "period": "last_month", "format": "json"}
        if area:
            params["area"] = area
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("data") or data.get("transactions") or []
    except Exception as e:
        print(f"[dxb] Rental error: {e}")
    return results


def fetch_dubai_rest_areas() -> list[dict]:
    """
    Fetch area statistics from Dubai REST (open data).
    Returns area-level aggregated data.
    """
    results = []
    try:
        # Dubai REST open endpoints
        endpoints = [
            "https://gateway.dubairest.ae/api/real-estate/areas/statistics",
            "https://api.dubailand.gov.ae/odata/transactions",
        ]
        for url in endpoints:
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("value") or data.get("data") or []
                    if results:
                        print(f"[rest] Got {len(results)} area records from {url}")
                        break
            except:
                continue
    except Exception as e:
        print(f"[rest] Error: {e}")
    return results


def fetch_dari_abudhabi() -> list[dict]:
    """
    Fetch Abu Dhabi real estate data from DARI (dari.ae).
    Official Abu Dhabi Real Estate Centre.
    """
    results = []
    try:
        # DARI open API
        url = "https://dari.ae/api/v1/market-data"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("data") or []
            print(f"[dari] Got {len(results)} Abu Dhabi records")
    except Exception as e:
        print(f"[dari] Error: {e}")
    return results


# ── Data Processing ────────────────────────────────────────────────────────────
def _avg(values: list) -> float:
    vals = [v for v in values if v and v > 0]
    return round(sum(vals) / len(vals), 2) if vals else None


def _median(values: list) -> float:
    vals = sorted([v for v in values if v and v > 0])
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid-1] + vals[mid]) / 2


def process_transactions(transactions: list, area: str, emirate: str) -> dict:
    """Process raw transactions into market_data record."""
    if not transactions:
        return {}

    prices = []
    prices_1br = []
    prices_2br = []
    prices_3br = []
    sqft_prices = []

    for t in transactions:
        price = t.get("price") or t.get("amount") or t.get("transaction_value")
        beds  = t.get("bedrooms") or t.get("beds") or t.get("no_of_bedrooms")
        sqft  = t.get("sqft") or t.get("area_sqft") or t.get("property_size")

        if price:
            price = float(price)
            prices.append(price)
            if beds == 1 or beds == "1":
                prices_1br.append(price)
            elif beds == 2 or beds == "2":
                prices_2br.append(price)
            elif beds == 3 or beds == "3":
                prices_3br.append(price)

            if sqft and float(sqft) > 0:
                sqft_prices.append(price / float(sqft))

    count = len(transactions)
    avg_sqft = _avg(sqft_prices)

    # Liquidity score based on transaction count
    liquidity = min(10.0, round(count / 10, 1)) if count else 0

    # ROI estimate if we have rental data
    avg_1br = _avg(prices_1br)
    roi = None

    return {
        "area": area,
        "emirate": emirate,
        "avg_price_sqft": avg_sqft,
        "avg_price_1br": int(_avg(prices_1br)) if prices_1br else None,
        "avg_price_2br": int(_avg(prices_2br)) if prices_2br else None,
        "avg_price_3br": int(_avg(prices_3br)) if prices_3br else None,
        "median_price": int(_median(prices)) if prices else None,
        "transactions_count": count,
        "liquidity_score": liquidity,
        "source": "DXBInteract/DLD",
        "data_month": datetime.now().strftime("%Y-%m"),
    }


def process_rentals(rentals: list, area: str) -> dict:
    """Process rental transactions."""
    if not rentals:
        return {}

    rents_studio = []
    rents_1br = []
    rents_2br = []
    rents_3br = []

    for r in rentals:
        rent  = r.get("annual_rent") or r.get("rent") or r.get("amount")
        beds  = r.get("bedrooms") or r.get("beds") or 1

        if rent:
            rent = float(rent)
            if beds == 0 or str(beds) in ["0", "studio"]:
                rents_studio.append(rent)
            elif beds == 1 or str(beds) == "1":
                rents_1br.append(rent)
            elif beds == 2 or str(beds) == "2":
                rents_2br.append(rent)
            elif beds == 3 or str(beds) == "3":
                rents_3br.append(rent)

    return {
        "avg_rent_studio": int(_avg(rents_studio)) if rents_studio else None,
        "avg_rent_1br":    int(_avg(rents_1br)) if rents_1br else None,
        "avg_rent_2br":    int(_avg(rents_2br)) if rents_2br else None,
        "avg_rent_3br":    int(_avg(rents_3br)) if rents_3br else None,
    }


# ── Fallback: static data when APIs unavailable ────────────────────────────────
# Based on DLD published reports — updated manually when needed
STATIC_MARKET_DATA = [
    # Dubai
    {"area": "Downtown Dubai",           "emirate": "Dubai",          "avg_price_sqft": 2650, "avg_rent_1br": 135000, "avg_rent_2br": 200000, "avg_rent_3br": 285000, "avg_roi": 5.8, "yoy_change_pct": 7.2, "demand_score": 9.2, "liquidity_score": 8.5, "transactions_count": 245},
    {"area": "Business Bay",             "emirate": "Dubai",          "avg_price_sqft": 1850, "avg_rent_1br": 108000, "avg_rent_2br": 158000, "avg_rent_3br": 225000, "avg_roi": 6.2, "yoy_change_pct": 6.8, "demand_score": 8.8, "liquidity_score": 8.2, "transactions_count": 312},
    {"area": "Dubai Marina",             "emirate": "Dubai",          "avg_price_sqft": 1950, "avg_rent_1br": 112000, "avg_rent_2br": 165000, "avg_rent_3br": 235000, "avg_roi": 6.0, "yoy_change_pct": 5.5, "demand_score": 9.0, "liquidity_score": 8.8, "transactions_count": 289},
    {"area": "Palm Jumeirah",            "emirate": "Dubai",          "avg_price_sqft": 3600, "avg_rent_1br": 185000, "avg_rent_2br": 280000, "avg_rent_3br": 420000, "avg_roi": 5.2, "yoy_change_pct": 8.5, "demand_score": 9.5, "liquidity_score": 7.8, "transactions_count": 98},
    {"area": "Jumeirah Village Circle",  "emirate": "Dubai",          "avg_price_sqft": 1120, "avg_rent_1br": 67000,  "avg_rent_2br": 95000,  "avg_rent_3br": 130000, "avg_roi": 7.8, "yoy_change_pct": 4.2, "demand_score": 7.5, "liquidity_score": 7.2, "transactions_count": 445},
    {"area": "Dubai Hills Estate",       "emirate": "Dubai",          "avg_price_sqft": 1650, "avg_rent_1br": 97000,  "avg_rent_2br": 145000, "avg_rent_3br": 210000, "avg_roi": 6.1, "yoy_change_pct": 6.5, "demand_score": 8.5, "liquidity_score": 7.5, "transactions_count": 187},
    {"area": "Dubai Creek Harbour",      "emirate": "Dubai",          "avg_price_sqft": 1750, "avg_rent_1br": 102000, "avg_rent_2br": 152000, "avg_rent_3br": 215000, "avg_roi": 6.4, "yoy_change_pct": 7.8, "demand_score": 8.2, "liquidity_score": 7.0, "transactions_count": 156},
    {"area": "Jumeirah Beach Residence", "emirate": "Dubai",          "avg_price_sqft": 2050, "avg_rent_1br": 122000, "avg_rent_2br": 178000, "avg_rent_3br": 255000, "avg_roi": 6.2, "yoy_change_pct": 5.2, "demand_score": 8.8, "liquidity_score": 8.0, "transactions_count": 134},
    {"area": "MBR City",                 "emirate": "Dubai",          "avg_price_sqft": 1620, "avg_rent_1br": 97000,  "avg_rent_2br": 142000, "avg_rent_3br": 205000, "avg_roi": 6.5, "yoy_change_pct": 6.8, "demand_score": 8.0, "liquidity_score": 7.2, "transactions_count": 178},
    {"area": "Meydan",                   "emirate": "Dubai",          "avg_price_sqft": 1420, "avg_rent_1br": 87000,  "avg_rent_2br": 128000, "avg_rent_3br": 185000, "avg_roi": 6.8, "yoy_change_pct": 5.8, "demand_score": 7.8, "liquidity_score": 7.0, "transactions_count": 142},
    {"area": "Emaar South",              "emirate": "Dubai",          "avg_price_sqft": 920,  "avg_rent_1br": 62000,  "avg_rent_2br": 88000,  "avg_rent_3br": 125000, "avg_roi": 7.2, "yoy_change_pct": 5.5, "demand_score": 7.0, "liquidity_score": 6.5, "transactions_count": 98},
    {"area": "Al Furjan",                "emirate": "Dubai",          "avg_price_sqft": 1020, "avg_rent_1br": 67000,  "avg_rent_2br": 95000,  "avg_rent_3br": 135000, "avg_roi": 7.5, "yoy_change_pct": 4.5, "demand_score": 7.2, "liquidity_score": 6.8, "transactions_count": 112},
    {"area": "DAMAC Hills",              "emirate": "Dubai",          "avg_price_sqft": 1020, "avg_rent_1br": 67000,  "avg_rent_2br": 95000,  "avg_rent_3br": 138000, "avg_roi": 7.2, "yoy_change_pct": 5.0, "demand_score": 7.5, "liquidity_score": 6.8, "transactions_count": 125},
    {"area": "Bluewaters Island",        "emirate": "Dubai",          "avg_price_sqft": 3050, "avg_rent_1br": 185000, "avg_rent_2br": 268000, "avg_rent_3br": 385000, "avg_roi": 5.5, "yoy_change_pct": 7.2, "demand_score": 9.0, "liquidity_score": 7.5, "transactions_count": 45},
    {"area": "Dubai South",             "emirate": "Dubai",          "avg_price_sqft": 820,  "avg_rent_1br": 57000,  "avg_rent_2br": 82000,  "avg_rent_3br": 115000, "avg_roi": 7.8, "yoy_change_pct": 4.8, "demand_score": 6.8, "liquidity_score": 6.2, "transactions_count": 87},
    {"area": "Sports City",             "emirate": "Dubai",          "avg_price_sqft": 820,  "avg_rent_1br": 54000,  "avg_rent_2br": 78000,  "avg_rent_3br": 112000, "avg_roi": 7.5, "yoy_change_pct": 3.8, "demand_score": 6.5, "liquidity_score": 6.0, "transactions_count": 78},
    {"area": "Silicon Oasis",           "emirate": "Dubai",          "avg_price_sqft": 720,  "avg_rent_1br": 52000,  "avg_rent_2br": 75000,  "avg_rent_3br": 108000, "avg_roi": 8.0, "yoy_change_pct": 3.5, "demand_score": 6.2, "liquidity_score": 5.8, "transactions_count": 65},
    {"area": "International City",      "emirate": "Dubai",          "avg_price_sqft": 520,  "avg_rent_1br": 42000,  "avg_rent_2br": 62000,  "avg_rent_3br": 88000,  "avg_roi": 9.2, "yoy_change_pct": 2.5, "demand_score": 5.8, "liquidity_score": 5.5, "transactions_count": 145},
    {"area": "DIFC",                    "emirate": "Dubai",          "avg_price_sqft": 2850, "avg_rent_1br": 152000, "avg_rent_2br": 225000, "avg_rent_3br": 320000, "avg_roi": 5.5, "yoy_change_pct": 6.2, "demand_score": 8.8, "liquidity_score": 7.8, "transactions_count": 67},
    {"area": "City Walk",               "emirate": "Dubai",          "avg_price_sqft": 2250, "avg_rent_1br": 125000, "avg_rent_2br": 182000, "avg_rent_3br": 262000, "avg_roi": 5.8, "yoy_change_pct": 5.8, "demand_score": 8.5, "liquidity_score": 7.5, "transactions_count": 58},
    {"area": "Dubai Harbour",           "emirate": "Dubai",          "avg_price_sqft": 2280, "avg_rent_1br": 128000, "avg_rent_2br": 188000, "avg_rent_3br": 268000, "avg_roi": 6.0, "yoy_change_pct": 6.5, "demand_score": 8.8, "liquidity_score": 7.8, "transactions_count": 72},
    {"area": "Sobha Hartland",          "emirate": "Dubai",          "avg_price_sqft": 1850, "avg_rent_1br": 103000, "avg_rent_2br": 152000, "avg_rent_3br": 218000, "avg_roi": 6.2, "yoy_change_pct": 6.2, "demand_score": 8.2, "liquidity_score": 7.2, "transactions_count": 95},
    {"area": "Arjan",                   "emirate": "Dubai",          "avg_price_sqft": 970,  "avg_rent_1br": 62000,  "avg_rent_2br": 89000,  "avg_rent_3br": 128000, "avg_roi": 7.5, "yoy_change_pct": 4.2, "demand_score": 7.0, "liquidity_score": 6.5, "transactions_count": 89},
    # Abu Dhabi
    {"area": "Yas Island",              "emirate": "Abu Dhabi",      "avg_price_sqft": 1250, "avg_rent_1br": 78000,  "avg_rent_2br": 115000, "avg_rent_3br": 162000, "avg_roi": 6.8, "yoy_change_pct": 5.5, "demand_score": 8.2, "liquidity_score": 7.2, "transactions_count": 125},
    {"area": "Saadiyat Island",         "emirate": "Abu Dhabi",      "avg_price_sqft": 1650, "avg_rent_1br": 98000,  "avg_rent_2br": 145000, "avg_rent_3br": 208000, "avg_roi": 6.0, "yoy_change_pct": 6.2, "demand_score": 8.5, "liquidity_score": 7.0, "transactions_count": 87},
    {"area": "Al Reem Island",          "emirate": "Abu Dhabi",      "avg_price_sqft": 1120, "avg_rent_1br": 74000,  "avg_rent_2br": 108000, "avg_rent_3br": 155000, "avg_roi": 7.2, "yoy_change_pct": 5.0, "demand_score": 8.0, "liquidity_score": 7.5, "transactions_count": 145},
    {"area": "Al Raha",                 "emirate": "Abu Dhabi",      "avg_price_sqft": 1050, "avg_rent_1br": 68000,  "avg_rent_2br": 98000,  "avg_rent_3br": 142000, "avg_roi": 7.0, "yoy_change_pct": 4.8, "demand_score": 7.5, "liquidity_score": 7.0, "transactions_count": 98},
    # RAK
    {"area": "Al Marjan Island",        "emirate": "Ras Al Khaimah", "avg_price_sqft": 1350, "avg_rent_1br": 82000,  "avg_rent_2br": 122000, "avg_rent_3br": 175000, "avg_roi": 7.5, "yoy_change_pct": 12.5,"demand_score": 8.8, "liquidity_score": 7.5, "transactions_count": 165},
    {"area": "Mina Al Arab",            "emirate": "Ras Al Khaimah", "avg_price_sqft": 1180, "avg_rent_1br": 72000,  "avg_rent_2br": 105000, "avg_rent_3br": 152000, "avg_roi": 7.2, "yoy_change_pct": 10.2,"demand_score": 8.2, "liquidity_score": 7.0, "transactions_count": 98},
    {"area": "Al Hamra Village",        "emirate": "Ras Al Khaimah", "avg_price_sqft": 950,  "avg_rent_1br": 58000,  "avg_rent_2br": 85000,  "avg_rent_3br": 122000, "avg_roi": 7.0, "yoy_change_pct": 8.5, "demand_score": 7.5, "liquidity_score": 6.8, "transactions_count": 75},
]


def save_market_data(records: list[dict]):
    """Save market data to database."""
    conn = get_conn()
    saved = 0
    try:
        with conn.cursor() as cur:
            for rec in records:
                data_month = rec.get("data_month", datetime.now().strftime("%Y-%m"))
                cur.execute("""
                    INSERT INTO market_data (
                        area, emirate,
                        avg_price_sqft, avg_price_1br, avg_price_2br, avg_price_3br, median_price,
                        avg_rent_studio, avg_rent_1br, avg_rent_2br, avg_rent_3br,
                        transactions_count, avg_days_on_market, price_change_pct, yoy_change_pct,
                        avg_roi, demand_score, liquidity_score, source, data_month
                    ) VALUES (
                        %(area)s, %(emirate)s,
                        %(avg_price_sqft)s, %(avg_price_1br)s, %(avg_price_2br)s, %(avg_price_3br)s, %(median_price)s,
                        %(avg_rent_studio)s, %(avg_rent_1br)s, %(avg_rent_2br)s, %(avg_rent_3br)s,
                        %(transactions_count)s, %(avg_days_on_market)s, %(price_change_pct)s, %(yoy_change_pct)s,
                        %(avg_roi)s, %(demand_score)s, %(liquidity_score)s, %(source)s, %(data_month)s
                    )
                    ON CONFLICT (area, emirate, data_month) DO UPDATE SET
                        avg_price_sqft = EXCLUDED.avg_price_sqft,
                        avg_rent_1br = EXCLUDED.avg_rent_1br,
                        avg_rent_2br = EXCLUDED.avg_rent_2br,
                        avg_rent_3br = EXCLUDED.avg_rent_3br,
                        transactions_count = EXCLUDED.transactions_count,
                        yoy_change_pct = EXCLUDED.yoy_change_pct,
                        avg_roi = EXCLUDED.avg_roi,
                        demand_score = EXCLUDED.demand_score,
                        liquidity_score = EXCLUDED.liquidity_score,
                        updated_at = NOW()
                """, {
                    "area": rec.get("area"),
                    "emirate": rec.get("emirate"),
                    "avg_price_sqft": rec.get("avg_price_sqft"),
                    "avg_price_1br": rec.get("avg_price_1br"),
                    "avg_price_2br": rec.get("avg_price_2br"),
                    "avg_price_3br": rec.get("avg_price_3br"),
                    "median_price": rec.get("median_price"),
                    "avg_rent_studio": rec.get("avg_rent_studio"),
                    "avg_rent_1br": rec.get("avg_rent_1br"),
                    "avg_rent_2br": rec.get("avg_rent_2br"),
                    "avg_rent_3br": rec.get("avg_rent_3br"),
                    "transactions_count": rec.get("transactions_count"),
                    "avg_days_on_market": rec.get("avg_days_on_market"),
                    "price_change_pct": rec.get("price_change_pct"),
                    "yoy_change_pct": rec.get("yoy_change_pct"),
                    "avg_roi": rec.get("avg_roi"),
                    "demand_score": rec.get("demand_score"),
                    "liquidity_score": rec.get("liquidity_score"),
                    "source": rec.get("source", "static"),
                    "data_month": data_month,
                })
                saved += 1
        conn.commit()
        print(f"[market] Saved {saved} records")
    except Exception as e:
        conn.rollback()
        print(f"[market] Save error: {e}")
    finally:
        conn.close()


def get_market_data(area: str, emirate: str = None) -> dict:
    """Get latest market data for an area."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if emirate:
                cur.execute("""
                    SELECT * FROM market_data
                    WHERE LOWER(area) = LOWER(%s) AND emirate = %s
                    ORDER BY data_month DESC LIMIT 1
                """, (area, emirate))
            else:
                cur.execute("""
                    SELECT * FROM market_data
                    WHERE LOWER(area) = LOWER(%s)
                    ORDER BY data_month DESC LIMIT 1
                """, (area,))
            return cur.fetchone()
    finally:
        conn.close()


def run_update():
    """
    Main update function — runs once per month.
    1. Try to fetch live data from DXBInteract/DLD
    2. Fallback to static data if APIs unavailable
    """
    print(f"[market] Starting monthly update at {datetime.now()}")
    init_market_db()

    records_to_save = []
    data_month = datetime.now().strftime("%Y-%m")

    # Try live APIs first
    print("[market] Fetching live data from DXBInteract...")
    live_success = False

    try:
        # Try DXBInteract for Dubai transactions
        dubai_transactions = fetch_dxb_transactions()
        dubai_rentals = fetch_dxb_rental()

        if dubai_transactions:
            print(f"[market] Got {len(dubai_transactions)} live transactions")
            live_success = True
            # Process by area
            # Group transactions by area
            area_groups = {}
            for t in dubai_transactions:
                area = t.get("area_name") or t.get("area") or t.get("location") or "Unknown"
                if area not in area_groups:
                    area_groups[area] = []
                area_groups[area].append(t)

            for area, txns in area_groups.items():
                rec = process_transactions(txns, area, "Dubai")
                if rec:
                    # Add rental data
                    area_rentals = [r for r in dubai_rentals
                                    if (r.get("area_name") or r.get("area")) == area]
                    rental_data = process_rentals(area_rentals, area)
                    rec.update(rental_data)
                    rec["data_month"] = data_month
                    records_to_save.append(rec)

    except Exception as e:
        print(f"[market] Live fetch failed: {e}")

    # Also try Abu Dhabi
    try:
        dari_data = fetch_dari_abudhabi()
        if dari_data:
            for item in dari_data:
                area = item.get("area") or item.get("district") or "Unknown"
                rec = {
                    "area": area,
                    "emirate": "Abu Dhabi",
                    "avg_price_sqft": item.get("avg_price_sqft"),
                    "avg_rent_1br": item.get("avg_rent_1br"),
                    "avg_roi": item.get("avg_roi"),
                    "transactions_count": item.get("count"),
                    "source": "DARI",
                    "data_month": data_month,
                }
                records_to_save.append(rec)
    except Exception as e:
        print(f"[market] DARI fetch failed: {e}")

    # Always save static data as baseline
    print("[market] Saving static baseline data...")
    static_with_month = []
    for rec in STATIC_MARKET_DATA:
        r = dict(rec)
        r["data_month"] = data_month
        r["source"] = "DLD_static" if not live_success else "DLD_live+static"
        static_with_month.append(r)

    # Merge: live data overrides static for same areas
    all_records = static_with_month.copy()
    live_areas = {r["area"] for r in records_to_save}
    for r in records_to_save:
        # Remove static record for this area if we have live data
        all_records = [x for x in all_records if x["area"] != r["area"]]
        all_records.append(r)

    save_market_data(all_records)
    print(f"[market] Update complete. {len(all_records)} areas saved.")
    return len(all_records)


def should_update() -> bool:
    """Check if market data needs updating (once per month)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            current_month = datetime.now().strftime("%Y-%m")
            cur.execute(
                "SELECT COUNT(*) as cnt FROM market_data WHERE data_month = %s",
                (current_month,)
            )
            row = cur.fetchone()
            return (row["cnt"] if row else 0) == 0
    except:
        return True
    finally:
        conn.close()


def start_market_scheduler():
    """Start monthly market data scheduler in background thread."""
    import threading

    def _scheduler():
        # Run immediately if no data for this month
        if should_update():
            print("[market] No data for this month — running update now...")
            run_update()
        else:
            print("[market] Market data up to date for this month.")

        # Then check daily at midnight
        while True:
            now = datetime.now()
            # Next check: tomorrow at 03:00
            next_check = (now + timedelta(days=1)).replace(
                hour=3, minute=0, second=0, microsecond=0)
            wait = (next_check - now).total_seconds()
            print(f"[market] Next check at {next_check.strftime('%d.%m %H:%M')}")
            time.sleep(wait)

            # Run update on 1st of each month
            if datetime.now().day == 1 or should_update():
                run_update()

    t = threading.Thread(target=_scheduler, daemon=True, name="market-updater")
    t.start()
    print("[market] Scheduler started.")
    return t
