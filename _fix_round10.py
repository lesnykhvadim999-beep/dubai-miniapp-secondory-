"""Round 10: building garbage patterns from multi-listing audit.
- 'Two Villas' / 'Three Villas' / 'Multiple Units' → NULL
- 'X Area' / 'X area' suffix → strip ' Area'
- 'X Total Apartments' / 'Floors Total ...' → NULL
- 'Damac lagoon' (without sub-community) → NULL (use area instead)
"""
import sys, io, re, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB)
cur = conn.cursor()

HARD_NULL = {
    'two villas', 'three villas', 'multiple units', 'units for sale',
    'floors total apartments', 'total apartments', 'one unit',
    'damac lagoon', 'damac lagoons', 'damac hills', 'damac hills 2',
    'creek harbour', 'creek beach', 'sobha hartland', 'palm jumeirah',
    'dubai marina', 'business bay', 'downtown', 'jumeirah',
    'distress deal', 'hot deal', 'investor deal', 'flip sale',
}

cur.execute("SELECT id, building FROM listings WHERE is_audit IS NOT TRUE AND building IS NOT NULL")
n_null = n_strip = 0
for lid, bld in cur.fetchall():
    bl = bld.strip().lower()
    if bl in HARD_NULL:
        cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
        n_null += 1
        continue
    # Strip trailing " Area" / " area"
    if re.search(r'\s+area$', bl, re.I):
        new = re.sub(r'\s+area$', '', bld, flags=re.I).strip()
        if len(new) >= 3:
            cur.execute("UPDATE listings SET building=%s WHERE id=%s", (new, lid))
            n_strip += 1
        else:
            cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
            n_null += 1
        continue
    # Strip "X Total Apartments"
    if 'total apartments' in bl or 'total apt' in bl:
        cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
        n_null += 1

print(f'NULLed: {n_null}, stripped Area suffix: {n_strip}', flush=True)
conn.commit()
conn.close()
