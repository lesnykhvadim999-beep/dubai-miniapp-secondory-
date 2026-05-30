"""v105 cleanup — применяет новый stoplist + emoji-strip к существующим записям.

Использует _is_building_stopword из обновлённого parser_engine, чтобы
обнулить building у уже сохранённых записей которые matched новые правила:
  - Marketing leak: Below OP, Iconic Views, Surrounded, Roots etc.
  - Emoji-spoofed: "Society House🏮🏮"
  - Currency-in-name: "850,000 AED"
  - Multi-cluster slurp: "Act One Act Two"
  - Unicode-spoofed: cyrillic/greek letters

После этого парсер при ре-парсинге получит NULL и попытается LLM extract.
"""
import os, sys, io
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
except Exception:
    pass

sys.path.insert(0, '.')
import psycopg2
from parser_engine import _is_building_stopword, _clean_building_candidate

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set"))
)

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()
cur.execute("""
    SELECT id, building
      FROM listings
     WHERE building IS NOT NULL
       AND length(building) > 0
       AND created_at > NOW() - INTERVAL '90 days'
""")
rows = cur.fetchall()
print(f"Checking {len(rows)} listings against v105 stoplist...")

flagged_ids = []
for lid, bld in rows:
    if not bld:
        continue
    # First strip emoji via _clean_building_candidate
    cleaned = _clean_building_candidate(bld)
    if cleaned is None or _is_building_stopword(cleaned or bld):
        flagged_ids.append(lid)

print(f"Flagged: {len(flagged_ids)}")

if flagged_ids:
    # Bulk null building + mark audit_flagged
    cur.execute("""
        UPDATE listings
           SET building = NULL,
               status = COALESCE(NULLIF(status, ''), 'audit_flagged'),
               audit_reason = COALESCE(audit_reason, 'v105: building matched updated stoplist'),
               audit_flagged_at = COALESCE(audit_flagged_at, NOW())
         WHERE id = ANY(%s)
    """, (flagged_ids,))
    print(f"Updated: {cur.rowcount}")

conn.commit()
cur.close()
conn.close()
print("Done.")
