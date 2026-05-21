"""Round 6: building cleanup based on observed garbage patterns.
Patterns nulled / fixed:
1. building = "year", "month" — keyword leak
2. building with " - PLOT" / " PLOT" suffix → strip
3. building "X Plot Area" / "X Bedroom" / starts with digit + word → NULL
4. building with leading "apartment in" / "studio in" / "villa in" → strip prefix
5. building with leading emoji/symbol char → strip
6. building with " - <KnownArea>" → keep only part before
"""
import sys, io, re, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB)
cur = conn.cursor()

# Hard-NULL words
HARD_NULL = {'year','month','day','week','price','sale','rent','studio',
             'apartment','villa','townhouse','plot','land','unit',
             '1 bedroom','2 bedroom','3 bedroom','4 bedroom','5 bedroom',
             'bedroom','available','vacant','furnished','unfurnished',
             'high floor','low floor','mid floor','top floor'}

cur.execute("SELECT id, building, area FROM listings WHERE is_audit IS NOT TRUE AND building IS NOT NULL")
nulled = 0; stripped = 0
for lid, bld, area in cur.fetchall():
    orig = bld
    bl = bld.strip().lower()
    # Hard NULL
    if bl in HARD_NULL:
        cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
        nulled += 1
        continue
    # Building starts with digit followed by "Bedroom"/"BR"/"Bed"
    if re.match(r'^\d+[\s\-]*(?:bedroom|bed|br|bdr|bhk)s?\b', bl):
        cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
        nulled += 1
        continue
    # Plot Area / Sqft Area phrases
    if re.search(r'\b(?:plot\s+area|sqft\s+area|bua|gfa)\b', bl):
        cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
        nulled += 1
        continue
    cleaned = bld
    # Strip leading "apartment in"/"studio in"/"villa in"/"townhouse in"/"penthouse in"
    cleaned = re.sub(r'^(?:apartment|studio|villa|townhouse|penthouse|duplex|office|flat|unit)\s+in\s+', '', cleaned, flags=re.I)
    # Strip leading emoji/symbol chars
    cleaned = re.sub(r'^[^\w\d]+', '', cleaned).strip()
    # Strip " - PLOT" / " PLOT" suffix
    cleaned = re.sub(r'\s*[-–—]\s*plot\s*$', '', cleaned, flags=re.I)
    cleaned = re.sub(r'\s+plot\s*$', '', cleaned, flags=re.I)
    # Strip trailing " - <Area>" if area equals the suffix
    if area:
        pat = re.compile(r'\s*[-–—]\s*' + re.escape(area) + r'\s*$', re.I)
        cleaned = pat.sub('', cleaned).strip()
    cleaned = cleaned.strip(' -—–|*')
    if cleaned and cleaned != orig:
        # If after stripping it's empty or too short, null
        if len(cleaned) < 3:
            cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
            nulled += 1
        else:
            cur.execute("UPDATE listings SET building=%s WHERE id=%s", (cleaned, lid))
            stripped += 1

print(f'Buildings nulled: {nulled}', flush=True)
print(f'Buildings cleaned: {stripped}', flush=True)
conn.commit()

cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE"); t=cur.fetchone()[0]
cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE AND building IS NOT NULL"); b=cur.fetchone()[0]
print(f'\nVisible: {t}, bld: {b} ({100*b/t:.1f}%)', flush=True)
conn.close()
