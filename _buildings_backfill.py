# -*- coding: utf-8 -*-
"""
_buildings_backfill.py
======================
Backfill справочника `buildings` (resale-bot DB) из листингов + read-model.

Заполняет: name, area, emirate, developer (если можно вытащить из read-model
по building_stats.area_name+developer), aliases (пустой список).

Безопасный UPSERT: INSERT ... ON CONFLICT (name, area) DO UPDATE
SET emirate = EXCLUDED.emirate где было NULL, developer = COALESCE(...).

Запуск: python _buildings_backfill.py
"""
from __future__ import annotations

import os
import sys
import time

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("psycopg2 not installed")


RESALE_DSN = os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL")
ANALYTICS_DSN = os.environ.get("ANALYTICS_DATABASE_URL") or os.environ.get("READ_MODEL_DATABASE_URL")

if not RESALE_DSN:
    sys.exit("DATABASE_URL is not configured")


def main():
    t0 = time.time()
    print(f"[buildings_backfill] start", flush=True)

    # 1) Pull distinct (building, area) from listings
    rconn = psycopg2.connect(RESALE_DSN, application_name="buildings_backfill")
    rconn.autocommit = False
    with rconn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT building, area, emirate, COUNT(*) AS n
            FROM listings
            WHERE building IS NOT NULL AND building <> ''
              AND TRIM(building) <> ''
            GROUP BY building, area, emirate
            ORDER BY n DESC
        """)
        pairs = cur.fetchall()
    print(f"[buildings_backfill] distinct (building,area,emirate) from listings: {len(pairs)}", flush=True)

    # 2) Try enrich with developer from read-model (building_stats has area_name only;
    #    мы можем матчить через name = building_name_display ILIKE).
    enrich = {}
    if ANALYTICS_DSN:
        try:
            aconn = psycopg2.connect(ANALYTICS_DSN, application_name="buildings_backfill_rm",
                                      connect_timeout=10)
            aconn.autocommit = True
            # Берём только distinct buildings + area, чтобы не тянуть 159k строк.
            with aconn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT LOWER(building_name_display) AS bk,
                                    building_name_display, area_name
                    FROM building_stats
                    WHERE deals_count >= 1
                """)
                for bk, bname, area_name in cur.fetchall():
                    if bk and bk not in enrich:
                        enrich[bk] = {"display": bname, "area_name": area_name}
            aconn.close()
            print(f"[buildings_backfill] enrich map size: {len(enrich)}", flush=True)
        except Exception as e:
            print(f"[buildings_backfill] enrich skipped: {e}", flush=True)

    # 3) UPSERT into buildings
    inserted = 0
    updated = 0
    with rconn.cursor() as cur:
        for row in pairs:
            name = (row["building"] or "").strip()
            area = (row["area"] or "").strip() or None
            emirate = (row["emirate"] or "").strip() or "Dubai"
            if not name:
                continue
            developer = None
            # (we don't have developer in building_stats; skip — оставим NULL)
            try:
                cur.execute("""
                    INSERT INTO buildings (name, area, emirate, developer, aliases, created_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (name, area) DO UPDATE
                    SET emirate = COALESCE(buildings.emirate, EXCLUDED.emirate),
                        developer = COALESCE(buildings.developer, EXCLUDED.developer)
                    RETURNING (xmax = 0) AS inserted
                """, (name, area, emirate, developer, []))
                ins = cur.fetchone()[0]
                if ins:
                    inserted += 1
                else:
                    updated += 1
            except Exception as e:
                print(f"[buildings_backfill] ERR row {name!r}/{area!r}: {e}", flush=True)
                rconn.rollback()
                cur = rconn.cursor()
                continue
        rconn.commit()

    # 4) Final stats
    with rconn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM buildings")
        total = cur.fetchone()[0]
    rconn.close()

    dt = time.time() - t0
    print(f"[buildings_backfill] DONE in {dt:.1f}s   inserted={inserted} updated={updated}  total_buildings={total}", flush=True)


if __name__ == "__main__":
    main()
