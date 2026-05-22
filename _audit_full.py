"""Full DB audit — re-extract prices for all sales >2M, plus null junk buildings."""
import sys, io, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, '.')
from parser_engine import extract_price, _is_building_stopword

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB); cur = conn.cursor()

# Step 1: null junk buildings via stopword
cur.execute("SELECT id, building FROM listings WHERE is_audit IS NOT TRUE AND building IS NOT NULL")
n_null = 0
for lid, bld in cur.fetchall():
    if _is_building_stopword(bld):
        cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
        n_null += 1
print(f'Junk buildings nulled: {n_null}', flush=True)

# Step 2: re-extract prices for ALL sale records
cur.execute("""SELECT id, original_text, price FROM listings
    WHERE is_audit IS NOT TRUE AND deal_type='sale'
      AND price IS NOT NULL AND original_text IS NOT NULL""")
rows = cur.fetchall()
print(f"Processing {len(rows)} sale records (full range)...", flush=True)

n_pr = 0
for lid, txt, old_pr in rows:
    new = extract_price(txt)
    new_pr = new.get('price')
    if new_pr and old_pr:
        try:
            ratio = abs(float(new_pr) - float(old_pr)) / max(float(new_pr), float(old_pr))
            if ratio > 0.05:
                cur.execute("UPDATE listings SET price=%s WHERE id=%s", (new_pr, lid))
                n_pr += 1
        except: pass

print(f'prices updated: {n_pr}', flush=True)
conn.commit()
conn.close()
