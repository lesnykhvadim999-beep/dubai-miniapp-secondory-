"""Phase Y — backfill furnishing / view / floor_number / floor_category /
handover_year / handover_quarter / handover_text on all active listings.

Rule-based only (no LLM) — safe to re-run; idempotent.

Source text picked per row (first non-empty wins):
    v2_source_message_text > v2_source_block_text > original_text > description

After backfill: prints fill-rate stats per field.
"""
from __future__ import annotations
import os
import sys
import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parser_v2_extras import extract_extras  # noqa: E402

DSN = (
    "postgresql://postgres:REDACTED_DSN_PASSWORD"
    "@tramway.proxy.rlwy.net:23228/railway"
)

BATCH = 500


def pick_text(row: dict) -> str:
    for k in ("v2_source_message_text", "original_text", "description"):
        v = row.get(k)
        if v and isinstance(v, str) and len(v.strip()) > 10:
            return v
    return ""


def backfill() -> None:
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    upd = conn.cursor()

    # Use a server-side iterator-ish approach via OFFSET/LIMIT pages on id.
    cur.execute(
        """
        SELECT id, v2_source_message_text,
               original_text, description, furnishing, view, floor,
               floor_number, floor_category,
               handover_year, handover_quarter, handover_text, handover_date
        FROM listings
        WHERE is_active = true
        ORDER BY id
        """
    )

    processed = 0
    updated = 0
    while True:
        rows = cur.fetchmany(BATCH)
        if not rows:
            break
        for row in rows:
            processed += 1
            text = pick_text(row)
            if not text:
                continue
            extras = extract_extras(text)

            # Build updates: only fill columns that are currently NULL
            # (preserve any existing LLM-extracted value).
            sets = []
            params: list = []

            # Map: (extras_key, db_column, current_value)
            mapping = [
                ("furnishing",       "furnishing",       row.get("furnishing")),
                ("view",             "view",             row.get("view")),
                ("floor_number",     "floor_number",     row.get("floor_number")),
                ("floor_category",   "floor_category",   row.get("floor_category")),
                ("handover_year",    "handover_year",    row.get("handover_year")),
                ("handover_quarter", "handover_quarter", row.get("handover_quarter")),
                ("handover_text",    "handover_text",    row.get("handover_text")),
            ]
            for ek, col, cur_val in mapping:
                new_val = extras.get(ek)
                if new_val is not None and cur_val in (None, ""):
                    sets.append(f"{col} = %s")
                    params.append(new_val)

            # Also fill canonical `floor` (legacy column) if missing and we found a number
            if row.get("floor") in (None,) and extras.get("floor_number") is not None:
                sets.append("floor = %s"); params.append(extras["floor_number"])

            # And handover_date (DATE) if missing and we got a year — use end-of-quarter day
            if not row.get("handover_date") and extras.get("handover_year"):
                y = extras["handover_year"]; q = extras.get("handover_quarter")
                month, day = {1: ("03", "31"), 2: ("06", "30"),
                              3: ("09", "30"), 4: ("12", "31")}.get(q or 0, ("12", "31"))
                sets.append("handover_date = %s"); params.append(f"{y}-{month}-{day}")

            if not sets:
                continue
            params.append(row["id"])
            upd.execute(
                "UPDATE listings SET " + ", ".join(sets) + " WHERE id = %s",
                params,
            )
            updated += 1

        conn.commit()
        print(f"  processed {processed} / updated {updated}")

    cur.close()
    upd.close()
    conn.close()
    print(f"Done. processed={processed} updated={updated}")


def stats() -> None:
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*)                                                 AS total,
               COUNT(furnishing)                                        AS furnishing,
               COUNT(view)                                              AS view,
               COUNT(floor_number)                                      AS floor_number,
               COUNT(floor_category)                                    AS floor_category,
               COUNT(handover_year)                                     AS handover_year,
               COUNT(handover_quarter)                                  AS handover_quarter,
               COUNT(handover_text)                                     AS handover_text,
               COUNT(handover_date)                                     AS handover_date
        FROM listings
        WHERE is_active = true
        """
    )
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    d = dict(zip(cols, row))
    total = d["total"] or 1
    print()
    print("=== Fill-rate stats (active listings) ===")
    print(f"Total active:        {d['total']}")
    for k in ("furnishing", "view", "floor_number", "floor_category",
              "handover_year", "handover_quarter", "handover_text", "handover_date"):
        n = d[k]
        print(f"  {k:<18s} {n:>6d}  ({100.0 * n / total:5.1f}%)")
    cur.close()
    conn.close()


if __name__ == "__main__":
    backfill()
    stats()
