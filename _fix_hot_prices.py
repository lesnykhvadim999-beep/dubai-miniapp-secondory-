"""Re-extract price + deal_type for all hot deals and any record with suspicious price."""
import sys, io, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, '.')
from parser_engine import extract_price, detect_deal_type

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB)
cur = conn.cursor()

# Все hot deals + suspect: 4BR/5BR villas с price <800K, 1BR Dubai с price <300K, etc
cur.execute("""SELECT id, original_text, price, deal_type, building, area, bedrooms
    FROM listings
    WHERE is_audit IS NOT TRUE
      AND original_text IS NOT NULL
      AND (
        is_hot_deal = TRUE OR
        (price IS NOT NULL AND deal_type='sale' AND price::numeric < 500000) OR
        (price IS NOT NULL AND deal_type='sale' AND
         original_text ~* '\$\s*[\d.,]+\s*[kK]')
      )""")
rows = cur.fetchall()
print(f"Processing {len(rows)} records...", flush=True)

n_pr = n_dt = 0
for lid, txt, old_pr, old_dt, bld, area, br in rows:
    new_pr_obj = extract_price(txt)
    new_pr = new_pr_obj.get('price')
    new_dt = detect_deal_type(txt, new_pr, br)
    updates = {}
    if new_pr and old_pr and float(new_pr) != float(old_pr):
        if abs(float(new_pr) - float(old_pr)) / max(float(new_pr), float(old_pr)) > 0.1:
            updates['price'] = new_pr
            n_pr += 1
    if new_dt and new_dt != old_dt:
        updates['deal_type'] = new_dt
        n_dt += 1
    if updates:
        sets = ', '.join(f'{k}=%s' for k in updates)
        cur.execute(f"UPDATE listings SET {sets} WHERE id=%s",
                    list(updates.values()) + [lid])

print(f'Updated: price={n_pr}, deal_type={n_dt}', flush=True)
conn.commit()
conn.close()
