"""Re-parse multi-listing records: building, area, ptype, br, sz, pr.
Apply parse_message to first-block. Update DB where different.
"""
import sys, io, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from parser_engine import parse_message

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB)
cur = conn.cursor()

cur.execute("""SELECT id, building, area, property_type, bedrooms, size_sqft, price,
                       original_text
    FROM listings
    WHERE is_audit IS NOT TRUE AND original_text IS NOT NULL
      AND (length(lower(original_text)) - length(replace(lower(original_text), 'sqft', ''))) / 4 >= 3
""")
rows = cur.fetchall()
print(f'Processing {len(rows)} multi-listing records...', flush=True)

c_bld = c_area = c_ptype = c_br = c_sz = c_pr = 0
for lid, bld, area, ptype, br, sz, pr, txt in rows:
    try:
        new = parse_message(txt, lid, None, 0)
    except Exception:
        continue
    if not new:
        continue

    updates = {}
    new_bld = (new.get('building') or '').strip() or None
    new_area = (new.get('area') or '').strip() or None
    new_ptype = new.get('property_type')
    new_br = new.get('bedrooms')
    new_sz = new.get('size_sqft')
    new_pr = new.get('price')

    # Building: take new if it differs AND not a landmark issue (Burj Khalifa is a special area)
    if new_bld and new_bld != bld and new_bld.lower() not in ('burj khalifa', 'palm jumeirah', 'business bay'):
        updates['building'] = new_bld
        c_bld += 1
    elif bld and not new_bld:
        # Parser now returns None — drop the stale value (likely multi-listing leak)
        updates['building'] = None
        c_bld += 1

    if new_area and new_area != area and new_area.lower() not in ('burj khalifa',):
        updates['area'] = new_area
        c_area += 1

    if new_ptype and new_ptype != ptype:
        updates['property_type'] = new_ptype
        c_ptype += 1

    if new_br is not None and new_br != br:
        updates['bedrooms'] = new_br
        c_br += 1

    if new_sz and float(new_sz) > 0:
        if not sz or abs(float(sz) - float(new_sz)) > 50:
            updates['size_sqft'] = new_sz
            c_sz += 1

    if new_pr and pr:
        try:
            r = float(new_pr) / float(pr)
            if r < 0.7 or r > 1.5:
                updates['price'] = new_pr
                c_pr += 1
        except: pass
    elif new_pr and not pr:
        updates['price'] = new_pr
        c_pr += 1

    if updates:
        sets = ', '.join(f'{k}=%s' for k in updates)
        cur.execute(f"UPDATE listings SET {sets} WHERE id=%s",
                    list(updates.values()) + [lid])

print(f'bld:{c_bld} area:{c_area} ptype:{c_ptype} br:{c_br} sz:{c_sz} pr:{c_pr}', flush=True)
conn.commit()
conn.close()
