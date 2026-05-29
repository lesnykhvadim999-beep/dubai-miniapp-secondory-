import os
import psycopg2, json

DLD_URL = os.environ.get("DLD_DB_URL") or os.environ.get("ANALYTICS_DATABASE_URL")
if not DLD_URL:
    raise RuntimeError("DLD_DB_URL or ANALYTICS_DATABASE_URL required")
conn = psycopg2.connect(DLD_URL)
benchmarks = {}

with conn.cursor() as c:
    print('Loading sale prices...')
    c.execute("""
        SELECT building_name_en, area_name_en,
               AVG(actual_worth::numeric), PERCENTILE_CONT(0.5) WITHIN GROUP(ORDER BY actual_worth::numeric),
               MIN(actual_worth::numeric), MAX(actual_worth::numeric), COUNT(*)
        FROM dld_sales_unified
        WHERE building_name_en != '' AND building_name_en IS NOT NULL
        AND actual_worth::numeric > 50000
        GROUP BY building_name_en, area_name_en HAVING COUNT(*) >= 3
    """)
    for row in c.fetchall():
        bname, area, avg, median, mn, mx, cnt = row
        key = bname.strip()
        if key not in benchmarks:
            benchmarks[key] = {"area": area}
        benchmarks[key].update({"sale_avg": round(float(avg)), "sale_median": round(float(median)), "sale_min": round(float(mn)), "sale_max": round(float(mx)), "sale_count": int(cnt)})
    print(f'Sale: {len(benchmarks)} buildings')

    print('Loading rent prices...')
    c.execute("""
        SELECT project, area,
               AVG(annual_amount), PERCENTILE_CONT(0.5) WITHIN GROUP(ORDER BY annual_amount),
               MIN(annual_amount), MAX(annual_amount), COUNT(*)
        FROM rent_residential_365d
        WHERE project IS NOT NULL AND project != ''
        AND annual_amount > 5000
        GROUP BY project, area HAVING COUNT(*) >= 3
    """)
    rent_cnt = 0
    for row in c.fetchall():
        bname, area, avg, median, mn, mx, cnt = row
        key = bname.strip()
        if key not in benchmarks:
            benchmarks[key] = {"area": area}
        benchmarks[key].update({"rent_avg_yearly": round(float(avg)), "rent_median_yearly": round(float(median)), "rent_min_yearly": round(float(mn)), "rent_max_yearly": round(float(mx)), "rent_count": int(cnt)})
        rent_cnt += 1
    print(f'Rent: {rent_cnt} buildings')

conn.close()

with open('price_benchmarks.json', 'w', encoding='utf-8') as f:
    json.dump(benchmarks, f, ensure_ascii=False, indent=2)

both = sum(1 for v in benchmarks.values() if 'sale_avg' in v and 'rent_avg_yearly' in v)
print(f'Total: {len(benchmarks)}, Both sale+rent: {both}')

# Покажем пример
sample = {k: v for k, v in list(benchmarks.items())[:2]}
print(json.dumps(sample, indent=2))
