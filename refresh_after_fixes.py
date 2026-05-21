# -*- coding: utf-8 -*-
"""Refresh price/deal_type/building/property_type for all active records
after fixes to: AED stripping, multi-listing separators, emoji stripping,
sale-keyword guard, rent>1M validation."""
import sys, time
sys.stdout.reconfigure(encoding='utf-8')
import psycopg2
from parser_engine import (
    extract_price, extract_bedrooms, extract_property_type,
    detect_building, validate_deal_type_by_price, compute_deal_quality,
)

conn = psycopg2.connect('REDACTED_DSN_USE_DATABASE_URL_ENV')
cur = conn.cursor()
cur.execute("""
    SELECT id, original_text, deal_type, property_type, price, original_price,
           bedrooms, building, area, emirate, size_sqft
    FROM listings WHERE is_active=TRUE AND original_text IS NOT NULL
""")
rows = cur.fetchall()
print(f"refreshing {len(rows)} records", flush=True)

stats = {'price': 0, 'deal_type': 0, 'building': 0, 'property_type': 0, 'bedrooms': 0}
start = time.time()

for i, (rid, t, dt, pt, p, op, br, bld, area, emir, sqft) in enumerate(rows):
    sets = {}

    new_pr = extract_price(t)
    np = new_pr.get('price')
    nop = new_pr.get('original_price')
    if np != p:
        sets['price'] = np; stats['price'] += 1
    if nop != op and nop:
        sets['original_price'] = nop

    new_br = extract_bedrooms(t)
    if new_br != br:
        sets['bedrooms'] = new_br; stats['bedrooms'] += 1

    new_pt = extract_property_type(t, new_br if new_br is not None else br)
    if new_pt != pt:
        sets['property_type'] = new_pt; stats['property_type'] += 1

    if not bld or len(bld) < 4:
        nb, _, _, _, _ = detect_building(t)
        if nb and nb != bld:
            sets['building'] = nb; stats['building'] += 1
            bld = nb

    final_price = sets.get('price', p) or 0
    final_br = sets.get('bedrooms', br)
    new_dt = validate_deal_type_by_price(final_price, dt, bedrooms=final_br, area=area, text=t, building=bld)
    if new_dt != dt:
        sets['deal_type'] = new_dt; stats['deal_type'] += 1

    if sets:
        da = compute_deal_quality(final_price, sets.get('original_price', op),
                                  sqft, area, final_br, building=bld, deal_type=new_dt)
        if da:
            sets['is_hot_deal'] = da.get('is_hot_deal')
            sets['deal_quality'] = da.get('deal_quality')
            sets['price_vs_market_percent'] = da.get('price_vs_market_percent')
            sets['investment_score'] = da.get('investment_score')
        clause = ', '.join(f'{k}=%s' for k in sets)
        cur.execute(f"UPDATE listings SET {clause} WHERE id=%s", list(sets.values()) + [rid])

    if (i + 1) % 500 == 0:
        conn.commit()
        print(f"  {i+1}/{len(rows)} ({time.time()-start:.0f}s)", flush=True)

conn.commit()
print(f"\n[done] {time.time()-start:.0f}s")
print("Stats:")
for k, v in stats.items():
    print(f"  {k:20s} {v}")
conn.close()
