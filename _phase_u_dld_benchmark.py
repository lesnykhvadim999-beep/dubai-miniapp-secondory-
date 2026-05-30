"""PHASE U: DLD-benchmark fallback for missing listing fields.

For active listings where:
  - building IS NOT NULL
  - price IS NULL  OR  size_sqft IS NULL
we look up DLD `dld_sale_archive` for the SAME building + same bedroom count
in the last 180 days. If we find >= 5 comparable transactions we use the
median to fill the missing value(s):

  price     := median(actual_worth) * 0.95     (conservative 5% discount)
  size_sqft := median(actual_worth / meter_sale_price * 10.7639)
               (m^2 -> sqft, since meter_sale_price is per m^2)

Every field we fill is marked in `derived_from_dld` JSONB:
    {"price": true, "size_sqft": true, "source": "dld_180d", "n": 12}

NEVER overwrite an existing non-null value.
Requires >=5 comparable DLD txns.
"""
from __future__ import annotations
import io
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                   line_buffering=True)
except Exception:
    pass

import psycopg2
from psycopg2.extras import Json, execute_batch

DLD_DSN = os.environ.get(
    "DLD_DATABASE_URL",
    "postgresql://postgres:REDACTED_ARCHIVE_DB_PASSWORD"
    "@switchback.proxy.rlwy.net:23244/railway",
)
RESALE_DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:REDACTED_DSN_PASSWORD"
    "@tramway.proxy.rlwy.net:23228/railway",
)
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
MIN_TXNS = 5
WINDOW_DAYS = 180
DISCOUNT = 0.95           # conservative discount applied to median price
SQM_TO_SQFT = 10.7639


def br_label(br):
    """Map listings.bedrooms (int) -> DLD rooms_en label."""
    if br is None:
        return None
    if br == 0:
        return "Studio"
    if 1 <= br <= 10:
        return f"{br} B/R"
    return None


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> None:
    log(f"DRY_RUN={DRY_RUN}  MIN_TXNS={MIN_TXNS}  WINDOW={WINDOW_DAYS}d  "
        f"DISCOUNT={DISCOUNT}")
    dld = psycopg2.connect(DLD_DSN, connect_timeout=10)
    rs = psycopg2.connect(RESALE_DSN, connect_timeout=10)

    # ------------------------------------------------------------------
    # 1) Collect candidate listings
    # ------------------------------------------------------------------
    rcur = rs.cursor()
    rcur.execute("""
        SELECT id, building, bedrooms, price, size_sqft, area
        FROM listings
        WHERE is_active = TRUE
          AND building IS NOT NULL
          AND TRIM(building) <> ''
          AND (price IS NULL OR size_sqft IS NULL)
    """)
    cands = rcur.fetchall()
    log(f"candidate listings (need price OR size_sqft): {len(cands):,}")
    rcur.close()

    # Group needed (building_lower, br_label) pairs
    needed: set[tuple[str, str | None]] = set()
    for lid, bld, br, price, sqft, area in cands:
        bk = bld.strip().lower()
        label = br_label(br)
        needed.add((bk, label))
    log(f"unique (building, br) keys to lookup in DLD: {len(needed):,}")

    # ------------------------------------------------------------------
    # 2) Pre-fetch DLD benchmarks per (building, br) in one pass
    # ------------------------------------------------------------------
    log("loading DLD 180-day txns for needed buildings …")
    bldgs = sorted({bk for bk, _ in needed if bk})
    bench: dict[tuple[str, str | None], dict] = {}
    # Bench[(bk, label)] = {"price_med": float, "sqft_med": float, "n": int}
    if bldgs:
        dcur = dld.cursor()
        # Use ANY(%s) with lower(building) match
        dcur.execute("""
            SELECT LOWER(building_name_en) AS bk, rooms_en,
                   actual_worth, meter_sale_price
            FROM dld_sale_archive
            WHERE LOWER(building_name_en) = ANY(%s)
              AND instance_date::timestamp > NOW() - INTERVAL '180 days'
              AND actual_worth IS NOT NULL AND actual_worth <> ''
              AND meter_sale_price IS NOT NULL AND meter_sale_price <> ''
        """, (bldgs,))
        bucket: dict[tuple[str, str | None],
                     list[tuple[float, float]]] = defaultdict(list)
        for bk, rooms, aw, msp in dcur.fetchall():
            try:
                aw_f = float(aw)
                msp_f = float(msp)
            except (TypeError, ValueError):
                continue
            if aw_f <= 0 or msp_f <= 0:
                continue
            sqm = aw_f / msp_f
            sqft = sqm * SQM_TO_SQFT
            # Only sane sqft (avoid plot/land outliers)
            if not (100 <= sqft <= 20000):
                continue
            # Normalise rooms label (DLD: '1 B/R', 'Studio', etc.)
            label = (rooms or "").strip() or None
            bucket[(bk, label)].append((aw_f, sqft))
        dcur.close()
        for key, rows in bucket.items():
            if len(rows) < MIN_TXNS:
                continue
            prices = [r[0] for r in rows]
            sqfts = [r[1] for r in rows]
            bench[key] = {
                "price_med": statistics.median(prices),
                "sqft_med": statistics.median(sqfts),
                "n": len(rows),
            }
    log(f"  → {len(bench):,} (building,br) buckets have >= {MIN_TXNS} comps")

    # ------------------------------------------------------------------
    # 3) Compute per-listing fills
    # ------------------------------------------------------------------
    updates: list[tuple] = []
    stats = Counter()
    benefit_by_building: Counter = Counter()
    samples: list[str] = []

    for lid, bld, br, price, sqft, area in cands:
        bk = bld.strip().lower()
        label = br_label(br)
        b = bench.get((bk, label))
        if not b:
            stats["no_dld_match"] += 1
            continue
        derived: dict = {"source": f"dld_{WINDOW_DAYS}d", "n": b["n"]}
        new_price = price
        new_sqft = sqft
        if price is None:
            new_price = int(round(b["price_med"] * DISCOUNT))
            derived["price"] = True
            stats["filled_price"] += 1
        if sqft is None:
            new_sqft = round(b["sqft_med"], 1)
            derived["size_sqft"] = True
            stats["filled_sqft"] += 1
        # Skip if nothing to do (shouldn't happen due to WHERE)
        if "price" not in derived and "size_sqft" not in derived:
            stats["nothing_to_fill"] += 1
            continue
        updates.append((new_price, new_sqft, Json(derived), lid))
        benefit_by_building[bld.strip()] += 1
        if len(samples) < 15:
            samples.append(
                f"id={lid} bldg='{bld}' br={br} → "
                f"price={new_price} sqft={new_sqft} (n={b['n']})"
            )

    log(f"stats: {dict(stats)}")
    log(f"updates queued: {len(updates):,}")
    if samples:
        log("Sample fills:")
        for s in samples:
            log("  " + s)

    log("Top 10 buildings benefited:")
    for bldg, n in benefit_by_building.most_common(10):
        log(f"  {n:>4}  {bldg}")

    if DRY_RUN:
        log("DRY_RUN — no writes")
        return

    # ------------------------------------------------------------------
    # 4) Apply updates — never overwrite existing non-null values
    #    (safety: WHERE id=%s AND (price IS NULL OR size_sqft IS NULL))
    # ------------------------------------------------------------------
    if updates:
        wcur = rs.cursor()
        execute_batch(
            wcur,
            """
            UPDATE listings
            SET price = COALESCE(price, %s),
                size_sqft = COALESCE(size_sqft, %s),
                derived_from_dld = %s,
                updated_at = NOW()
            WHERE id = %s
              AND is_active = TRUE
              AND (price IS NULL OR size_sqft IS NULL)
            """,
            updates,
            page_size=300,
        )
        rs.commit()
        log(f"applied {len(updates):,} row updates")
    rs.close()
    dld.close()


if __name__ == "__main__":
    main()
