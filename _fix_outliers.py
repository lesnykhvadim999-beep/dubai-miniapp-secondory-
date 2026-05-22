"""Force-NULL prices that parser now rejects as outliers (>500M for short text)."""
import sys, io, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, '.')
from parser_engine import extract_price

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB); cur = conn.cursor()

# Force-update all outliers (>100M apartments / shorts where parser rejects)
cur.execute("""SELECT id, original_text, price FROM listings
    WHERE is_audit IS NOT TRUE AND deal_type='sale'
      AND price::numeric > 100000000
      AND original_text IS NOT NULL""")
rows = cur.fetchall()
n_nulled = n_fixed = 0
for lid, txt, old_pr in rows:
    new = extract_price(txt)
    new_pr = new.get('price')
    if new_pr is None:
        cur.execute("UPDATE listings SET price=NULL WHERE id=%s", (lid,))
        n_nulled += 1
    elif new_pr and abs(float(new_pr) - float(old_pr)) / float(old_pr) > 0.1:
        cur.execute("UPDATE listings SET price=%s WHERE id=%s", (new_pr, lid))
        n_fixed += 1

print(f'>100M nulled: {n_nulled}, updated: {n_fixed}', flush=True)

# Same for hot deals — apartments with absurd PSF
cur.execute("""SELECT count(*) FROM listings
    WHERE is_audit IS NOT TRUE AND deal_type='sale' AND price::numeric > 500000000""")
print(f'Remaining >500M: {cur.fetchone()[0]}', flush=True)

conn.commit()
conn.close()
