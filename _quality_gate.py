"""Quality gate: NULL building → is_audit=TRUE.
Также: re-extract building+area для всех видимых через parse_message
+ применить DLD-normalize."""
import sys, io, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.path.insert(0, '.')
from parser_engine import parse_message, normalize_via_dld, _DLD_CANONICAL

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"
conn = psycopg2.connect(DB); cur = conn.cursor()

# Step 1: count what we have
cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE")
visible_before = cur.fetchone()[0]
cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE AND building IS NULL")
no_bld = cur.fetchone()[0]
print(f"Visible: {visible_before}, no building: {no_bld}", flush=True)

# Step 2: re-run DLD normalisation for all visible WITH building
# (overrides area from DLD canonical building→area mapping)
cur.execute("""SELECT id, building, area FROM listings
    WHERE is_audit IS NOT TRUE AND building IS NOT NULL""")
n_norm = 0
for lid, bld, area in cur.fetchall():
    canon_bld, canon_area = normalize_via_dld(bld, area)
    updates = {}
    if canon_bld and canon_bld != bld:
        updates['building'] = canon_bld
    if canon_area and canon_area != area:
        updates['area'] = canon_area
    if updates:
        sets = ', '.join(f'{k}=%s' for k in updates)
        cur.execute(f"UPDATE listings SET {sets} WHERE id=%s",
                    list(updates.values()) + [lid])
        n_norm += 1
print(f"DLD-normalized: {n_norm}", flush=True)

# Step 3: send NULL-building records to audit
cur.execute("""UPDATE listings SET is_audit=TRUE,
    review_reason = COALESCE(review_reason || '; no_building', 'no_building'),
    needs_manual_review = TRUE
    WHERE is_audit IS NOT TRUE AND building IS NULL""")
n_audit = cur.rowcount
print(f"Sent to audit (no building): {n_audit}", flush=True)

conn.commit()

cur.execute("SELECT count(*) FROM listings WHERE is_audit IS NOT TRUE"); v = cur.fetchone()[0]
cur.execute("SELECT count(*) FROM listings WHERE is_audit=TRUE"); a = cur.fetchone()[0]
print(f"\nFINAL: visible={v}, audit={a}", flush=True)
conn.close()
