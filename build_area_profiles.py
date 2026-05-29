"""B039: Build area × property_type profile table from DLD analytics data.

Idea: каждый район имеет известный профиль типов недвижимости. Если parsed
property_type составляет <5% сделок в районе → suspicious mis-parse.

Source: DLD analytics DB (ANALYTICS_DATABASE_URL) — dld_transactions_full.
Target: resale-bot DB (DATABASE_URL) — area_property_profile.

Window: last 36 months by transaction_date.
Min total_in_area: 20 (skip districts with insufficient data).

Mapping DLD → our property_type taxonomy:
  prop_sub_type_en='Villa' or contains 'Villa' → villa
  prop_sub_type_en='Flat' or 'Hotel Apartment' → apartment
  prop_type_en='Land'                          → plot
  prop_type_en='Building'                      → commercial
  (townhouse/penthouse in DLD live under 'Flat'; skipped — parser won't be
   gated on those types because profile won't have entries for them)

Run manually (weekly cron later):
  py -3 build_area_profiles.py
"""
from __future__ import annotations

import os
import sys
import time

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

MIN_TOTAL = 20  # минимум транзакций по району, иначе skip
WINDOW_MONTHS = 36

DDL = """
CREATE TABLE IF NOT EXISTS area_property_profile (
    area          TEXT NOT NULL,
    property_type TEXT NOT NULL,
    share_pct     NUMERIC(5,2),
    tx_count      INTEGER NOT NULL,
    total_in_area INTEGER NOT NULL,
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (area, property_type)
);
CREATE INDEX IF NOT EXISTS idx_app_lookup
    ON area_property_profile(area, property_type);
"""


def _load_friendly_map() -> dict:
    """Pull DLD→friendly alias map from parser_engine so profile keys match
    what the parser emits at runtime."""
    try:
        from parser_engine import _DLD_TO_FRIENDLY
        return dict(_DLD_TO_FRIENDLY)
    except Exception as e:
        print(f"[profile] friendly-map load fail: {e}")
        return {}


_FRIENDLY = _load_friendly_map()

# Local aliases for B039 profile only: DLD names that are NOT in
# parser_engine._DLD_TO_FRIENDLY but the parser DOES emit at runtime.
_LOCAL_ALIASES = {
    "Mbr District 1": "District One",
    "Mbr District 7": "District 11",  # rough — same Meydan family
}


def norm_area(area_en: str | None) -> str | None:
    if not area_en:
        return None
    s = area_en.strip()
    if not s:
        return None
    title = s.title()
    # parser_engine alias first, then local profile-specific alias
    return _FRIENDLY.get(title, _LOCAL_ALIASES.get(title, title))


def norm_property_type(prop_type: str | None, sub_type: str | None) -> str | None:
    pt = (prop_type or "").strip().lower()
    st = (sub_type or "").strip().lower()
    if "villa" in pt or "villa" in st:
        return "villa"
    if st == "flat" or ("flat" in st and "flat" in st.split()):
        return "apartment"
    if "hotel apartment" in st or st == "hotel apartment":
        return "apartment"
    if "residential flat" in st:
        return "apartment"
    if pt == "land":
        return "plot"
    if pt == "building":
        return "commercial"
    if st in ("office", "shop", "commercial", "warehouse", "industrial"):
        return "commercial"
    return None


def fetch_rows(conn) -> list[dict]:
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        f"""
        SELECT area_en, prop_type_en, prop_sub_type_en
        FROM dld_transactions_full
        WHERE area_en IS NOT NULL
          AND transaction_date::date >= (NOW() - INTERVAL '{WINDOW_MONTHS} months')::date
        """
    )
    rows = cur.fetchall()
    cur.close()
    return rows


def aggregate(rows) -> list[tuple]:
    """Group → (area, property_type) → count + total per area → share_pct."""
    pair_counts: dict[tuple[str, str], int] = {}
    area_totals: dict[str, int] = {}

    for r in rows:
        area = norm_area(r.get("area_en"))
        pt = norm_property_type(r.get("prop_type_en"), r.get("prop_sub_type_en"))
        if not area or not pt:
            continue
        pair_counts[(area, pt)] = pair_counts.get((area, pt), 0) + 1
        area_totals[area] = area_totals.get(area, 0) + 1

    out = []
    for (area, pt), n in pair_counts.items():
        total = area_totals[area]
        if total < MIN_TOTAL:
            continue
        share = (n / total) * 100.0
        out.append((area, pt, round(share, 2), n, total))
    return out


def upsert(conn, rows: list[tuple]) -> int:
    if not rows:
        return 0
    cur = conn.cursor()
    psycopg2.extras.execute_batch(
        cur,
        """INSERT INTO area_property_profile
             (area, property_type, share_pct, tx_count, total_in_area, updated_at)
           VALUES (%s,%s,%s,%s,%s, NOW())
           ON CONFLICT (area, property_type) DO UPDATE SET
             share_pct     = EXCLUDED.share_pct,
             tx_count      = EXCLUDED.tx_count,
             total_in_area = EXCLUDED.total_in_area,
             updated_at    = NOW()""",
        rows,
        page_size=500,
    )
    conn.commit()
    cur.close()
    return len(rows)


def main():
    t0 = time.time()
    print("[profile] connecting to resale-bot DB ...")
    rconn = psycopg2.connect(RESALE_DSN, connect_timeout=15)
    rcur = rconn.cursor()
    for stmt in DDL.split(";"):
        s = stmt.strip()
        if s:
            rcur.execute(s)
    rconn.commit()
    rcur.close()
    print("[profile] DDL ok")

    print("[profile] connecting to DLD analytics DB ...")
    dconn = psycopg2.connect(DLD_DSN, connect_timeout=15)
    print(f"[profile] fetching last {WINDOW_MONTHS}m transactions ...")
    rows = fetch_rows(dconn)
    print(f"[profile] {len(rows)} raw rows")
    dconn.close()

    aggs = aggregate(rows)
    print(f"[profile] {len(aggs)} (area, property_type) buckets (>= {MIN_TOTAL} tx/area)")

    if aggs:
        # Покажем несколько примеров edge cases
        for ex in ("Downtown Dubai", "Al Barari", "District One", "Emirates Hills",
                   "Jumeirah Village Circle", "Palm Jumeirah", "Dubai Marina"):
            sub = [a for a in aggs if a[0] == ex]
            if sub:
                sub.sort(key=lambda x: -x[2])
                print(f"  [{ex}] (total={sub[0][4]}):")
                for area, pt, share, n, total in sub:
                    print(f"    {pt:12s} {share:6.2f}%  ({n}/{total})")

    print("[profile] upserting ...")
    n = upsert(rconn, aggs)
    rconn.close()
    print(f"[profile] upserted {n} rows  elapsed={time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
