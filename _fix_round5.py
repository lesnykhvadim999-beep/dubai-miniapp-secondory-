"""Round 5 bug fixes:
1. Plot/whole_building → bedrooms=NULL
2. Apartment with first-block 'Office N sqft' → property_type=office
3. Apartment with 'PRIME RETAIL' / retail context → property_type=retail
4. 1-bed / 1bd / 1bdr patterns → bedrooms autofill
"""
import sys, io, re, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB)
cur = conn.cursor()

# Fix 1: plot/whole_building bedrooms NULL
cur.execute("""UPDATE listings SET bedrooms = NULL
    WHERE is_audit IS NOT TRUE AND property_type IN ('plot','whole_building')
      AND bedrooms IS NOT NULL""")
n1 = cur.rowcount
print(f'plot/whole_building bedrooms nulled: {n1}', flush=True)

# Fix 2: apartment with first-block "Office N sqft" → office
cur.execute("""SELECT id, original_text FROM listings
    WHERE is_audit IS NOT TRUE AND property_type='apartment'
      AND original_text ~* 'office\s*[-–—:]\s*[\d\.,]+\s*sq'
""")
n2 = 0
for lid, txt in cur.fetchall():
    head = re.split(r'\n\s*\n\s*\n\s*\n|\n\s*[-—–=━]{3,}\s*\n', txt[:600], maxsplit=1)[0].lower()
    if re.search(r'office\s*[-–—:]\s*[\d\.,]+\s*sq', head):
        cur.execute("UPDATE listings SET property_type='office', bedrooms=NULL WHERE id=%s", (lid,))
        n2 += 1
print(f'reclassified apartment→office: {n2}', flush=True)

# Fix 3: apartment with retail context → retail
cur.execute("""SELECT id, original_text FROM listings
    WHERE is_audit IS NOT TRUE AND property_type='apartment'
      AND original_text ~* '\b(?:prime|premium|hot)\s+retail\b|\bretail\s+(?:unit|space|opportunity)\b'
""")
n3 = 0
for lid, txt in cur.fetchall():
    head = re.split(r'\n\s*\n\s*\n\s*\n|\n\s*[-—–=━]{3,}\s*\n', txt[:600], maxsplit=1)[0].lower()
    if re.search(r'\b(?:prime|premium|hot)\s+retail\b|\bretail\s+(?:unit|space|opportunity)\b', head):
        cur.execute("UPDATE listings SET property_type='retail', bedrooms=NULL WHERE id=%s", (lid,))
        n3 += 1
print(f'reclassified apartment→retail: {n3}', flush=True)

# Fix 4: extract bedrooms for "1-bed", "1bd", "1bdr"
cur.execute("""SELECT id, original_text FROM listings
    WHERE is_audit IS NOT TRUE AND bedrooms IS NULL
      AND property_type IN ('apartment','penthouse','villa','townhouse','duplex','studio','hotel_apartment','serviced_apartment')
      AND original_text IS NOT NULL""")
n4 = 0
for lid, txt in cur.fetchall():
    head = re.split(r'\n\s*\n\s*\n\s*\n|\n\s*[-—–=━]{3,}\s*\n', txt[:600], maxsplit=1)[0].lower()
    # Pattern: "1-bed", "1bd", "2-bedroom"
    m = re.search(r'(?<![\d\.])(\d+)[ \t\-]*(?:bedrooms?|bdrs?|brs?|beds?|bds?)\b', head)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 15:
            cur.execute("UPDATE listings SET bedrooms=%s WHERE id=%s", (v, lid))
            n4 += 1
print(f'bedrooms autofilled (1-bed/1bd patterns): {n4}', flush=True)

conn.commit()

cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE"); t = cur.fetchone()[0]
cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE AND bedrooms IS NOT NULL"); b = cur.fetchone()[0]
print(f'\nVisible: {t}, bedrooms: {b} ({100*b/t:.1f}%)', flush=True)
conn.close()
