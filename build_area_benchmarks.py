"""B038: Build area+category price-benchmark table from DLD analytics data.

Source: DLD analytics DB (ANALYTICS_DATABASE_URL) — dld_transactions_full, dld_rents_full.
Target: resale-bot DB (DATABASE_URL) — area_price_benchmark.

Aggregates: percentile_cont 0.1/0.5/0.9 per (area, property_type, bedrooms, deal_type).
Window: last 24 months of registered data (last 1 month at runtime ⇒ fallback to whole table
if filtered set is small — DLD pipeline only has recent batches today).
Min sample_size = 5; smaller groups skipped.

Run manually (weekly cron later):
  py -3 build_area_benchmarks.py
"""
from __future__ import annotations

import os
import re
import sys
import time
from typing import Iterable

try:
    import psycopg2
    import psycopg2.extras
except Exception as e:
    print(f"psycopg2 missing: {e}")
    sys.exit(1)

RESALE_DSN = os.environ.get("DATABASE_URL")
if not RESALE_DSN:
    raise RuntimeError("DATABASE_URL required")
DLD_DSN = os.environ.get("ANALYTICS_DATABASE_URL")
if not DLD_DSN:
    raise RuntimeError("ANALYTICS_DATABASE_URL required")

MIN_SAMPLE = 5

DDL = """
CREATE TABLE IF NOT EXISTS area_price_benchmark (
    area          TEXT NOT NULL,
    property_type TEXT NOT NULL,
    bedrooms      INTEGER,
    deal_type     TEXT NOT NULL,
    p10           NUMERIC,
    median        NUMERIC,
    p90           NUMERIC,
    sample_size   INTEGER,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_apb_lookup ON area_price_benchmark(area, property_type, bedrooms, deal_type);
"""

# bedrooms is nullable (for non-categorised buckets / rents w/o rooms), so PK
# uses COALESCE on bedrooms to keep NULL distinct from -1.
DDL_NULL_UNIQUE = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_apb_pk_nullable
ON area_price_benchmark(area, property_type, COALESCE(bedrooms, -1), deal_type);
"""


# ── property_type / bedrooms normalisation ────────────────────────────────────
def norm_property_type(prop_type: str | None, sub_type: str | None) -> str | None:
    pt = (prop_type or "").strip().lower()
    st = (sub_type or "").strip().lower()
    if "villa" in pt or "villa" in st:
        return "villa"
    if "townhouse" in st or "town house" in st:
        return "townhouse"
    if "penthouse" in st:
        return "penthouse"
    if st == "flat" or pt == "unit" and "flat" in st:
        # studios handled by bedrooms
        return "apartment"
    if "hotel apartment" in st:
        return "apartment"
    return None  # skip plot/commercial/etc


_BR_RE = re.compile(r"^\s*(\d+)\s*B\s*/?\s*R\s*$", re.I)


def norm_bedrooms(rooms_en: str | None, sub_type: str | None) -> int | None:
    s = (rooms_en or "").strip()
    if not s:
        # rents have no rooms; treat as unknown → NULL bucket
        if (sub_type or "").strip().lower().startswith("studio"):
            return 0
        return None
    s_low = s.lower()
    if "studio" in s_low:
        return 0
    m = _BR_RE.match(s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    # "7 B/R+" / "PH" etc → cap at 7
    m2 = re.search(r"(\d+)", s_low)
    if m2:
        try:
            n = int(m2.group(1))
            return n if 0 <= n <= 12 else None
        except Exception:
            return None
    return None


def _load_friendly_map() -> dict:
    """Pull DLD→friendly alias map from parser_engine so bench keys match what
    the parser emits at runtime ('Downtown Dubai' not 'Burj Khalifa')."""
    try:
        from parser_engine import _DLD_TO_FRIENDLY
        return dict(_DLD_TO_FRIENDLY)
    except Exception as e:
        print(f"[bench] friendly-map load fail: {e}")
        return {}


_FRIENDLY = _load_friendly_map()


def norm_area(area_en: str | None) -> str | None:
    if not area_en:
        return None
    s = area_en.strip()
    if not s:
        return None
    # Title-case (DLD often UPPER CASE).
    title = s.title()
    # Apply DLD→friendly alias (Burj Khalifa→Downtown Dubai, Marsa Dubai→Dubai Marina, ...)
    return _FRIENDLY.get(title, title)


# ── Compute aggregates ────────────────────────────────────────────────────────
def fetch_sale_rows(conn) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Pull rows; aggregation done in Python to handle bedroom/property_type
    # normalisation that SQL would make ugly. Dataset is small enough.
    cur.execute(
        """
        SELECT area_en, prop_type_en, prop_sub_type_en, rooms_en, actual_worth
        FROM dld_transactions_full
        WHERE actual_worth IS NOT NULL AND actual_worth > 0
          AND area_en IS NOT NULL
        """
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def fetch_rent_rows(conn) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT area_en, prop_type_en, prop_sub_type_en, annual_amount
        FROM dld_rents_full
        WHERE annual_amount IS NOT NULL AND annual_amount > 0
          AND area_en IS NOT NULL
        """
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def aggregate(rows: Iterable[dict], deal_type: str) -> list[tuple]:
    """Group rows into (area, property_type, bedrooms, deal_type) → percentiles."""
    buckets: dict[tuple, list[float]] = {}
    for r in rows:
        area = norm_area(r.get("area_en"))
        pt = norm_property_type(r.get("prop_type_en"), r.get("prop_sub_type_en"))
        if not area or not pt:
            continue
        br = norm_bedrooms(r.get("rooms_en") if deal_type == "sale" else None,
                           r.get("prop_sub_type_en"))
        price = r.get("actual_worth") if deal_type == "sale" else r.get("annual_amount")
        try:
            price = float(price)
        except Exception:
            continue
        if price <= 0:
            continue
        key = (area, pt, br, deal_type)
        buckets.setdefault(key, []).append(price)

    out = []
    for key, prices in buckets.items():
        n = len(prices)
        if n < MIN_SAMPLE:
            continue
        ps = sorted(prices)

        def pct(p: float) -> float:
            # linear interpolation, percentile_cont semantics
            if n == 1:
                return ps[0]
            k = (n - 1) * p
            lo = int(k)
            hi = min(lo + 1, n - 1)
            frac = k - lo
            return ps[lo] + (ps[hi] - ps[lo]) * frac

        p10 = pct(0.10)
        med = pct(0.50)
        p90 = pct(0.90)
        area, pt, br, dt = key
        out.append((area, pt, br, dt, p10, med, p90, n))
    return out


def upsert(conn, rows: list[tuple]) -> int:
    if not rows:
        return 0
    cur = conn.cursor()
    # Two-step strategy: delete + insert (UPSERT with NULL-in-PK is awkward).
    # Build a set of keys we're rewriting, delete those, then insert.
    keys = list({(r[0], r[1], r[2], r[3]) for r in rows})
    psycopg2.extras.execute_batch(
        cur,
        """DELETE FROM area_price_benchmark
           WHERE area=%s AND property_type=%s
             AND COALESCE(bedrooms, -1) = COALESCE(%s, -1)
             AND deal_type=%s""",
        keys,
        page_size=500,
    )
    psycopg2.extras.execute_batch(
        cur,
        """INSERT INTO area_price_benchmark
           (area, property_type, bedrooms, deal_type, p10, median, p90, sample_size, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s, NOW())""",
        rows,
        page_size=500,
    )
    conn.commit()
    cur.close()
    return len(rows)


def main():
    t0 = time.time()
    print("[bench] connecting to resale-bot DB ...")
    rconn = psycopg2.connect(RESALE_DSN, connect_timeout=15)
    rcur = rconn.cursor()
    for stmt in DDL.split(";"):
        s = stmt.strip()
        if s:
            rcur.execute(s)
    rcur.execute(DDL_NULL_UNIQUE)
    rconn.commit()
    rcur.close()
    print("[bench] DDL ok")

    print("[bench] connecting to DLD analytics DB ...")
    dconn = psycopg2.connect(DLD_DSN, connect_timeout=15)

    print("[bench] fetching sales ...")
    sale_rows = fetch_sale_rows(dconn)
    print(f"[bench] {len(sale_rows)} raw sale rows")
    sale_aggs = aggregate(sale_rows, "sale")
    print(f"[bench] {len(sale_aggs)} sale buckets (>= {MIN_SAMPLE} samples)")

    print("[bench] fetching rents ...")
    rent_rows = fetch_rent_rows(dconn)
    print(f"[bench] {len(rent_rows)} raw rent rows")
    rent_aggs = aggregate(rent_rows, "rent")
    print(f"[bench] {len(rent_aggs)} rent buckets (>= {MIN_SAMPLE} samples)")

    dconn.close()

    print("[bench] upserting ...")
    n1 = upsert(rconn, sale_aggs)
    n2 = upsert(rconn, rent_aggs)
    rconn.close()
    print(f"[bench] inserted sale={n1} rent={n2} elapsed={time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
