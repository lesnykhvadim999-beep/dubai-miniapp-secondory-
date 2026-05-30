"""
Runtime price-outlier check for live parser.

Usage:
    from price_outlier_check import is_price_outlier
    if is_price_outlier(area, property_type, deal_type, bedrooms, price, conn):
        listing['needs_manual_review'] = True
        listing['review_reason'] = 'price_outlier_3sigma'

Implementation:
    - Pulls all existing prices for the (area, property_type, deal_type, bedrooms)
      bucket from `listings`.
    - If bucket has <10 listings → return False (not enough sample).
    - Computes median + MAD; flags if |price - median| > 3 * 1.4826 * MAD
      OR price > 5 * median OR price < 0.2 * median.
    - Has a small in-process cache keyed by bucket (TTL = 600s) to avoid hammering
      DB on every parse.
"""

import statistics
import time

_CACHE = {}   # (area, pt, deal, br) -> (ts, median, mad, n)
_TTL_SEC = 600

MIN_BUCKET_SIZE = 10
MAD_K = 1.4826
SIGMA_CUTOFF = 3.0
HARD_HIGH = 5.0
HARD_LOW = 0.2


def _bucket_stats(area, property_type, deal_type, bedrooms, conn):
    key = (area, property_type, deal_type, bedrooms)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and (now - cached[0]) < _TTL_SEC:
        return cached[1], cached[2], cached[3]

    cur = conn.cursor()
    cur.execute(
        """
        SELECT price FROM listings
         WHERE area = %s
           AND property_type = %s
           AND deal_type = %s
           AND bedrooms = %s
           AND price IS NOT NULL AND price > 0
        """,
        (area, property_type, deal_type, bedrooms),
    )
    prices = [int(r[0]) for r in cur.fetchall()]
    cur.close()
    n = len(prices)
    if n < MIN_BUCKET_SIZE:
        _CACHE[key] = (now, None, None, n)
        return None, None, n
    med = statistics.median(prices)
    m = statistics.median([abs(p - med) for p in prices])
    _CACHE[key] = (now, med, m, n)
    return med, m, n


def is_price_outlier(area, property_type, deal_type, bedrooms, price, conn) -> bool:
    """Return True if `price` is an outlier for its (area, pt, deal, br) bucket."""
    if price is None or price <= 0:
        return False
    if not area or not property_type or not deal_type or bedrooms is None:
        return False
    med, m, n = _bucket_stats(area, property_type, deal_type, bedrooms, conn)
    if med is None or med <= 0:
        return False  # bucket too small — can't decide
    # sigma rule (only if MAD > 0; if MAD == 0 bucket is degenerate)
    if m and m > 0:
        if abs(price - med) > SIGMA_CUTOFF * MAD_K * m:
            return True
    # hard safety guards regardless of MAD
    if price > HARD_HIGH * med:
        return True
    if price < HARD_LOW * med:
        return True
    return False


def clear_cache():
    """Force-refresh bucket stats on next call (e.g. after a backfill)."""
    _CACHE.clear()
