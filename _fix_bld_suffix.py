"""Strip suffix junk from building names: ' Type', ' Property', ' (X)' etc.
Also fix 'Studio | X' prefix → X."""
import sys, io, re, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB); cur = conn.cursor()

cur.execute("SELECT id, building FROM listings WHERE is_audit IS NOT TRUE AND building IS NOT NULL")
n_strip = n_null = 0
for lid, bld in cur.fetchall():
    orig = bld
    cleaned = bld
    # Strip "Studio | " / "1 BR | " / "Apartment | " prefix
    cleaned = re.sub(r'^(?:studio|1\s*br|2\s*br|3\s*br|4\s*br|apartment|villa|townhouse|penthouse|duplex)\s*\|\s*', '', cleaned, flags=re.I)
    # Strip trailing " Type" / " Property" / " Unit"
    cleaned = re.sub(r'\s+(?:type|property|unit|apartment|villa|townhouse)\s*$', '', cleaned, flags=re.I)
    # Strip trailing parenthetical "(Damac Hills 2)" if it looks like area
    cleaned = re.sub(r'\s*\([^)]{3,30}\)\s*$', '', cleaned)
    # Strip leading "FOR SALE" / "FOR RENT" / emoji prefix
    cleaned = re.sub(r'^(?:for\s+sale|for\s+rent|distress\s+deal|hot\s+deal)\s*[:|\-]?\s*', '', cleaned, flags=re.I)
    cleaned = cleaned.strip(' -—–|*:,.')

    if cleaned != orig:
        if len(cleaned) < 3:
            cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
            n_null += 1
        else:
            cur.execute("UPDATE listings SET building=%s WHERE id=%s", (cleaned, lid))
            n_strip += 1

print(f'stripped: {n_strip}, nulled: {n_null}', flush=True)
conn.commit()
conn.close()
