"""Audit 1M-2M sales — re-extract with new parser."""
import sys, io, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, '.')
from parser_engine import extract_price, _is_building_stopword

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB); cur = conn.cursor()

# Step 1: month-name buildings → NULL
cur.execute("SELECT id, building FROM listings WHERE is_audit IS NOT TRUE AND building IS NOT NULL")
n_null = 0
months = {'january','february','march','april','may','june','july',
          'august','september','october','november','december'}
for lid, bld in cur.fetchall():
    if bld.strip().lower() in months:
        cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
        n_null += 1
print(f'Month-name buildings nulled: {n_null}', flush=True)

# Step 2: re-extract prices 1M-2M
cur.execute("""SELECT id, original_text, price FROM listings
    WHERE is_audit IS NOT TRUE AND deal_type='sale'
      AND price::numeric BETWEEN 800000 AND 2500000
      AND original_text IS NOT NULL""")
rows = cur.fetchall()
print(f"Processing {len(rows)} records in 0.8M-2.5M range...", flush=True)

n_pr = 0
for lid, txt, old_pr in rows:
    new = extract_price(txt)
    new_pr = new.get('price')
    if new_pr and old_pr:
        try:
            ratio = abs(float(new_pr) - float(old_pr)) / max(float(new_pr), float(old_pr))
            if ratio > 0.05:  # 5% diff threshold
                cur.execute("UPDATE listings SET price=%s WHERE id=%s", (new_pr, lid))
                n_pr += 1
        except: pass

print(f'prices updated: {n_pr}', flush=True)
conn.commit()
conn.close()
