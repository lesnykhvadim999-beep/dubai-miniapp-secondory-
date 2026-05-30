"""Parser v2.0 — LLM-first extraction pipeline.

Stages (per Telegram message):
  1. PRE-SPLIT: ask LLM to split text into independent listing blocks.
     Returns JSON array of block texts.
  2. EXTRACT: for each block, ask LLM to extract structured fields
     (deal_type, property_type, area, building, br, sqft, price, ...).
  3. CANONICALISE: replace area/building aliases with user-friendly names
     from a hardcoded map (Marina → Dubai Marina, JLT → Jumeirah Lake
     Towers etc.).
  4. VERIFY: ask a different LLM to score 0-100 how well the JSON
     reflects the source text. If <60 → flag for review, mark low conf.
  5. CONFIDENCE per field: each field gets its own 0-1 score from
     verification.
"""
from __future__ import annotations
import os
import re
import json
import time
from typing import Optional, List, Dict, Any

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_chain import llm_call  # type: ignore
from parser_v2_extras import extract_extras  # rule-based extras: furnishing, view, floor, handover


# ── Canonical map: aliases → user-friendly Dubai community names ──────────
AREA_CANONICAL = {
    # User shortcuts → full canonical
    "marina": "Dubai Marina",
    "jlt": "Jumeirah Lake Towers",
    "jbr": "Jumeirah Beach Residence",
    "jvc": "Jumeirah Village Circle",
    "jvt": "Jumeirah Village Triangle",
    "downtown": "Downtown Dubai",
    "downtown dubai": "Downtown Dubai",
    "tecom": "Barsha Heights",
    "barsha heights": "Barsha Heights",
    "media city": "Dubai Media City",
    "internet city": "Dubai Internet City",
    "production city": "Dubai Production City",
    "studio city": "Dubai Studio City",
    "sports city": "Dubai Sports City",
    "academic city": "Dubai Academic City",
    "investment park": "Dubai Investment Park",
    "industrial city": "Dubai Industrial City",
    "festival city": "Dubai Festival City",
    "silicon oasis": "Dubai Silicon Oasis",
    "creek harbor": "Dubai Creek Harbour",
    "creek harbour": "Dubai Creek Harbour",
    "south": "Dubai South",
    "dubai south": "Dubai South",
    "hills": "Dubai Hills",
    "dubai hills": "Dubai Hills Estate",
    "dubai hills estate": "Dubai Hills Estate",
    "mbr city": "MBR City",
    "mbr": "MBR City",
    "mohammed bin rashid city": "MBR City",
    "city walk": "City Walk",
    "business bay": "Business Bay",
    "palm": "Palm Jumeirah",
    "palm jumeirah": "Palm Jumeirah",
    "the palm": "Palm Jumeirah",
    # DLD admin → user-friendly
    "marsa dubai": "Dubai Marina",
    "al thanyah fifth": "Jumeirah Lake Towers",
    "al thanyah 5": "Jumeirah Lake Towers",
    "al merkadh": "Meydan",
    "al hebiah fourth": "Dubai Sports City",
    "al hebiah 4": "Dubai Sports City",
    "al hebiah third": "DAMAC Hills",
    "al hebiah 3": "DAMAC Hills",
    "al hebiah fifth": "DAMAC Lagoons",
    "al barsha south fourth": "Jumeirah Village Circle",
    "al barsha south 4": "Jumeirah Village Circle",
    "al barshaa south third": "Jumeirah Village Triangle",
    "al barshaa south 3": "Jumeirah Village Triangle",
    "hadaeq sheikh mohammed bin rashid": "Dubai Hills Estate",
    "burj khalifa": "Downtown Dubai",  # community name, not building
    "al kheeran first": "Dubai Creek Harbour",
    "al khairan first": "Dubai Creek Harbour",
    "al wasl": "Al Wasl",
    "zabeel second": "Zabeel",
    "zaabeel second": "Zabeel",
    "zaa'beel second": "Zabeel",
    "ras al khor industrial area 1": "Ras Al Khor",
    "palm deira": "Deira Islands",
    "saih al salam": "DAMAC Lagoons",
    "wadi al safa 5": "Al Barari",
    "wadi al safa 2": "Dubailand",
    "wadi al safa 3": "Al Barari",
    "wadi al safa 4": "Dubailand",
    "wadi al safa 7": "Al Quoz",
    "liwan1": "Liwan",
    "liwan 1": "Liwan",
    "dmcc master community": "Jumeirah Lake Towers",
    # Spelling variants
    "jumeirah lakes towers": "Jumeirah Lake Towers",
    "jumeriah beach residence": "Jumeirah Beach Residence",
    "jumeriah beach residence  - jbr": "Jumeirah Beach Residence",
    "jumeriah beach residence - jbr": "Jumeirah Beach Residence",
    "dubai harbour": "Dubai Harbour",
    "dubai marina ": "Dubai Marina",
    "dubai island": "Dubai Islands",
    "emaar beachfront": "Emaar Beachfront",
    "bluewaters": "Bluewaters Island",
    "blue waters": "Bluewaters Island",
    "blue water island": "Bluewaters Island",
    # Less common
    "town square": "Town Square Dubai",
    "majan": "Majan",
    "arjan": "Arjan",
    "discovery gardens": "Discovery Gardens",
    "international city": "International City",
    "ic": "International City",
    "dso": "Dubai Silicon Oasis",
    "dlrc": "Dubailand Residence Complex",
    "dlcr": "Dubailand Residence Complex",
    "dubailand residence complex": "Dubailand Residence Complex",
}


def canonicalise_area(area: Optional[str]) -> Optional[str]:
    if not area:
        return area
    key = area.strip().lower()
    # Direct lookup
    if key in AREA_CANONICAL:
        return AREA_CANONICAL[key]
    # Try stripping common suffixes like "1", "phase 1"
    stripped = re.sub(r"\s*(phase\s*)?\d+\s*$", "", key).strip()
    if stripped and stripped in AREA_CANONICAL:
        return AREA_CANONICAL[stripped]
    return area.strip()


# ── Prompts ───────────────────────────────────────────────────────────────
SPLIT_PROMPT = """You are reading a Telegram real-estate broadcast.
The same message MAY contain multiple separate property listings,
or it may be a single listing, or it may be junk/spam.

Source text:
\"\"\"
{text}
\"\"\"

Return a single JSON object describing what is in this message:
{{
  "is_spam": true|false,
  "listings": ["<verbatim text of listing 1>", "<verbatim text of listing 2>", ...]
}}

Rules:
- If the whole text is one listing → put it as the only element of the array.
- If multiple listings, split them by a sensible boundary (line of dashes,
  emoji separator, "FOR RENT!" repeating, blank line between buildings).
- If it's spam, buyer-request, "looking for", broker ad, recruitment,
  car ad, hotel ad → is_spam=true and "listings": [].
- If it's a Dubai property listing → is_spam=false.

Output ONLY the JSON object, no prose, no markdown."""

EXTRACT_PROMPT = """Extract structured data from this Dubai property listing
text. Some fields may be missing — return null for missing.

Listing text:
\"\"\"
{text}
\"\"\"

Return a single JSON object with exactly these keys:
{{
  "deal_type": "sale" | "rent" | null,
  "property_type": "apartment" | "studio" | "villa" | "townhouse" | "penthouse" | "duplex" | "office" | "shop" | "retail" | "warehouse" | "hotel" | "hotel_apartment" | "serviced_apartment" | "commercial" | "plot" | "land" | "whole_building" | null,
  "emirate": "Dubai" | "Abu Dhabi" | "Sharjah" | "Ras Al Khaimah" | "Ajman" | "Umm Al Quwain" | "Fujairah" | null,
  "area": "<user-friendly community name like 'Dubai Marina', 'Downtown Dubai', 'JVC'>" | null,
  "building": "<exact building/project name>" | null,
  "bedrooms": 0..10 | null,
  "bathrooms": 0..10 | null,
  "size_sqft": <integer or float> | null,
  "size_sqm": <integer or float> | null,
  "floor": <integer> | null,
  "view": "<sea|park|burj|marina|city|...>" | null,
  "furnishing": "furnished" | "unfurnished" | "semi-furnished" | null,
  "is_off_plan": true | false | null,
  "handover_date": "YYYY-MM" | null,
  "price": <integer AED, NO decimal places> | null,
  "currency": "AED" | "USD" | null,
  "price_period": "year" | "month" | null,
  "agent_name": "<name>" | null,
  "phone": "<+971...>" | null
}}

Rules:
- For studio: bedrooms = 0.
- "Apartment" / "flat" / "BHK" / "BR" without other type word → property_type=apartment.
- For "AED 50k/year" set price=50000, currency=AED, price_period=year, deal_type=rent.
- For rent prices >5M AED → deal_type is likely sale, set deal_type=sale.
- For sale prices <100K AED → likely rent (annual), set deal_type=rent.
- Area should be the USER-FRIENDLY community name buyers actually search
  for ("Dubai Marina", NOT "Marsa Dubai"; "JLT" or "Jumeirah Lake Towers",
  NOT "Al Thanyah Fifth"; "Dubai Hills Estate", NOT
  "Hadaeq Sheikh Mohammed Bin Rashid").

Output ONLY the JSON, no prose, no markdown."""

VERIFY_PROMPT = """You are an extraction-quality checker.

Source text:
\"\"\"
{text}
\"\"\"

Extracted fields:
{fields_json}

For each field, judge whether the extracted value is:
- "ok"      → correct given the source text
- "wrong"   → incorrect or misleading
- "missing" → the source text does not specify this field
  (it's OK that the extractor returned null)

Return JSON with the same keys as the extracted fields, with one of
"ok"/"wrong"/"missing" for each. Output ONLY JSON."""


_NUM_SUFFIX = {"k": 1_000, "K": 1_000, "m": 1_000_000, "M": 1_000_000,
                "mn": 1_000_000, "MN": 1_000_000, "mln": 1_000_000,
                "b": 1_000_000_000, "B": 1_000_000_000}


def _coerce_number(v, *, integer: bool = False) -> Optional[float]:
    """Robust number coercion. Handles: '2.15M', '1,500,000', '50k',
    '1200 sqft', 1500000, 2.5, None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v) if integer else float(v)
    if not isinstance(v, str):
        return None
    s = v.strip().replace(",", "")
    # strip currency prefix and trailing unit words
    s = re.sub(r"^\s*(aed|usd|eur|\$|€)\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*(aed|usd|eur|sqft|sq\.?\s*ft|sqm|sq\.?\s*m|sm|"
               r"/year|/month|/yr|/mo|годовая|per\s+year|per\s+month)\s*$",
               "", s, flags=re.IGNORECASE)
    s = s.replace(" ", "").strip().lstrip("$")
    # match number + optional suffix M/K/B
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)([KkMmBb]|mn|MN|mln)?", s)
    if not m:
        return None
    n = float(m.group(1))
    suffix = m.group(2)
    if suffix:
        n *= _NUM_SUFFIX.get(suffix, 1)
    return int(n) if integer else n


def _coerce_bedrooms(v) -> Optional[int]:
    """Handles 'studio', '1', 1, '2BR', '3 bed', '3+1', '2.5'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    if "studio" in s or s == "0":
        return 0
    # Match leading number
    m = re.match(r"(\d+)(?:\+\d+)?", s)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def _strip_to_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    s = raw.strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", s, re.DOTALL)
        if m:
            s = m.group(1)
    # find outermost balanced JSON
    for opener, closer in (("{", "}"), ("[", "]")):
        i = s.find(opener)
        j = s.rfind(closer)
        if 0 <= i < j:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                pass
    return None


# ── Pipeline ──────────────────────────────────────────────────────────────
def split_message(text: str) -> tuple[bool, list[str]]:
    """Split a Telegram message into listing-blocks via LLM.

    Returns (is_spam, list_of_block_texts)."""
    prompt = SPLIT_PROMPT.format(text=(text or "")[:3000])
    raw = llm_call(prompt, max_tokens=900, timeout=15)
    parsed = _strip_to_json(raw or "")
    text_clean = (text or "").strip()
    fallback_blocks = [text_clean] if len(text_clean) > 20 else []
    if not isinstance(parsed, dict):
        # Fallback: treat as single block (LLM failed)
        return (False, fallback_blocks) if fallback_blocks else (True, [])
    if parsed.get("is_spam"):
        return True, []
    blocks = parsed.get("listings") or []
    if not isinstance(blocks, list):
        return False, fallback_blocks
    blocks = [b for b in blocks if isinstance(b, str) and len(b.strip()) > 15]
    if not blocks:
        # LLM said not spam but gave no blocks → use the full text
        return (False, fallback_blocks) if fallback_blocks else (True, [])
    return False, blocks


def extract_block(block: str) -> Optional[dict]:
    """Extract structured fields from a single listing block."""
    if not block or len(block.strip()) < 15:
        return None
    prompt = EXTRACT_PROMPT.format(text=block[:2000])
    # Retry up to 2 times on JSON parse failure (different LLM may succeed)
    parsed = None
    for attempt in range(2):
        raw = llm_call(prompt, max_tokens=500, timeout=20)
        parsed = _strip_to_json(raw or "")
        if isinstance(parsed, dict):
            break
        time.sleep(0.5)
    if not isinstance(parsed, dict):
        return None
    # Canonicalise area
    parsed["area"] = canonicalise_area(parsed.get("area"))
    # Canonicalise building (strip trailing whitespace, normalize spaces)
    if parsed.get("building"):
        b = str(parsed["building"]).strip()
        b = re.sub(r"\s+", " ", b)
        # strip leading "tower" / "the " duplications
        parsed["building"] = b if b else None
    # Robust numeric coercion
    parsed["bedrooms"] = _coerce_bedrooms(parsed.get("bedrooms"))
    parsed["bathrooms"] = _coerce_bedrooms(parsed.get("bathrooms"))
    parsed["size_sqft"] = _coerce_number(parsed.get("size_sqft"))
    parsed["size_sqm"] = _coerce_number(parsed.get("size_sqm"))
    parsed["price"] = _coerce_number(parsed.get("price"), integer=True)
    parsed["floor"] = _coerce_number(parsed.get("floor"), integer=True)
    # Cross-fill size_sqft from sqm if missing (1 sqm = 10.7639 sqft)
    if parsed.get("size_sqft") is None and parsed.get("size_sqm"):
        parsed["size_sqft"] = round(parsed["size_sqm"] * 10.7639, 1)
    elif parsed.get("size_sqm") is None and parsed.get("size_sqft"):
        parsed["size_sqm"] = round(parsed["size_sqft"] / 10.7639, 1)
    # Sanity: rent >5M → flip to sale, sale <100K → flip to rent
    p = parsed.get("price")
    if p is not None:
        if parsed.get("deal_type") == "rent" and p > 5_000_000:
            parsed["deal_type"] = "sale"
        elif parsed.get("deal_type") == "sale" and 30_000 < p < 200_000:
            parsed["deal_type"] = "rent"
    # Studio rule: bedrooms 0 + has size → property_type=studio
    if parsed.get("bedrooms") == 0 and not parsed.get("property_type"):
        parsed["property_type"] = "studio"
    return parsed


def verify_extraction(block: str, fields: dict) -> dict:
    """Ask a second LLM to grade each field as ok/wrong/missing."""
    prompt = VERIFY_PROMPT.format(text=block[:1500],
                                    fields_json=json.dumps(fields, ensure_ascii=False))
    raw = llm_call(prompt, max_tokens=300, timeout=15)
    parsed = _strip_to_json(raw or "") or {}
    return parsed if isinstance(parsed, dict) else {}


def confidence_from_verdict(verdict: dict, fields: Optional[dict] = None) -> dict:
    """Map verdict dict to 0-1 confidence per field.

    Smarter: 'missing' verdict on a NULL field = 1.0 (LLM agrees nothing there).
    'missing' verdict on a non-null field = 0.0 (false-positive extraction)."""
    out: dict = {}
    for k, v in verdict.items():
        v_lc = str(v or "").lower()
        if v_lc == "ok":
            out[k] = 1.0
        elif v_lc == "missing":
            # If we returned null and LLM says missing → high confidence agreement
            if fields and fields.get(k) is None:
                out[k] = 1.0
            else:
                # We returned something but LLM says source doesn't have it → likely wrong
                out[k] = 0.0
        elif v_lc == "wrong":
            out[k] = 0.0
        else:
            out[k] = 0.5
    return out


def parse_message_v2(text: str, source_channel: str | None = None) -> list[dict]:
    """Full v2 pipeline. Returns list of extracted listing dicts, one per
    block. Each dict has extracted fields + 'v2_field_conf' per-field
    confidence + 'v2_block_index' position in the source message."""
    out: list[dict] = []
    is_spam, blocks = split_message(text or "")
    if is_spam:
        return out
    for i, block in enumerate(blocks):
        fields = extract_block(block)
        if not fields:
            continue
        # Only verify if at least price OR (building+area) extracted — saves quota
        verdict = {}
        important_present = bool(fields.get("price") or
                                  (fields.get("building") and fields.get("area")))
        if important_present:
            verdict = verify_extraction(block, fields)
        conf = confidence_from_verdict(verdict, fields) if verdict else {}
        fields["v2_field_conf"] = conf
        fields["v2_block_index"] = i
        fields["v2_total_blocks"] = len(blocks)
        fields["v2_source_block_text"] = block[:1500]
        fields["v2_source_channel"] = source_channel
        # ── Rule-based extras: furnishing / view / floor / handover ──
        # Don't overwrite anything the LLM already produced; just fill gaps
        # and populate the dedicated extras columns.
        try:
            extras = extract_extras(block)
        except Exception:
            extras = {}
        if extras:
            if not fields.get("furnishing") and extras.get("furnishing"):
                fields["furnishing"] = extras["furnishing"]
            if not fields.get("view") and extras.get("view"):
                fields["view"] = extras["view"]
            if not fields.get("floor") and extras.get("floor_number") is not None:
                fields["floor"] = extras["floor_number"]
            fields["floor_number"] = extras.get("floor_number")
            fields["floor_category"] = extras.get("floor_category")
            fields["handover_year"] = extras.get("handover_year")
            fields["handover_quarter"] = extras.get("handover_quarter")
            fields["handover_text"] = extras.get("handover_text")
            if not fields.get("handover_date") and extras.get("handover_year"):
                y = extras["handover_year"]; q = extras.get("handover_quarter")
                month = {1: "03", 2: "06", 3: "09", 4: "12"}.get(q or 0, "12")
                fields["handover_date"] = f"{y}-{month}"
        out.append(fields)
    return out


if __name__ == "__main__":
    # quick smoke test
    sample = """FOR SALE!
Burj Crown Downtown
1 bedroom
585 sqft
Sea view
Price 2.15M

——————

FOR RENT!
Marina Vista, Dubai Marina
2 BR
Furnished, 1200 sqft
180k AED/year"""
    import pprint
    pprint.pprint(parse_message_v2(sample, source_channel="test"))
