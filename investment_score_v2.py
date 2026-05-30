"""investment_score_v2.py — 0-100 rating for each listing.

Formula:
    score = 25 * price_vs_median_factor + 25 * growth_factor +
            25 * yield_factor             + 25 * risk_factor

    price_vs_median_factor = clamp((1 - price/median)/0.3, 0, 1)
    growth_factor          = clamp(yoy_growth / 15, 0, 1)         # 15%+ = max
    yield_factor           = clamp(annual_yield / 10, 0, 1)       # 10%+ = max
    risk_factor            = 0.5*completion + 0.3*building_size + 0.2*deal_count

Rating:
    A+ 90-100, A 80-89, B+ 70-79, B 60-69, C+ 50-59, C 40-49, D <40

Public API:
    compute_score(listing_dict, dld_conn, intel_conn) -> dict
        {"score": float, "rating": str, "breakdown": {price,growth,yield,risk}}

Pure (no DB writes). Caches median/growth/yield by (area_norm, pt, br_bucket).
"""
from functools import lru_cache
from typing import Optional


# ── Helpers ────────────────────────────────────────────────────────────────
def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if x is None:
        return 0.0
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    return max(lo, min(hi, x))


def _norm_area(area: Optional[str]) -> str:
    if not area:
        return ""
    return area.strip().lower()


def _br_bucket(br) -> str:
    """Convert bedrooms int → DLD `rooms` bucket: studio / 1br..4br / 5br+ / all."""
    if br is None:
        return "all"
    try:
        n = int(br)
    except (TypeError, ValueError):
        return "all"
    if n <= 0:
        return "studio"
    if n >= 5:
        return "5br+"
    return f"{n}br"


def _pt_norm(pt: Optional[str]) -> str:
    """Normalize property_type to DLD bucket: apartment / villa / townhouse /
    office / retail / land. Default → apartment."""
    if not pt:
        return "apartment"
    p = pt.strip().lower()
    if p in ("apartment", "studio", "penthouse", "duplex", "hotel_apartment",
             "serviced_apartment"):
        return "apartment"
    if p in ("villa",):
        return "villa"
    if p in ("townhouse",):
        return "townhouse"
    if p in ("office",):
        return "office"
    if p in ("retail", "shop"):
        return "retail"
    if p in ("plot", "land"):
        return "land"
    if p in ("warehouse",):
        return "warehouse"
    return "apartment"


# ── Cached intel lookups ───────────────────────────────────────────────────
# Cache holds (median_aed, yoy_growth_pct, yield_pct, deals_count_area).
# Connection objects can't be hashed reliably, so we keep them outside the
# cache key — but the cache must be invalidated if the connection is replaced
# (we expose `reset_cache()` for tests).

_intel_conn_ref = [None]


def _area_intel_uncached(area_norm: str, pt: str, br_bucket: str, deal_type: str) -> dict:
    """Latest area_stats row for (area, pt, rooms, deal_type)."""
    conn = _intel_conn_ref[0]
    if conn is None or not area_norm:
        return {}
    try:
        with conn.cursor() as cur:
            # Try exact bucket first, then fall back to 'all'.
            for rooms_try in (br_bucket, "all"):
                cur.execute(
                    """
                    SELECT median_price_aed, yoy_growth_pct,
                           avg_rental_yield_pct, deals_count
                      FROM area_stats
                     WHERE LOWER(area_name) = %s
                       AND property_type   = %s
                       AND rooms           = %s
                       AND deal_type       = %s
                     ORDER BY period_month DESC
                     LIMIT 1
                    """,
                    (area_norm, pt, rooms_try, deal_type),
                )
                row = cur.fetchone()
                if row:
                    return {
                        "median": float(row[0]) if row[0] is not None else None,
                        "yoy":    float(row[1]) if row[1] is not None else None,
                        "yield":  float(row[2]) if row[2] is not None else None,
                        "deals":  int(row[3])  if row[3] is not None else 0,
                    }
    except Exception as e:
        print(f"[score_v2] area intel error: {e}")
    return {}


@lru_cache(maxsize=2048)
def _area_intel_cached(area_norm: str, pt: str, br_bucket: str, deal_type: str) -> tuple:
    """LRU-cached. Returns tuple so it's hashable."""
    d = _area_intel_uncached(area_norm, pt, br_bucket, deal_type)
    return (
        d.get("median"),
        d.get("yoy"),
        d.get("yield"),
        d.get("deals", 0),
    )


def _building_deals_uncached(building: str) -> int:
    """Total deals_count across periods/rooms for the building (proxy for
    project size / liquidity)."""
    conn = _intel_conn_ref[0]
    if conn is None or not building:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(deals_count), 0)
                  FROM building_stats
                 WHERE LOWER(building_name_display) = %s
                """,
                (building.strip().lower(),),
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception as e:
        print(f"[score_v2] building intel error: {e}")
    return 0


@lru_cache(maxsize=2048)
def _building_deals_cached(building_norm: str) -> int:
    return _building_deals_uncached(building_norm)


def reset_cache():
    """Test helper: clear LRU caches."""
    _area_intel_cached.cache_clear()
    _building_deals_cached.cache_clear()


# ── Rating bands ───────────────────────────────────────────────────────────
def _rating(score: float) -> str:
    if score >= 90: return "A+"
    if score >= 80: return "A"
    if score >= 70: return "B+"
    if score >= 60: return "B"
    if score >= 50: return "C+"
    if score >= 40: return "C"
    return "D"


# ── Public API ─────────────────────────────────────────────────────────────
def compute_score(listing: dict,
                  dld_conn=None,
                  intel_conn=None) -> dict:
    """Compute Investment Score 2.0 (0-100) for a listing.

    Args:
        listing: dict with at least price, area; optional building, bedrooms,
                 property_type, deal_type, size_sqft, status, is_off_plan.
        dld_conn: reserved for future per-building DLD lookups (currently unused;
                  building stats come from intel_conn).
        intel_conn: open psycopg2 connection to intel DB (area_stats /
                    building_stats). If None — fallback factors used.

    Returns:
        {
          "score": 0..100 (float, rounded to 1 decimal),
          "rating": "A+/A/B+/B/C+/C/D",
          "breakdown": {"price": 0..25, "growth": 0..25,
                         "yield": 0..25, "risk": 0..25},
          "components": {        # raw inputs used (for debugging)
              "median_aed": ...,
              "yoy_pct": ...,
              "yield_pct": ...,
              "area_deals": ...,
              "building_deals": ...,
              "price": ...,
          },
        }
    """
    if intel_conn is not None and _intel_conn_ref[0] is not intel_conn:
        _intel_conn_ref[0] = intel_conn
        # New connection → drop stale cached intel.
        reset_cache()

    price      = listing.get("price") or 0
    area       = listing.get("area")
    building   = listing.get("building")
    bedrooms   = listing.get("bedrooms")
    property_t = listing.get("property_type")
    deal_type  = (listing.get("deal_type") or "sale").lower()
    size_sqft  = listing.get("size_sqft") or 0
    is_off_plan= bool(listing.get("is_off_plan"))
    status     = (listing.get("status") or "").lower()

    area_norm  = _norm_area(area)
    pt         = _pt_norm(property_t)
    br_bucket  = _br_bucket(bedrooms)

    # Intel pulls (cached). For rent listings we still look up sale stats for
    # median/growth (capital benchmark), and rent stats for yield.
    sale_intel = _area_intel_cached(area_norm, pt, br_bucket, "sale") \
                 if area_norm else (None, None, None, 0)
    median, yoy, _sale_yield_unused, area_deals = sale_intel

    # Yield: prefer rent_residential table proxy via avg_rental_yield_pct from
    # area_stats (sale-side stores rental_yield computed from rent overlay).
    yield_pct = _sale_yield_unused

    # ── Factor 1: price vs median ──────────────────────────────────────────
    if price and median and median > 0:
        price_factor = _clamp((1 - price / median) / 0.30)
    else:
        price_factor = 0.5  # neutral if unknown

    # ── Factor 2: YoY growth ───────────────────────────────────────────────
    if yoy is not None:
        growth_factor = _clamp(yoy / 15.0)
    else:
        growth_factor = 0.4  # mild positive default (Dubai trend)

    # ── Factor 3: rental yield ─────────────────────────────────────────────
    if yield_pct is not None:
        yield_factor = _clamp(yield_pct / 10.0)
    else:
        yield_factor = 0.5

    # ── Factor 4: risk ─────────────────────────────────────────────────────
    # 0.5 * completion: ready=1.0, off_plan=0.4
    if is_off_plan:
        completion = 0.4
    elif status in ("ready", "vacant", "rented", "tenanted", "occupied"):
        completion = 1.0
    else:
        completion = 0.8  # unknown → assume mostly ready (Dubai resale skew)

    # 0.3 * building_size — total deals tracked for this building (>=200 = max)
    bld_deals = _building_deals_cached(building.strip().lower()) \
                if building else 0
    building_size = _clamp(bld_deals / 200.0)

    # 0.2 * area_deals — liquidity of the area segment (>=100 deals = max)
    deal_count_factor = _clamp(area_deals / 100.0)

    risk_factor = (0.5 * completion +
                   0.3 * building_size +
                   0.2 * deal_count_factor)
    risk_factor = _clamp(risk_factor)

    # ── Assemble ───────────────────────────────────────────────────────────
    price_pts  = 25.0 * price_factor
    growth_pts = 25.0 * growth_factor
    yield_pts  = 25.0 * yield_factor
    risk_pts   = 25.0 * risk_factor
    total      = price_pts + growth_pts + yield_pts + risk_pts

    return {
        "score":   round(total, 1),
        "rating":  _rating(total),
        "breakdown": {
            "price":  round(price_pts,  1),
            "growth": round(growth_pts, 1),
            "yield":  round(yield_pts,  1),
            "risk":   round(risk_pts,   1),
        },
        "components": {
            "median_aed":    median,
            "yoy_pct":       yoy,
            "yield_pct":     yield_pct,
            "area_deals":    area_deals,
            "building_deals": bld_deals,
            "price":         price,
            "completion":    completion,
        },
    }


# ── CLI smoke test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os, psycopg2
    INTEL_URL = os.environ.get("INTEL_DB_URL") or os.environ.get("ARCHIVE_DATABASE_URL")
    if not INTEL_URL:
        raise RuntimeError("INTEL_DB_URL / ARCHIVE_DATABASE_URL not set")
    RESALE_URL = os.environ.get(
        "RESALE_DB_URL",
        os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set")),
    )
    intel = psycopg2.connect(INTEL_URL)
    resale = psycopg2.connect(RESALE_URL)
    with resale.cursor() as cur:
        cur.execute(
            "SELECT id, price, area, building, bedrooms, property_type, "
            "deal_type, size_sqft, status, is_off_plan, investment_score "
            "FROM listings WHERE is_active=TRUE AND price IS NOT NULL "
            "AND area IS NOT NULL ORDER BY random() LIMIT 5"
        )
        rows = cur.fetchall()
    for r in rows:
        listing = {
            "id": r[0], "price": r[1], "area": r[2], "building": r[3],
            "bedrooms": r[4], "property_type": r[5], "deal_type": r[6],
            "size_sqft": r[7], "status": r[8], "is_off_plan": r[9],
        }
        res = compute_score(listing, dld_conn=None, intel_conn=intel)
        print(f"id={r[0]} | {r[3] or '?'} / {r[2]} / {r[4]}br "
              f"{r[5]} {r[6]} | price={r[1]:,}")
        print(f"   score={res['score']}  rating={res['rating']}  "
              f"breakdown={res['breakdown']}")
        print(f"   comp={res['components']}")
        print()
    intel.close(); resale.close()
