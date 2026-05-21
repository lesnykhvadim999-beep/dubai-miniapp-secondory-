"""Round 9: building↔area consistency fixes.
- Building has '\n' → take first line
- Building has prefix "Below Op" / "Distress" / "Op" → strip
- Sobha Creek Vistas/Creek Vista Heights → area=Sobha Hartland
- Marina Residences on Palm — keep (real building)
- Building matches text first-line only — drop bleed
"""
import sys, io, re, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB)
cur = conn.cursor()

# Fix 1: building has '\n' → first line
cur.execute("SELECT id, building FROM listings WHERE is_audit IS NOT TRUE AND building LIKE %s", ('%\n%',))
n1 = 0
for lid, bld in cur.fetchall():
    first = bld.split('\n')[0].strip()
    if first and 3 <= len(first) <= 60:
        cur.execute("UPDATE listings SET building=%s WHERE id=%s", (first, lid))
        n1 += 1
print(f'newline-in-building fixed: {n1}', flush=True)

# Fix 2: strip leading "Below Op" / "Op" / "DLD" / marketing prefixes
STRIP_PREFIX = re.compile(
    r'^(?:below\s+op|op\s+|original\s+price|sale\s+price|distress|urgent|hot|'
    r'best|new|exclusive|prime|premium|brand\s+new)\s+', re.I)
cur.execute("SELECT id, building FROM listings WHERE is_audit IS NOT TRUE AND building IS NOT NULL")
n2 = 0
for lid, bld in cur.fetchall():
    cleaned = STRIP_PREFIX.sub('', bld).strip()
    if cleaned != bld:
        if len(cleaned) < 3:
            cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
        else:
            cur.execute("UPDATE listings SET building=%s WHERE id=%s", (cleaned, lid))
        n2 += 1
print(f'prefix-stripped buildings: {n2}', flush=True)

# Fix 3: known Sobha buildings → area Sobha Hartland
SOBHA_BUILDINGS = [
    'sobha creek vistas', 'sobha creek vista', 'creek vista heights',
    'creek vistas heights', 'sobha hartland',
    '310 riverside crescent', '320 riverside crescent', '330 riverside crescent',
    '340 riverside crescent', '350 riverside crescent', '360 riverside crescent',
]
cur.execute("""SELECT id, building, area FROM listings
    WHERE is_audit IS NOT TRUE AND building IS NOT NULL""")
n3 = 0
for lid, bld, area in cur.fetchall():
    bl = bld.lower()
    if any(sb in bl for sb in SOBHA_BUILDINGS):
        if area != 'Sobha Hartland':
            cur.execute("UPDATE listings SET area=%s WHERE id=%s", ('Sobha Hartland', lid))
            n3 += 1
print(f'Sobha area fixed: {n3}', flush=True)

# Fix 4: Damac Lagoons buildings → area Damac Lagoons
LAGOONS_BUILDINGS = ['santorini','monte carlo','marabella','ibiza','mykonos',
                       'venice','nice','portofino','marbella','morocco','malta']
cur.execute("""SELECT id, building, area, original_text FROM listings
    WHERE is_audit IS NOT TRUE AND building IS NOT NULL""")
n4 = 0
for lid, bld, area, txt in cur.fetchall():
    if not txt: continue
    bl = bld.lower()
    tl = txt.lower()[:400]
    if any(lb in bl for lb in LAGOONS_BUILDINGS) and 'damac lagoon' in tl:
        if area != 'DAMAC Lagoons':
            cur.execute("UPDATE listings SET area=%s WHERE id=%s", ('DAMAC Lagoons', lid))
            n4 += 1
print(f'Damac Lagoons area fixed: {n4}', flush=True)

conn.commit()

cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE"); t=cur.fetchone()[0]
cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE AND building IS NOT NULL"); b=cur.fetchone()[0]
cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE AND area IS NOT NULL"); a=cur.fetchone()[0]
print(f'\nVisible: {t}, bld: {b} ({100*b/t:.1f}%), area: {a} ({100*a/t:.1f}%)', flush=True)
conn.close()
