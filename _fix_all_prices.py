"""Full price audit — re-extract ALL sale records under 5M and update where current
DB value differs >10% from new parser output."""
import os
import sys, io, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, '.')
from parser_engine import extract_price, detect_deal_type

DB = os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set"))
conn = psycopg2.connect(DB); cur = conn.cursor()

cur.execute("""SELECT id, original_text, price, deal_type, bedrooms
    FROM listings
    WHERE is_audit IS NOT TRUE AND price IS NOT NULL AND deal_type='sale'
      AND price::numeric < 5000000
      AND original_text IS NOT NULL""")
rows = cur.fetchall()
print(f"Processing {len(rows)} records...", flush=True)

n_pr = n_dt = 0
for lid, txt, old_pr, old_dt, br in rows:
    new = extract_price(txt)
    new_pr = new.get('price')
    new_dt = detect_deal_type(txt, new_pr, br)
    updates = {}
    if new_pr and old_pr:
        try:
            ratio = abs(float(new_pr) - float(old_pr)) / max(float(new_pr), float(old_pr))
            if ratio > 0.1:
                updates['price'] = new_pr
                n_pr += 1
        except: pass
    if new_dt and new_dt != old_dt:
        updates['deal_type'] = new_dt
        n_dt += 1
    if updates:
        sets = ', '.join(f'{k}=%s' for k in updates)
        cur.execute(f"UPDATE listings SET {sets} WHERE id=%s",
                    list(updates.values()) + [lid])

print(f'price updated: {n_pr}, deal_type updated: {n_dt}', flush=True)
conn.commit()
conn.close()
