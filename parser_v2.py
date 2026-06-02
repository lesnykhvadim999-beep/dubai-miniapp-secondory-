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

TWO-PASS MODE (env PARSER_V2_TWO_PASS=1, OFF by default):
  Replaces step 2 with:
    2a. CLASSIFY: cheap small-prompt LLM call → {deal_type, property_type}
    2b. EXTRACT-TYPED: dispatch to a focused extractor whose prompt is
        tuned for that property class (apartment / villa / commercial /
        land) with type-specific few-shot examples.
  This improves accuracy on villas and commercial properties whose
  vocabulary differs from generic apartment listings.
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

# Feature flag — off by default, enable with PARSER_V2_TWO_PASS=1
TWO_PASS_ENABLED = os.getenv("PARSER_V2_TWO_PASS", "0").strip() in ("1", "true", "yes", "on")

# Active learning loop (#9) — optional few-shot injection. Imports lazily
# so environments without DB / module still parse normally.
try:
    from active_learning import (
        is_enabled as _al_enabled,
        pick_few_shot_examples as _al_pick,
        format_few_shot_prompt as _al_format,
    )
except Exception:
    def _al_enabled() -> bool:  # type: ignore
        return False
    def _al_pick(*a, **k):  # type: ignore
        return []
    def _al_format(text, ex):  # type: ignore
        return f'"""\n{(text or "")[:2000]}\n"""'


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


# Extended maps (autogenerated): 505 areas + 1997 buildings from DLD
try:
    from area_canonical_extended import AREA_CANONICAL_EXTENDED as _AREA_EXT  # type: ignore
except Exception:
    _AREA_EXT = {}

try:
    from building_canonical import BUILDING_TO_AREA as _BLD_TO_AREA  # type: ignore
except Exception:
    _BLD_TO_AREA = {}


def canonicalise_area(area: Optional[str]) -> Optional[str]:
    if not area:
        return area
    key = area.strip().lower()
    if key in AREA_CANONICAL:
        return AREA_CANONICAL[key]
    if key in _AREA_EXT:
        return _AREA_EXT[key]
    stripped = re.sub(r"\s*(phase\s*)?\d+\s*$", "", key).strip()
    if stripped:
        if stripped in AREA_CANONICAL:
            return AREA_CANONICAL[stripped]
        if stripped in _AREA_EXT:
            return _AREA_EXT[stripped]
    return area.strip()


def canonicalise_building(building: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Returns (canonical_building, area_from_dld). Area=None if no DLD match."""
    if not building:
        return (building, None)
    key = re.sub(r"\s+", " ", building.strip().lower())
    info = _BLD_TO_AREA.get(key)
    if info:
        return (info.get("name") or building.strip(), info.get("area") or None)
    stripped = re.sub(r"\s+(tower\s+)?[abcivx\d]+$", "", key).strip()
    if stripped and stripped != key:
        info = _BLD_TO_AREA.get(stripped)
        if info:
            return (info.get("name") or building.strip(), info.get("area") or None)
    return (building.strip(), None)


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
  "price": <integer AED — the CURRENT asking/selling price> | null,
  "price_original": <integer AED — launch/developer/original price if different from current> | null,
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
- PRICE FIELD = the CURRENT asking/selling/distress/discounted price (what
  the buyer actually pays today). NEVER use the "original price", "launch
  price", "developer price", "OP", or "list price" for the `price` field —
  those go to `price_original` instead.
- When both are present (e.g. "Original 5.4M / Selling 4.9M") → `price=4900000`,
  `price_original=5400000`. When only one price → `price=<that price>`,
  `price_original=null`.
- Numbers like "22.8 m" / "1.3 m" with lowercase trailing m mean MILLION AED.
  Cyrillic "1.5 млн" / "1,5 М" also mean MILLION. So "22.8 m" → price=22800000.
- "Verdana Townhouses • 3BR from 1.91M / 4BR from 2.26M" — this is a price
  RANGE across multiple unit configs. Use the LOWEST price (1910000), set
  bedrooms=null (mixed configs), set property_type from the listed type.
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


# ── Two-pass prompts (PASS 1 = classify, PASS 2 = focused extractor) ──────
CLASSIFY_PROMPT = """You classify a Dubai property listing snippet.

Listing text:
\"\"\"
{text}
\"\"\"

Return ONLY this JSON object (no markdown, no prose):
{{
  "deal_type": "sale" | "rent" | null,
  "property_type": "apartment" | "studio" | "penthouse" | "duplex" | "villa" | "townhouse" | "office" | "shop" | "retail" | "warehouse" | "plot" | "whole_building" | null,
  "confidence": <0..1 float>
}}

Heuristics:
- "BR"/"BHK"/"flat"/"apartment" with floor or tower → apartment.
- "studio" → studio.
- "penthouse"/"PH" → penthouse.
- "duplex"/"loft" → duplex.
- "villa"/"compound"/"standalone"/"G+1"/"G+2" with bedrooms+garden/plot → villa.
- "townhouse"/"TH" → townhouse.
- "office"/"fitted"/"shell & core"/"commercial floor" → office.
- "shop"/"showroom"/"retail"/"F&B" → shop or retail.
- "warehouse"/"labour camp"/"staff accommodation" → warehouse.
- "plot"/"land"/"freehold land"/"G+N" without bedrooms → plot.
- "whole building"/"full building"/"G+X residential" tower for sale → whole_building.
- Price like "AED 50k/year", "monthly", "p.a." → rent. Big lump sum no period → sale.
- If genuinely unclear, set confidence < 0.5 and pick best guess."""


# Common JSON schema fragment — shared by all pass2 extractors so output
# stays backward-compatible with the single-pass extractor.
_COMMON_SCHEMA_TAIL = """
  "emirate": "Dubai" | "Abu Dhabi" | "Sharjah" | "Ras Al Khaimah" | "Ajman" | "Umm Al Quwain" | "Fujairah" | null,
  "area": "<user-friendly community name>" | null,
  "building": "<exact building/project name>" | null,
  "bedrooms": 0..10 | null,
  "bathrooms": 0..10 | null,
  "size_sqft": <number> | null,
  "size_sqm": <number> | null,
  "floor": <integer> | null,
  "view": "<sea|park|burj|marina|city|garden|pool|golf|...>" | null,
  "furnishing": "furnished" | "unfurnished" | "semi-furnished" | null,
  "is_off_plan": true | false | null,
  "handover_date": "YYYY-MM" | null,
  "price": <integer AED, NO decimals> | null,
  "currency": "AED" | "USD" | null,
  "price_period": "year" | "month" | null,
  "agent_name": "<name>" | null,
  "phone": "<+971...>" | null
}}

Rules:
- Area must be the USER-FRIENDLY community name buyers search for
  ("Dubai Marina" NOT "Marsa Dubai"; "JLT" NOT "Al Thanyah Fifth";
   "Dubai Hills Estate" NOT "Hadaeq Sheikh Mohammed Bin Rashid").
- "AED 120k/year" → price=120000, currency=AED, price_period=year, deal_type=rent.
- Rent prices > 5M AED → likely a sale, set deal_type=sale.
- Sale prices < 100k AED → likely annual rent, set deal_type=rent.
- Return null for any field you cannot confidently fill.

Output ONLY the JSON, no prose, no markdown."""


APARTMENT_EXTRACT_PROMPT = """Extract data from this Dubai APARTMENT/STUDIO/
PENTHOUSE/DUPLEX listing. Use apartment vocabulary: BHK/BR, sqft, tower,
floor, unit, view (sea/marina/burj/park/community).

Listing text:
\"\"\"
{text}
\"\"\"

Known so far:
- deal_type: {deal_type}
- property_type: {property_type}

Few-shot examples (study the mapping carefully):

EX1 input:  "Marina Vista, Dubai Marina | 2 BR | 1,200 sqft | Furnished | Floor 35 | Sea view | AED 180k/year"
EX1 output: {{"deal_type":"rent","property_type":"apartment","area":"Dubai Marina","building":"Marina Vista","bedrooms":2,"size_sqft":1200,"floor":35,"view":"sea","furnishing":"furnished","price":180000,"currency":"AED","price_period":"year"}}

EX2 input:  "Studio 415 sqft, Park Heights T1, Dubai Hills Estate, Sale 950,000 AED, vacant on transfer"
EX2 output: {{"deal_type":"sale","property_type":"studio","area":"Dubai Hills Estate","building":"Park Heights T1","bedrooms":0,"size_sqft":415,"price":950000,"currency":"AED"}}

EX3 input:  "Penthouse for sale | Five Palm Jumeirah | 4 BR + maid | 5,200 sqft | Full Sea View | Floor 20 (top) | AED 22,500,000"
EX3 output: {{"deal_type":"sale","property_type":"penthouse","area":"Palm Jumeirah","building":"Five Palm Jumeirah","bedrooms":4,"size_sqft":5200,"floor":20,"view":"sea","price":22500000,"currency":"AED"}}

EX4 input:  "Burj Royale Downtown, 1BHK, 720 sqft, Burj view, off-plan handover Q3 2026, AED 2.15M"
EX4 output: {{"deal_type":"sale","property_type":"apartment","area":"Downtown Dubai","building":"Burj Royale","bedrooms":1,"size_sqft":720,"view":"burj","is_off_plan":true,"handover_date":"2026-09","price":2150000,"currency":"AED"}}

EX5 input:  "Duplex 3BR Bluewaters | Bluewaters Residences B7 | 2,850 sqft | Marina + Sea View | 6.8M AED"
EX5 output: {{"deal_type":"sale","property_type":"duplex","area":"Bluewaters Island","building":"Bluewaters Residences B7","bedrooms":3,"size_sqft":2850,"view":"marina","price":6800000,"currency":"AED"}}

Now extract from the listing above. Return JSON object with these keys:
{{
  "deal_type": "sale" | "rent" | null,
  "property_type": "apartment" | "studio" | "penthouse" | "duplex" | null,""" + _COMMON_SCHEMA_TAIL


VILLA_EXTRACT_PROMPT = """Extract data from this Dubai VILLA/TOWNHOUSE
listing. Use villa vocabulary: BUA (built-up area), plot size, G+1/G+2
levels, community/cluster, garden/pool/golf view, maid/driver rooms.

Listing text:
\"\"\"
{text}
\"\"\"

Known so far:
- deal_type: {deal_type}
- property_type: {property_type}

NOTE FOR VILLAS:
- "BUA" or "built-up" = size_sqft (the house itself).
- "Plot" / "plot size" = LAND parcel size. If both BUA and Plot given,
  use BUA for size_sqft and put plot size in "plot_sqft".
- "G+1" means ground + 1 upper floor (set floor=null, this is a layout
  not a floor number).
- Communities like "Arabian Ranches", "Mudon", "Tilal Al Ghaf",
  "DAMAC Hills", "The Springs", "Jumeirah Golf Estates" = area.
  The cluster/phase name = building.

Few-shot examples:

EX1 input:  "Standalone villa | Tilal Al Ghaf, Harmony 2 | 5BR + maid + driver | BUA 6,200 sqft | Plot 7,800 sqft | Park view | G+1 | AED 14.5M"
EX1 output: {{"deal_type":"sale","property_type":"villa","area":"Tilal Al Ghaf","building":"Harmony 2","bedrooms":5,"size_sqft":6200,"plot_sqft":7800,"view":"park","price":14500000,"currency":"AED"}}

EX2 input:  "Townhouse, Mudon Al Ranim 4, 3BR + study, BUA 1,950 sqft, Plot 1,800 sqft, single row, AED 3.2M"
EX2 output: {{"deal_type":"sale","property_type":"townhouse","area":"Mudon","building":"Al Ranim 4","bedrooms":3,"size_sqft":1950,"plot_sqft":1800,"price":3200000,"currency":"AED"}}

EX3 input:  "4BR villa for rent | Arabian Ranches 2, Camelia | 4,100 sqft BUA | landscaped garden | maid + driver | AED 285,000/year"
EX3 output: {{"deal_type":"rent","property_type":"villa","area":"Arabian Ranches 2","building":"Camelia","bedrooms":4,"size_sqft":4100,"view":"garden","price":285000,"currency":"AED","price_period":"year"}}

EX4 input:  "DAMAC Hills 2 / Aknan villas / 3BR townhouse / 2,150 sqft / community pool view / 1.85M AED"
EX4 output: {{"deal_type":"sale","property_type":"townhouse","area":"DAMAC Hills 2","building":"Aknan","bedrooms":3,"size_sqft":2150,"view":"pool","price":1850000,"currency":"AED"}}

EX5 input:  "Jumeirah Golf Estates – Sienna Views – 6BR villa – BUA 8,400 sqft – Plot 11,500 sqft – Full Golf Course View – AED 28,000,000"
EX5 output: {{"deal_type":"sale","property_type":"villa","area":"Jumeirah Golf Estates","building":"Sienna Views","bedrooms":6,"size_sqft":8400,"plot_sqft":11500,"view":"golf","price":28000000,"currency":"AED"}}

Now extract. Return JSON with these keys:
{{
  "deal_type": "sale" | "rent" | null,
  "property_type": "villa" | "townhouse" | null,
  "plot_sqft": <number> | null,""" + _COMMON_SCHEMA_TAIL


COMMERCIAL_EXTRACT_PROMPT = """Extract data from this Dubai COMMERCIAL
property listing (office / shop / retail / warehouse). Use commercial
vocabulary: fitted vs shell-and-core, parking bays, business district,
DED licence zone, free zone, ceiling height (warehouses), F&B-ready.

Listing text:
\"\"\"
{text}
\"\"\"

Known so far:
- deal_type: {deal_type}
- property_type: {property_type}

NOTE FOR COMMERCIAL:
- "Fitted" / "Shell & core" / "Semi-fitted" → furnishing field.
  Map: fitted → "furnished", shell & core → "unfurnished",
  semi-fitted → "semi-furnished".
- bedrooms is almost always null for commercial — DO NOT invent it.
- Common areas: Business Bay, DIFC, JLT, Barsha Heights (TECOM),
  Sheikh Zayed Road, Deira, Al Quoz, DSO, JAFZA, Ras Al Khor.

Few-shot examples:

EX1 input:  "Fitted office | One by Omniyat, Business Bay | 1,850 sqft | 2 parking | Sea + Burj view | AED 380,000/year"
EX1 output: {{"deal_type":"rent","property_type":"office","area":"Business Bay","building":"One by Omniyat","size_sqft":1850,"view":"sea","furnishing":"furnished","price":380000,"currency":"AED","price_period":"year"}}

EX2 input:  "Shell & core office, ICD Brookfield Place, DIFC, 4,200 sqft, AED 6.3M for sale"
EX2 output: {{"deal_type":"sale","property_type":"office","area":"DIFC","building":"ICD Brookfield Place","size_sqft":4200,"furnishing":"unfurnished","price":6300000,"currency":"AED"}}

EX3 input:  "Retail shop for rent | The Walk JBR, ground floor | 620 sqft | F&B licence ready | AED 320,000/year"
EX3 output: {{"deal_type":"rent","property_type":"shop","area":"Jumeirah Beach Residence","building":"The Walk JBR","size_sqft":620,"floor":0,"price":320000,"currency":"AED","price_period":"year"}}

EX4 input:  "Warehouse Al Quoz Industrial 3, 8,500 sqft, 8m ceiling, 3-phase power, AED 45/sqft yearly"
EX4 output: {{"deal_type":"rent","property_type":"warehouse","area":"Al Quoz","building":null,"size_sqft":8500,"price":382500,"currency":"AED","price_period":"year"}}

EX5 input:  "Showroom for sale | Sheikh Zayed Road | Tower X ground floor | 1,200 sqft | AED 7.5M"
EX5 output: {{"deal_type":"sale","property_type":"shop","area":"Sheikh Zayed Road","building":"Tower X","size_sqft":1200,"floor":0,"price":7500000,"currency":"AED"}}

Now extract. Return JSON with these keys:
{{
  "deal_type": "sale" | "rent" | null,
  "property_type": "office" | "shop" | "retail" | "warehouse" | null,""" + _COMMON_SCHEMA_TAIL


LAND_EXTRACT_PROMPT = """Extract data from this Dubai PLOT/LAND listing.
Use land vocabulary: plot size (sqft/sqm), G+N permission, freehold vs
leasehold, residential/commercial/mixed-use zoning, FAR ratio.

Listing text:
\"\"\"
{text}
\"\"\"

Known so far:
- deal_type: {deal_type}
- property_type: {property_type}

NOTE FOR LAND:
- bedrooms, bathrooms, floor, view, furnishing → almost always null. DO NOT invent.
- "Plot" / "Land" size → size_sqft (or size_sqm if metric).
- "Freehold" / "Leasehold" → "tenure" field.
- "Residential" / "Commercial" / "Mixed-use" / "Hotel" → "zoning".

Few-shot examples:

EX1 input:  "Residential plot, MBR City District 11, 12,500 sqft, G+4 permission, freehold, AED 18M"
EX1 output: {{"deal_type":"sale","property_type":"plot","area":"MBR City","building":"District 11","size_sqft":12500,"zoning":"residential","tenure":"freehold","price":18000000,"currency":"AED"}}

EX2 input:  "Commercial land for sale | Business Bay | 25,000 sqft | G+30 | AED 95M"
EX2 output: {{"deal_type":"sale","property_type":"plot","area":"Business Bay","size_sqft":25000,"zoning":"commercial","price":95000000,"currency":"AED"}}

EX3 input:  "Plot Al Furjan | 8,200 sqft | G+1 villa permission | freehold | AED 4.1M"
EX3 output: {{"deal_type":"sale","property_type":"plot","area":"Al Furjan","size_sqft":8200,"zoning":"residential","tenure":"freehold","price":4100000,"currency":"AED"}}

EX4 input:  "Hotel plot, Jumeirah Beach Road, 45,000 sqft, mixed-use G+15, AED 220M"
EX4 output: {{"deal_type":"sale","property_type":"plot","area":"Jumeirah","size_sqft":45000,"zoning":"mixed-use","price":220000000,"currency":"AED"}}

EX5 input:  "Land for sale | Dubai South Residential | 6,500 sqft | G+1 | freehold | AED 1.85M"
EX5 output: {{"deal_type":"sale","property_type":"plot","area":"Dubai South","size_sqft":6500,"zoning":"residential","tenure":"freehold","price":1850000,"currency":"AED"}}

Now extract. Return JSON with these keys:
{{
  "deal_type": "sale" | "rent" | null,
  "property_type": "plot" | "whole_building" | null,
  "zoning": "residential" | "commercial" | "mixed-use" | "hotel" | "industrial" | null,
  "tenure": "freehold" | "leasehold" | null,""" + _COMMON_SCHEMA_TAIL


_NUM_SUFFIX = {"k": 1_000, "K": 1_000, "к": 1_000, "К": 1_000,
                "m": 1_000_000, "M": 1_000_000, "м": 1_000_000, "М": 1_000_000,
                "mn": 1_000_000, "MN": 1_000_000, "mln": 1_000_000,
                "млн": 1_000_000, "млна": 1_000_000, "миллион": 1_000_000,
                "b": 1_000_000_000, "B": 1_000_000_000, "млрд": 1_000_000_000}


def _coerce_number(v, *, integer: bool = False) -> Optional[float]:
    """Robust number coercion. Handles:
       '2.15M', '1,500,000', '50k', '1200 sqft',
       'AED 22.8 m' (lowercase m with space),
       '1.5 млн' / '1,5 М' (Cyrillic suffix),
       '$3.5M', '1.2M per year'."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v) if integer else float(v)
    if not isinstance(v, str):
        return None
    # Normalize: trim, comma → dot for decimal (RU style "1,5"), remove thousand-grouping commas
    s = v.strip()
    # If multiple commas → thousands; if single comma surrounded by digits → decimal separator
    if s.count(",") == 1 and re.match(r"^\d+,\d{1,3}(?!\d)\s", s + " "):
        # Looks like "1,5" decimal — convert to dot
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    # Strip currency prefix and trailing unit words (EN + RU)
    s = re.sub(r"^\s*(aed|usd|eur|\$|€|د\.إ)\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*(aed|usd|eur|sqft|sq\.?\s*ft|sqm|sq\.?\s*m|sm|"
               r"/year|/month|/yr|/mo|годовая|год\.?|"
               r"per\s+year|per\s+month|в\s+год|в\s+месяц)\s*$",
               "", s, flags=re.IGNORECASE)
    s = s.replace(" ", "").replace(" ", "").replace(" ", "").strip().lstrip("$")
    # Match number + optional suffix (M/K/B Latin + Cyrillic М/К/млн/млрд)
    m = re.fullmatch(
        r"(-?\d+(?:\.\d+)?)"
        r"(млна?|млн|миллионов?|млрд|mn|MN|mln|[KkMmBbКкМм])?", s)
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
try:
    from split_helpers import regex_split, collapse_duplicate_blocks  # type: ignore
    _HAS_SPLIT_HELPERS = True
except Exception:
    _HAS_SPLIT_HELPERS = False


def _llm_split(text: str) -> tuple[bool, list[str]]:
    """LLM-only split (legacy path)."""
    prompt = SPLIT_PROMPT.format(text=(text or "")[:3000])
    raw = llm_call(prompt, max_tokens=900, timeout=15)
    parsed = _strip_to_json(raw or "")
    text_clean = (text or "").strip()
    fallback_blocks = [text_clean] if len(text_clean) > 20 else []
    if not isinstance(parsed, dict):
        return (False, fallback_blocks) if fallback_blocks else (True, [])
    if parsed.get("is_spam"):
        return True, []
    blocks = parsed.get("listings") or []
    if not isinstance(blocks, list):
        return False, fallback_blocks
    blocks = [b for b in blocks if isinstance(b, str) and len(b.strip()) > 15]
    if not blocks:
        return (False, fallback_blocks) if fallback_blocks else (True, [])
    return False, blocks


def split_message(text: str) -> tuple[bool, list[str]]:
    """Split a Telegram message into listing-blocks.

    Strategy:
      1. Regex pre-pass for explicit separators (FOR SALE!, ===, numbered,
         inline "5BR HAVEN 12M | 4BR PALM 8M"). Fast, no LLM, robust.
      2. If regex finds 1 block but text is suspicious of multi → LLM split.
      3. LLM split for spam classification on short/single-block text.
      4. POST: collapse blocks that have identical building+price signature
         (over-split detection).

    Returns (is_spam, list_of_block_texts)."""
    if not text or not text.strip():
        return (True, [])

    text_clean = text.strip()

    # 1. Regex pre-pass
    if _HAS_SPLIT_HELPERS:
        blocks = regex_split(text_clean)
        if len(blocks) >= 2:
            # Post-collapse: undo over-split (same building+price → merge)
            blocks = collapse_duplicate_blocks(blocks)
            if len(blocks) >= 2:
                return (False, blocks)

    # 2. Check if single-block text looks suspicious of multi-listing
    suspicious = (
        len(text_clean) > 500 and (
            text_clean.lower().count("for sale") >= 2 or
            text_clean.lower().count("for rent") >= 2 or
            text_clean.count("AED") + text_clean.lower().count("aed") >= 3
        )
    )

    # 3. LLM split (always for spam classification, or if suspicious)
    return _llm_split(text)


def _build_extract_prompt(block: str) -> str:
    """Build EXTRACT prompt, optionally prepending few-shot examples
    learned from admin corrections (active learning loop, #9). Capped at
    3 examples (~600 tokens). OFF by default — gated by
    ACTIVE_LEARNING_ENABLED env var."""
    text_for_prompt = block[:2000]
    if _al_enabled():
        try:
            picked = _al_pick(text_for_prompt, n=3)
            if picked:
                # _al_format returns the body with examples + final
                # `"""<text>"""` placeholder, ready to drop into
                # EXTRACT_PROMPT where {text} was. We replace the
                # triple-quoted text block in EXTRACT_PROMPT.
                body = _al_format(text_for_prompt, picked)
                # EXTRACT_PROMPT wraps text in its own """ """ — strip
                # those wrappers and feed body verbatim.
                tpl = EXTRACT_PROMPT.replace(
                    '"""\n{text}\n"""', "{text}"
                )
                return tpl.format(text=body)
        except Exception as e:
            print(f"[parser_v2/al] few-shot err: {e}")
    return EXTRACT_PROMPT.format(text=text_for_prompt)


def extract_block(block: str) -> Optional[dict]:
    """Extract structured fields from a single listing block."""
    if not block or len(block.strip()) < 15:
        return None
    prompt = _build_extract_prompt(block)
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
    parsed["price_original"] = _coerce_number(parsed.get("price_original"), integer=True)
    parsed["floor"] = _coerce_number(parsed.get("floor"), integer=True)
    # Discount % if both prices present
    p_now = parsed.get("price")
    p_orig = parsed.get("price_original")
    if p_now and p_orig and p_orig > 0 and p_orig != p_now:
        parsed["discount_pct"] = round((p_orig - p_now) * 100 / p_orig, 1)
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
    # Studio rule: bedrooms=0 → studio (override 'apartment' misclassification too)
    if parsed.get("bedrooms") == 0 and parsed.get("property_type") in (None, "apartment"):
        parsed["property_type"] = "studio"
    return parsed


# ── Two-pass extractor (PARSER_V2_TWO_PASS=1) ─────────────────────────────
# Module-level cache so retries on the same block don't re-classify.
_CLASSIFY_CACHE: Dict[str, dict] = {}
_CLASSIFY_CACHE_MAX = 1024


def _classify_cache_key(block: str) -> str:
    # Cheap stable key — trim. We don't need crypto hashing.
    return (block or "").strip()[:500]


def _classify_type(text: str) -> dict:
    """PASS 1: classify {deal_type, property_type, confidence}. Cached
    per block — retries on the same block don't re-classify."""
    key = _classify_cache_key(text)
    cached = _CLASSIFY_CACHE.get(key)
    if cached is not None:
        return cached

    prompt = CLASSIFY_PROMPT.format(text=(text or "")[:1500])
    raw = llm_call(prompt, max_tokens=120, timeout=20)
    parsed = _strip_to_json(raw or "") or {}
    if not isinstance(parsed, dict):
        parsed = {}

    deal = parsed.get("deal_type")
    if deal not in ("sale", "rent", None):
        deal = None
    ptype = parsed.get("property_type")
    valid_pt = {"apartment", "studio", "penthouse", "duplex", "villa",
                "townhouse", "office", "shop", "retail", "warehouse",
                "plot", "whole_building"}
    if ptype not in valid_pt:
        ptype = None
    try:
        conf = float(parsed.get("confidence", 0.0))
    except Exception:
        conf = 0.0

    result = {"deal_type": deal, "property_type": ptype, "confidence": conf}

    # Bounded cache (drop ~25% oldest when full)
    if len(_CLASSIFY_CACHE) >= _CLASSIFY_CACHE_MAX:
        for k in list(_CLASSIFY_CACHE.keys())[: _CLASSIFY_CACHE_MAX // 4]:
            _CLASSIFY_CACHE.pop(k, None)
    _CLASSIFY_CACHE[key] = result
    return result


_RESIDENTIAL_TYPES = {"apartment", "studio", "penthouse", "duplex"}
_VILLA_TYPES = {"villa", "townhouse"}
_COMMERCIAL_TYPES = {"office", "shop", "retail", "warehouse"}
_LAND_TYPES = {"plot", "whole_building"}


def _coerce_v2(parsed: dict) -> dict:
    """Apply the same coercions extract_block() uses to pass2 output so
    schema stays consistent."""
    parsed["area"] = canonicalise_area(parsed.get("area"))
    if parsed.get("building"):
        b = str(parsed["building"]).strip()
        b = re.sub(r"\s+", " ", b)
        parsed["building"] = b if b else None
    parsed["bedrooms"] = _coerce_bedrooms(parsed.get("bedrooms"))
    parsed["bathrooms"] = _coerce_bedrooms(parsed.get("bathrooms"))
    parsed["size_sqft"] = _coerce_number(parsed.get("size_sqft"))
    parsed["size_sqm"] = _coerce_number(parsed.get("size_sqm"))
    if "plot_sqft" in parsed:
        parsed["plot_sqft"] = _coerce_number(parsed.get("plot_sqft"))
    parsed["price"] = _coerce_number(parsed.get("price"), integer=True)
    parsed["floor"] = _coerce_number(parsed.get("floor"), integer=True)
    if parsed.get("size_sqft") is None and parsed.get("size_sqm"):
        parsed["size_sqft"] = round(parsed["size_sqm"] * 10.7639, 1)
    elif parsed.get("size_sqm") is None and parsed.get("size_sqft"):
        parsed["size_sqm"] = round(parsed["size_sqft"] / 10.7639, 1)
    p = parsed.get("price")
    if p is not None:
        if parsed.get("deal_type") == "rent" and p > 5_000_000:
            parsed["deal_type"] = "sale"
        elif parsed.get("deal_type") == "sale" and 30_000 < p < 200_000:
            parsed["deal_type"] = "rent"
    # Studio rule: bedrooms=0 → studio (override 'apartment' misclassification too)
    if parsed.get("bedrooms") == 0 and parsed.get("property_type") in (None, "apartment"):
        parsed["property_type"] = "studio"
    return parsed


def _run_typed_extractor(prompt: str) -> Optional[dict]:
    """Run a pass-2 extractor prompt with 2-try JSON-parse retry."""
    parsed = None
    for _ in range(2):
        raw = llm_call(prompt, max_tokens=500, timeout=20)
        parsed = _strip_to_json(raw or "")
        if isinstance(parsed, dict):
            break
        time.sleep(0.5)
    if not isinstance(parsed, dict):
        return None
    return _coerce_v2(parsed)


def _extract_apartment(text: str, type_info: dict) -> Optional[dict]:
    prompt = APARTMENT_EXTRACT_PROMPT.format(
        text=text[:2000],
        deal_type=type_info.get("deal_type"),
        property_type=type_info.get("property_type"),
    )
    return _run_typed_extractor(prompt)


def _extract_villa(text: str, type_info: dict) -> Optional[dict]:
    prompt = VILLA_EXTRACT_PROMPT.format(
        text=text[:2000],
        deal_type=type_info.get("deal_type"),
        property_type=type_info.get("property_type"),
    )
    return _run_typed_extractor(prompt)


def _extract_commercial(text: str, type_info: dict) -> Optional[dict]:
    prompt = COMMERCIAL_EXTRACT_PROMPT.format(
        text=text[:2000],
        deal_type=type_info.get("deal_type"),
        property_type=type_info.get("property_type"),
    )
    return _run_typed_extractor(prompt)


def _extract_land(text: str, type_info: dict) -> Optional[dict]:
    prompt = LAND_EXTRACT_PROMPT.format(
        text=text[:2000],
        deal_type=type_info.get("deal_type"),
        property_type=type_info.get("property_type"),
    )
    return _run_typed_extractor(prompt)


def extract_block_two_pass(block: str) -> Optional[dict]:
    """Two-pass extractor: classify first, then dispatch to type-focused
    extractor. Output schema matches `extract_block` — with a few extra
    OPTIONAL fields (plot_sqft / zoning / tenure) that downstream code
    can ignore safely if absent."""
    if not block or len(block.strip()) < 15:
        return None

    type_info = _classify_type(block)
    ptype = type_info.get("property_type")

    if ptype in _VILLA_TYPES:
        result = _extract_villa(block, type_info)
    elif ptype in _COMMERCIAL_TYPES:
        result = _extract_commercial(block, type_info)
    elif ptype in _LAND_TYPES:
        result = _extract_land(block, type_info)
    else:
        # Residential or unknown → apartment extractor (broadest coverage)
        result = _extract_apartment(block, type_info)

    if result is None:
        # Fallback to legacy single-pass extractor if focused one failed
        result = extract_block(block)
        if result is None:
            return None

    # Trust pass-1 classification when pass-2 left those keys blank
    # (the focused extractor sometimes returns only its narrow enum).
    if not result.get("property_type") and ptype:
        result["property_type"] = ptype
    if not result.get("deal_type") and type_info.get("deal_type"):
        result["deal_type"] = type_info["deal_type"]

    result["v2_classify_confidence"] = type_info.get("confidence")
    return result


def verify_extraction(block: str, fields: dict) -> dict:
    """Ask a second LLM to grade each field as ok/wrong/missing."""
    # Feature flag: FF_LLM_EXTRA_PASS_ENABLED=0 → skip verify pass (saves tokens).
    try:
        from stability import ff as _ff
        if not _ff("LLM_EXTRA_PASS"):
            return {}
    except Exception:
        pass
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


def _parse_message_v2_impl(text: str, source_channel: str | None,
                            extractor) -> list[dict]:
    """Shared pipeline body — `extractor` is callable (block: str) -> dict
    or None. Lets us share the split/verify/extras/output-shape logic
    between the single-pass and two-pass implementations."""
    out: list[dict] = []
    is_spam, blocks = split_message(text or "")
    if is_spam:
        return out
    for i, block in enumerate(blocks):
        fields = extractor(block)
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
                month, day = {1: ("03", "31"), 2: ("06", "30"),
                              3: ("09", "30"), 4: ("12", "31")}.get(q or 0, ("12", "31"))
                fields["handover_date"] = f"{y}-{month}-{day}"
        out.append(fields)
    return out


def parse_message_v2_v1(text: str, source_channel: str | None = None) -> list[dict]:
    """Original single-pass v2 pipeline. Kept as fallback for env
    PARSER_V2_TWO_PASS=0 (default) and for A/B comparison."""
    return _parse_message_v2_impl(text, source_channel, extract_block)


def parse_message_v2_two_pass(text: str, source_channel: str | None = None) -> list[dict]:
    """New two-pass pipeline (classify → type-specific extractor).
    Direct caller — bypasses the PARSER_V2_TWO_PASS feature flag."""
    return _parse_message_v2_impl(text, source_channel, extract_block_two_pass)


def parse_message_v2(text: str, source_channel: str | None = None) -> list[dict]:
    """Full v2 pipeline. Returns list of extracted listing dicts, one per
    block. Each dict has extracted fields + 'v2_field_conf' per-field
    confidence + 'v2_block_index' position in the source message.

    Default = single-pass (v1). Enable two-pass via env PARSER_V2_TWO_PASS=1."""
    if TWO_PASS_ENABLED:
        return parse_message_v2_two_pass(text, source_channel)
    return parse_message_v2_v1(text, source_channel)


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
