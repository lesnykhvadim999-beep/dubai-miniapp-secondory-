"""PHASE BC — re-process all active listings through improved parser_v2 rules.

Apply WITHOUT LLM (avoids 429):
  1. Canonical area lookup (505 entries)
  2. Canonical building lookup (5558 entries) → fill missing area
  3. Re-extract extras: furnishing, view, floor, handover (parser_v2_extras)
  4. Re-extract price_original + discount_pct from v2_source_message_text
  5. Sanity guards: rent>5M → sale, sale<200K → rent
  6. Studio auto-detect: bedrooms=0 + has size → property_type=studio
  7. Robust price re-coerce: 'AED 2.5M' → 2_500_000
"""
from __future__ import annotations
import os
import re
import sys
import psycopg2
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import canonicals + extractors
try:
    from area_canonical_extended import AREA_CANONICAL_EXTENDED as _AREA_EXT
except Exception:
    _AREA_EXT = {}
try:
    from building_canonical import BUILDING_TO_AREA as _BLD
except Exception:
    _BLD = {}
try:
    from parser_v2_extras import (
        extract_furnishing, extract_view, extract_floor, extract_handover
    )
except Exception:
    extract_furnishing = lambda t: None
    extract_view = lambda t: None
    extract_floor = lambda t: (None, None)
    extract_handover = lambda t: (None, None, None)

DB = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:REDACTED_DSN_PASSWORD"
    "@tramway.proxy.rlwy.net:23228/railway",
)


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def canon_area(area):
    if not area:
        return area
    key = area.strip().lower()
    if key in _AREA_EXT:
        return _AREA_EXT[key]
    stripped = re.sub(r"\s*(phase\s*)?\d+\s*$", "", key).strip()
    if stripped and stripped in _AREA_EXT:
        return _AREA_EXT[stripped]
    return area.strip()


def canon_bld(building):
    if not building:
        return (None, None)
    key = re.sub(r"\s+", " ", building.strip().lower())
    info = _BLD.get(key)
    if info:
        return (info.get("name") or building.strip(), info.get("area") or None)
    stripped = re.sub(r"\s+(tower\s+)?[abcivx\d]+$", "", key).strip()
    if stripped and stripped != key:
        info = _BLD.get(stripped)
        if info:
            return (info.get("name") or building.strip(), info.get("area") or None)
    return (building.strip(), None)


# Extract original/asking price from text
_ORIG_RX = re.compile(
    r"(?:original|launch|developer|list)\s*price[^0-9]{0,12}([\d\.,]+)\s*([mMkKbB])?",
    re.IGNORECASE,
)
_SELL_RX = re.compile(
    r"(?:asking|selling|distress)\s*price[^0-9]{0,12}([\d\.,]+)\s*([mMkKbB])?",
    re.IGNORECASE,
)
_NUM_SUFFIX = {"k": 1_000, "K": 1_000, "m": 1_000_000, "M": 1_000_000,
                "b": 1_000_000_000, "B": 1_000_000_000}


def _parse_price_match(m):
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except Exception:
        return None
    suf = m.group(2)
    if suf:
        n *= _NUM_SUFFIX.get(suf, 1)
    # Plausibility check
    if n < 1000 or n > 1_000_000_000:
        return None
    return int(n)


def extract_orig_and_selling(text):
    """Returns (selling_price, original_price)."""
    if not text:
        return (None, None)
    orig = _parse_price_match(_ORIG_RX.search(text))
    sell = _parse_price_match(_SELL_RX.search(text))
    return (sell, orig)


def main():
    c = psycopg2.connect(DB, connect_timeout=10)
    cur = c.cursor()

    cur.execute("""
        SELECT id, area, building, bedrooms, price, deal_type, property_type,
               size_sqft, v2_source_message_text,
               furnishing, view, floor_number, floor_category,
               handover_year, handover_quarter, handover_text,
               price_original
        FROM listings
        WHERE v2_extracted_at IS NOT NULL AND is_active = TRUE
    """)
    rows = cur.fetchall()
    log(f"Processing {len(rows):,} active listings")

    stats = {
        "area_canonicalized": 0,
        "building_canonicalized": 0,
        "area_filled_from_bld": 0,
        "furnishing_filled": 0,
        "view_filled": 0,
        "floor_filled": 0,
        "handover_filled": 0,
        "price_original_filled": 0,
        "deal_type_flipped_to_sale": 0,
        "deal_type_flipped_to_rent": 0,
        "studio_auto_detected": 0,
    }

    for i, r in enumerate(rows):
        (lid, area, building, br, price, deal, pt, sqft, txt,
         furn, view, fl_num, fl_cat,
         ho_y, ho_q, ho_t,
         price_orig) = r
        upd = {}

        # 1. Canonical area
        new_area = canon_area(area)
        if new_area and new_area != area:
            upd["area"] = new_area
            stats["area_canonicalized"] += 1
            area = new_area

        # 2. Canonical building + fill area from building
        new_bld, bld_area = canon_bld(building)
        if new_bld and new_bld != building:
            upd["building"] = new_bld
            stats["building_canonicalized"] += 1
        if not area and bld_area:
            upd["area"] = bld_area
            stats["area_filled_from_bld"] += 1

        # 3. Extract extras from text (if missing)
        if txt:
            if furn is None:
                f = extract_furnishing(txt)
                if f:
                    upd["furnishing"] = f
                    stats["furnishing_filled"] += 1
            if view is None:
                v = extract_view(txt)
                if v:
                    upd["view"] = v
                    stats["view_filled"] += 1
            if fl_num is None and fl_cat is None:
                num, cat = extract_floor(txt)
                if num is not None:
                    upd["floor_number"] = num
                if cat:
                    upd["floor_category"] = cat
                if num is not None or cat:
                    stats["floor_filled"] += 1
            if ho_t is None and ho_y is None:
                yr, qt, hstr = extract_handover(txt)
                if yr:
                    upd["handover_year"] = yr
                if qt:
                    upd["handover_quarter"] = qt
                if hstr:
                    upd["handover_text"] = hstr
                if yr or qt or hstr:
                    stats["handover_filled"] += 1
            # 4. price_original
            if price_orig is None:
                sell, orig = extract_orig_and_selling(txt)
                if orig and orig != price and orig > 0:
                    upd["price_original"] = orig
                    if price:
                        disc = round((orig - price) * 100 / orig, 1)
                        # Clip to plausible range (-9999.99 .. 9999.99)
                        if -9000 < disc < 9000:
                            upd["discount_pct"] = disc
                    stats["price_original_filled"] += 1

        # 5. Sanity guards
        if price and deal == "rent" and price > 5_000_000:
            upd["deal_type"] = "sale"
            stats["deal_type_flipped_to_sale"] += 1
        elif price and deal == "sale" and 30_000 < price < 200_000:
            upd["deal_type"] = "rent"
            stats["deal_type_flipped_to_rent"] += 1

        # 6. Studio auto-detect
        if br == 0 and (not pt or pt in ("apartment", None)) and sqft:
            upd["property_type"] = "studio"
            stats["studio_auto_detected"] += 1

        # Apply updates
        if upd:
            set_parts = []
            params = []
            for k, v in upd.items():
                set_parts.append(f"{k} = %s")
                params.append(v)
            params.append(lid)
            cur.execute(
                f"UPDATE listings SET {', '.join(set_parts)} WHERE id = %s",
                params,
            )

        if (i + 1) % 500 == 0:
            c.commit()
            log(f"  [{i+1}/{len(rows)}] processed")

    c.commit()
    log("Done. Stats:")
    for k, v in stats.items():
        log(f"  {k}: {v:,}")
    c.close()


if __name__ == "__main__":
    main()
