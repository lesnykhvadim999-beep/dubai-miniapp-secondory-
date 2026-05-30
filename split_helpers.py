"""Multi-listing split helpers — close all weak spots.

Strategy (in priority order):
  1. INLINE detection: "5BR HAVEN 12M | 4BR PALM 8M" → split by |, /, &
  2. EXPLICIT separators: FOR SALE!/RENT! markers, lines of dashes/equals/emoji
  3. NUMERIC list pattern: "1." "2." "🟦 1." "📍 #1"
  4. LLM split (existing) — fallback when regex finds 1 block
  5. POST: collapse duplicate blocks (same building+price likely over-split)
"""
from __future__ import annotations
import re
from typing import List


# ─── Pattern: forwarded-message headers (Telegram quotes from another channel) ───
_FORWARDED_HEADER_RX = re.compile(
    r"^\s*(?:"
    r"Forwarded\s+from\s+[^\n]{1,80}|"
    r"From\s+[@:]?\w[\w\s_-]{1,80}|"
    r"Переслано\s+(?:из\s+|от\s+)?[^\n]{1,80}|"
    r"📨\s+[\w\s]{1,40}|"
    r"💬\s+@\w+\s+writes?:|"
    r"\[Channel:\s+[^\]]+\]"
    r")\s*\n+",
    re.IGNORECASE | re.MULTILINE,
)


def strip_forwarded_header(text: str) -> str:
    """Remove Telegram-style forwarded-message headers from start of text."""
    if not text:
        return text
    # Allow up to 3 nested forwards
    for _ in range(3):
        new = _FORWARDED_HEADER_RX.sub("", text, count=1)
        if new == text:
            break
        text = new
    return text.strip()


# ─── Pattern: explicit FOR SALE! / FOR RENT! repeating markers ───
_HEADER_RX = re.compile(
    r"(?:^|\n)\s*(?:[🟦🔥📍💎⚡🚨✨🌟▪️▶️🏠🏢🏖]\s*)?"
    r"(?:#?\d+[\.\)]\s*)?"
    r"(?:FOR\s+SALE|FOR\s+RENT|SALE|RENT|"
    r"For\s+Sale|For\s+Rent|"
    r"Listing\s+#?\d+|Property\s+#?\d+|Option\s+#?\d+)"
    r"[!:\.\s]*",
    re.IGNORECASE,
)

# Lines of repeating separator characters
_SEP_LINE_RX = re.compile(
    r"\n[\s]*(?:[-=–—_]{3,}|[~]{3,}|"
    r"[•●▪️·∙]{3,}|"
    r"[*]{3,}|"
    r"[🔻🔺▼▲]{2,})\s*\n",
)

# Numbered list pattern (one block starts at "1." or "🟦 1." etc.)
_NUMBERED_RX = re.compile(
    r"(?:^|\n)\s*(?:[🟦🔥📍💎⚡🚨✨🌟▪️🏠🏢]\s*)?"
    r"#?(?:[1-9]|1[0-9])[\.\)]\s+",
)

# Inline ALL-CAPS building + price pattern (most aggressive)
# Matches: "5BR HAVEN 12M | 4BR PALM 8M"
_INLINE_BR_RX = re.compile(
    r"(?<=[\|/])\s*(?:[0-9]+\s*BR|studio)",
    re.IGNORECASE,
)


def split_by_inline(text: str) -> List[str]:
    """Split on '|' or '/' when they separate independent listings.

    Conservative: require both sides to look like listings (have BR + price)."""
    if not text or "|" not in text and "/" not in text:
        return [text]
    # Only consider | and / that are followed by another BR/studio
    # Avoid normal usage like "BR/Maid" or "1/2 BR"
    parts = re.split(r"\s*[\|]\s*", text)
    if len(parts) < 2:
        return [text]
    # Each part must contain a BR-like token AND a price-like token
    def looks_like_listing(p: str) -> bool:
        has_br = bool(re.search(r"\b(\d+\s*BR|\d+\s*bedroom|studio)\b", p, re.I))
        has_price = bool(re.search(r"\b\d+(?:\.\d+)?\s*[KMm]\b|aed\s*[\d,]+", p, re.I))
        return has_br and has_price
    if all(looks_like_listing(p) for p in parts):
        return [p.strip() for p in parts if p.strip()]
    return [text]


def regex_split(text: str) -> List[str]:
    """Try to split text via explicit-marker regex. Returns 1+ blocks.

    Returns [text] (single-block) if no markers found."""
    if not text or not text.strip():
        return []

    # 0. Strip forwarded-message headers first
    text = strip_forwarded_header(text)
    if not text:
        return []

    # 1. Inline split (highest priority — must be obvious, works for short texts too)
    inline = split_by_inline(text)
    if len(inline) > 1:
        return inline

    if len(text.strip()) < 30:
        return [text.strip()]

    # 2. Separator lines (dashes/equals/dots in own line)
    parts = _SEP_LINE_RX.split(text)
    parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 10]
    if len(parts) > 1:
        return parts

    # 3. FOR SALE / FOR RENT repeating headers
    # Find positions of all headers, then slice between them
    starts = [m.start() for m in _HEADER_RX.finditer(text)]
    if len(starts) > 1:
        slices = []
        for i, s in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(text)
            chunk = text[s:end].strip()
            if len(chunk) > 10:
                slices.append(chunk)
        if len(slices) > 1:
            return slices

    # 4. Numbered list (1., 2., 3.)
    starts = [m.start() for m in _NUMBERED_RX.finditer(text)]
    if len(starts) >= 2:
        slices = []
        for i, s in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(text)
            chunk = text[s:end].strip()
            if len(chunk) > 10:
                slices.append(chunk)
        if len(slices) >= 2:
            return slices

    # No split possible
    return [text.strip()]


# ─── Post-pass: collapse blocks that are clearly the same listing ───
def _block_signature(block: str) -> tuple:
    """Extract (building_hash, price_value, br) signature for comparison."""
    t = block.lower()
    # Price (M / K / direct number)
    price = None
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*[mM]\b", t)
    if m:
        price = int(float(m.group(1)) * 1_000_000)
    else:
        m = re.search(r"aed\s*([\d,]+)", t)
        if m:
            try:
                price = int(m.group(1).replace(",", ""))
            except Exception:
                pass
    # Building — first non-trivial word group (very rough)
    m = re.search(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\b", block)
    bld = m.group(1).lower() if m else None
    # BR
    m = re.search(r"\b(\d+)\s*(?:BR|bedroom|bhk)\b", t, re.I)
    br = int(m.group(1)) if m else None
    return (bld, price, br)


def collapse_duplicate_blocks(blocks: List[str]) -> List[str]:
    """If 2+ consecutive blocks have identical (building, price, br) signature,
    keep only the longest (most info)."""
    if len(blocks) <= 1:
        return blocks
    sigs = [_block_signature(b) for b in blocks]
    out: List[str] = []
    prev_sig = None
    for b, s in zip(blocks, sigs):
        # Skip if signature matches previous AND has at least price OR building+br
        if (prev_sig and s == prev_sig
                and (s[0] is not None or (s[1] is not None and s[2] is not None))):
            # Keep the longer (more detail)
            if len(b) > len(out[-1]):
                out[-1] = b
            continue
        out.append(b)
        prev_sig = s
    return out


def hybrid_split(text: str, llm_split_fn=None) -> tuple[bool, List[str]]:
    """Main entry: regex-first, LLM-fallback, then post-collapse.

    Args:
      text: source telegram message
      llm_split_fn: callable returning (is_spam, blocks). Used as fallback
                    when regex finds only 1 block but text looks multi.
    Returns:
      (is_spam, list_of_block_texts)
    """
    if not text or not text.strip():
        return (True, [])

    # Try regex first
    blocks = regex_split(text)
    if len(blocks) >= 2:
        # Post-collapse: undo over-split
        blocks = collapse_duplicate_blocks(blocks)
        return (False, blocks)

    # Regex found 1 block — but is the text suspicious of multi-listing?
    suspicious = (
        len(text) > 500 and (
            text.lower().count("for sale") >= 2 or
            text.lower().count("for rent") >= 2 or
            text.lower().count("price") >= 2 or
            text.count("AED") >= 3 or
            text.count("aed") >= 3
        )
    )
    if suspicious and llm_split_fn is not None:
        return llm_split_fn(text)

    # Single listing or spam
    if llm_split_fn is not None:
        # Use LLM for spam classification even on single block
        return llm_split_fn(text)
    return (False, [text.strip()])


if __name__ == "__main__":
    # Smoke tests
    tests = [
        # 1. Numbered list
        ("""🟦 1. Burj Crown Downtown
1BR, 585 sqft, AED 2.15M

🟦 2. Marina Vista Dubai Marina
2BR, 1200 sqft, AED 3.8M""", 2),
        # 2. FOR SALE separator
        ("""FOR SALE!
Address Residences Downtown
2BR, AED 5.2M

————

FOR SALE!
Bluewaters Residence 1
3BR, AED 7.8M""", 2),
        # 3. Inline
        ("5BR HAVEN 12M | 4BR PALM 8M", 2),
        # 4. Single listing
        ("Burj Crown Downtown 1BR 585 sqft sea view AED 2.15M", 1),
    ]
    for txt, want in tests:
        _, blocks = hybrid_split(txt)
        status = "OK" if len(blocks) == want else f"FAIL want {want}"
        print(f"{status}: got {len(blocks)} blocks from text: {txt[:60]!r}...")
