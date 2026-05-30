"""_recompute_investment_scores.py — batch backfill of investment_score v2.

Run manually after v2 backfill is complete:
    py _recompute_investment_scores.py [--limit N] [--dry-run]

What it does:
    1. SELECT all is_active=TRUE listings.
    2. compute_score(listing, intel) → rating + score 0..100.
    3. UPDATE listings SET investment_score = <0..100 float>.
    4. Progress every 100 records.

The new score is 0..100 (legacy was 0..10). UI code in resale_bot.py is
updated to detect score>10 → render as v2 (with rating + breakdown).
"""
import os
import sys
import time
import psycopg2

from investment_score_v2 import compute_score


RESALE_URL = os.environ.get(
    "RESALE_DB_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set")),
)
INTEL_URL = os.environ.get(
    "INTEL_DB_URL",
    "postgresql://postgres:REDACTED_ARCHIVE_DB_PASSWORD"
    "@switchback.proxy.rlwy.net:23244/railway",
)


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    limit = None
    if "--limit" in args:
        idx = args.index("--limit")
        limit = int(args[idx + 1])

    print(f"[recompute] dry_run={dry_run} limit={limit}")

    resale = psycopg2.connect(RESALE_URL)
    intel  = psycopg2.connect(INTEL_URL)

    sql = (
        "SELECT id, price, area, building, bedrooms, property_type, "
        "deal_type, size_sqft, status, is_off_plan "
        "FROM listings WHERE is_active = TRUE "
        "ORDER BY id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"

    with resale.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    print(f"[recompute] {len(rows)} active listings to process")
    t0 = time.time()
    rating_counts = {}
    updated = 0

    with resale.cursor() as cur_u:
        for i, r in enumerate(rows, 1):
            listing = {
                "id": r[0], "price": r[1], "area": r[2], "building": r[3],
                "bedrooms": r[4], "property_type": r[5], "deal_type": r[6],
                "size_sqft": r[7], "status": r[8], "is_off_plan": r[9],
            }
            try:
                res = compute_score(listing, dld_conn=None, intel_conn=intel)
            except Exception as e:
                print(f"[recompute] id={r[0]} error: {e}")
                continue
            rating_counts[res["rating"]] = rating_counts.get(res["rating"], 0) + 1

            if not dry_run:
                cur_u.execute(
                    "UPDATE listings SET investment_score = %s WHERE id = %s",
                    (res["score"], r[0]),
                )
                updated += 1

            if i % 100 == 0:
                dt = time.time() - t0
                rate = i / dt if dt > 0 else 0
                print(f"[recompute] {i}/{len(rows)}  "
                      f"({rate:.1f} rec/s)  "
                      f"ratings={rating_counts}")
                if not dry_run:
                    resale.commit()

    if not dry_run:
        resale.commit()

    dt = time.time() - t0
    print(f"[recompute] DONE in {dt:.1f}s  updated={updated}  "
          f"rating_dist={rating_counts}")
    resale.close(); intel.close()


if __name__ == "__main__":
    main()
