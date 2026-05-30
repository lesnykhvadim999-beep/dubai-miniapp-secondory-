"""Round 7: reclassify apartment→townhouse/villa for community-villa contexts.

When text mentions a known townhouse/villa community (Damac Lagoons, Damac Hills 2,
Tilal Al Ghaf, The Valley, Arabian Ranches, etc.) AND has BUA/Plot dimensions
AND has 3+ BR → it's a townhouse, not apartment.

Also fix 1,5/2,5 bedroom that wrongly stored as 5.
"""
import os
import sys, io, re, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB = os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set"))
conn = psycopg2.connect(DB)
cur = conn.cursor()

# Communities that are predominantly townhouses/villas
VILLA_COMMUNITIES = [
    'damac lagoons', 'damac lagoon', 'damac hills', 'tilal al ghaf',
    'the valley', 'arabian ranches', 'mudon', 'reem', 'town square',
    'townsquare', 'serena', 'cherrywoods', 'mira', 'sun', 'noya',
    'yas acres', 'saadiyat reserve', 'al jurf', 'reem hills',
    'al furjan', 'maple', 'dubai south', 'expo city', 'bloom living',
    'damac islands', 'opal gardens', 'farm gardens', 'parkside',
    'parkside hills', 'la rosa', 'amaranta', 'ranches', 'lila',
    'spring', 'autumn', 'paradise hills', 'palmiera', 'haven',
    'al barari', 'dubai hills estate', 'maple at', 'aurora',
]

# 1. Reclassify apartment with villa-community + plot dimensions → townhouse
cur.execute("""SELECT id, original_text, bedrooms, size_sqft FROM listings
    WHERE is_audit IS NOT TRUE AND property_type='apartment'
      AND bedrooms >= 3 AND original_text IS NOT NULL""")
n1 = 0
for lid, txt, br, sz in cur.fetchall():
    tl = txt.lower()[:600]
    has_community = any(c in tl for c in VILLA_COMMUNITIES)
    has_plot_bua = bool(re.search(r'\bplot\s*[:\-]?\s*\d|\bbua\s*[:\-]?\s*\d', tl))
    if has_community and has_plot_bua:
        cur.execute("UPDATE listings SET property_type='townhouse' WHERE id=%s", (lid,))
        n1 += 1
print(f'apartment→townhouse reclassified: {n1}', flush=True)

# 2. Fix 1,5/2,5 bedroom (Russian half-bedroom)
cur.execute("""SELECT id, original_text, bedrooms FROM listings
    WHERE is_audit IS NOT TRUE AND bedrooms >= 5 AND original_text IS NOT NULL""")
n2 = 0
for lid, txt, br in cur.fetchall():
    m = re.search(r'(\d)[,.]5\s*(?:bedroom|bed\b|br\b|bdr\b|bhk)', txt.lower()[:600])
    if m:
        actual = int(m.group(1))
        if actual != br:
            cur.execute("UPDATE listings SET bedrooms=%s WHERE id=%s", (actual, lid))
            n2 += 1
print(f'1,5/2,5 bedroom fix: {n2}', flush=True)

conn.commit()
conn.close()
