"""Fix all records where reference number was picked as price.
Re-run extract_price with the fixed parser.
"""
import sys, io, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from parser_engine import extract_price

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB)
cur = conn.cursor()

# Find suspected reference-as-price records: huge prices in records with ref number
cur.execute("""SELECT id, original_text, price FROM listings
    WHERE is_audit IS NOT TRUE
      AND price::numeric BETWEEN 10000000 AND 100000000
      AND original_text ~* 'reference\s+no|ref\.?\s*no|permit\s+no|rera|dld\s+permit'""")
rows = cur.fetchall()
print(f"Suspected: {len(rows)}", flush=True)

n_fixed = n_nulled = 0
for lid, txt, old_pr in rows:
    new = extract_price(txt)
    new_pr = new.get('price')
    if new_pr and new_pr != float(old_pr):
        cur.execute("UPDATE listings SET price=%s WHERE id=%s", (new_pr, lid))
        n_fixed += 1
    elif not new_pr:
        cur.execute("UPDATE listings SET price=NULL WHERE id=%s", (lid,))
        n_nulled += 1

print(f"Fixed (new price): {n_fixed}, Nulled (no price found): {n_nulled}", flush=True)
conn.commit()

# Also check 7-digit prices that look like reference numbers
cur.execute("""SELECT id, original_text, price FROM listings
    WHERE is_audit IS NOT TRUE
      AND price::numeric BETWEEN 1000000 AND 99999999
      AND price::numeric > 5000000
      AND original_text ~* '#\s*\d{6,10}'""")
rows2 = cur.fetchall()
print(f"\nAlso checking {len(rows2)} records with #-ref tokens...", flush=True)
n_fix2 = 0
for lid, txt, old_pr in rows2:
    # Only fix if old price equals a #-hash number in text
    import re
    hashes = re.findall(r'#\s*(\d{6,10})', txt)
    for h in hashes:
        if int(h) == int(float(old_pr)):
            new = extract_price(txt)
            new_pr = new.get('price')
            if new_pr != float(old_pr):
                if new_pr:
                    cur.execute("UPDATE listings SET price=%s WHERE id=%s", (new_pr, lid))
                else:
                    cur.execute("UPDATE listings SET price=NULL WHERE id=%s", (lid,))
                n_fix2 += 1
            break
print(f"hash-ref fix: {n_fix2}", flush=True)
conn.commit()
conn.close()
