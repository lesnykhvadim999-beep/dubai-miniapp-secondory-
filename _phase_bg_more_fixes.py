"""PHASE BG — more rule-based recoveries:

1. size_sqft regex fallback for missing values (markdown-aware)
   Patterns: '921 sq. m', '544 sq m', '853 sqft', '216 SQM', 'BUA: 4,959 sqft'
2. floor_category re-extract using improved regex (Higher floor / Medium floor)
3. unit_number extraction (B-1205 / Apt 12A / Unit 305)
4. handover quarter from 'Handover: Q3 2026' format
"""
from __future__ import annotations
import os
import re
import sys
import psycopg2
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from parser_v2_extras import extract_floor, extract_handover
except Exception:
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


# Size regexes — handle "sq. m", "SQM", "sq.ft" with optional periods/spaces
_SQFT_RX = re.compile(
    r"([\d,]+\.?\d*)\s*"
    r"(?:sq\.?\s*ft|sqft|sqf|s\.?\s*f\.?|кв\.?\s*фут)",
    re.IGNORECASE,
)
_SQM_RX = re.compile(
    r"([\d,]+\.?\d*)\s*"
    r"(?:sq\.?\s*m|sqm|sq\.?\s*meter|кв\.?\s*м|кв\.?\s*метр)",
    re.IGNORECASE,
)


def _strip_md(s):
    if not s: return s
    return re.sub(r"\*+|`+|~~+", "", s)


def to_num(s):
    try:
        return float(s.replace(",", ""))
    except Exception:
        return None


def extract_size(text):
    """Returns (size_sqft, size_sqm), prefer sqft from text, derive sqm."""
    if not text:
        return None, None
    t = _strip_md(text)
    # Try sqft first
    m = _SQFT_RX.search(t)
    sqft = None
    if m:
        sqft = to_num(m.group(1))
    m2 = _SQM_RX.search(t)
    sqm = None
    if m2:
        sqm = to_num(m2.group(1))
    # Cross-derive
    if sqft and sqm is None:
        sqm = round(sqft / 10.7639, 1)
    elif sqm and sqft is None:
        sqft = round(sqm * 10.7639, 1)
    # Plausibility: 100..50000 sqft
    if sqft and (sqft < 100 or sqft > 50000):
        return None, None
    return sqft, sqm


_UNIT_RX = re.compile(
    r"\bunit\s*[#:\-]?\s*([A-Z]?-?\d{1,5}[A-Z]?)\b|"
    r"\bapt\s*[#:\-]?\s*([A-Z]?-?\d{1,5}[A-Z]?)\b|"
    r"\bapartment\s*[#:\-]?\s*([A-Z]?-?\d{1,5}[A-Z]?)\b",
    re.IGNORECASE,
)


def extract_unit(text):
    if not text:
        return None
    m = _UNIT_RX.search(text)
    if not m:
        return None
    for g in m.groups():
        if g:
            return g.upper().strip("-")
    return None


def main():
    c = psycopg2.connect(DB, connect_timeout=10)
    cur = c.cursor()

    # Add unit_number column
    cur.execute("""
        ALTER TABLE listings ADD COLUMN IF NOT EXISTS unit_number TEXT;
    """)
    c.commit()

    cur.execute("""
        SELECT id,
               COALESCE(v2_source_message_text, original_text, description) AS txt,
               size_sqft, size_sqm,
               floor_number, floor_category, unit_number,
               handover_year, handover_quarter
        FROM listings
        WHERE v2_extracted_at IS NOT NULL AND is_active = TRUE
          AND COALESCE(v2_source_message_text, original_text, description) IS NOT NULL
    """)
    rows = cur.fetchall()
    log(f"Scanning {len(rows):,} listings")

    stats = {
        "size_sqft_filled": 0,
        "size_sqm_filled": 0,
        "floor_filled": 0,
        "unit_filled": 0,
        "handover_filled": 0,
    }

    for (lid, txt, sqft, sqm, fl_num, fl_cat, unit,
         ho_y, ho_q) in rows:
        upd = {}
        # 1. size_sqft / size_sqm
        if sqft is None or sqm is None:
            sf, sm = extract_size(txt)
            if sf and sqft is None:
                upd["size_sqft"] = sf
                stats["size_sqft_filled"] += 1
            if sm and sqm is None:
                upd["size_sqm"] = sm
                stats["size_sqm_filled"] += 1
        # 2. floor (re-extract with improved regex)
        if fl_num is None and fl_cat is None:
            fn, fc = extract_floor(txt)
            if fn is not None:
                upd["floor_number"] = fn
            if fc:
                upd["floor_category"] = fc
            if fn is not None or fc:
                stats["floor_filled"] += 1
        # 3. unit number
        if not unit:
            un = extract_unit(txt)
            if un:
                upd["unit_number"] = un
                stats["unit_filled"] += 1
        # 4. handover (re-extract with improved patterns)
        if ho_y is None and ho_q is None:
            y, q, _ = extract_handover(txt)
            if y:
                upd["handover_year"] = y
            if q:
                upd["handover_quarter"] = q
            if y or q:
                stats["handover_filled"] += 1

        if upd:
            set_parts = ", ".join(f"{k}=%s" for k in upd.keys())
            params = list(upd.values()) + [lid]
            cur.execute(f"UPDATE listings SET {set_parts} WHERE id=%s", params)
    c.commit()
    log("\n=== STATS ===")
    for k, v in stats.items():
        log(f"  {k}: {v:,}")
    c.close()


if __name__ == "__main__":
    main()
