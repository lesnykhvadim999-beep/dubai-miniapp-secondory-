# ═══════════════════════════════════════════════════════════════
# DUBAI REAL ESTATE DATABASE — known_dubai_data.py
# Районы, застройщики, билдинги Дубая
# Используется для нормализации названий при парсинге
# ═══════════════════════════════════════════════════════════════

# ── ЭМИРАТЫ ─────────────────────────────────────────────────────
EMIRATES = [
    "Dubai", "Abu Dhabi", "Sharjah", "Ras Al Khaimah",
    "Ajman", "Fujairah", "Umm Al Quwain"
]

# ── РАЙОНЫ ДУБАЯ ────────────────────────────────────────────────
DUBAI_AREAS = [
    "Downtown Dubai", "Business Bay", "Dubai Marina", "Palm Jumeirah",
    "Jumeirah Village Circle", "JVC", "Dubai Hills Estate", "Dubai Hills",
    "Dubai Creek Harbour", "Jumeirah Beach Residence", "JBR",
    "DIFC", "City Walk", "Bluewaters Island", "Bluewaters",
    "La Mer", "Jumeirah", "Umm Suqeim", "Al Barsha", "Al Barsha 1",
    "Al Barsha 2", "Al Barsha 3", "Al Barsha South", "Barsha Heights",
    "Tecom", "Al Quoz", "Al Furjan", "Discovery Gardens",
    "Jumeirah Village Triangle", "JVT", "Motor City", "Sports City",
    "Dubai Sports City", "Studio City", "Dubai Studio City",
    "Arabian Ranches", "Arabian Ranches 2", "Arabian Ranches 3",
    "Damac Hills", "Damac Hills 2", "Akoya", "Tilal Al Ghaf",
    "Mohammed Bin Rashid City", "MBR City", "Meydan", "Nad Al Sheba",
    "Sobha Hartland", "Sobha Hartland 2", "Dubai Creek",
    "Al Jaddaf", "Al Jadaf", "Ras Al Khor", "Al Quoz Industrial",
    "Dubai Investment Park", "DIP", "Dubai South", "Dubai World Central",
    "Expo City Dubai", "Expo 2020", "Al Maktoum", "Emaar South",
    "Dubai Silicon Oasis", "Silicon Oasis", "DSO",
    "International City", "Dragon Mart", "Al Warsan",
    "Deira", "Bur Dubai", "Karama", "Oud Metha", "Garhoud",
    "Al Rigga", "Naif", "Al Hamriya", "Port Saeed",
    "Al Nahda", "Al Qusais", "Muhaisnah", "Al Twar",
    "Mirdif", "Rashidiya", "Al Mizhar", "Mushrif",
    "Jumeirah Islands", "Jumeirah Park", "Meadows", "Springs",
    "Lakes", "The Views", "Greens", "Emirates Hills",
    "Emirates Living", "Al Sufouh", "Knowledge Village",
    "Media City", "Internet City", "Dubai Internet City",
    "Dubai Media City", "Palm Cove", "Palm West Beach",
    "Nakheel Harbour", "Dubai Harbour", "Maritime City",
    "Mina Rashid", "Culture Village", "Marasi Business Bay",
    "Al Safa", "Al Wasl", "Umm Al Sheif", "Al Manara",
    "Jumeirah 1", "Jumeirah 2", "Jumeirah 3",
    "Madinat Jumeirah", "Al Seef", "Creekside",
    "Dubai Festival City", "Festival City", "Al Khail",
    "Al Khail Heights", "The Valley", "Villanova", "Town Square",
    "Serena", "Reem", "Mudon", "Dubailand", "Liwan",
    "Remraam", "Al Furjan West", "Discovery Point",
    "Green Community", "Motor City South", "Uptown Motor City",
    "Arjan", "Arjan Dubai", "Al Barsha South 4",
    "Jumeirah Golf Estates", "Victory Heights",
    "International Media Production Zone", "IMPZ",
    "Production City", "Dubai Production City",
    "Al Hebiah", "Al Yufrah", "Maidan", "Nad Al Hamar",
    "Oud Al Muteena", "Al Twar 1", "Al Twar 2", "Al Twar 3",
    "Al Badrah", "Layan", "Habtoor City", "Sheikh Zayed Road",
    "Trade Centre", "World Trade Centre", "Burj Khalifa",
    "Old Town", "Downtown Old Town", "Emaar Boulevard",
    "Creek Beach", "Creek Island", "Harbour Views",
    "Ras Al Khor Industrial", "Al Quoz 1", "Al Quoz 2",
    "Al Quoz 3", "Al Quoz 4",
]

# ── РАЙОНЫ АБУ-ДАБИ ─────────────────────────────────────────────
ABU_DHABI_AREAS = [
    "Al Reem Island", "Reem Island", "Al Maryah Island", "Maryah Island",
    "Saadiyat Island", "Yas Island", "Al Raha Beach", "Al Raha Gardens",
    "Khalifa City", "Khalifa City A", "Khalifa City B",
    "Mohammed Bin Zayed City", "MBZ City", "Al Shamkha",
    "Al Reef", "Al Reef Downtown", "Al Reef Villas",
    "Masdar City", "Al Falah", "Al Ghadeer",
    "Corniche", "Al Corniche", "Corniche Road",
    "Al Bateen", "Al Mushrif", "Al Karamah",
    "Al Khalidiyah", "Al Danah", "Al Markaziyah",
    "Tourist Club Area", "Al Zahiyah", "Al Wahda",
    "Electra Street", "Hamdan Street", "Al Manhal",
    "Al Muroor", "Al Nahyan", "Al Mushrif",
    "Between Two Bridges", "Al Maqtaa", "Bain Al Jessrain",
    "Yas Acres", "Ansam", "Waterfont", "Yas Bay",
    "Ferrari World Area", "Warner Bros Area",
    "Louvre Abu Dhabi Area", "Guggenheim Area",
    "Al Jubail Island", "Jubail Island",
    "Al Shamkhah", "Wathba", "Al Wathba",
    "Hudayriat Island", "Al Hudayriat",
    "Nurai Island", "Sir Bani Yas Island",
]

# ── РАЙОНЫ РАС-ЭЛЬ-ХАЙМА ────────────────────────────────────────
RAK_AREAS = [
    "Al Hamra Village", "Mina Al Arab", "Al Marjan Island",
    "Ras Al Khaimah City", "Al Nakheel", "Al Qurm",
    "Al Dhait", "Al Rams", "Al Jazeerah Al Hamra",
    "Wynn Al Marjan Island", "Gateway Residences RAK",
]

# ── РАЙОНЫ ШАРДЖИ ────────────────────────────────────────────────
SHARJAH_AREAS = [
    "Aljada", "Tilal City", "Muwaileh", "Al Zahia",
    "Al Khan", "Al Majaz", "Al Taawun", "Al Nahda Sharjah",
    "Sharjah Waterfront", "Sharjah City",
]

# ── ЗАСТРОЙЩИКИ ──────────────────────────────────────────────────
DEVELOPERS = {
    # Крупнейшие
    "Emaar": ["Emaar Properties", "Emaar Beachfront", "Emaar South"],
    "Nakheel": ["Nakheel Properties", "Nakheel Malls"],
    "Damac": ["Damac Properties", "Damac Real Estate", "DAMAC"],
    "Meraas": ["Meraas Holding", "Meraas Developer"],
    "Aldar": ["Aldar Properties", "Aldar"],
    "Dubai Properties": ["Dubai Properties Group", "DP"],
    "Sobha": ["Sobha Realty", "Sobha Developers", "Sobha Group"],
    "Azizi": ["Azizi Developments", "Azizi Real Estate"],
    "Danube": ["Danube Properties", "Danube Group"],
    "Binghatti": ["Binghatti Developers", "Binghatti Real Estate"],
    "Ellington": ["Ellington Properties", "Ellington Real Estate"],
    "Select Group": ["Select Group Properties"],
    "Tiger": ["Tiger Properties", "Tiger Real Estate"],
    "Object 1": ["Object 1 Developer"],
    "Imtiaz": ["Imtiaz Developments"],
    "Reportage": ["Reportage Properties", "Reportage Villages"],
    "Expo Real Estate": [],
    "MAG": ["MAG Property Development", "MAG Developer"],
    "Mag Of Life": [],
    "Seven Tides": ["Seven Tides International"],
    "Omniyat": ["Omniyat Properties", "Omniyat Developer"],
    "Deyaar": ["Deyaar Development", "Deyaar Properties"],
    "Union Properties": ["Union Real Estate"],
    "Wasl": ["Wasl Properties", "Wasl Asset Management"],
    "Al Fattan": ["Al Fattan Properties"],
    "Lootah": ["Lootah Real Estate"],
    "National Bonds": [],
    "Bloom": ["Bloom Holding", "Bloom Properties"],
    "RAK Properties": ["RAK Properties Developer"],
    "Marjan": ["Marjan Island Developer"],
    "Miral": ["Miral Destinations", "Miral Asset Management"],
    "Reem Developers": [],
    "Masaar": ["Masaar Community"],
    "Arada": ["Arada Developer"],
    "Sharjah Holding": [],
    "Samana": ["Samana Developers"],
    "AHS Properties": [],
    "Prescott": ["Prescott Real Estate"],
    "Dugasta": ["Dugasta Properties"],
    "Condor": ["Condor Real Estate"],
    "GJ Properties": [],
    "SRG": ["SRG Holding"],
    "Taraf": ["Taraf Holding"],
    "Muraba": ["Muraba Properties"],
    "IFA Hotels": ["IFA Hotels & Resorts"],
    "Falconcity": ["Falconcity of Wonders"],
    "Nshama": ["Nshama Developer"],
    "Emaar Misr": [],
    "Sunrise Capital": [],
}

# ── БИЛДИНГИ ДУБАЯ ───────────────────────────────────────────────
DUBAI_BUILDINGS = {

    # ═══ DOWNTOWN DUBAI ════════════════════════════════════════
    "Burj Khalifa": ["burj khalifa", "burj tower"],
    "The Address Downtown": ["address downtown", "address hotel downtown"],
    "The Address Sky View": ["address sky view", "sky view tower"],
    "The Address Residences": ["address residences downtown"],
    "Opera Grand": ["opera grand", "opera grand downtown"],
    "Act One Act Two": ["act one", "act two", "act 1 act 2"],
    "29 Boulevard": ["29 boulevard", "twenty nine boulevard"],
    "Burj Vista": ["burj vista", "burj vista 1", "burj vista 2"],
    "IL Primo": ["il primo", "ilprimo"],
    "Grande Signature Residences": ["grande signature", "grande downtown"],
    "Forte": ["forte 1", "forte 2", "forte downtown"],
    "The Residences": ["the residences downtown", "residences at downtown"],
    "Claren Tower": ["claren 1", "claren 2", "claren tower"],
    "8 Boulevard Walk": ["8 boulevard walk", "boulevard walk"],
    "Standpoint": ["standpoint a", "standpoint b", "standpoint tower"],
    "South Ridge": ["south ridge 1", "south ridge 2", "south ridge 3",
                    "south ridge 4", "south ridge 5", "south ridge 6"],
    "Loft Downtown": ["loft downtown east", "loft downtown west"],
    "Boulevard Heights": ["boulevard heights", "blvd heights"],
    "Bellevue Towers": ["bellevue tower 1", "bellevue tower 2"],
    "Mada Residences": ["mada residences"],
    "Vida Residences Downtown": ["vida residences", "vida downtown"],
    "The Cobbler": ["cobbler"],
    "DT1": ["dt1 downtown"],

    # ═══ BUSINESS BAY ══════════════════════════════════════════
    "Executive Tower": ["executive tower a", "executive tower b",
                        "executive tower c", "executive tower d",
                        "executive tower e", "executive tower f",
                        "executive tower g", "executive tower h",
                        "executive tower i", "executive tower j",
                        "executive tower k", "executive tower l",
                        "executive tower m"],
    "Damac Towers by Paramount": ["damac towers paramount", "paramount towers"],
    "Vera Residences": ["vera residences business bay"],
    "DAMAC Maison": ["damac maison", "damac maison business bay"],
    "Capital Bay": ["capital bay a", "capital bay b"],
    "Aykon City": ["aykon city tower", "aykon city"],
    "Dorchester Collection": ["dorchester collection dubai"],
    "The Opus": ["opus business bay", "the opus"],
    "Marasi Business Bay": ["marasi"],
    "Bayz 101": ["bayz 101", "bayz by danube"],
    "SABA Towers": ["saba tower 1", "saba tower 2", "saba tower 3"],
    "Bay Square": ["bay square 1", "bay square 2", "bay square 3",
                   "bay square 4", "bay square 5", "bay square 6",
                   "bay square 7", "bay square 8", "bay square 9",
                   "bay square 10", "bay square 11", "bay square 12"],
    "Churchill Residency": ["churchill residency", "churchill tower"],
    "Elara": ["elara business bay"],
    "Mayfair Tower": ["mayfair tower business bay"],
    "Belford Tower": ["belford tower"],
    "West Avenue": ["west avenue business bay"],
    "Majestique Residences": ["majestique residences"],
    "Nobles Tower": ["nobles tower"],
    "Regalia": ["regalia business bay"],
    "Binghatti Crest": ["binghatti crest"],
    "Binghatti Corner": ["binghatti corner"],
    "Paramount Tower Hotel": ["paramount tower hotel business bay"],
    "Volante": ["volante business bay"],
    "Opal Tower": ["opal tower business bay"],

    # ═══ DUBAI MARINA ══════════════════════════════════════════
    "Princess Tower": ["princess tower marina"],
    "23 Marina": ["23 marina tower", "twenty three marina"],
    "Cayan Tower": ["cayan tower", "infinity tower marina"],
    "Marina Gate": ["marina gate 1", "marina gate 2", "marina gate 3"],
    "Marina Pinnacle": ["marina pinnacle"],
    "Marina Crown": ["marina crown"],
    "Al Sahab": ["al sahab 1", "al sahab 2"],
    "The Point": ["the point marina"],
    "Marina Heights": ["marina heights tower"],
    "Botanica Tower": ["botanica marina"],
    "Marina Terrace": ["marina terrace"],
    "Rimal": ["rimal 1", "rimal 2", "rimal 3", "rimal 4", "rimal 5", "rimal 6"],
    "Murjan": ["murjan 1", "murjan 2", "murjan 3", "murjan 4",
               "murjan 5", "murjan 6", "murjan 7"],
    "Bahar": ["bahar 1", "bahar 2", "bahar 3", "bahar 4",
              "bahar 5", "bahar 6", "bahar 7"],
    "Sadaf": ["sadaf 1", "sadaf 2", "sadaf 3", "sadaf 4",
              "sadaf 5", "sadaf 6", "sadaf 7"],
    "Amwaj": ["amwaj 1", "amwaj 2", "amwaj 3", "amwaj 4",
              "amwaj 5", "amwaj 6"],
    "Shams": ["shams 1", "shams 2", "shams 3", "shams 4"],
    "Al Majara": ["al majara 1", "al majara 2", "al majara 3",
                  "al majara 4", "al majara 5"],
    "Al Fattan Marine Tower": ["al fattan marine"],
    "Sulafa Tower": ["sulafa tower"],
    "MAG 218": ["mag 218 marina"],
    "Silverene": ["silverene a", "silverene b"],
    "The Torch": ["torch tower marina"],
    "Al Yass Tower": ["al yass tower"],
    "Escan Tower": ["escan tower"],
    "Elite Residences": ["elite residences marina"],
    "Dream Tower": ["dream tower marina"],
    "Trident Grand Residence": ["trident grand"],
    "Grosvenor House": ["grosvenor house marina"],
    "Dubai Marina Mall Residences": ["marina mall residences"],
    "Horizon Tower": ["horizon tower marina"],
    "Ocean Heights": ["ocean heights marina"],
    "Infinity": ["infinity tower", "infinity marina"],
    "1 JBR": ["one jbr", "1 jbr"],
    "Address Dubai Marina": ["address marina", "address dubai marina"],
    "Le Reve": ["le reve marina"],
    "Vida Residences Marina": ["vida marina"],

    # ═══ PALM JUMEIRAH ══════════════════════════════════════════
    "Atlantis The Royal Residences": ["atlantis royal", "atlantis the royal"],
    "Atlantis The Palm": ["atlantis palm"],
    "One Palm": ["one palm", "one at palm jumeirah"],
    "COMO Residences": ["como residences palm"],
    "Palme Couture": ["palme couture"],
    "Palm Beach Towers": ["palm beach tower 1", "palm beach tower 2",
                          "palm beach tower 3"],
    "Shoreline Apartments": ["shoreline 1", "shoreline 2", "shoreline 3",
                              "shoreline 4", "shoreline 5", "shoreline 6",
                              "shoreline 7", "shoreline 8", "shoreline 9",
                              "shoreline 10", "shoreline 11", "shoreline 12"],
    "The Fairmont Palm": ["fairmont palm residences", "fairmont palm"],
    "Golden Mile": ["golden mile 1", "golden mile 2", "golden mile 3",
                    "golden mile 4", "golden mile 5", "golden mile 6",
                    "golden mile 7", "golden mile 8", "golden mile 9",
                    "golden mile 10"],
    "Oceana": ["oceana atlantic", "oceana caribbean", "oceana mediterranean",
               "oceana pacific", "oceana baltic"],
    "Marina Residences": ["marina residences palm", "marina residences 1",
                          "marina residences 2", "marina residences 3",
                          "marina residences 4", "marina residences 5",
                          "marina residences 6"],
    "Signature Villas Palm": ["signature villas", "palm signature villas"],
    "Garden Homes Palm": ["garden homes palm", "garden homes frond"],
    "Beach Villas Palm": ["beach villas palm"],
    "Serenia Residences": ["serenia residences", "serenia palm"],
    "SLS Dubai Hotel": ["sls dubai"],
    "W Residences Palm": ["w residences palm"],
    "Five Palm Jumeirah": ["five palm", "five hotel palm"],
    "Th8 Palm": ["th8 palm"],
    "Waldorf Astoria Palm": ["waldorf palm"],
    "Anantara Residences": ["anantara residences palm"],
    "Como Residences Palm": ["como residences"],

    # ═══ JUMEIRAH BEACH RESIDENCE (JBR) ════════════════════════
    "Rimal JBR": ["rimal jbr"],
    "Murjan JBR": ["murjan jbr"],
    "Bahar JBR": ["bahar jbr"],
    "Sadaf JBR": ["sadaf jbr"],
    "Amwaj JBR": ["amwaj jbr"],
    "Shams JBR": ["shams jbr"],
    "La Plage": ["la plage jbr"],
    "The Walk JBR": ["the walk"],
    "Address Beach Resort": ["address beach resort jbr"],

    # ═══ DUBAI HILLS ESTATE ═════════════════════════════════════
    "Golf Place": ["golf place 1", "golf place 2", "golf place dubai hills"],
    "Park Ridge": ["park ridge dubai hills", "park ridge tower"],
    "Collective": ["collective dubai hills", "collective 2.0"],
    "Acacia": ["acacia dubai hills"],
    "Mulberry": ["mulberry dubai hills", "mulberry at park heights"],
    "Maple": ["maple 1", "maple 2", "maple 3", "maple dubai hills"],
    "Sidra Villas": ["sidra villas", "sidra 1", "sidra 2", "sidra 3"],
    "Elara Dubai Hills": ["elara at dubai hills"],
    "Mayfair Dubai Hills": ["mayfair dubai hills"],
    "Dubai Hills View": ["dubai hills view"],
    "Lime Gardens": ["lime gardens dubai hills"],
    "Club Villas Dubai Hills": ["club villas"],
    "Fairway Vistas": ["fairway vistas"],
    "Golf Grove": ["golf grove dubai hills"],
    "Emerald Hills": ["emerald hills"],
    "Elvira": ["elvira dubai hills"],
    "Sun": ["sun dubai hills"],
    "Address Hillcrest": ["address hillcrest"],

    # ═══ DUBAI CREEK HARBOUR ════════════════════════════════════
    "Harbour Views": ["harbour views 1", "harbour views 2"],
    "Creek Horizon": ["creek horizon 1", "creek horizon 2"],
    "Creek Gate": ["creek gate 1", "creek gate 2"],
    "Creek Rise": ["creek rise 1", "creek rise 2"],
    "Island Park": ["island park 1", "island park 2"],
    "Surf": ["surf dubai creek"],
    "Bayshore": ["bayshore creek"],
    "Address Harbour Point": ["address harbour point"],
    "The Cove": ["the cove creek harbour", "the cove 1", "the cove 2"],
    "Palace Residences Creek": ["palace residences creek"],
    "Palace Residences North": ["palace residences north"],
    "Vida Creek Harbour": ["vida creek harbour"],
    "Orchid": ["orchid creek harbour"],
    "Cedar": ["cedar creek harbour"],
    "Lotus": ["lotus creek harbour"],

    # ═══ JUMEIRAH VILLAGE CIRCLE (JVC) ══════════════════════════
    "Bloom Heights": ["bloom heights jvc"],
    "Seasons Community": ["seasons community jvc"],
    "Empire Suites": ["empire suites jvc"],
    "Plazzo Residence": ["plazzo residence"],
    "Diamond Views": ["diamond views 1", "diamond views 2",
                      "diamond views 3", "diamond views 4"],
    "Ghalia": ["ghalia jvc"],
    "Park Lane": ["park lane jvc"],
    "Loci Residences": ["loci residences"],
    "Binghatti Phantom": ["binghatti phantom"],
    "Binghatti Orchid": ["binghatti orchid jvc"],
    "Binghatti Luna": ["binghatti luna"],
    "Binghatti Onyx": ["binghatti onyx"],
    "Q Gardens Lofts": ["q gardens lofts", "q gardens"],
    "Oia Residence": ["oia residence jvc"],
    "Me Do Re": ["me do re jvc"],
    "Elz By Danube": ["elz by danube", "elz danube"],
    "Pearlz By Danube": ["pearlz by danube", "pearlz danube"],
    "Fashionz": ["fashionz jvc"],
    "Oxford Terraces": ["oxford terraces jvc"],
    "Oxford Residence": ["oxford residence jvc"],
    "Hameni": ["hameni jvc"],
    "Neva Residences": ["neva residences"],
    "Five JVC": ["five jvc"],
    "Belgravia": ["belgravia 1", "belgravia 2", "belgravia 3",
                  "belgravia square", "belgravia heights"],
    "Emirates Garden": ["emirates garden 1", "emirates garden 2"],
    "Al Burooj Residence": ["al burooj residence"],
    "The Dreamz": ["the dreamz jvc"],

    # ═══ SOBHA HARTLAND ═════════════════════════════════════════
    "Sobha Creek Vistas": ["creek vistas 1", "creek vistas 2",
                           "creek vistas grande", "creek vistas reserve"],
    "Sobha One": ["sobha one", "sobha one tower"],
    "Sobha Verde": ["sobha verde"],
    "Sobha Waves": ["sobha waves", "sobha waves grande"],
    "Hartland Greens": ["hartland greens"],
    "Forest Villas Sobha": ["forest villas sobha"],
    "Gardenia Villas": ["gardenia villas sobha"],
    "The Crest": ["the crest sobha", "sobha the crest"],
    "Hartland Estates": ["hartland estates"],

    # ═══ DAMAC HILLS ═══════════════════════════════════════════
    "Golf Veduta": ["golf veduta damac"],
    "Golf Panorama": ["golf panorama damac"],
    "Golf Horizon": ["golf horizon damac"],
    "Damac Paramount": ["damac paramount hills"],
    "Artesia": ["artesia damac hills"],
    "Carson": ["carson damac hills"],
    "Loreto": ["loreto damac hills"],
    "Navitas": ["navitas damac"],
    "Pelham": ["pelham damac"],
    "Park Residences": ["park residences damac"],
    "Topanga": ["topanga damac"],
    "Brookfield": ["brookfield damac"],
    "Damac Lagoons": ["damac lagoons"],

    # ═══ MBR CITY / MEYDAN ══════════════════════════════════════
    "Meydan One": ["meydan one mall", "meydan one residences"],
    "The Polo Residence": ["polo residence meydan"],
    "Millennium Talia Residences": ["millennium talia"],
    "Azizi Riviera": ["azizi riviera", "azizi riviera 1", "azizi riviera 2",
                      "azizi riviera 3", "azizi riviera 4"],
    "District One": ["district one villas", "d1 residences"],
    "Hartland": ["meydan hartland"],
    "Waves Grande": ["waves grande meydan"],
    "Nad Al Sheba Villas": ["nad al sheba villas"],

    # ═══ CITY WALK ══════════════════════════════════════════════
    "Central Park City Walk": ["central park city walk"],
    "The Centrale": ["the centrale city walk"],
    "Building 15 City Walk": ["building 15 city walk"],
    "Eaton Place": ["eaton place city walk"],

    # ═══ BLUEWATERS ISLAND ══════════════════════════════════════
    "Bluewaters Residences": ["bluewaters residences 1", "bluewaters residences 2",
                               "bluewaters residences 3", "bluewaters residences 4",
                               "bluewaters residences 5", "bluewaters residences 6",
                               "bluewaters residences 7", "bluewaters residences 8",
                               "bluewaters residences 9", "bluewaters residences 10"],
    "Ain Dubai Residences": ["ain dubai residences"],

    # ═══ BUSINESS BAY / CANAL ═══════════════════════════════════
    "DAMAC Residenze": ["damac residenze"],
    "Waves Tower": ["waves tower business bay"],
    "SLS Residences": ["sls residences dubai"],

    # ═══ ARJAN ══════════════════════════════════════════════════
    "Samana Greens": ["samana greens arjan"],
    "Samana Park Views": ["samana park views"],
    "Samana Hills": ["samana hills"],
    "Vincitore Boulevard": ["vincitore boulevard"],
    "Vincitore Palacio": ["vincitore palacio"],
    "Guzel Towers": ["guzel towers"],
    "Rukan Tower": ["rukan tower"],

    # ═══ AL FURJAN ══════════════════════════════════════════════
    "Azizi Feirouz": ["azizi feirouz"],
    "Azizi Pearl": ["azizi pearl"],
    "Azizi Star": ["azizi star"],
    "Azizi Berton": ["azizi berton"],
    "Masakin Al Furjan": ["masakin al furjan"],
    "Chaimaa Avenue": ["chaimaa avenue"],
    "Genesis By Meraki": ["genesis by meraki"],

    # ═══ EMIRATES HILLS / MEADOWS / SPRINGS ═════════════════════
    "Emirates Hills Villas": ["emirates hills villas"],
    "Meadows Villas": ["meadows 1", "meadows 2", "meadows 3",
                       "meadows 4", "meadows 5", "meadows 6",
                       "meadows 7", "meadows 8", "meadows 9"],
    "Springs Villas": ["springs 1", "springs 2", "springs 3",
                       "springs 4", "springs 5", "springs 6",
                       "springs 7", "springs 8", "springs 9",
                       "springs 10", "springs 11", "springs 12",
                       "springs 13", "springs 14"],
    "The Lakes": ["the lakes dubai"],
    "The Greens": ["the greens dubai"],
    "The Views": ["the views dubai"],

    # ═══ ARABIAN RANCHES ═════════════════════════════════════════
    "Arabian Ranches Villas": ["arabian ranches 1", "arabian ranches 2"],
    "Mirador": ["mirador arabian ranches"],
    "Palmera": ["palmera arabian ranches"],
    "Alvorada": ["alvorada arabian ranches"],
    "Savannah": ["savannah arabian ranches"],
    "Saheel": ["saheel arabian ranches"],
    "Hattan": ["hattan arabian ranches"],
    "Rosa": ["rosa arabian ranches 2"],
    "Rasha": ["rasha arabian ranches 2"],
    "Lila": ["lila arabian ranches 2"],
    "Camelia": ["camelia arabian ranches 2"],
    "Yasmin": ["yasmin arabian ranches"],
    "Bliss": ["bliss arabian ranches 3"],
    "Spring": ["spring arabian ranches 3"],
    "Caya": ["caya arabian ranches 3"],
    "June": ["june arabian ranches 3"],
    "Ruba": ["ruba arabian ranches 3"],

    # ═══ DUBAI SOUTH / EMAAR SOUTH ══════════════════════════════
    "Golf Links": ["golf links emaar south"],
    "Golf Views": ["golf views emaar south"],
    "Expo Golf Villas": ["expo golf villas"],
    "Urbana": ["urbana emaar south"],
    "Greenview": ["greenview emaar south"],

    # ═══ TOWN SQUARE ════════════════════════════════════════════
    "Zahra Apartments": ["zahra apartments town square"],
    "Hayat Boulevard": ["hayat boulevard town square"],
    "Safi Apartments": ["safi apartments town square"],
    "Rawda Apartments": ["rawda apartments town square"],
    "Jenna": ["jenna main square town square"],
    "Warda Apartments": ["warda town square"],

    # ═══ DAMAC LAGOONS ══════════════════════════════════════════
    "Marbella Lagoons": ["marbella damac lagoons"],
    "Nice Lagoons": ["nice damac lagoons"],
    "Venice Lagoons": ["venice damac lagoons"],
    "Santorini Lagoons": ["santorini damac lagoons"],
    "Malta Lagoons": ["malta damac lagoons"],
    "Costa Brava": ["costa brava damac lagoons"],
    "Mykonos Lagoons": ["mykonos damac lagoons"],
    "Morocco Lagoons": ["morocco damac lagoons"],
    "Ibiza Lagoons": ["ibiza damac lagoons"],
    "Portofino Lagoons": ["portofino damac lagoons"],

    # ═══ BINGHATTI ═══════════════════════════════════════════════
    "Binghatti Nova": ["binghatti nova"],
    "Binghatti Rose": ["binghatti rose"],
    "Binghatti Amber": ["binghatti amber"],
    "Binghatti Azure": ["binghatti azure"],
    "Binghatti Stars": ["binghatti stars"],
    "Binghatti Avenue": ["binghatti avenue"],
    "Binghatti House": ["binghatti house"],
    "Binghatti Gateway": ["binghatti gateway"],
    "Binghatti Canal": ["binghatti canal"],
    "Bugatti Residences": ["bugatti residences binghatti"],
    "Mercedes Benz Places": ["mercedes benz places binghatti"],
    "Jacob & Co Residences": ["jacob co residences binghatti"],

    # ═══ DANUBE ══════════════════════════════════════════════════
    "Elitz By Danube": ["elitz by danube", "elitz danube", "elitz 1", "elitz 2"],
    "Sportz By Danube": ["sportz by danube"],
    "Timez By Danube": ["timez by danube"],
    "Wavez By Danube": ["wavez by danube"],
    "Fashionz By Danube": ["fashionz by danube"],
    "Gemz By Danube": ["gemz by danube"],
    "Lawnz By Danube": ["lawnz by danube"],
    "Miraclz By Danube": ["miraclz by danube"],
    "Olivz By Danube": ["olivz by danube"],
    "Skyz By Danube": ["skyz by danube"],
    "Viewz By Danube": ["viewz by danube"],
    "Bayz 102 By Danube": ["bayz 102"],
    "Opalz By Danube": ["opalz by danube"],
    "Aquaz By Danube": ["aquaz by danube"],

    # ═══ ELLINGTON ════════════════════════════════════════════════
    "Belgravia Ellington": ["belgravia ellington"],
    "DT1 Ellington": ["dt1 ellington"],
    "Wilton Park Residences": ["wilton park"],
    "Wilton Terraces": ["wilton terraces 1", "wilton terraces 2"],
    "Claydon House": ["claydon house"],
    "Ellington House": ["ellington house 1", "ellington house 2",
                        "ellington house 3", "ellington house 4"],
    "The Highbury": ["the highbury"],
    "Mercer House": ["mercer house"],
    "The Quayside": ["the quayside"],
    "Crestmark": ["crestmark ellington"],
    "Palmiera Ellington": ["palmiera ellington"],

    # ═══ TILAL AL GHAF ════════════════════════════════════════════
    "Elan Tilal Al Ghaf": ["elan tilal al ghaf"],
    "Serenity Mansions": ["serenity mansions tilal"],
    "Plagette 32": ["plagette 32"],
    "Harmony": ["harmony tilal al ghaf"],
    "Aura Gardens": ["aura gardens tilal"],

    # ═══ SILICON OASIS ════════════════════════════════════════════
    "Axis Residences": ["axis residences silicon oasis"],
    "Cedre Villas": ["cedre villas"],
    "Semmer Villas": ["semmer villas"],
    "Silicon Gates": ["silicon gates 1", "silicon gates 2",
                      "silicon gates 3", "silicon gates 4"],
    "Palatium Residences": ["palatium residences"],

    # ═══ INTERNATIONAL CITY ═══════════════════════════════════════
    "Persia Cluster": ["persia cluster international city"],
    "China Cluster": ["china cluster international city"],
    "England Cluster": ["england cluster international city"],
    "Morocco Cluster": ["morocco cluster international city"],
    "Spain Cluster": ["spain cluster international city"],
    "France Cluster": ["france cluster international city"],
    "Greece Cluster": ["greece cluster international city"],
    "Italy Cluster": ["italy cluster international city"],
    "Russia Cluster": ["russia cluster international city"],
    "Emirates Cluster": ["emirates cluster international city"],

    # ═══ AL REEM ISLAND (ABU DHABI) ═══════════════════════════════
    "Shams Abu Dhabi": ["shams 1 abu dhabi", "shams 2 abu dhabi"],
    "The Gate Tower": ["gate tower 1", "gate tower 2", "gate tower 3"],
    "Sigma Towers": ["sigma towers reem"],
    "Sun Tower": ["sun tower reem"],
    "Sky Tower": ["sky tower reem"],
    "Najmat Abu Dhabi": ["najmat reem"],
    "Marina Square": ["marina square reem"],
    "Mangrove Place": ["mangrove place"],
    "Tala Tower": ["tala tower reem"],
    "Burooj Views": ["burooj views"],
    "C5 Tower": ["c5 tower reem"],
    "Hydra Avenue": ["hydra avenue reem"],
    "Meera": ["meera reem island"],
    "Maha": ["maha reem island"],
    "Reflection": ["reflection reem island"],
    "Pixel": ["pixel reem island"],
    "Eaton Place Abu Dhabi": ["eaton place reem"],
    "Al Maha Tower": ["al maha tower reem"],

    # ═══ SAADIYAT ISLAND (ABU DHABI) ══════════════════════════════
    "Mamsha Al Saadiyat": ["mamsha al saadiyat"],
    "Saadiyat Reserve": ["saadiyat reserve"],
    "The Collection": ["the collection saadiyat"],
    "Nudra": ["nudra saadiyat"],
    "Hidd Al Saadiyat": ["hidd al saadiyat"],
    "Saadiyat Beach Villas": ["saadiyat beach villas"],
    "Jawaher Saadiyat": ["jawaher saadiyat"],

    # ═══ YAS ISLAND (ABU DHABI) ═══════════════════════════════════
    "Yas Acres": ["yas acres"],
    "Ansam": ["ansam yas island"],
    "Water's Edge": ["waters edge yas"],
    "Lea": ["lea yas island"],
    "Reflections Yas Island": ["reflections yas"],
    "Mayan": ["mayan yas island"],
    "Noya": ["noya yas island"],

    # ═══ AL MARJAN ISLAND (RAK) ═══════════════════════════════════
    "Al Hamra Village": ["al hamra village rak"],
    "Mina Al Arab": ["mina al arab rak"],
    "Gateway Residences": ["gateway residences rak"],
    "Ellington Beach House RAK": ["ellington beach house rak"],
    "Wynn Residences": ["wynn residences rak"],
    "Anantara Mina Al Arab": ["anantara mina al arab"],
    "Rosso Bay Residences": ["rosso bay"],

    # ═══ PORTO PLAYA / DUBAI HARBOUR ══════════════════════════════
    "Porto Playa": ["porto playa dubai harbour"],
    "Beach Mansion": ["beach mansion emaar beachfront"],
    "Grand Bleu Tower": ["grand bleu tower emaar"],
    "Sunrise Bay": ["sunrise bay emaar beachfront"],
    "Marina Vista": ["marina vista emaar beachfront"],
    "Beach Vista": ["beach vista emaar beachfront"],
    "Beach Isle": ["beach isle emaar beachfront"],
    "Seapoint": ["seapoint emaar beachfront"],
    "Six Senses Residences Dubai Marina": ["six senses residences"],

    # ═══ JUMEIRAH GOLF ESTATES ════════════════════════════════════
    "Fire Villas": ["fire villas jge"],
    "Earth Villas": ["earth villas jge"],
    "Flame Tree Ridge": ["flame tree ridge"],
    "Redwood Park": ["redwood park jge"],
    "Whispering Pines": ["whispering pines jge"],
    "Aston Martin Residences JGE": ["aston martin residences jge"],

    # ═══ THE VALLEY ═══════════════════════════════════════════════
    "Nara": ["nara the valley"],
    "Elora": ["elora the valley"],
    "Eden": ["eden the valley"],
    "Orla": ["orla the valley"],
    "Farm Gardens": ["farm gardens the valley"],

    # ═══ VILLANOVA ═══════════════════════════════════════════════
    "La Rosa": ["la rosa villanova"],
    "La Quinta": ["la quinta villanova"],
    "La Violeta": ["la violeta villanova"],
    "La Tilia": ["la tilia villanova"],
    "Amaranta": ["amaranta villanova"],

    # ═══ MUDON ═══════════════════════════════════════════════════
    "Mudon Views": ["mudon views"],
    "Al Salam": ["al salam mudon"],
    "Al Ranim": ["al ranim mudon"],
    "Naseem": ["naseem mudon"],
}

# ── АЛИАСЫ РАЙОНОВ (сокращения → официальное название) ──────────
AREA_ALIASES = {
    "bb": "Business Bay",
    "jvc": "Jumeirah Village Circle",
    "jvt": "Jumeirah Village Triangle",
    "dt": "Downtown Dubai",
    "downtown": "Downtown Dubai",
    "dm": "Dubai Marina",
    "marina": "Dubai Marina",
    "dh": "Dubai Hills Estate",
    "dubai hills": "Dubai Hills Estate",
    "palm": "Palm Jumeirah",
    "pj": "Palm Jumeirah",
    "creek": "Dubai Creek Harbour",
    "dch": "Dubai Creek Harbour",
    "es": "Emaar South",
    "furjan": "Al Furjan",
    "al furjan": "Al Furjan",
    "arjan": "Arjan",
    "damac hills": "Damac Hills",
    "ds": "Dubai South",
    "jbr": "Jumeirah Beach Residence",
    "meydan": "Meydan",
    "sc": "Sports City",
    "dsc": "Dubai Sports City",
    "ic": "International City",
    "mbr": "Mohammed Bin Rashid City",
    "mbr city": "Mohammed Bin Rashid City",
    "sobha": "Sobha Hartland",
    "bluewaters": "Bluewaters Island",
    "citywalk": "City Walk",
    "city walk": "City Walk",
    "difc": "DIFC",
    "motor city": "Motor City",
    "rak": "Ras Al Khaimah",
    "abudhabi": "Abu Dhabi",
    "ad": "Abu Dhabi",
    "sharjah": "Sharjah",
    "reem": "Al Reem Island",
    "saadiyat": "Saadiyat Island",
    "yas": "Yas Island",
    "hamra": "Al Hamra Village",
    "marjan": "Al Marjan Island",
    "silicon": "Silicon Oasis",
    "dso": "Silicon Oasis",
    "meadows": "Meadows",
    "springs": "Springs",
    "lakes": "The Lakes",
    "greens": "The Greens",
    "views": "The Views",
    "ranches": "Arabian Ranches",
    "ar1": "Arabian Ranches",
    "ar2": "Arabian Ranches 2",
    "ar3": "Arabian Ranches 3",
    "tilal": "Tilal Al Ghaf",
    "the valley": "The Valley",
    "town square": "Town Square",
    "villanova": "Villanova",
    "mudon": "Mudon",
    "harbour": "Dubai Harbour",
    "dubai harbour": "Dubai Harbour",
    "beachfront": "Dubai Harbour",
    "emaar beachfront": "Dubai Harbour",
    "jge": "Jumeirah Golf Estates",
    "golf estates": "Jumeirah Golf Estates",
    "production city": "Dubai Production City",
    "impz": "Dubai Production City",
    "studio city": "Dubai Studio City",
    "barsha": "Al Barsha",
    "tecom": "Barsha Heights",
    "barsha heights": "Barsha Heights",
    "garhoud": "Garhoud",
    "festival city": "Dubai Festival City",
    "al jaddaf": "Al Jaddaf",
    "jaddaf": "Al Jaddaf",
    "khail heights": "Al Khail Heights",
}

# ── ОПРЕДЕЛЕНИЕ ЭМИРАТА ПО РАЙОНУ ───────────────────────────────
EMIRATE_BY_AREA = {}

for area in DUBAI_AREAS:
    EMIRATE_BY_AREA[area.lower()] = "Dubai"

for area in ABU_DHABI_AREAS:
    EMIRATE_BY_AREA[area.lower()] = "Abu Dhabi"

for area in RAK_AREAS:
    EMIRATE_BY_AREA[area.lower()] = "Ras Al Khaimah"

for area in SHARJAH_AREAS:
    EMIRATE_BY_AREA[area.lower()] = "Sharjah"

# Добавляем алиасы
EMIRATE_BY_AREA["jvc"] = "Dubai"
EMIRATE_BY_AREA["jvt"] = "Dubai"
EMIRATE_BY_AREA["jbr"] = "Dubai"
EMIRATE_BY_AREA["difc"] = "Dubai"
EMIRATE_BY_AREA["dso"] = "Dubai"
EMIRATE_BY_AREA["mbr city"] = "Dubai"
EMIRATE_BY_AREA["reem island"] = "Abu Dhabi"
EMIRATE_BY_AREA["al reem"] = "Abu Dhabi"
EMIRATE_BY_AREA["yas"] = "Abu Dhabi"
EMIRATE_BY_AREA["saadiyat"] = "Abu Dhabi"
EMIRATE_BY_AREA["marjan"] = "Ras Al Khaimah"
EMIRATE_BY_AREA["al hamra"] = "Ras Al Khaimah"
EMIRATE_BY_AREA["mina al arab"] = "Ras Al Khaimah"
EMIRATE_BY_AREA["aljada"] = "Sharjah"
EMIRATE_BY_AREA["tilal city"] = "Sharjah"

# ── ОБРАТНЫЙ ИНДЕКС: alias → canonical building name ────────────
BUILDING_ALIASES = {}
for canonical, aliases in DUBAI_BUILDINGS.items():
    BUILDING_ALIASES[canonical.lower()] = canonical
    for alias in aliases:
        BUILDING_ALIASES[alias.lower()] = canonical

# ── ФУНКЦИЯ НОРМАЛИЗАЦИИ НАЗВАНИЯ БИЛДИНГА ───────────────────────
def normalize_building_name(raw_name: str) -> str | None:
    """
    Принимает сырое название из объявления.
    Возвращает нормализованное официальное название или None.
    """
    if not raw_name:
        return None

    # Очистка
    import re
    clean = raw_name.strip()
    clean = re.sub(r'[*#@🏠🏡🏢🏗🔑💰📍]+', '', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    clean = clean.lower()

    # Прямое совпадение
    if clean in BUILDING_ALIASES:
        return BUILDING_ALIASES[clean]

    # Fuzzy matching
    try:
        from rapidfuzz import process, fuzz
        result = process.extractOne(
            clean,
            BUILDING_ALIASES.keys(),
            scorer=fuzz.token_sort_ratio,
            score_cutoff=75
        )
        if result:
            return BUILDING_ALIASES[result[0]]
    except ImportError:
        pass

    return None


def normalize_area(raw_area: str) -> str | None:
    """Нормализует название района."""
    if not raw_area:
        return None
    clean = raw_area.strip().lower()
    if clean in AREA_ALIASES:
        return AREA_ALIASES[clean]
    all_areas = DUBAI_AREAS + ABU_DHABI_AREAS + RAK_AREAS + SHARJAH_AREAS
    for area in all_areas:
        if clean == area.lower():
            return area
    try:
        from rapidfuzz import process, fuzz
        result = process.extractOne(
            clean,
            [a.lower() for a in all_areas],
            scorer=fuzz.token_sort_ratio,
            score_cutoff=80
        )
        if result:
            idx = [a.lower() for a in all_areas].index(result[0])
            return all_areas[idx]
    except ImportError:
        pass
    return None


def get_emirate(area: str) -> str:
    """Определяет эмират по названию района."""
    if not area:
        return "Dubai"
    normalized = normalize_area(area)
    if normalized:
        return EMIRATE_BY_AREA.get(normalized.lower(), "Dubai")
    return EMIRATE_BY_AREA.get(area.lower(), "Dubai")


if __name__ == "__main__":
    # Тест
    tests = [
        "burj khalifa",
        "elitz by danube",
        "princess tower marina",
        "damac hills",
        "arabian ranches 2",
        "sobha creek vistas",
    ]
    for t in tests:
        result = normalize_building_name(t)
        print(f"{t} → {result}")
