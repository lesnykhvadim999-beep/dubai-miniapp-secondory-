"""parser_v2 extras — rule-based extractors for fields that significantly
drive buyer conversion: furnishing, view, floor, handover_date.

These are intentionally rule-based (no LLM) so they are fast, deterministic,
free, and safe to run on the entire historical corpus during backfill.

Supports EN / RU / AR keywords for furnishing & view where reasonable.

Each extractor accepts any text-ish input and returns a small typed result.
Returns None / empty when the signal isn't present — never raises.
"""
from __future__ import annotations
import re
from typing import Optional, Tuple


# ── helpers ───────────────────────────────────────────────────────────────
def _norm(text: Optional[str]) -> str:
    if not text:
        return ""
    # Strip Telegram markdown (**bold**, __italic__, `code`, ~~strike~~)
    cleaned = re.sub(r"[*_~`]+", " ", text)
    return cleaned.lower()


# ── 1. Furnishing ─────────────────────────────────────────────────────────
# Important: check semi/unfurnished BEFORE plain "furnished" because
# "semi-furnished" / "unfurnished" both contain the substring "furnished".

_SEMI_PATTERNS = [
    r"\bsemi[\s\-]?furnished\b",
    r"\bpartially\s+furnished\b",
    r"\bpart\s+furnished\b",
    r"полу[\s\-]?мебл",       # полу-меблирована
    r"частично\s+мебл",        # частично меблирована
    r"نصف\s+مفروش",            # AR: half furnished
]

_UNFURN_PATTERNS = [
    r"\bun[\s\-]?furnished\b",
    r"\bnot\s+furnished\b",
    r"\bno\s+furniture\b",
    r"\bwithout\s+furniture\b",
    r"без\s+мебели",
    r"не\s+мебл",              # не меблирована
    r"غير\s+مفروش",            # AR: not furnished
]

_FURN_PATTERNS = [
    r"\bfully\s+furnished\b",
    r"\bfurnished\b",
    r"\bwith\s+furniture\b",
    r"меблирован",            # меблирована/меблированная
    r"с\s+мебелью",
    r"мебл\.",
    r"مفروش",                  # AR: furnished
]


def extract_furnishing(text: Optional[str]) -> Optional[str]:
    """Return 'furnished' | 'semi' | 'unfurnished' | None."""
    s = _norm(text)
    if not s:
        return None
    for p in _SEMI_PATTERNS:
        if re.search(p, s):
            return "semi"
    for p in _UNFURN_PATTERNS:
        if re.search(p, s):
            return "unfurnished"
    for p in _FURN_PATTERNS:
        if re.search(p, s):
            return "furnished"
    return None


# ── 2. View ───────────────────────────────────────────────────────────────
# Order matters: more specific first. We return the FIRST matched view tag.
_VIEW_PATTERNS: list[tuple[str, str]] = [
    ("burj_khalifa", r"\bburj\s*khalifa\s*view\b|\bbk\s*view\b|вид\s+на\s+бурдж"),
    ("burj_al_arab", r"\bburj\s*al[\s\-]?arab\s*view\b"),
    ("sea",          r"\bsea\s*view\b|\bocean\s*view\b|\bfull\s*sea\b|\bpartial\s*sea\b|вид\s+на\s+море|на\s+море|إطلالة\s+بحر|بحر"),
    ("beach",        r"\bbeach\s*view\b|\bbeachfront\s*view\b"),
    ("marina",       r"\bmarina\s*view\b|вид\s+на\s+марину|إطلالة\s+مارينا"),
    ("canal",        r"\bcanal\s*view\b|\bwater\s*canal\b|вид\s+на\s+канал"),
    ("lagoon",       r"\blagoon\s*view\b"),
    ("lake",         r"\blake\s*view\b|вид\s+на\s+озеро"),
    ("fountain",     r"\bfountain\s*view\b|вид\s+на\s+фонтан"),
    ("park",         r"\bpark\s*view\b|\bgarden\s*view\b|вид\s+на\s+парк|إطلالة\s+حديقة"),
    ("golf",         r"\bgolf\s*view\b|\bgolf\s*course\s*view\b|вид\s+на\s+гольф"),
    ("pool",         r"\bpool\s*view\b|вид\s+на\s+бассейн"),
    ("community",    r"\bcommunity\s*view\b|\bcompound\s*view\b|\bvilla\s*view\b"),
    ("skyline",      r"\bskyline\s*view\b|\bdubai\s*skyline\b"),
    ("city",         r"\bcity\s*view\b|вид\s+на\s+город|إطلالة\s+مدينة"),
    ("downtown",     r"\bdowntown\s*view\b"),
    ("boulevard",    r"\bboulevard\s*view\b"),
    ("creek",        r"\bcreek\s*view\b"),
    ("desert",       r"\bdesert\s*view\b"),
    ("street",       r"\bstreet\s*view\b"),
]


def extract_view(text: Optional[str]) -> Optional[str]:
    """Return canonical view tag like 'sea', 'marina', 'burj_khalifa' or None."""
    s = _norm(text)
    if not s:
        return None
    for tag, pat in _VIEW_PATTERNS:
        if re.search(pat, s):
            return tag
    return None


# ── 3. Floor ──────────────────────────────────────────────────────────────
# Prefer a specific floor number when present, else categorise.
_FLOOR_NUM_PATTERNS = [
    r"\bfloor\s*[:\#]?\s*(\d{1,3})\b",
    r"\b(\d{1,3})\s*(?:st|nd|rd|th)?\s*floor\b",
    r"\bon\s+(?:the\s+)?(\d{1,3})\s*(?:st|nd|rd|th)?\s+floor\b",
    r"\blevel\s*[:\#]?\s*(\d{1,3})\b",
    r"\b(\d{1,3})\s*-?\s*(?:го|й|ый)?\s*этаж\b",   # 36 этаж / 36-й этаж
    r"\bэтаж\s*[:\#]?\s*(\d{1,3})\b",
    r"الطابق\s*(\d{1,3})",                          # AR: floor N
]

_PENTHOUSE_RE = re.compile(r"\bpenthouse\b|\bпентхаус\b|بنتهاوس", re.IGNORECASE)
_HIGH_FLOOR_RE = re.compile(
    r"\bhigh\s+floor\b|\bhigh\s+level\b|\bhigher\s+floor\b|\bhigher\s+level\b|"
    r"\bupper\s+floor\b|\btop\s+floor\b|\bупper\s+level\b|"
    r"высок\w+\s+этаж|верхн\w+\s+этаж|طابق\s+عالي",
    re.IGNORECASE,
)
_MID_FLOOR_RE = re.compile(
    r"\bmid(?:dle)?\s+floor\b|\bmid\s+level\b|\bmedium\s+floor\b|\bmedium\s+level\b|"
    r"средн\w+\s+этаж|سطو\w+\s+متوسط|طابق\s+متوسط",
    re.IGNORECASE,
)
_LOW_FLOOR_RE = re.compile(
    r"\blow\s+floor\b|\blower\s+floor\b|\blow\s+level\b|\blower\s+level\b|"
    r"низк\w+\s+этаж|нижн\w+\s+этаж|طابق\s+منخفض",
    re.IGNORECASE,
)
_GROUND_FLOOR_RE = re.compile(r"\bground\s+floor\b|\bgf\b|первый\s+этаж|الطابق\s+الأرضي", re.IGNORECASE)


def _classify_floor_number(n: int) -> str:
    if n >= 20:
        return "high"
    if n >= 5:
        return "mid"
    return "low"


def extract_floor(text: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
    """Return (floor_number, floor_category).

    Rules:
    - Specific number wins. If a number is found, also derive a category
      (low<5, mid 5..19, high>=20).
    - Else penthouse → ('penthouse' category, no number).
    - Else explicit high/mid/low/ground → that category.
    - Else (None, None).
    """
    s = text or ""
    if not s:
        return None, None
    # Strip markdown formatting (Telegram-style **bold**, __italic__, `code`, ~~strike~~)
    s = re.sub(r"[*_~`]+", " ", s)
    low = s.lower()

    # Specific number
    for pat in _FLOOR_NUM_PATTERNS:
        m = re.search(pat, low, re.IGNORECASE)
        if m:
            try:
                n = int(m.group(1))
            except Exception:
                continue
            if 0 < n < 200:
                return n, _classify_floor_number(n)

    # Penthouse counts as high-floor category, no number
    if _PENTHOUSE_RE.search(s):
        return None, "penthouse"
    if _HIGH_FLOOR_RE.search(s):
        return None, "high"
    if _MID_FLOOR_RE.search(s):
        return None, "mid"
    if _LOW_FLOOR_RE.search(s):
        return None, "low"
    if _GROUND_FLOOR_RE.search(s):
        return 0, "low"

    return None, None


# ── 4. Handover ───────────────────────────────────────────────────────────
_READY_RE = re.compile(
    r"\bready\s+to\s+move\b|\bready\s+now\b|\bready\b|\bmove[\s\-]?in\s+ready\b|"
    r"готов(?:ый|о|а)?\s+(?:к\s+)?(?:заселению|въезду)|готов\s+сейчас|"
    r"جاهز",
    re.IGNORECASE,
)
_QTR_YEAR_RE = re.compile(r"\bq\s*([1-4])\s*[\-\/\s]?\s*(20\d{2})\b", re.IGNORECASE)
_YEAR_QTR_RE = re.compile(r"\b(20\d{2})\s*[\-\/\s]?\s*q\s*([1-4])\b", re.IGNORECASE)
_HANDOVER_YEAR_RE = re.compile(
    r"\b(?:handover|completion|delivery|ready\s+by|обещают|сдача|сдают?)\D{0,20}?(20\d{2})\b",
    re.IGNORECASE,
)
_OFFPLAN_YEAR_RE = re.compile(r"\boff[\s\-]?plan\D{0,20}?(20\d{2})\b", re.IGNORECASE)
_BARE_FUTURE_YEAR_RE = re.compile(r"\b(20[2-9]\d)\b")


def extract_handover(text: Optional[str]) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """Return (year, quarter, text) — any may be None.

    'text' is a short canonical phrase: 'ready' | 'Q3 2026' | '2027' | None.
    """
    s = text or ""
    if not s:
        return None, None, None
    low = s.lower()

    # 'ready' should not override an explicit year nearby — try year-with-quarter first.
    m = _QTR_YEAR_RE.search(low) or None
    if m:
        q = int(m.group(1)); y = int(m.group(2))
        if 2020 <= y <= 2099:
            return y, q, f"Q{q} {y}"
    m = _YEAR_QTR_RE.search(low)
    if m:
        y = int(m.group(1)); q = int(m.group(2))
        if 2020 <= y <= 2099:
            return y, q, f"Q{q} {y}"

    # handover/completion/delivery <year>
    m = _HANDOVER_YEAR_RE.search(low)
    if m:
        y = int(m.group(1))
        if 2020 <= y <= 2099:
            return y, None, str(y)

    # off-plan <year>
    m = _OFFPLAN_YEAR_RE.search(low)
    if m:
        y = int(m.group(1))
        if 2020 <= y <= 2099:
            return y, None, str(y)

    # 'ready'
    if _READY_RE.search(low):
        return None, None, "ready"

    return None, None, None


# ── public combined helper ────────────────────────────────────────────────
def extract_extras(text: Optional[str]) -> dict:
    """Run all four extractors. Returns a dict with keys:
    furnishing, view, floor_number, floor_category,
    handover_year, handover_quarter, handover_text.
    """
    furn = extract_furnishing(text)
    view = extract_view(text)
    fn, fc = extract_floor(text)
    hy, hq, ht = extract_handover(text)
    return {
        "furnishing": furn,
        "view": view,
        "floor_number": fn,
        "floor_category": fc,
        "handover_year": hy,
        "handover_quarter": hq,
        "handover_text": ht,
    }


if __name__ == "__main__":
    samples = [
        "FOR SALE! Burj Crown Downtown, 1BR, 585sqft, sea view, high floor, fully furnished, handover Q3 2026, 2.15M",
        "Marina Vista, 2BR, marina view, floor 36, semi-furnished, ready to move in",
        "JVC Bloom Towers, studio, unfurnished, low floor, completion 2027",
        "Penthouse Address Beach Resort, full sea view, handover 2026",
        "Виллa в JVC, меблирована, вид на парк, 3-й этаж, готова к заселению",
    ]
    for s in samples:
        print(s)
        print("  →", extract_extras(s))
