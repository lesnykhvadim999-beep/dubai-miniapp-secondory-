# -*- coding: utf-8 -*-
"""
reparse_all.py — Re-parse every active listing with the current parser_engine.
Updates: is_off_plan, handover_date, extra_info, description, view, floor,
         unit_number, bathrooms, furnishing, status, currency.

Idempotent — safe to re-run.
"""
import sys, os, time, json
sys.stdout.reconfigure(encoding='utf-8')

os.environ.setdefault(
    "DATABASE_URL",
    "REDACTED_DSN_USE_DATABASE_URL_ENV"
)

import psycopg2
from psycopg2.extras import RealDictCursor

from parser_engine import (
    extract_offplan, extract_handover_date,
    extract_extra_info, extract_property_type,
    extract_view, extract_floor, extract_unit_number,
    extract_bathrooms, extract_furnishing, extract_status,
)

DB_URL = os.environ["DATABASE_URL"]

def main():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, original_text, property_type, bedrooms, view, floor,
               is_off_plan, handover_date, extra_info, status, furnishing
        FROM listings
        WHERE is_active = TRUE AND original_text IS NOT NULL
    """)
    rows = cur.fetchall()
    print(f"[reparse] Processing {len(rows)} records...", flush=True)

    updated = 0
    start = time.time()
    for i, r in enumerate(rows, 1):
        if i % 500 == 0:
            print(f"  [{i}/{len(rows)}] elapsed={time.time()-start:.0f}s updated={updated}",
                  flush=True)

        text = r["original_text"] or ""
        ptype = r["property_type"] or extract_property_type(text, r.get("bedrooms"))

        updates = {}
        # Always overwrite — current parser is the source of truth.
        new_offplan  = extract_offplan(text)
        new_handover = extract_handover_date(text)
        new_extra    = extract_extra_info(text, ptype) or None
        new_view     = extract_view(text)
        new_floor    = extract_floor(text)
        new_unit     = extract_unit_number(text)
        new_baths    = extract_bathrooms(text)
        new_furn     = extract_furnishing(text)
        new_status   = extract_status(text)

        if new_offplan != bool(r["is_off_plan"]):
            updates["is_off_plan"] = new_offplan
        if new_handover and str(r["handover_date"] or "") != str(new_handover):
            updates["handover_date"] = new_handover
        if new_extra and new_extra != (r["extra_info"] or {}):
            updates["extra_info"] = json.dumps(new_extra, ensure_ascii=False)
        if new_view and new_view != r["view"]:
            updates["view"] = new_view
        if new_floor and new_floor != r["floor"]:
            updates["floor"] = new_floor
        if new_unit:
            updates["unit_number"] = new_unit
        if new_baths:
            updates["bathrooms"] = new_baths
        if new_furn and new_furn != r["furnishing"]:
            updates["furnishing"] = new_furn
        if new_status and new_status != r["status"]:
            updates["status"] = new_status

        if updates:
            set_parts = ", ".join(f"{k} = %s" for k in updates)
            cur.execute(f"UPDATE listings SET {set_parts}, updated_at = NOW() WHERE id = %s",
                        list(updates.values()) + [r["id"]])
            updated += 1

        # Commit in batches of 500
        if i % 500 == 0:
            conn.commit()

    conn.commit()
    elapsed = time.time() - start
    print(f"\n[reparse] DONE: {updated}/{len(rows)} records updated in {elapsed:.0f}s")

    # Quick statistics on coverage
    cur.execute("SELECT COUNT(*) AS n FROM listings WHERE is_off_plan = TRUE")
    print(f"  off-plan:       {cur.fetchone()['n']}")
    cur.execute("SELECT COUNT(*) AS n FROM listings WHERE handover_date IS NOT NULL")
    print(f"  handover date:  {cur.fetchone()['n']}")
    cur.execute("SELECT COUNT(*) AS n FROM listings WHERE extra_info IS NOT NULL")
    print(f"  extra_info:     {cur.fetchone()['n']}")
    cur.execute("SELECT COUNT(*) AS n FROM listings WHERE view IS NOT NULL")
    print(f"  view:           {cur.fetchone()['n']}")
    cur.execute("SELECT COUNT(*) AS n FROM listings WHERE floor IS NOT NULL")
    print(f"  floor:          {cur.fetchone()['n']}")
    conn.close()


if __name__ == "__main__":
    main()
