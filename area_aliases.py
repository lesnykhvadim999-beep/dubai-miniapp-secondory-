"""Area alias system — defence-in-depth для поиска.

Пользователи говорят «JVC», в DB может оказаться любой из:
  - Jumeirah Village Circle
  - JVC
  - Al Barsha South Fourth  (DLD official)
  - Village Circle
  - JVCircle

Этот модуль раскрывает любой пользовательский запрос на ВСЕ варианты
чтобы search_listings возвращал все matched records.

Пользователь не знает официальных имён — мы должны быть умнее.
"""

# Каждая группа = синонимы одного места.
# При поиске по любому имени группы — расширяем на всю группу.
AREA_GROUPS = [
    # Jumeirah Village Circle
    ["jumeirah village circle", "jvc", "village circle", "jvcircle",
     "al barsha south fourth"],
    # Jumeirah Village Triangle
    ["jumeirah village triangle", "jvt", "village triangle",
     "al barsha south third", "al barsha south second"],
    # Dubai Marina
    ["dubai marina", "marina", "marsa dubai"],
    # Jumeirah Beach Residence
    ["jumeirah beach residence", "jbr", "jumeirah beach"],
    # Jumeirah Lake Towers
    ["jumeirah lake towers", "jlt", "lake towers",
     "al thanyah fifth", "al thanyah fourth", "al thanyah third"],
    # Downtown Dubai
    ["downtown dubai", "downtown", "burj khalifa district", "burj khalifa area"],
    # Business Bay
    ["business bay", "bay", "bb"],
    # Palm Jumeirah
    ["palm jumeirah", "palm", "the palm", "palm island"],
    # Palm Jebel Ali
    ["palm jebel ali", "jebel ali palm"],
    # Dubai Creek Harbour
    ["dubai creek harbour", "creek harbour", "creek harbor", "dch",
     "al khairan first", "al khairan second"],
    # Dubai Hills Estate
    ["dubai hills estate", "dubai hills", "hills estate", "dhe", "dh",
     "wadi al safa 2", "wadi al safa 3", "wadi al safa 5", "wadi al safa 7"],
    # MBR City
    ["mbr city", "mbr", "mohammed bin rashid city",
     "hadaeq sheikh mohammed bin rashid"],
    # Sobha Hartland
    ["sobha hartland", "hartland", "hartland mbr"],
    # Damac Lagoons
    ["damac lagoons", "damac lagoon", "lagoons", "damaclagoons"],
    # Damac Hills
    ["damac hills", "akoya"],
    # Damac Hills 2
    ["damac hills 2", "damac hills ii"],
    # Tilal Al Ghaf
    ["tilal al ghaf", "tilal ghaf", "ghaf"],
    # Emaar Beachfront
    ["emaar beachfront", "beachfront", "ebf"],
    # Bluewaters Island
    ["bluewaters island", "bluewaters", "blue waters"],
    # Saadiyat Island
    ["saadiyat island", "saadiyat"],
    # Yas Island
    ["yas island", "yas"],
    # Al Reem Island
    ["al reem island", "al reem", "reem island", "reem"],
    # Dubai Investment Park
    ["dubai investment park", "dip", "investment park",
     "saih shuaib 2", "dubai investments park"],
    # Dubai South
    ["dubai south", "south dubai", "madinat al mataar"],
    # Dubailand
    ["dubailand", "dubai land", "dlrc", "dubai land residence complex",
     "dubai land residence"],
    # The Valley
    ["the valley", "valley", "valley emaar"],
    # Town Square
    ["town square", "townsquare", "town square nshama",
     "al yufrah 1", "al yufrah 2"],
    # Arabian Ranches
    ["arabian ranches", "arabian ranch"],
    ["arabian ranches 2"],
    ["arabian ranches 3"],
    # Reem Hills
    ["reem hills"],
    # Mudon
    ["mudon"],
    # Mira
    ["mira"],
    # Cherrywoods
    ["cherrywoods"],
    # Serena
    ["serena"],
    # Sports City
    ["sports city", "dubai sports city", "dsc"],
    # Motor City
    ["motor city", "dubai motor city"],
    # Silicon Oasis
    ["silicon oasis", "dso", "dubai silicon oasis"],
    # International City
    ["international city", "ic"],
    # JADDAF
    ["al jaddaf", "jaddaf"],
    # Studio City
    ["studio city", "dubai studio city"],
    # Production City
    ["dubai production city", "production city", "impz", "immpz"],
    # DIFC
    ["difc", "trade center second", "trade center first"],
    # Bur Dubai / Deira / older areas
    ["bur dubai"],
    ["deira"],
    # Mirdiff Hills
    ["mirdiff hills", "mirdif", "mirdiff"],
    # Madinat Jumeirah
    ["madinat jumeirah", "mjl", "madinat jumeirah living"],
    # Marjan Island
    ["al marjan island", "marjan island", "al marjan", "marjan"],
    # Mina Al Arab
    ["mina al arab"],
    # Aljada
    ["aljada", "al jada"],
    # Sufouh
    ["sufouh", "al sufouh", "marsa al arab"],
    # Al Furjan
    ["al furjan", "furjan"],
    # Arjan
    ["arjan"],
    # Majan
    ["majan"],
    # Meydan
    ["meydan", "meydan one"],
    # Dubai Islands
    ["dubai islands", "dubai island"],
    # La Mer
    ["la mer", "lamer"],
    # City Walk
    ["city walk"],
    # Mina Rashid
    ["mina rashid", "port rashid"],
    # Emaar South
    ["emaar south"],
    # Discovery Gardens
    ["discovery gardens"],
    # Jebel Ali
    ["jebel ali", "jabal ali", "jabal ali first"],
    # Za'abeel
    ["za'abeel", "zaabeel"],
    # Al Barari
    ["al barari", "barari"],
    # Al Wasl
    ["al wasl"],
    # Al Satwa
    ["al satwa", "satwa"],
    # Al Warsan
    ["al warsan", "warsan"],
]


# Build O(1) lookup: name → group
_ALIAS_LOOKUP = {}
for group in AREA_GROUPS:
    for name in group:
        _ALIAS_LOOKUP[name.lower().strip()] = group


def expand_area_query(user_input: str) -> list:
    """Возвращает list всех синонимов введённого пользователем area.
    Если совпадений нет — возвращает [user_input] (поиск как есть).
    """
    if not user_input:
        return []
    key = user_input.lower().strip()
    group = _ALIAS_LOOKUP.get(key)
    if group:
        return group
    # Также пробуем substring match — «JVC» в «JVC tower»
    for name, grp in _ALIAS_LOOKUP.items():
        if name in key or key in name:
            return grp
    return [user_input]


if __name__ == "__main__":
    # Quick test
    for q in ["JVC", "Al Barsha South Fourth", "Marina", "Marsa Dubai", "Dubai Marina",
              "Creek Harbour", "Al Khairan First", "Random Place"]:
        print(f"  {q!r:30} → {expand_area_query(q)}")
