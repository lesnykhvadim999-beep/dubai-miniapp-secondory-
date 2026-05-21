"""Re-parse building + area + property_type for multi-listing records."""
import sys, io, re, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from parser_engine import (
    parse_message, _first_listing_block, _is_building_stopword
)

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB)
cur = conn.cursor()

cur.execute("""SELECT id, building, area, property_type, original_text
    FROM listings
    WHERE is_audit IS NOT TRUE AND original_text IS NOT NULL
      AND (length(lower(original_text)) - length(replace(lower(original_text), 'sqft', ''))) / 4 >= 3
""")
rows = cur.fetchall()
print(f'Processing {len(rows)} multi-listing records for building+area...', flush=True)

c_bld = c_area = c_ptype = 0
for lid, bld, area, ptype, txt in rows:
    try:
        new = parse_message(txt)
    except Exception as e:
        continue
    updates = {}
    new_bld = (new.get('building') or '').strip() or None
    new_area = (new.get('area') or '').strip() or None
    new_ptype = new.get('property_type')

    # Replace building if first-block extraction gives a different one
    if new_bld and new_bld != bld:
        updates['building'] = new_bld
        c_bld += 1
    # Replace area if extraction differs
    if new_area and new_area != area:
        updates['area'] = new_area
        c_area += 1
    # Replace property_type if differs and new isn't apartment/None
    if new_ptype and new_ptype != ptype and new_ptype not in ('apartment',):
        updates['property_type'] = new_ptype
        c_ptype += 1

    if updates:
        sets = ', '.join(f'{k}=%s' for k in updates)
        cur.execute(f"UPDATE listings SET {sets} WHERE id=%s",
                    list(updates.values()) + [lid])

print(f'building updated: {c_bld}, area updated: {c_area}, ptype updated: {c_ptype}', flush=True)
conn.commit()
conn.close()
