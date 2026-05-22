"""Revert audit updates that left garbage building values.
For records where new building is now flagged as stopword → NULL.
Also re-fill for records with B-update where new value is garbage.
"""
import sys, io, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from parser_engine import _is_building_stopword

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB)
cur = conn.cursor()

cur.execute("SELECT id, building FROM listings WHERE is_audit IS NOT TRUE AND building IS NOT NULL")
n = 0
for lid, bld in cur.fetchall():
    if _is_building_stopword(bld):
        cur.execute("UPDATE listings SET building=NULL WHERE id=%s", (lid,))
        n += 1

print(f'Reverted (set to NULL): {n}', flush=True)
conn.commit()

cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE"); t = cur.fetchone()[0]
cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE AND building IS NOT NULL"); b = cur.fetchone()[0]
print(f'Visible: {t}, bld: {b} ({100*b/t:.1f}%)', flush=True)
conn.close()
