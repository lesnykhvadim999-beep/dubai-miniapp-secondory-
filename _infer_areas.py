"""Run building→area inference on existing records to fix wrong/missing areas.
ВНИМАНИЕ: Groq делает HTTP request на каждое новое building (cached after).
Run на 200 records as initial — потом постепенно."""
import sys, io, os, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, '.')
# API keys must be set via env vars (avoid secret-scanning push rejection):
#   export GROQ_API_KEY=...
#   export DATABASE_URL=...
assert os.environ.get("GROQ_API_KEY"), "Set GROQ_API_KEY env var"
assert os.environ.get("DATABASE_URL"), "Set DATABASE_URL env var"

from parser_engine import infer_area_from_building, _DLD_CANONICAL, _DLD_TO_FRIENDLY

DB = os.environ["DATABASE_URL"]
conn = psycopg2.connect(DB); cur = conn.cursor()

# Step 1: records WITH building but NO area
cur.execute("""SELECT id, building FROM listings
    WHERE is_audit IS NOT TRUE AND building IS NOT NULL AND area IS NULL
    LIMIT 200""")
rows = cur.fetchall()
print(f"Filling NULL area for {len(rows)} records...", flush=True)
n_fill = 0
for lid, bld in rows:
    area = infer_area_from_building(bld)
    if area:
        cur.execute("UPDATE listings SET area=%s, area_confidence=0.85 WHERE id=%s",
                    (area, lid))
        n_fill += 1
print(f"area filled: {n_fill}", flush=True)

# Step 2: records where DLD building→area conflicts with stored area (clear cases)
cur.execute("""SELECT id, building, area FROM listings
    WHERE is_audit IS NOT TRUE AND building IS NOT NULL AND area IS NOT NULL
    LIMIT 1000""")
rows = cur.fetchall()
print(f"Cross-checking {len(rows)} records via DLD...", flush=True)
n_override = 0
for lid, bld, area in rows:
    bld_key = bld.strip().lower()
    dld_area = _DLD_CANONICAL.get("building_areas", {}).get(bld_key)
    if not dld_area:
        continue
    friendly = _DLD_TO_FRIENDLY.get(dld_area, dld_area)
    if friendly.strip().lower() != area.strip().lower():
        cur.execute("UPDATE listings SET area=%s, area_confidence=0.95 WHERE id=%s",
                    (friendly, lid))
        n_override += 1
print(f"DLD-override: {n_override}", flush=True)

conn.commit(); conn.close()
