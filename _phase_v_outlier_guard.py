#!/usr/bin/env python
"""
Phase V — Per-bucket price outlier guard.

For every (area, property_type, deal_type, bedrooms) bucket with >=10 listings:
  - compute median + MAD (median absolute deviation, robust)
  - flag any listing where |price - median| > 3 * 1.4826 * MAD
  - also flag if price > 5*median or price < 0.2*median (safety guards)
  - set needs_manual_review=TRUE, review_reason='price_outlier_3sigma'
  - ONLY if was NULL/FALSE before (do not overwrite existing flags)

Run with DRY_RUN=1 to preview, DRY_RUN=0 (or unset) to apply.
"""

import os
import sys
import statistics
from collections import defaultdict

import psycopg2

DB_URL = os.environ.get(
    "DATABASE_URL",
    "REDACTED_DSN_USE_DATABASE_URL_ENV",
)
DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"

MIN_BUCKET_SIZE = 10
MAD_K = 1.4826  # makes MAD a consistent estimator of stddev for normal dist
SIGMA_CUTOFF = 3.0
HARD_HIGH = 5.0  # >5x median
HARD_LOW = 0.2   # <0.2x median


def median(values):
    return statistics.median(values)


def mad(values, med):
    return statistics.median([abs(v - med) for v in values])


def main():
    print(f"=== Phase V: outlier guard ===  DRY_RUN={DRY_RUN}")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # Load every listing with all bucket keys + price + current flag
    cur.execute(
        """
        SELECT id, area, property_type, deal_type, bedrooms, price,
               needs_manual_review
          FROM listings
         WHERE price IS NOT NULL AND price > 0
           AND area IS NOT NULL
           AND property_type IS NOT NULL
           AND deal_type IS NOT NULL
           AND bedrooms IS NOT NULL
        """
    )
    rows = cur.fetchall()
    print(f"loaded {len(rows)} priced listings")

    # group by bucket
    buckets = defaultdict(list)
    for r in rows:
        lid, area, pt, deal, br, price, flagged = r
        buckets[(area, pt, deal, br)].append((lid, price, flagged))

    eligible_buckets = {k: v for k, v in buckets.items() if len(v) >= MIN_BUCKET_SIZE}
    print(f"buckets total: {len(buckets)}, eligible (>={MIN_BUCKET_SIZE}): {len(eligible_buckets)}")

    # compute stats + flag candidates
    to_flag = []  # list of (id, area, pt, deal, br, price, med, ratio)
    per_bucket_flagged = []  # (bucket, n_total, n_flagged, median)

    for bucket, items in eligible_buckets.items():
        prices = [p for (_, p, _) in items]
        med = median(prices)
        if med <= 0:
            continue
        m = mad(prices, med)
        # cutoff in absolute price units
        sigma_cut = SIGMA_CUTOFF * MAD_K * m

        n_flagged_in_bucket = 0
        for lid, price, already in items:
            deviation = abs(price - med)
            is_sigma_outlier = (m > 0) and (deviation > sigma_cut)
            is_hard_high = price > HARD_HIGH * med
            is_hard_low = price < HARD_LOW * med
            if not (is_sigma_outlier or is_hard_high or is_hard_low):
                continue
            if already:  # already TRUE — do not overwrite
                continue
            ratio = price / med if med else 0
            area, pt, deal, br = bucket
            to_flag.append((lid, area, pt, deal, br, price, med, ratio))
            n_flagged_in_bucket += 1

        if n_flagged_in_bucket:
            per_bucket_flagged.append((bucket, len(items), n_flagged_in_bucket, med))

    print(f"\nlistings to flag as outlier: {len(to_flag)}")
    print(f"buckets with at least 1 outlier: {len(per_bucket_flagged)}")

    # distribution per bucket (top 20 by flagged count)
    per_bucket_flagged.sort(key=lambda x: -x[2])
    print("\n=== Top 20 buckets by # of outliers ===")
    print(f"{'area':30s} {'pt':10s} {'deal':6s} {'br':3s} {'n':>5s} {'flag':>5s} {'median':>12s}")
    for bucket, n_total, n_flag, med in per_bucket_flagged[:20]:
        area, pt, deal, br = bucket
        print(f"{(area or '')[:30]:30s} {(pt or '')[:10]:10s} {(deal or '')[:6]:6s} "
              f"{br:>3d} {n_total:>5d} {n_flag:>5d} {med:>12.0f}")

    # top 10 outlier examples (by ratio distance from 1.0)
    to_flag_sorted = sorted(to_flag, key=lambda x: abs(x[7] - 1), reverse=True)
    print("\n=== Top 10 outlier examples (by |ratio - 1|) ===")
    # try to fetch building names for nicer report
    ids_top = [t[0] for t in to_flag_sorted[:10]]
    bmap = {}
    if ids_top:
        cur.execute(
            "SELECT id, COALESCE(building, '') FROM listings WHERE id = ANY(%s)",
            (ids_top,),
        )
        for lid, bld in cur.fetchall():
            bmap[lid] = bld
    print(f"{'id':>8s} {'area':25s} {'building':28s} {'price':>12s} {'median':>12s} {'ratio':>7s}")
    for lid, area, pt, deal, br, price, med, ratio in to_flag_sorted[:10]:
        print(f"{lid:>8d} {(area or '')[:25]:25s} {(bmap.get(lid, '') or '')[:28]:28s} "
              f"{price:>12.0f} {med:>12.0f} {ratio:>7.2f}")

    if DRY_RUN:
        print("\nDRY_RUN=1 — no DB changes made.")
        conn.close()
        return

    # APPLY: only flip listings that are currently NOT flagged (extra guard in SQL)
    ids = [t[0] for t in to_flag]
    if not ids:
        print("\nnothing to apply.")
        conn.close()
        return

    cur.execute(
        """
        UPDATE listings
           SET needs_manual_review = TRUE,
               review_reason = 'price_outlier_3sigma'
         WHERE id = ANY(%s)
           AND (needs_manual_review IS NULL OR needs_manual_review = FALSE)
        """,
        (ids,),
    )
    updated = cur.rowcount
    conn.commit()
    print(f"\nAPPLIED: updated {updated} rows (set needs_manual_review=TRUE, review_reason='price_outlier_3sigma')")
    conn.close()


if __name__ == "__main__":
    main()
