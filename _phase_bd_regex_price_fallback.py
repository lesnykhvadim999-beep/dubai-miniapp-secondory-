"""PHASE BD — regex price fallback for listings with NULL price.

Scan v2_source_message_text for price-like patterns:
  - "22.8 m" / "1.3 m" / "16 m" (lowercase m = million AED)
  - "Price: AED 1.5M" / "Asking 2.7M"
  - "1,500,000 AED" / "AED 1500000"
  - "1.5 млн" (Russian)
  - "Selling Price: 4.9M"
Fill price (NULL → extracted), keeping LLM-extracted values intact.
"""
from __future__ import annotations
import os
import re
import psycopg2
from datetime import datetime, timezone

DB = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set")),
)


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# Priority-ordered regex: most explicit price markers first
PRICE_PATTERNS = [
    # "Selling Price: AED 4.9M" / "Asking Price: 2.7M"
    (r"(?:selling|asking|distress)\s*price\s*[:\-=]?\s*(?:aed\s*)?([\d\.,]+)\s*([mMкКmМмbB]|млн|млрд|mn|mln)?", "explicit_sell"),
    # "Price: AED 2.5M" / "Price 1.5M"
    (r"\bprice\s*[:\-=]?\s*(?:aed\s*)?([\d\.,]+)\s*([mMкКmМмbB]|млн|млрд|mn|mln)?", "explicit_price"),
    # "AED 22.8 m" / "AED 1,500,000"
    (r"\baed\s*([\d\.,]+)\s*([mMкКmМмbB]|млн|млрд|mn|mln)?", "aed_prefix"),
    # "22.8 m" / "1.3 m" / "16 m" at end of line OR standalone after BR/sqft
    (r"(?:^|[\s,\|])([\d]+(?:[\.,]\d+)?)\s*(m|M|М|м|млн|mn|mln)\b(?!\s*(?:in|with|by|sq|metr|m2))", "bare_m"),
    # "💰 4.9M AED"
    (r"💰\s*([\d\.,]+)\s*([mMкКmМмbB]|млн|млрд|mn|mln)?\s*aed", "emoji_money"),
]

_NUM_SUFFIX = {
    "k": 1_000, "K": 1_000, "к": 1_000, "К": 1_000,
    "m": 1_000_000, "M": 1_000_000, "м": 1_000_000, "М": 1_000_000,
    "mn": 1_000_000, "mln": 1_000_000, "млн": 1_000_000,
    "b": 1_000_000_000, "B": 1_000_000_000, "млрд": 1_000_000_000,
}


def parse_match(num_str, suffix):
    try:
        s = num_str.replace(",", "") if num_str.count(",") > 1 else num_str.replace(",", ".")
        n = float(s)
    except Exception:
        return None
    if suffix:
        n *= _NUM_SUFFIX.get(suffix, 1)
    return int(n) if n > 0 else None


def extract_price(text):
    """Returns int price in AED, or None."""
    if not text:
        return None
    t = text
    for pattern, label in PRICE_PATTERNS:
        for m in re.finditer(pattern, t, re.IGNORECASE | re.MULTILINE):
            price = parse_match(m.group(1), m.group(2) if m.lastindex >= 2 else None)
            # Plausibility: between 30k (rent monthly) and 500M (luxury villa)
            if price and 30_000 <= price <= 500_000_000:
                return price
    return None


def _strip_markdown(s):
    """Remove **bold** / *italic* / `code` / ~~strike~~ markers."""
    if not s:
        return s
    s = re.sub(r"\*+", "", s)
    s = re.sub(r"~~", "", s)
    s = re.sub(r"`", "", s)
    return s


def main():
    c = psycopg2.connect(DB, connect_timeout=10)
    cur = c.cursor()
    cur.execute("""
        SELECT id, v2_source_message_text
        FROM listings
        WHERE v2_extracted_at IS NOT NULL AND is_active=TRUE
          AND price IS NULL AND v2_source_message_text IS NOT NULL
    """)
    rows = cur.fetchall()
    log(f"Candidates: {len(rows):,}")

    filled = 0
    examples = []
    for lid, txt in rows:
        # Strip markdown formatting that can break regex
        txt_clean = _strip_markdown(txt)
        p = extract_price(txt_clean)
        if p:
            cur.execute("UPDATE listings SET price=%s WHERE id=%s", (p, lid))
            filled += 1
            if len(examples) < 8:
                snippet = txt[:120].replace("\n", " ")
                examples.append((lid, p, snippet))
    c.commit()
    log(f"\nFilled price: {filled}/{len(rows)} ({filled*100/max(len(rows),1):.1f}%)")
    log("\nSample fills:")
    for lid, p, sn in examples:
        log(f"  id={lid} price={p:,} text: {sn}...")
    c.close()


if __name__ == "__main__":
    main()
