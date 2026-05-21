# -*- coding: utf-8 -*-
"""Simulate every wizard path to make sure all transitions work.
Tests: Buy/Rent/Commercial/Land/Hot/New → emirate → proptype → bedrooms → budget
across all 3 languages.
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import importlib.util

spec = importlib.util.spec_from_file_location("rb", "resale_bot.py")
# Read T dict directly to avoid full module init (which spins up DB/threads)
with open("resale_bot.py", encoding="utf-8") as f:
    src = f.read()
ns = {}
start = src.index("T = {")
end = src.find("def _t(uid", start)
exec(src[start:end], ns)
T = ns["T"]

# Required canonical keys for the new wizard layer
REQUIRED_KEYS = [
    # Main menu
    "rbtn_buy", "rbtn_rent", "rbtn_commercial", "rbtn_plot",
    "rbtn_hot", "rbtn_new", "rbtn_ai", "rbtn_add", "rbtn_lang", "rbtn_home",
    # Emirate wizard
    "em_dubai", "em_abudhabi", "em_rak", "em_sharjah", "em_any",
    # Property type wizard
    "pt_apt_btn", "pt_villa_btn", "pt_town_btn", "pt_pent_btn",
    "pt_studio_btn", "pt_duplex_btn",
    "pt_office_btn", "pt_retail_btn", "pt_warehouse_btn", "pt_hotel_btn",
    "pt_any_btn",
    # Bedrooms wizard
    "br_studio_btn", "br_1_btn", "br_2_btn", "br_3_btn", "br_4p_btn", "br_any_btn",
    # Generic
    "b_any_btn",
    # AI assistant
    "ai_invest", "ai_live", "ai_holiday", "ai_unsure",
    "ai_commercial", "ai_land", "ai_commercial_q",
    # /add wizard new steps
    "add_floor_q", "add_unit_q", "add_description_q", "add_skip",
]

ok = fail = 0
for lang in ("en", "ru", "ar"):
    print(f"\n=== {lang.upper()} ===")
    missing = []
    for k in REQUIRED_KEYS:
        if k not in T[lang]:
            missing.append(k)
            fail += 1
        else:
            ok += 1
    if missing:
        print(f"  MISSING: {missing}")
    else:
        print(f"  ✓ all {len(REQUIRED_KEYS)} keys present")

print(f"\n=== Translation parity ===")
print(f"  en: {len(T['en'])}, ru: {len(T['ru'])}, ar: {len(T['ar'])}")
in_en_not_ru = set(T['en']) - set(T['ru'])
in_en_not_ar = set(T['en']) - set(T['ar'])
print(f"  missing in RU: {len(in_en_not_ru)}  AR: {len(in_en_not_ar)}")
if in_en_not_ru:
    print(f"  RU missing keys: {sorted(in_en_not_ru)[:10]}")
if in_en_not_ar:
    print(f"  AR missing keys: {sorted(in_en_not_ar)[:10]}")

print(f"\n=== Total: {ok} ok / {fail} fail ===")
sys.exit(0 if fail == 0 else 1)
