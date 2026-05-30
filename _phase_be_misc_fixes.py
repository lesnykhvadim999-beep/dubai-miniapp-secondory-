"""PHASE BE — misc parser fixes.

1. Buyer-request detection: text starts with "Required:" / "Looking for" / "Wanted:"
   → mark inactive as spam_buyer_request
2. URL-only spam: text is essentially just a URL → mark inactive
3. Aggressive area inference: scan text for any known area name (from AREA_CANONICAL_EXTENDED)
   when area is NULL → fill it
4. Forwarded chain cleanup: strip "Forwarded from @channel" headers
   when building/area are NULL (try parsing the remainder)
"""
from __future__ import annotations
import os
import re
import sys
import psycopg2
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from area_canonical_extended import AREA_CANONICAL_EXTENDED as _AREA_EXT
except Exception:
    _AREA_EXT = {}

try:
    from building_canonical import BUILDING_TO_AREA as _BLD
except Exception:
    _BLD = {}

DB = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set")),
)


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# Buyer-request patterns
BUYER_RX = re.compile(
    r"^\s*[*]*\s*(required|looking\s+for|wanted|searching\s+for|need\s+to\s+(rent|buy)|"
    r"i\s+want\s+to\s+(rent|buy)|"
    r"клиент\s+ищет|ищу|нужен|требуется|"
    r"my\s+client\s+is\s+looking|client\s+is\s+looking|"
    r"buyer\s+requirement)\s*[:\-]?",
    re.IGNORECASE | re.MULTILINE,
)


def is_buyer_request(text):
    if not text:
        return False
    return bool(BUYER_RX.search(text[:300]))


def is_url_only(text):
    if not text:
        return False
    stripped = re.sub(r"https?://\S+", "", text).strip()
    # If after URL removal less than 30 chars of meaningful content → URL spam
    meaningful = re.sub(r"[\s\.,\-!\?\*\(\)]+", "", stripped)
    return len(meaningful) < 30


def _strip_forwarded_header(text):
    """Strip 'Forwarded from @channel' / 'Переслано из ...' headers."""
    if not text:
        return text
    # Common forwarded patterns
    patterns = [
        r"^Forwarded\s+from\s+[^\n]{1,80}\n+",
        r"^From\s+[@:]?\w[\w\s]{1,80}\n+",
        r"^Переслано\s+(?:из\s+)?[^\n]{1,80}\n+",
        r"^📨\s+\w+\s+[\w\s]{1,40}\n+",
    ]
    for p in patterns:
        text = re.sub(p, "", text, flags=re.IGNORECASE)
    return text.strip()


def infer_area_from_text(text):
    """Aggressive scan for ANY known area name in text body."""
    if not text or not _AREA_EXT:
        return None
    t = text.lower()
    # Sort by length descending so longer matches win
    for key in sorted(_AREA_EXT.keys(), key=lambda k: -len(k)):
        if len(key) < 4:  # avoid very short matches like "jbr"
            # But allow common abbrevs
            if key in {"jlt", "jvc", "jbr", "jvt", "dso"}:
                pattern = r"\b" + re.escape(key) + r"\b"
                if re.search(pattern, t):
                    return _AREA_EXT[key]
            continue
        if key in t:
            return _AREA_EXT[key]
    return None


def main():
    c = psycopg2.connect(DB, connect_timeout=10)
    cur = c.cursor()

    # 1. Buyer-request detection
    log("=== FIX 1: buyer-request detection ===")
    cur.execute("""
        SELECT id, v2_source_message_text FROM listings
        WHERE v2_extracted_at IS NOT NULL AND is_active=TRUE
          AND v2_source_message_text IS NOT NULL
    """)
    buyer = 0
    url_spam = 0
    for lid, txt in cur.fetchall():
        if is_buyer_request(txt):
            cur.execute("""UPDATE listings SET is_active=FALSE,
                            status='spam_buyer_request' WHERE id=%s""", (lid,))
            buyer += 1
        elif is_url_only(txt):
            cur.execute("""UPDATE listings SET is_active=FALSE,
                            status='spam_url_only' WHERE id=%s""", (lid,))
            url_spam += 1
    c.commit()
    log(f"  buyer-request flagged: {buyer}")
    log(f"  URL-only spam flagged: {url_spam}")

    # 2. Aggressive area inference for NULL area
    log("\n=== FIX 2: aggressive area inference ===")
    cur.execute("""
        SELECT id, v2_source_message_text FROM listings
        WHERE v2_extracted_at IS NOT NULL AND is_active=TRUE
          AND area IS NULL AND v2_source_message_text IS NOT NULL
    """)
    area_filled = 0
    for lid, txt in cur.fetchall():
        # Try stripped forwarded header
        clean = _strip_forwarded_header(txt)
        area = infer_area_from_text(clean)
        if area:
            cur.execute("UPDATE listings SET area=%s WHERE id=%s",
                         (area, lid))
            area_filled += 1
    c.commit()
    log(f"  area inferred from text: {area_filled}")

    # 3. Final stats
    cur.execute("""SELECT COUNT(*) FROM listings
        WHERE v2_extracted_at IS NOT NULL AND is_active=TRUE""")
    active = cur.fetchone()[0]
    log(f"\nActive listings now: {active:,}")
    c.close()


if __name__ == "__main__":
    main()
