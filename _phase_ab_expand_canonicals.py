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
    "postgresql://postgres:REDACTED_DSN_PASSWORD"
    "@tramway.proxy.rlwy.net:23228/railway",
)
INTEL = os.environ.get(
    "INTEL_DB_URL",
    "postgresql://postgres:REDACTED_ARCHIVE_DB_PASSWORD"
    "@switchback.proxy.rlwy.net:23244/railway",
)

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

    # 5. Also build BUILDING_CANONICAL — top-2000 buildings
    icur.execute("""
        SELECT building_name_en, master_project_en, area_name_en, COUNT(*) AS n
        FROM dld_sale_archive
        WHERE building_name_en IS NOT NULL AND building_name_en <> ''
        GROUP BY 1, 2, 3
        ORDER BY 4 DESC
        LIMIT 2000
    """)
    buildings = []
    for b, mp, an, n in icur.fetchall():
        area = mp if mp and mp.strip() and mp.lower() != "n/a" else (an or "")
        buildings.append({
            "name": b.strip(),
            "canonical": b.strip().title(),  # rough title-case canonical
            "area": area.strip(),
            "tx_count": n,
        })

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
