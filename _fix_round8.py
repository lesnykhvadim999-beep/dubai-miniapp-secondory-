"""Round 8: clean marketing/descriptor leaks into building field.
Patterns:
- "For Salle", "For Sale", "Brand New" → NULL
- "Spacious / Huge / Big / Prime / Premium" + noun → NULL
- "Huge Terrace", "Huge Balcony" → NULL
- "Covered" alone → NULL
- "JVC", "DXB", "DCH" — area-acronyms used as building → NULL
"""
import sys, io, re, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB)
cur = conn.cursor()

# Hard nullify these exact buildings (case-insensitive)
HARD_NULL = {
    'covered','jvc','dxb','dch','dha','dhe','rta','jbr','jlt','jvt',
    'for sale','for rent','for salle','brand new','new tower','prime tower',
    'huge terrace','huge balcony','huge rooftop terrace','huge rooftop',
    'big balcony','spacious layout','spacious twin villas','spacious','luxury',
    'for serious buyers','huge balcony with park','prime business bay',
    'big balcony & roof terrace','spacious villa','big terrace',
}

# Prefix-NULL: building starts with these
PREFIX_NULL_RE = re.compile(
    r'^(?:hot|new|for|ready|vacant|rented|distress|exclusive|prime|premium|'
    r'spacious|brand|big|huge|cheap|affordable|best|top|luxury|luxurious|'
    r'modern|elegant|cozy|bright|sunny|rare|unique|stunning|beautiful|'
    r'massive|charming|fully|partially|semi|upgraded|renovated)\s',
    re.I)

cur.execute("SELECT id, building FROM listings WHERE is_audit IS NOT TRUE AND building IS NOT NULL")
n = 0
for lid, bld in cur.fetchall():
    bl = bld.strip().lower()
    if bl in HARD_NULL or PREFIX_NULL_RE.match(bl):
        cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
        n += 1

print(f'Marketing/descriptor buildings nulled: {n}', flush=True)
conn.commit()

cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE"); t=cur.fetchone()[0]
cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE AND building IS NOT NULL"); b=cur.fetchone()[0]
print(f'\nVisible: {t}, bld: {b} ({100*b/t:.1f}%)', flush=True)
conn.close()
