"""PHASE AB — expand AREA_CANONICAL with all DLD master_projects + non-Dubai emirates.

Strategy:
1. Pull all distinct master_project_en from DLD.
2. Pull all distinct area_name_en + their majority master_project (map admin → user-friendly).
3. Add Abu Dhabi / RAK / Sharjah popular communities (hardcoded list).
4. Write to AREA_CANONICAL_EXTENDED.py (imported by parser_v2.py).
5. Also build BUILDING_CANONICAL — top 1000 buildings by tx volume with normalized names.
"""
from __future__ import annotations
import os
import re
import json
import psycopg2
from collections import Counter

DB = os.environ.get(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or os.environ.get("RESALE_DATABASE_URL") or (_ for _ in ()).throw(RuntimeError("DATABASE_URL not set")),
)
INTEL = os.environ.get("INTEL_DB_URL") or os.environ.get("ARCHIVE_DATABASE_URL")
if not INTEL:
    raise RuntimeError("INTEL_DB_URL / ARCHIVE_DATABASE_URL not set")

# Non-Dubai emirate communities (manually curated)
NON_DUBAI = {
    # Abu Dhabi
    "saadiyat": "Saadiyat Island",
    "saadiyat island": "Saadiyat Island",
    "yas": "Yas Island",
    "yas island": "Yas Island",
    "al reem": "Al Reem Island",
    "al reem island": "Al Reem Island",
    "reem island": "Al Reem Island",
    "al raha": "Al Raha Beach",
    "al raha beach": "Al Raha Beach",
    "al reef": "Al Reef",
    "al ghadeer": "Al Ghadeer",
    "masdar city": "Masdar City",
    "khalifa city": "Khalifa City",
    "al shamkha": "Al Shamkha",
    "al maryah": "Al Maryah Island",
    "al maryah island": "Al Maryah Island",
    "al bateen": "Al Bateen",
    "al mushrif": "Al Mushrif",
    # RAK
    "al marjan": "Al Marjan Island",
    "al marjan island": "Al Marjan Island",
    "mina al arab": "Mina Al Arab",
    "al hamra village": "Al Hamra Village",
    "al hamra": "Al Hamra Village",
    # Sharjah
    "al mamzar": "Al Mamzar",
    "aljada": "Aljada",
    "al zahia": "Al Zahia",
    "muwaileh": "Muwaileh",
    "sharjah waterfront city": "Sharjah Waterfront City",
    "tilal city": "Tilal City",
    # Ajman
    "ajman corniche": "Ajman Corniche",
    "al rashidiya": "Al Rashidiya",
    "emirates city": "Emirates City",
}


def main():
    intel = psycopg2.connect(INTEL, connect_timeout=10)
    icur = intel.cursor()

    # 1. master_projects → user-friendly map
    icur.execute("""
        SELECT master_project_en, COUNT(*) AS n
        FROM dld_sale_archive
        WHERE master_project_en IS NOT NULL AND master_project_en <> ''
          AND master_project_en !~* '^n/a$'
        GROUP BY 1
        ORDER BY 2 DESC
    """)
    master_projects = {row[0]: row[1] for row in icur.fetchall()}
    print(f"DLD master_projects: {len(master_projects)}")

    # 2. admin area_name → most common master_project mapping
    icur.execute("""
        SELECT area_name_en, master_project_en, COUNT(*) AS n
        FROM dld_sale_archive
        WHERE area_name_en IS NOT NULL AND master_project_en IS NOT NULL
          AND master_project_en !~* '^n/a$'
        GROUP BY 1, 2
    """)
    rows = icur.fetchall()
    admin_to_mp: dict[str, Counter] = {}
    for an, mp, n in rows:
        admin_to_mp.setdefault(an.lower().strip(), Counter())[mp] += n
    admin_map = {}
    for an, c in admin_to_mp.items():
        mp, _ = c.most_common(1)[0]
        admin_map[an] = mp
    print(f"DLD admin areas → master_project: {len(admin_map)}")

    # 3. Build final AREA_CANONICAL_EXTENDED
    canonical = dict(NON_DUBAI)
    # Add master_projects (lowercase key → original)
    for mp in master_projects:
        canonical[mp.lower().strip()] = mp.strip()
    # Add admin → mp
    for an, mp in admin_map.items():
        if an not in canonical:
            canonical[an] = mp.strip()

    # 4. Add common abbreviations (manually curated extension)
    abbrevs = {
        "biz bay": "Business Bay",
        "bus bay": "Business Bay",
        "bb": "Business Bay",
        "mar": "Dubai Marina",
        "old town": "Downtown Dubai",
        "old town downtown": "Downtown Dubai",
        "the old town": "Downtown Dubai",
        "deira": "Deira",
        "bur dubai": "Bur Dubai",
        "satwa": "Al Satwa",
        "karama": "Al Karama",
        "garhoud": "Al Garhoud",
        "umm suqeim": "Umm Suqeim",
        "al barsha": "Al Barsha",
        "al barsha 1": "Al Barsha",
        "al barsha 2": "Al Barsha",
        "al sufouh": "Al Sufouh",
        "al safa": "Al Safa",
        "al quoz": "Al Quoz",
        "remraam": "Remraam",
        "motor city": "Motor City",
        "miracle garden": "Arjan",
        "jvt": "Jumeirah Village Triangle",
        "jvc": "Jumeirah Village Circle",
        "jbr": "Jumeirah Beach Residence",
        "jge": "Jumeirah Golf Estates",
        "the lakes": "The Lakes",
        "the meadows": "The Meadows",
        "the springs": "The Springs",
        "emirates living": "Emirates Living",
        "arabian ranches 3": "Arabian Ranches 3",
        "arabian ranches 2": "Arabian Ranches 2",
        "arabian ranches": "Arabian Ranches",
        "dhe": "Dubai Hills Estate",
        "creek": "Dubai Creek Harbour",
        "creek harbour": "Dubai Creek Harbour",
        "dxh": "Dubai Harbour",
        "expo city": "Expo City Dubai",
        "expo city dubai": "Expo City Dubai",
        "mall of emirates": "Al Barsha",
        "the world islands": "The World Islands",
        "world islands": "The World Islands",
        "deira islands": "Deira Islands",
        "dubai islands": "Dubai Islands",
        "dubai island": "Dubai Islands",
        "palm jebel ali": "Palm Jebel Ali",
        "jebel ali village": "Jebel Ali Village",
        "the heights country club": "The Heights Country Club & Wellness",
        "wellness": "The Heights Country Club & Wellness",
        "ghaf woods": "Ghaf Woods",
        "the valley": "The Valley",
        "the oasis": "The Oasis",
        "tilal al ghaf": "Tilal Al Ghaf",
        "athlon": "Athlon",
        "sobha hartland": "Sobha Hartland",
        "sobha hartland 2": "Sobha Hartland 2",
        "haven aldar": "Haven by Aldar",
        "haven": "Haven by Aldar",
    }
    for k, v in abbrevs.items():
        if k not in canonical:
            canonical[k] = v

    # Write Python module
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "area_canonical_extended.py")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write('"""Auto-generated extended area canonical map.\n\n'
                f'Sources: {len(master_projects)} DLD master_projects + '
                f'{len(admin_map)} DLD admin areas + curated non-Dubai + abbreviations.\n'
                'Regenerate via _phase_ab_expand_canonicals.py.\n"""\n\n')
        f.write("AREA_CANONICAL_EXTENDED = {\n")
        for k in sorted(canonical):
            f.write(f"    {k!r}: {canonical[k]!r},\n")
        f.write("}\n")
    print(f"\nWritten {out_path}")
    print(f"Total entries: {len(canonical)}")

    # 5. Build BUILDING_CANONICAL — ALL DLD buildings (no LIMIT)
    icur.execute("""
        SELECT building_name_en, master_project_en, area_name_en, COUNT(*) AS n
        FROM dld_sale_archive
        WHERE building_name_en IS NOT NULL AND building_name_en <> ''
        GROUP BY 1, 2, 3
        ORDER BY 4 DESC
    """)
    buildings = []
    for b, mp, an, n in icur.fetchall():
        area = mp if mp and mp.strip() and mp.lower() != "n/a" else (an or "")
        buildings.append({
            "name": b.strip(),
            "canonical": b.strip().title(),
            "area": area.strip(),
            "tx_count": n,
        })

    # 5b. Hardcoded off-plan + landmark buildings DLD might not cover
    HARDCODED_BLD = {
        # Off-plan & landmark
        "emaar beachfront": "Emaar Beachfront",
        "beach isle": "Emaar Beachfront",
        "beach vista": "Emaar Beachfront",
        "beach mansion": "Emaar Beachfront",
        "marina vista": "Emaar Beachfront",
        "sunrise bay": "Emaar Beachfront",
        "address residences emaar beachfront": "Emaar Beachfront",
        "address residences the bay": "Emaar Beachfront",
        "address harbour point": "Dubai Creek Harbour",
        "address harbour gate": "Dubai Creek Harbour",
        "atlantis the royal": "Palm Jumeirah",
        "atlantis royal": "Palm Jumeirah",
        "the royal atlantis resort": "Palm Jumeirah",
        "bluewaters residence": "Bluewaters Island",
        "bluewaters residences": "Bluewaters Island",
        "the residences at the st regis dubai the palm": "Palm Jumeirah",
        "address residences dubai opera": "Downtown Dubai",
        "address dubai opera": "Downtown Dubai",
        "address opera": "Downtown Dubai",
        "the address dubai opera": "Downtown Dubai",
        "address residences": "Downtown Dubai",
        # Sobha
        "sobha hartland waves": "Sobha Hartland",
        "sobha hartland waves opulence": "Sobha Hartland",
        "sobha creek vistas": "Sobha Hartland",
        "sobha one": "Sobha Hartland",
        "sobha hartland ii": "Sobha Hartland 2",
        "sobha hartland 2": "Sobha Hartland 2",
        "the crest": "Sobha Hartland",
        "crest grande": "Sobha Hartland",
        "sobha the crest": "Sobha Hartland",
        "sobha the crest tower a": "Sobha Hartland",
        "sobha the crest tower b": "Sobha Hartland",
        "sobha the crest tower c": "Sobha Hartland",
        "sobha seahaven": "Dubai Harbour",
        "sobha solis": "Motor City",
        # Business Bay
        "vision tower": "Business Bay",
        "peninsula": "Business Bay",
        "peninsula 1": "Business Bay",
        "peninsula 2": "Business Bay",
        "peninsula 3": "Business Bay",
        "peninsula 4": "Business Bay",
        "peninsula 5": "Business Bay",
        "peninsula three": "Business Bay",
        "peninsula four": "Business Bay",
        "select peninsula": "Business Bay",
        "the edge tower a": "Business Bay",
        "the edge tower b": "Business Bay",
        "the edge tower c": "Business Bay",
        "the edge": "Business Bay",
        # Binghatti
        "binghatti corner": "Jumeirah Village Circle",
        "binghatti skyrise": "Business Bay",
        "binghatti onyx": "Jumeirah Village Circle",
        "binghatti crystals": "Jumeirah Village Circle",
        "binghatti emerald": "Jumeirah Village Circle",
        "binghatti orchid": "Jumeirah Village Circle",
        "binghatti gardenia": "Jumeirah Village Circle",
        "binghatti tulip": "Jumeirah Village Circle",
        "binghatti lavender": "Jumeirah Village Circle",
        "binghatti rose": "Jumeirah Village Circle",
        "binghatti jasmine": "Jumeirah Village Circle",
        "binghatti point": "Dubai Silicon Oasis",
        "binghatti azure": "Jumeirah Village Circle",
        "binghatti house": "Jumeirah Village Circle",
        # Creek
        "creek waters": "Dubai Creek Harbour",
        "creek vista grande": "Dubai Creek Harbour",
        "creek vista heights": "Dubai Creek Harbour",
        "creek edge": "Dubai Creek Harbour",
        "creek rise": "Dubai Creek Harbour",
        "creek beach": "Dubai Creek Harbour",
        "creek gate": "Dubai Creek Harbour",
        "creek harbour": "Dubai Creek Harbour",
        "creek palace": "Dubai Creek Harbour",
        "harbour gate": "Dubai Creek Harbour",
        "harbour views": "Dubai Creek Harbour",
        # The Valley & The Oasis
        "valley by emaar": "The Valley",
        "the valley": "The Valley",
        "elora the valley": "The Valley",
        "rivana the valley": "The Valley",
        "alana the valley": "The Valley",
        "talia the valley": "The Valley",
        "nara the valley": "The Valley",
        "the oasis": "The Oasis",
        "grand polo": "The Oasis",
        # Azizi
        "azizi riviera": "Meydan",
        # Marina classics
        "marina shores": "Dubai Marina",
        "marina star": "Dubai Marina",
        "marina arcade tower": "Dubai Marina",
        "marina pinnacle": "Dubai Marina",
        "marina gate": "Dubai Marina",
        "marina gate 1": "Dubai Marina",
        "marina gate 2": "Dubai Marina",
        "marina gate 3": "Dubai Marina",
        "stella maris": "Dubai Marina",
        "studio one": "Dubai Marina",
        "five at jumeirah village circle": "Jumeirah Village Circle",
        # JLT
        "dubai gate 1": "Jumeirah Lake Towers",
        "dubai gate 2": "Jumeirah Lake Towers",
        "dubai gate one": "Jumeirah Lake Towers",
        "dubai gate two": "Jumeirah Lake Towers",
        "armada towers": "Jumeirah Lake Towers",
        "armada tower 1": "Jumeirah Lake Towers",
        "armada tower 2": "Jumeirah Lake Towers",
        "armada tower 3": "Jumeirah Lake Towers",
        "se7en city jlt": "Jumeirah Lake Towers",
        # DIFC + landmark
        "icd brookfield place": "DIFC",
        "fairmont residences": "Sheikh Zayed Road",
        "st regis residences": "Downtown Dubai",
        "the st regis residences": "Downtown Dubai",
        "serenia living": "Palm Jumeirah",
        "serenia residences": "Palm Jumeirah",
        # Brand new (2024-2026)
        "ghaf woods": "Dubailand",
        "midtown": "Production City",
        "midtown noor": "Production City",
        "midtown afnan": "Production City",
        "the 8": "Palm Jumeirah",
        "the highbury": "Mohammed Bin Rashid City",
        "haven by aldar": "Dubailand",
        "haven aldar": "Dubailand",
        "haven": "Dubailand",
        "downtown boulevard crescent": "Downtown Dubai",
        # Dubai Hills
        "acacia": "Dubai Hills Estate",
        "acacia a": "Dubai Hills Estate",
        "acacia b": "Dubai Hills Estate",
        "acacia c": "Dubai Hills Estate",
        "park ridge": "Dubai Hills Estate",
        "park ridge tower a": "Dubai Hills Estate",
        "park ridge tower b": "Dubai Hills Estate",
        "park ridge tower c": "Dubai Hills Estate",
        "park heights": "Dubai Hills Estate",
        "park field": "Dubai Hills Estate",
        "park gate": "Dubai Hills Estate",
        "park horizon": "Dubai Hills Estate",
        # Island
        "island park": "Dubai Creek Harbour",
        "island park 1": "Dubai Creek Harbour",
        "island park 2": "Dubai Creek Harbour",
        "rixos dubai islands": "Dubai Islands",
        "lcd rixos": "Dubai Islands",
        # JBR
        "address jbr": "Jumeirah Beach Residence",
        "the address jbr": "Jumeirah Beach Residence",
        "address residences jumeirah resort": "Jumeirah Beach Residence",
        "palm beach towers": "Palm Jumeirah",
        "palm beach towers 1": "Palm Jumeirah",
        "palm beach towers 2": "Palm Jumeirah",
        "palm beach towers 3": "Palm Jumeirah",
        # W
        "w residences": "Palm Jumeirah",
        "w residences dubai downtown": "Downtown Dubai",
        # MBR
        "the highbury mbr": "Mohammed Bin Rashid City",
        # Aykon
        "aykon city": "Business Bay",
        "aykon city tower a": "Business Bay",
        "aykon city tower b": "Business Bay",
        "aykon city tower c": "Business Bay",
        "aykon city 2": "Business Bay",
        "aykon city tower 2": "Business Bay",
        # Damac
        "damac bay": "Dubai Harbour",
        "damac bay 1": "Dubai Harbour",
        "damac bay 2": "Dubai Harbour",
        "damac bay tower 2": "Dubai Harbour",
        "damac heights": "Dubai Marina",
        "damac residenze": "Dubai Marina",
        "damac maison": "Business Bay",
        "damac maison the distinction": "Downtown Dubai",
        # Boutique 23
        "boutique 23": "Business Bay",
        # MBR / Meydan
        "maple": "Dubai Hills Estate",
        "maple 1": "Dubai Hills Estate",
        "maple 2": "Dubai Hills Estate",
        "maple 3": "Dubai Hills Estate",
    }
    # Convert hardcoded into the buildings list shape
    for k, area in HARDCODED_BLD.items():
        buildings.append({
            "name": k.title(),
            "canonical": k.title(),
            "area": area,
            "tx_count": 999999,  # high tx_count to override
        })

    # 5c. Self-discovery: from our own listings, find building→area pairs
    #     where many listings already have both filled — use that as map
    print("\nSelf-discovery from listings...")
    rb = psycopg2.connect(DB, connect_timeout=10)
    rb_cur = rb.cursor()
    rb_cur.execute("""
        SELECT LOWER(TRIM(building)) AS b, area, COUNT(*) AS n
        FROM listings
        WHERE v2_extracted_at IS NOT NULL AND is_active=TRUE
          AND building IS NOT NULL AND area IS NOT NULL
        GROUP BY 1, 2
        HAVING COUNT(*) >= 2
        ORDER BY 3 DESC
    """)
    self_pairs = rb_cur.fetchall()
    self_map: dict[str, Counter] = {}
    for b, area, n in self_pairs:
        self_map.setdefault(b, Counter())[area] += n
    self_added = 0
    for b, c in self_map.items():
        area, _ = c.most_common(1)[0]
        if b and area:
            buildings.append({
                "name": b.title(),
                "canonical": b.title(),
                "area": area,
                "tx_count": 100,
            })
            self_added += 1
    rb.close()
    print(f"  self-discovery added: {self_added}")

    bld_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "building_canonical.py")
    with open(bld_path, "w", encoding="utf-8") as f:
        f.write('"""Auto-generated building → area map (top 2000 by DLD tx volume).\n\n'
                'Regenerate via _phase_ab_expand_canonicals.py.\n"""\n\n')
        f.write("# (name_lower → {canonical_name, area})\n")
        f.write("BUILDING_TO_AREA = {\n")
        seen = set()
        for b in buildings:
            k = b["name"].lower()
            if k in seen:
                continue
            seen.add(k)
            f.write(f"    {k!r}: {{'name': {b['canonical']!r}, 'area': {b['area']!r}}},\n")
        f.write("}\n")
    print(f"Written {bld_path}")
    print(f"Total building entries: {len(seen)}")

    intel.close()


if __name__ == "__main__":
    main()
