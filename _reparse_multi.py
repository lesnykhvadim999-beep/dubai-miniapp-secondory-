"""Re-parse multi-listing posts with the new _first_listing_block logic.
For each suspect record (3+ sqft mentions in text), re-run extract_*
and update DB if values differ.
"""
import os
import sys, io, re, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from parser_engine import (
    extract_bedrooms, extract_size, extract_price, extract_view,
    extract_floor, extract_furnishing, extract_status, detect_building,
    detect_area, extract_property_type, _is_building_stopword,
)

DB = os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set"))
conn = psycopg2.connect(DB)
cur = conn.cursor()

cur.execute("""SELECT id, building, area, property_type, bedrooms, size_sqft, price,
                       deal_type, original_text
    FROM listings
    WHERE is_audit IS NOT TRUE AND original_text IS NOT NULL
      AND (length(lower(original_text)) - length(replace(lower(original_text), 'sqft', ''))) / 4 >= 3
""")
rows = cur.fetchall()
print(f'Processing {len(rows)} multi-listing records...', flush=True)

changed_br = changed_sz = changed_pr = 0
for lid, bld, area, ptype, br, sz, pr, dt, txt in rows:
    updates = {}
    # Re-extract bedrooms
    new_br = extract_bedrooms(txt)
    if new_br is not None and new_br != br:
        updates['bedrooms'] = new_br
        changed_br += 1
    # Re-extract size
    new_sz_obj = extract_size(txt)
    new_sz = new_sz_obj.get('size_sqft')
    if new_sz and float(sz or 0) > 0 and abs(float(sz) - float(new_sz)) > 50:
        updates['size_sqft'] = new_sz
        changed_sz += 1
    elif new_sz and not sz:
        updates['size_sqft'] = new_sz
        changed_sz += 1
    # Re-extract price
    new_pr_obj = extract_price(txt)
    new_pr = new_pr_obj.get('price')
    if new_pr and pr:
        try:
            ratio = float(new_pr) / float(pr)
            if ratio < 0.7 or ratio > 1.5:
                # Significant difference — prefer new (first-block) value
                updates['price'] = new_pr
                changed_pr += 1
        except: pass

    if updates:
        sets = ', '.join(f'{k}=%s' for k in updates)
        cur.execute(f"UPDATE listings SET {sets} WHERE id=%s",
                    list(updates.values()) + [lid])

print(f'bedrooms changed: {changed_br}', flush=True)
print(f'size_sqft changed: {changed_sz}', flush=True)
print(f'price changed: {changed_pr}', flush=True)
conn.commit()
conn.close()
