"""Regression tests for parser_engine v133 refactor.

Covers:
  • detect_deal_type — hierarchy (sale > rent > default) with rental-yield trap.
  • detect_rent_period — yearly / monthly / daily / unknown.
  • extract_property_type — studio / villa / townhouse / penthouse / duplex /
    office-tower-as-apartment / commercial.

Goal: 30 texts (10 sale + 10 rent + 10 mixed/edge). All must pass.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser_engine import (
    detect_deal_type,
    detect_rent_period,
    extract_property_type,
    detect_deal_type_v133,
)


# ─────────────────────────────────────────────────────────────────────────────
# 10 SALE texts — must all → deal_type='sale'
# ─────────────────────────────────────────────────────────────────────────────
SALE_TESTS = [
    ("🏙 For Sale | 2BR | Dubai Marina | AED 2,800,000 | Selling Price", 2_800_000, 2),
    ("Apartment for sale in Downtown Dubai. SP: 3.5M AED. Payment plan available.", 3_500_000, 2),
    ("Villa for sale | Palm Jumeirah | Asking Price AED 25M | handover Q4 2026", 25_000_000, 4),
    ("Resale unit in Burj Vista. Original Price 4.2M. Off-plan. Developer: Emaar.", 4_200_000, 2),
    ("HOT DEAL | 1BR for sale | JVC | Price AED 950K | rental yield 8% expected", 950_000, 1),
    ("Studio on sale in Business Bay | SP: 850,000 AED | distress deal", 850_000, 0),
    ("Penthouse for sale | DIFC | 12M AED | luxury investment | great rental potential", 12_000_000, 4),
    ("Продажа квартиры в Дубае | 2BR | 2.4M AED | SP: 2,400,000", 2_400_000, 2),
    ("Townhouse for sale Dubai Hills | 3BR | AED 4.5M | ready", 4_500_000, 3),
    ("Off-plan apartment | Dubai Creek Harbour | AED 1.8M | handover 2027 | Selling price", 1_800_000, 1),
]

# ─────────────────────────────────────────────────────────────────────────────
# 10 RENT texts — must all → deal_type='rent', period detection works
# ─────────────────────────────────────────────────────────────────────────────
RENT_TESTS = [
    ("🏠 For Rent | 1BR | Dubai Marina | AED 120,000/year | 4 cheques", 120_000, "yearly"),
    ("Apartment to let in JVC. Yearly rent AED 75,000. 2 cheques. Chiller free.", 75_000, "yearly"),
    ("Monthly rent AED 8,500 per month | Studio | Business Bay | short-term", 8_500, "monthly"),
    ("Villa for rent Palm Jumeirah | 6BR | per annum AED 800,000 | 4 cheques", 800_000, "yearly"),
    ("Сдается квартира в Дубае | 1BR | 90,000 AED в год | долгосрочно", 90_000, "yearly"),
    ("Long-term rent | 2BR | Downtown | AED 180,000 yearly | tenanted", 180_000, "yearly"),
    ("Short-term rent | Studio | JBR | AED 350/night | nightly", 10_500, "daily"),
    ("Annual rent 250k AED | 3BR apartment | Marina | 1 cheque", 250_000, "yearly"),
    ("Rent: 60,000 AED per year | Studio | Silicon Oasis | 6 cheques", 60_000, "yearly"),
    ("Monthly rent 12,000 AED | 1BR | Business Bay | per month", 12_000, "monthly"),
]

# ─────────────────────────────────────────────────────────────────────────────
# 10 MIXED / edge — sale ads mentioning rental yield, office-tower apartments,
# villa-community apartments, studios, townhouses, penthouses.
# ─────────────────────────────────────────────────────────────────────────────
MIXED_TESTS = [
    # (text, expected_deal_type, expected_property_type, bedrooms_hint)
    ("Investment opportunity | 1BR for sale | Marina | AED 1.6M | 8% rental yield",
     "sale", "apartment", 1),
    ("Selling price 4M AED | 2BR apartment | Downtown | high rental potential | passive income",
     "sale", "apartment", 2),
    ("Apartment in villa community Dubai Hills | 2BR for sale | AED 2.1M",
     "sale", "apartment", 2),
    ("Studio for sale | Business Bay Office Tower | AED 950K | great for rent",
     "sale", "studio", 0),
    ("Penthouse for sale | Palm Jumeirah | AED 35M | rental yield 6%",
     "sale", "penthouse", 5),
    ("Townhouse for sale Dubai Hills | 4BR | AED 5.5M | excellent rental income",
     "sale", "townhouse", 4),
    ("Duplex for sale | DIFC | 3BR | AED 8M | strong rental ROI",
     "sale", "duplex", 3),
    ("Villa for sale Emirates Hills | 7BR | AED 45M | offering yearly rental return 5%",
     "sale", "villa", 7),
    ("1BR apartment for sale | Office Tower | Business Bay | AED 1.1M",
     "sale", "apartment", 1),
    ("Studio for sale in JVC | AED 600K | brand new | rental yield up to 9%",
     "sale", "studio", 0),
]


def run_tests():
    passed = 0
    failed = []

    # SALE
    for i, (text, price, br) in enumerate(SALE_TESTS, 1):
        got = detect_deal_type(text, price=price, bedrooms=br)
        if got == "sale":
            passed += 1
        else:
            failed.append(f"SALE#{i} got={got} text={text[:60]!r}")

    # RENT
    for i, (text, price, expected_period) in enumerate(RENT_TESTS, 1):
        got_dt = detect_deal_type(text, price=price)
        got_period = detect_rent_period(text, price=price)
        if got_dt != "rent":
            failed.append(f"RENT#{i}-dt got={got_dt} text={text[:60]!r}")
        elif got_period != expected_period:
            failed.append(f"RENT#{i}-period got={got_period} want={expected_period} text={text[:60]!r}")
        else:
            passed += 1

    # MIXED
    for i, (text, expected_dt, expected_pt, br) in enumerate(MIXED_TESTS, 1):
        got_dt = detect_deal_type(text, bedrooms=br)
        got_pt = extract_property_type(text, bedrooms=br)
        ok = True
        if got_dt != expected_dt:
            failed.append(f"MIX#{i}-dt got={got_dt} want={expected_dt} text={text[:60]!r}")
            ok = False
        if got_pt != expected_pt:
            failed.append(f"MIX#{i}-pt got={got_pt} want={expected_pt} text={text[:60]!r}")
            ok = False
        if ok:
            passed += 1

    total = len(SALE_TESTS) + len(RENT_TESTS) + len(MIXED_TESTS)
    print(f"PASSED {passed}/{total}")
    if failed:
        print("FAILURES:")
        for f in failed:
            print("  " + f)
    return passed, total, failed


if __name__ == "__main__":
    p, t, f = run_tests()
    sys.exit(0 if not f else 1)
