"""
parser_engine.py — Smart Entity Recognition & Validation Engine
Полная реализация ТЗ блоки 1-9:

Иерархия определения локации (ТЗ раздел 4):
1. Прямое упоминание эмирата
2. Район → эмират
3. Здание → проверка в базе → район → эмират
4. Девелопер / master community → эмират
5. Амбигюные районы → доп. признаки (здание, цена)
6. Nominatim fallback
7. needs_manual_review = True

Здание — источник правды:
- Если здание есть в базе → берём район и эмират из базы
- Если район из текста не совпадает с базой → низкий confidence
- Если здание не в базе → Nominatim
"""
import re
import os
import json
import time as _time
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
# 1. EMIRATES
# ══════════════════════════════════════════════════════════════════════════════

import json as _json
import os as _os

def _load_rules() -> dict:
    path = _os.path.join(_os.path.dirname(__file__), 'parsing_rules.json')
    try:
        with open(path, encoding='utf-8') as f:
            return _json.load(f)
    except Exception as e:
        print(f'[rules] Failed to load parsing_rules.json: {e}')
        return {}

RULES = _load_rules()

def _get_abbr(text: str) -> str:
    abbr = RULES.get('abbreviations', {})
    words = text.split()
    result = []
    for w in words:
        result.append(abbr.get(w.upper(), w))
    return ' '.join(result)

EMIRATES = {
    "Dubai":           ["dubai", "dxb", "dbx", "دبي", "dubay"],
    "Abu Dhabi":       ["abu dhabi", "abudhabi", "auh", "ad", "أبوظبي", "abu-dhabi"],
    "Sharjah":         ["sharjah", "shj", "الشارقة"],
    "Ras Al Khaimah":  ["ras al khaimah", "rak", "رأس الخيمة"],
    "Ajman":           ["ajman", "ajm", "عجمان"],
    "Fujairah":        ["fujairah", "fuj", "الفجيرة"],
    "Umm Al Quwain":   ["umm al quwain", "uaq", "أم القيوين"],
}

# ══════════════════════════════════════════════════════════════════════════════
# 2. AREAS (with emirate mapping and aliases)
# ambiguous=True → area exists in multiple emirates
# ══════════════════════════════════════════════════════════════════════════════
AREAS = {
    # Dubai
    "Downtown Dubai":            {"emirate": "Dubai",     "aliases": ["downtown", "dt", "dtdxb", "burj khalifa area", "old town"]},
    "Business Bay":              {"emirate": "Dubai",     "aliases": ["bb", "biz bay", "businessbay", "бизнес бей", "бизнес-бей", "бизнес бэй"]},
    "Dubai Marina":              {"emirate": "Dubai",     "aliases": ["marina", "dm", "the marina"]},
    "Palm Jumeirah":             {"emirate": "Dubai",     "aliases": ["palm", "pj", "the palm", "palm jumeriah", "palm jumeira"]},
    "Jumeirah Village Circle":   {"emirate": "Dubai",     "aliases": ["jvc", "jumeirah village", "jumeirah village circle"]},
    "Jumeirah Village Triangle": {"emirate": "Dubai",     "aliases": ["jvt"]},
    "Jumeirah Beach Residence":  {"emirate": "Dubai",     "aliases": ["jbr", "the walk", "jumeirah beach"]},
    "Dubai Hills Estate":        {"emirate": "Dubai",     "aliases": ["dubai hills", "dhe"]},
    "Dubai Creek Harbour":       {"emirate": "Dubai",     "aliases": ["creek harbour", "dch", "dubai creek harbour"]},
    "MBR City":                  {"emirate": "Dubai",     "aliases": ["mbr", "mohammed bin rashid city", "meydan one"]},
    "Meydan":                    {"emirate": "Dubai",     "aliases": ["meydan city", "nad al sheba"]},
    "Emaar South":               {"emirate": "Dubai",     "aliases": ["emaar south"]},
    "Al Furjan":                 {"emirate": "Dubai",     "aliases": ["furjan", "al-furjan"]},
    "Arjan":                     {"emirate": "Dubai",     "aliases": ["arjan", "arjan dubailand"]},
    "DAMAC Hills":               {"emirate": "Dubai",     "aliases": ["damac hills", "akoya"]},
    "DAMAC Hills 2":             {"emirate": "Dubai",     "aliases": ["damac hills 2", "akoya oxygen"]},
    "Bluewaters Island":         {"emirate": "Dubai",     "aliases": ["bluewaters", "blue waters"]},
    "Dubai South":               {"emirate": "Dubai",     "aliases": ["dubai world central", "dwc", "expo city"]},
    "Jumeirah":                  {"emirate": "Dubai",     "aliases": ["jumeira", "jumeirah 1", "jumeirah 2", "jumeirah 3"]},
    "Sports City":               {"emirate": "Dubai",     "aliases": ["dsc", "dubai sports city"]},
    "Silicon Oasis":             {"emirate": "Dubai",     "aliases": ["dso", "dubai silicon oasis"]},
    "International City":        {"emirate": "Dubai",     "aliases": ["ic", "intl city", "dragon mart"]},
    "Dubai Harbour":             {"emirate": "Dubai",     "aliases": ["harbour", "dubai harbour"]},
    "City Walk":                 {"emirate": "Dubai",     "aliases": ["cw", "citywalk"]},
    "DIFC":                      {"emirate": "Dubai",     "aliases": ["dubai international financial centre", "financial centre"]},
    "Barsha Heights":            {"emirate": "Dubai",     "aliases": ["tecom", "al barsha heights"]},
    "Al Barsha":                 {"emirate": "Dubai",     "aliases": ["al barsha", "barsha", "al barsha 1", "al barsha 2", "al barsha 3"]},
    "Sobha Hartland":            {"emirate": "Dubai",     "aliases": ["sobha", "hartland", "sobha hartland 2"]},
    "Motor City":                {"emirate": "Dubai",     "aliases": ["motorcity"]},
    "La Mer":                    {"emirate": "Dubai",     "aliases": ["la mer", "la mer jumeirah"]},
    "Discovery Gardens":         {"emirate": "Dubai",     "aliases": ["dg", "discovery gardens"]},
    "The Valley":                {"emirate": "Dubai",     "aliases": ["emaar valley", "valley"]},
    "Dubailand":                 {"emirate": "Dubai",     "aliases": ["dubai land", "liwan", "villanova"]},
    "Arabian Ranches":           {"emirate": "Dubai",     "aliases": ["arabian ranches", "arabian ranch"]},
    "Arabian Ranches 2":         {"emirate": "Dubai",     "aliases": ["arabian ranches 2", "ar2"]},
    "Arabian Ranches 3":         {"emirate": "Dubai",     "aliases": ["arabian ranches 3", "ar3"]},
    "Tilal Al Ghaf":             {"emirate": "Dubai",     "aliases": ["tilal al ghaf", "tilal"]},
    "DAMAC Lagoons":             {"emirate": "Dubai",     "aliases": ["damac lagoons"]},
    "Mudon":                     {"emirate": "Dubai",     "aliases": ["mudon"]},
    "Town Square":               {"emirate": "Dubai",     "aliases": ["town square", "nshama"]},
    "Jumeirah Golf Estates":     {"emirate": "Dubai",     "aliases": ["jge", "jumeirah golf"]},
    "Emirates Hills":            {"emirate": "Dubai",     "aliases": ["emirates hills"]},
    "The Meadows":               {"emirate": "Dubai",     "aliases": ["meadows"]},
    "The Springs":               {"emirate": "Dubai",     "aliases": ["springs"]},
    "The Lakes":                 {"emirate": "Dubai",     "aliases": ["lakes"]},
    "The Greens":                {"emirate": "Dubai",     "aliases": ["greens"]},
    "The Views":                 {"emirate": "Dubai",     "aliases": ["views"]},
    "Bur Dubai":                 {"emirate": "Dubai",     "aliases": ["bur dubai", "burdubai"]},
    "Deira":                     {"emirate": "Dubai",     "aliases": ["deira"]},
    "Mirdif":                    {"emirate": "Dubai",     "aliases": ["mirdif", "al mirdif"]},
    "Al Quoz":                   {"emirate": "Dubai",     "aliases": ["al quoz", "quoz"]},
    "Dubai Investment Park":     {"emirate": "Dubai",     "aliases": ["dip"]},
    "Al Jaddaf":                 {"emirate": "Dubai",     "aliases": ["al jaddaf", "jaddaf"]},
    "Dubai Festival City":       {"emirate": "Dubai",     "aliases": ["festival city"]},
    "Madinat Jumeirah":          {"emirate": "Dubai",     "aliases": ["madinat jumeirah", "mjl"]},
    "Studio City":               {"emirate": "Dubai",     "aliases": ["studio city", "dubai studio city"]},
    "Jumeirah Islands":          {"emirate": "Dubai",     "aliases": ["jumeirah islands"]},
    "Jumeirah Park":             {"emirate": "Dubai",     "aliases": ["jumeirah park"]},
    "Karama":                    {"emirate": "Dubai",     "aliases": ["karama", "al karama"]},
    "Satwa":                     {"emirate": "Dubai",     "aliases": ["satwa", "al satwa"]},
    "Al Sufouh":                 {"emirate": "Dubai",     "aliases": ["al sufouh", "sufouh", "knowledge village", "media city"]},
    # Abu Dhabi
    "Yas Island":                {"emirate": "Abu Dhabi", "aliases": ["yas", "yas island"]},
    "Saadiyat Island":           {"emirate": "Abu Dhabi", "aliases": ["saadiyat", "saadiyat island"]},
    "Al Reem Island":            {"emirate": "Abu Dhabi", "aliases": ["reem island", "al reem", "reem"]},
    "Al Raha":                   {"emirate": "Abu Dhabi", "aliases": ["al raha", "al raha beach"]},
    "Al Maryah Island":          {"emirate": "Abu Dhabi", "aliases": ["maryah island", "al maryah"]},
    "Masdar City":               {"emirate": "Abu Dhabi", "aliases": ["masdar"]},
    "Khalifa City":              {"emirate": "Abu Dhabi", "aliases": ["khalifa city"]},
    # RAK
    "Al Marjan Island":          {"emirate": "Ras Al Khaimah", "aliases": ["marjan island", "al marjan", "marjan"]},
    "Mina Al Arab":              {"emirate": "Ras Al Khaimah", "aliases": ["mina arab", "mina al arab"]},
    "Al Hamra Village":          {"emirate": "Ras Al Khaimah", "aliases": ["al hamra", "hamra village"]},
    "Hayat Island":              {"emirate": "Ras Al Khaimah", "aliases": ["hayat island"]},
    # Sharjah
    "Al Zahia":                  {"emirate": "Sharjah",   "aliases": ["zahia", "al zahia"]},
    "Aljada":                    {"emirate": "Sharjah",   "aliases": ["aljada", "al jada"]},
    "Tilal City":                {"emirate": "Sharjah",   "aliases": ["tilal city"]},
    # AMBIGUOUS — exist in multiple emirates
    "Al Nahda":                  {"emirate": None, "ambiguous": True, "possible": ["Dubai", "Sharjah"], "aliases": ["al nahda", "nahda"]},
    "Al Qusais":                 {"emirate": None, "ambiguous": True, "possible": ["Dubai", "Sharjah"], "aliases": ["al qusais", "qusais"]},
    "Al Rashidiya":              {"emirate": None, "ambiguous": True, "possible": ["Dubai", "Ajman"],   "aliases": ["rashidiya", "al rashidiya"]},
    "Al Nuaimiya":               {"emirate": None, "ambiguous": True, "possible": ["Sharjah", "Ajman"],"aliases": ["al nuaimiya", "nuaimiya"]},
    # Dubai extras
    "Jumeirah Lake Towers":      {"emirate": "Dubai",     "aliases": ["jlt", "jumeirah lake towers", "jlt dubai"]},
    "Maritime City":             {"emirate": "Dubai",     "aliases": ["dubai maritime city", "maritime"]},
    "Al Wasl":                   {"emirate": "Dubai",     "aliases": ["al wasl"]},
    "Al Safa":                   {"emirate": "Dubai",     "aliases": ["al safa"]},
    "Creek Beach":               {"emirate": "Dubai",     "aliases": ["creek beach", "creek island"]},
    "Mina Rashid":               {"emirate": "Dubai",     "aliases": ["mina rashid", "rashid harbour"]},
    # Dubai — new areas/communities (added 2026-05-20)
    "Dubai Islands":             {"emirate": "Dubai",     "aliases": ["dubai islands", "dubai island"]},
    "District One":              {"emirate": "Dubai",     "aliases": ["district one", "d1", "district 1"]},
    "NARA":                      {"emirate": "Dubai",     "aliases": ["nara"]},
    "The Wilds":                 {"emirate": "Dubai",     "aliases": ["the wilds", "wilds 1", "wilds 2"]},
    "Wadi Al Safa":              {"emirate": "Dubai",     "aliases": ["wadi al safa", "wadi safa"]},
    "Emaar Beachfront":          {"emirate": "Dubai",     "aliases": ["emaar beachfront", "beachfront emaar", "beachfront"]},
    "Peninsula":                 {"emirate": "Dubai",     "aliases": ["peninsula"]},
    # Ambiguous — exists in multiple emirates
    "Al Mamzar":                 {"emirate": None, "ambiguous": True, "possible": ["Dubai", "Sharjah"], "aliases": ["al mamzar", "mamzar", "al-mamzar"]},
}

# ══════════════════════════════════════════════════════════════════════════════
# AREA ABBREVIATIONS — short codes → canonical area names
# ══════════════════════════════════════════════════════════════════════════════
AREA_ABBR = {
    "JLT":  "Jumeirah Lake Towers",
    "JVC":  "Jumeirah Village Circle",
    "JVT":  "Jumeirah Village Triangle",
    "JBR":  "Jumeirah Beach Residence",
    "MBR":  "MBR City",
    "DIFC": "DIFC",
    "DSO":  "Silicon Oasis",
    "DIP":  "Dubai Investment Park",
    "DCH":  "Dubai Creek Harbour",
    "DH":   "Dubai Hills Estate",
    "DHE":  "Dubai Hills Estate",
    "BB":   "Business Bay",
    "BBY":  "Business Bay",
    "DM":   "Dubai Marina",
    "PJ":   "Palm Jumeirah",
    "DFC":  "Dubai Festival City",
    "DXB":  "Dubai",
    "DBX":  "Dubai",
    "SZR":  "Sheikh Zayed Road",
    "IC":   "International City",
    "SC":   "Sports City",
    "MC":   "Motor City",
    "DH2":  "DAMAC Hills 2",
    "AR":   "Arabian Ranches",
    "AR2":  "Arabian Ranches 2",
    "AR3":  "Arabian Ranches 3",
    "MJL":  "Madinat Jumeirah",
}

# ══════════════════════════════════════════════════════════════════════════════
# 3. BUILDINGS DATABASE
# building_name → {area, emirate, developer, aliases}
# This is the SOURCE OF TRUTH — if building found here,
# we know area and emirate for certain
# ══════════════════════════════════════════════════════════════════════════════
BUILDINGS_DB = {
    # Downtown Dubai
    "Burj Khalifa":                    {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "The Address Downtown":            {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "The Address Boulevard":           {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "The Grande":                      {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "W Residence":           {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Marriott"},
    "W Residences":          {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Marriott"},
    "Sobha One":             {"area": "Sobha Hartland", "emirate": "Dubai", "developer": "Sobha"},
    "Creek Horizon":         {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Creek Gate":            {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Island Park":           {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Golf Place":            {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Maple":                 {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Sidra":                 {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Elara":                 {"area": "Palm Jumeirah", "emirate": "Dubai", "developer": "Nakheel"},
    "Oceana":                {"area": "Palm Jumeirah", "emirate": "Dubai", "developer": "Nakheel"},
    "Shoreline":             {"area": "Palm Jumeirah", "emirate": "Dubai", "developer": "Nakheel"},
    "The 8":                 {"area": "Palm Jumeirah", "emirate": "Dubai", "developer": "Nakheel"},
    "One at Palm":           {"area": "Palm Jumeirah", "emirate": "Dubai", "developer": "Omniyat"},
    "Dorchester":            {"area": "Business Bay", "emirate": "Dubai", "developer": "Omniyat"},
    "The Opus":              {"area": "Business Bay", "emirate": "Dubai", "developer": "Omniyat"},
    "Aykon City":            {"area": "Business Bay", "emirate": "Dubai", "developer": "DAMAC"},
    "IL Primo":                        {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Opera Grand":                     {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Burj Crown":                      {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Burj Royale":                     {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Boulevard Point":                 {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Act One Act Two":                 {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Forte Tower 1":                   {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Forte Tower 2":                   {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Downtown Views":                  {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Downtown Views II":               {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Burj Vista Tower 1":              {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Burj Vista Tower 2":              {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Standpoint Tower A":              {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Standpoint Tower B":              {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "29 Boulevard":                    {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "8 Boulevard Walk":                {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "South Ridge Tower 1":             {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "South Ridge Tower 2":             {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "South Ridge Tower 3":             {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar"},
    "Grande Signature Residences":     {"area": "Downtown Dubai", "emirate": "Dubai", "developer": "Emaar", "aliases": ["grande signature", "grande residences"]},
    # Business Bay
    "DAMAC Maison Prive":              {"area": "Business Bay",   "emirate": "Dubai", "developer": "DAMAC"},
    "SLS Dubai":                       {"area": "Business Bay",   "emirate": "Dubai", "developer": "WOW"},
    "The Opus":                        {"area": "Business Bay",   "emirate": "Dubai", "developer": "Omniyat"},
    "Aykon City Tower A":              {"area": "Business Bay",   "emirate": "Dubai", "developer": "DAMAC"},
    "Aykon City Tower B":              {"area": "Business Bay",   "emirate": "Dubai", "developer": "DAMAC"},
    "One Business Bay":                {"area": "Business Bay",   "emirate": "Dubai", "developer": "Omniyat"},
    "Vera Residences":                 {"area": "Business Bay",   "emirate": "Dubai", "developer": "Dar Al Arkan"},
    "Merano Tower":                    {"area": "Business Bay",   "emirate": "Dubai", "developer": "Al Barari"},
    "Al Habtoor City":                 {"area": "Business Bay",   "emirate": "Dubai", "developer": "Al Habtoor"},
    "Peninsula One":                   {"area": "Business Bay",   "emirate": "Dubai", "developer": "Select Group"},
    "Peninsula Two":                   {"area": "Business Bay",   "emirate": "Dubai", "developer": "Select Group"},
    "Peninsula Three":                 {"area": "Business Bay",   "emirate": "Dubai", "developer": "Select Group"},
    "Peninsula Four":                  {"area": "Business Bay",   "emirate": "Dubai", "developer": "Select Group"},
    "The Sterling East":               {"area": "Business Bay",   "emirate": "Dubai", "developer": "Omniyat"},
    "The Sterling West":               {"area": "Business Bay",   "emirate": "Dubai", "developer": "Omniyat"},
    "Volante":                         {"area": "Business Bay",   "emirate": "Dubai", "developer": "Select Group"},
    "Executive Bay":                   {"area": "Business Bay",   "emirate": "Dubai", "developer": "Tameer"},
    "Bay's Edge":                      {"area": "Business Bay",   "emirate": "Dubai", "developer": "Sobha"},
    "Capital Bay Tower A":             {"area": "Business Bay",   "emirate": "Dubai", "developer": "MAG"},
    "Capital Bay Tower B":             {"area": "Business Bay",   "emirate": "Dubai", "developer": "MAG"},
    # Dubai Marina
    "Marina Gate 1":                   {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Select Group"},
    "Marina Gate 2":                   {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Select Group"},
    "Marina Gate 3":                   {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Select Group"},
    "Princess Tower":                  {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Tameer"},
    "Elite Residence":                 {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Dubai Properties"},
    "Cayan Tower":                     {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Cayan"},
    "The Torch":                       {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Select Group"},
    "Marina Crown":                    {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Dubai Properties"},
    "LIV Marina":                      {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "LIV"},
    "LIV Residence":                   {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "LIV"},
    "Address Beach Resort":            {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Emaar"},
    "Address Dubai Marina": {"area": "Dubai Marina", "emirate": "Dubai", "developer": "Address Hotels", "aliases": ["address marina", "address dxb marina"]},
    "Grosvenor House":                 {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Grosvenor"},
    "Five Marina":                     {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Five Holdings"},
    "Emaar 6 Tower":                   {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Emaar"},
    "Silverene Tower A":               {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "DAMAC"},
    "Silverene Tower B":               {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "DAMAC"},
    "Horizon Tower":                   {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "Emaar"},
    # Palm Jumeirah
    "One Palm":                        {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Omniyat"},
    "Atlantis The Royal Residences":   {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Kerzner"},
    "FIVE Palm Jumeirah":              {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Five Holdings"},
    "Serenia Residences":              {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Palma"},
    "Ellington Beach House":           {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Ellington"},
    "The 8":                           {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "PALM824"},
    "Como Residences":                 {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Nakheel"},
    "Six Senses The Palm":             {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Six Senses"},
    "Armani Beach Residences":         {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Armani"},
    "Raffles The Palm":                {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Raffles"},
    "Fairmont Residences South":       {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Fairmont"},
    "Fairmont Residences North":       {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Fairmont"},
    "Balqis Residence":                {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Nakheel"},
    "Azure Residences":                {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Nakheel"},
    "Shoreline Apartments":            {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Nakheel", "aliases": ["shoreline"]},
    "Tiara Residences":                {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Tiara", "aliases": ["tiara"]},
    "Marina Residences":               {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Nakheel"},
    # JVC
    "Binghatti Corner":                {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Binghatti", "aliases": ["corner by binghatti"]},
    "Binghatti Stars":                 {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Binghatti"},
    "Binghatti Gateway":               {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Binghatti"},
    "Binghatti Avenue":                {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Binghatti"},
    "Binghatti Crest":                 {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Binghatti"},
    "Binghatti Luna":                  {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Binghatti"},
    "Binghatti Apex":                  {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Binghatti"},
    "Bloom Towers":                    {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Bloom", "aliases": ["bloom towers a", "bloom towers b", "bloom towers c"]},
    "Belgravia 1":                     {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Ellington"},
    "Belgravia 2":                     {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Ellington"},
    "Belgravia Heights":               {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Ellington"},
    "Loci Residences":                 {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Loci"},
    "Samana Park Views":               {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Samana"},
    "Plazzo Residence":                {"area": "Jumeirah Village Circle", "emirate": "Dubai", "developer": "Plazzo"},
    # Dubai Hills Estate
    "Mulberry 1":                      {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Mulberry 2":                      {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Golf Heights":                    {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Park Heights 1":                  {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Park Heights 2":                  {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Vida Residences Dubai Hills":     {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Lime Gardens":                    {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Ellington House":                 {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Ellington"},
    "Ellington House II":              {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Ellington"},
    "Elie Saab Residences":            {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Elie Saab"},
    "Sidra 1":                         {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Sidra 2":                         {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Sidra 3":                         {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Maple 1":                         {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Maple 2":                         {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Maple 3":                         {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Elvira Tower 1":                  {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    "Elvira Tower 2":                  {"area": "Dubai Hills Estate", "emirate": "Dubai", "developer": "Emaar"},
    # Dubai Creek Harbour
    "Creek Gate Tower 1":              {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Creek Gate Tower 2":              {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "The Cove Building 1":             {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "The Cove Building 2":             {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Creek Rise Tower 1":              {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Creek Rise Tower 2":              {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Palace Residences":               {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Address Harbour Point Tower 1":   {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Address Harbour Point Tower 2":   {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Creek Edge Tower 1":              {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Creek Edge Tower 2":              {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Island Park Tower 1":             {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    "Island Park Tower 2":             {"area": "Dubai Creek Harbour", "emirate": "Dubai", "developer": "Emaar"},
    # JBR
    "Murjan 1":                        {"area": "Jumeirah Beach Residence", "emirate": "Dubai", "developer": "Dubai Properties"},
    "Murjan 2":                        {"area": "Jumeirah Beach Residence", "emirate": "Dubai", "developer": "Dubai Properties"},
    "Murjan 3":                        {"area": "Jumeirah Beach Residence", "emirate": "Dubai", "developer": "Dubai Properties"},
    "Bahar 1":                         {"area": "Jumeirah Beach Residence", "emirate": "Dubai", "developer": "Dubai Properties"},
    "Bahar 2":                         {"area": "Jumeirah Beach Residence", "emirate": "Dubai", "developer": "Dubai Properties"},
    "Rimal 1":                         {"area": "Jumeirah Beach Residence", "emirate": "Dubai", "developer": "Dubai Properties"},
    "Rimal 2":                         {"area": "Jumeirah Beach Residence", "emirate": "Dubai", "developer": "Dubai Properties"},
    "Shems 1":                         {"area": "Jumeirah Beach Residence", "emirate": "Dubai", "developer": "Dubai Properties"},
    "Sadaf 1":                         {"area": "Jumeirah Beach Residence", "emirate": "Dubai", "developer": "Dubai Properties"},
    "Amwaj 1":                         {"area": "Jumeirah Beach Residence", "emirate": "Dubai", "developer": "Dubai Properties"},
    # Dubai Harbour / Beachfront
    "Emaar Beachfront Tower 1":        {"area": "Dubai Harbour",  "emirate": "Dubai", "developer": "Emaar"},
    "Emaar Beachfront Tower 2":        {"area": "Dubai Harbour",  "emirate": "Dubai", "developer": "Emaar"},
    "Beach Vista Tower 1":             {"area": "Dubai Harbour",  "emirate": "Dubai", "developer": "Emaar"},
    "Beach Vista Tower 2":             {"area": "Dubai Harbour",  "emirate": "Dubai", "developer": "Emaar"},
    "Beach Mansion Tower 1":           {"area": "Dubai Harbour",  "emirate": "Dubai", "developer": "Emaar"},
    "Beach Mansion Tower 2":           {"area": "Dubai Harbour",  "emirate": "Dubai", "developer": "Emaar"},
    "Grand Bleu Tower 1":              {"area": "Dubai Harbour",  "emirate": "Dubai", "developer": "Emaar"},
    "Grand Bleu Tower 2":              {"area": "Dubai Harbour",  "emirate": "Dubai", "developer": "Emaar"},
    # Bluewaters
    "Bluewaters Residences Building 1":{"area": "Bluewaters Island", "emirate": "Dubai", "developer": "Meraas"},
    "Bluewaters Residences Building 2":{"area": "Bluewaters Island", "emirate": "Dubai", "developer": "Meraas"},
    # DIFC
    "Index Tower":                     {"area": "DIFC",           "emirate": "Dubai", "developer": "Union Properties"},
    "Sky Gardens":                     {"area": "DIFC",           "emirate": "Dubai", "developer": "DIFC"},
    "Central Park Tower":              {"area": "DIFC",           "emirate": "Dubai", "developer": "DIFC"},
    # Meydan
    "Azizi Riviera":                   {"area": "Meydan",         "emirate": "Dubai", "developer": "Azizi", "aliases": ["azizi riviera block"]},
    "The Polo Residence":              {"area": "Meydan",         "emirate": "Dubai", "developer": "Meydan"},
    # MBR City
    "Crest Grande":                    {"area": "MBR City",       "emirate": "Dubai", "developer": "Sobha"},
    "Crest Avenues":                   {"area": "MBR City",       "emirate": "Dubai", "developer": "Sobha"},
    "Waves Grande":                    {"area": "MBR City",       "emirate": "Dubai", "developer": "Sobha"},
    # Sobha Hartland
    "Sobha Creek Vistas":              {"area": "Sobha Hartland", "emirate": "Dubai", "developer": "Sobha"},
    "Sobha One":                       {"area": "Sobha Hartland", "emirate": "Dubai", "developer": "Sobha"},
    # Luxury / Branded
    "Bugatti Residences":              {"area": "Business Bay",   "emirate": "Dubai", "developer": "Binghatti"},
    "Cavalli Tower":                   {"area": "Dubai Marina",   "emirate": "Dubai", "developer": "DAMAC"},
    "Damac Bay By Cavalli":            {"area": "Dubai Harbour",  "emirate": "Dubai", "developer": "DAMAC"},
    "Tonino Lamborghini Residences":   {"area": "Business Bay",   "emirate": "Dubai", "developer": "Tonino Lamborghini"},
    "Muraba Residences":               {"area": "Palm Jumeirah",  "emirate": "Dubai", "developer": "Muraba"},
    "Bulgari Residences":              {"area": "Jumeirah",       "emirate": "Dubai", "developer": "Bulgari"},
    # RAK
    "Mina Al Arab Gateway":            {"area": "Mina Al Arab",   "emirate": "Ras Al Khaimah", "developer": "RAK Properties"},
    "Al Hamra Palace":                 {"area": "Al Hamra Village","emirate": "Ras Al Khaimah", "developer": "Al Hamra"},
    "Anantara Mina Al Arab":           {"area": "Mina Al Arab",   "emirate": "Ras Al Khaimah", "developer": "Anantara"},
    # Abu Dhabi
    "Yas Acres":                       {"area": "Yas Island",     "emirate": "Abu Dhabi", "developer": "Aldar"},
    "Ansam":                           {"area": "Yas Island",     "emirate": "Abu Dhabi", "developer": "Aldar"},
    "Mayan":                           {"area": "Yas Island",     "emirate": "Abu Dhabi", "developer": "Aldar"},
    "Water's Edge":                    {"area": "Yas Island",     "emirate": "Abu Dhabi", "developer": "Aldar"},
    "Mamsha Al Saadiyat":              {"area": "Saadiyat Island","emirate": "Abu Dhabi", "developer": "TDIC"},
    "Louvre Abu Dhabi Residences":     {"area": "Saadiyat Island","emirate": "Abu Dhabi", "developer": "TDIC"},
    "City Of Lights Gate Tower 1":     {"area": "Al Reem Island", "emirate": "Abu Dhabi", "developer": "Tamouh"},
    "City Of Lights Gate Tower 2":     {"area": "Al Reem Island", "emirate": "Abu Dhabi", "developer": "Tamouh"},
    "Sigma Towers":                    {"area": "Al Reem Island", "emirate": "Abu Dhabi", "developer": "Sigma"},
    "Mangrove Place":                  {"area": "Al Reem Island", "emirate": "Abu Dhabi", "developer": "Aldar"},
}

# Build lowercase index for fast lookup
_BUILDINGS_LOWER = {k.lower(): k for k in BUILDINGS_DB}
# Build alias index
_BUILDING_ALIASES = {}
for bname, bdata in BUILDINGS_DB.items():
    for alias in bdata.get("aliases", []):
        _BUILDING_ALIASES[alias.lower()] = bname

# ══════════════════════════════════════════════════════════════════════════════
# 4. DEVELOPERS (for step 4 in hierarchy)
# ══════════════════════════════════════════════════════════════════════════════
DEVELOPERS = {
    "Emaar":       {"emirate": "Dubai",     "aliases": ["emaar properties", "emaar"]},
    "DAMAC":       {"emirate": "Dubai",     "aliases": ["damac properties", "damac"]},
    "Nakheel":     {"emirate": "Dubai",     "aliases": ["nakheel properties", "nakheel"]},
    "Meraas":      {"emirate": "Dubai",     "aliases": ["meraas", "meraas holding"]},
    "Dubai Properties": {"emirate": "Dubai","aliases": ["dubai properties", "dp"]},
    "Sobha":       {"emirate": "Dubai",     "aliases": ["sobha realty", "sobha group"]},
    "Binghatti":   {"emirate": "Dubai",     "aliases": ["binghatti developers"]},
    "Ellington":   {"emirate": "Dubai",     "aliases": ["ellington properties"]},
    "Omniyat":     {"emirate": "Dubai",     "aliases": ["omniyat"]},
    "Select Group":{"emirate": "Dubai",     "aliases": ["select group"]},
    "Azizi":       {"emirate": "Dubai",     "aliases": ["azizi developments"]},
    "Samana":      {"emirate": "Dubai",     "aliases": ["samana developers"]},
    "Aldar":       {"emirate": "Abu Dhabi", "aliases": ["aldar properties"]},
    "Mubadala":    {"emirate": "Abu Dhabi", "aliases": ["mubadala"]},
    "RAK Properties":{"emirate": "Ras Al Khaimah","aliases": ["rak properties"]},
    "Rakeen":      {"emirate": "Ras Al Khaimah","aliases": ["rakeen"]},
    "Arada":       {"emirate": "Sharjah",   "aliases": ["arada"]},
}

# ══════════════════════════════════════════════════════════════════════════════
# 5. MARKET DATA (per sqft AED, avg annual rent 1BR, annual growth %)
# ══════════════════════════════════════════════════════════════════════════════
MARKET = {
    "Business Bay":             {"sqft": 1800, "rent_1br": 105000, "growth": 6},
    "Downtown Dubai":           {"sqft": 2500, "rent_1br": 130000, "growth": 7},
    "Dubai Marina":             {"sqft": 1900, "rent_1br": 110000, "growth": 5},
    "Palm Jumeirah":            {"sqft": 3500, "rent_1br": 180000, "growth": 8},
    "Dubai Hills Estate":       {"sqft": 1600, "rent_1br": 95000,  "growth": 6},
    "Dubai Creek Harbour":      {"sqft": 1700, "rent_1br": 100000, "growth": 7},
    "Jumeirah Village Circle":  {"sqft": 1100, "rent_1br": 65000,  "growth": 4},
    "Emaar South":              {"sqft": 900,  "rent_1br": 60000,  "growth": 5},
    "Al Furjan":                {"sqft": 1000, "rent_1br": 65000,  "growth": 4},
    "Arjan":                    {"sqft": 950,  "rent_1br": 60000,  "growth": 4},
    "DAMAC Hills":              {"sqft": 1000, "rent_1br": 65000,  "growth": 5},
    "Bluewaters Island":        {"sqft": 3000, "rent_1br": 180000, "growth": 7},
    "Jumeirah Beach Residence": {"sqft": 2000, "rent_1br": 120000, "growth": 5},
    "Sobha Hartland":           {"sqft": 1800, "rent_1br": 100000, "growth": 6},
    "MBR City":                 {"sqft": 1600, "rent_1br": 95000,  "growth": 6},
    "Meydan":                   {"sqft": 1400, "rent_1br": 85000,  "growth": 5},
    "Dubai South":              {"sqft": 800,  "rent_1br": 55000,  "growth": 4},
    "Silicon Oasis":            {"sqft": 700,  "rent_1br": 50000,  "growth": 3},
    "Sports City":              {"sqft": 800,  "rent_1br": 52000,  "growth": 3},
    "International City":       {"sqft": 500,  "rent_1br": 40000,  "growth": 2},
    "Yas Island":               {"sqft": 1200, "rent_1br": 75000,  "growth": 5},
    "Al Reem Island":           {"sqft": 1100, "rent_1br": 72000,  "growth": 5},
    "Al Marjan Island":         {"sqft": 1300, "rent_1br": 80000,  "growth": 7},
    "DIFC":                     {"sqft": 2800, "rent_1br": 150000, "growth": 6},
    "City Walk":                {"sqft": 2200, "rent_1br": 120000, "growth": 5},
    "Dubai Harbour":            {"sqft": 2200, "rent_1br": 125000, "growth": 6},
}
DEFAULT_MKT = {"sqft": 1300, "rent_1br": 80000, "growth": 4}
RENT_MULT = {"studio": 0.7, "1br": 1.0, "2br": 1.6, "3br": 2.2, "4br+": 3.0}

# ══════════════════════════════════════════════════════════════════════════════
# 6. SPAM / EXCLUSION
# ══════════════════════════════════════════════════════════════════════════════
SPAM_KEYWORDS = [
    "seminar", "webinar", "training", "course", "mortgage ad", "loan offer",
    "vacancy", "hiring", "job offer", "we are looking for", "agent required",
    "good morning", "good evening", "happy new", "congratulations",
    "moving service", "cleaning service", "movers", "packers",
    "insurance", "visa service", "car for sale", "car rental",
]
COMMERCIAL_KEYWORDS = [
    "for lease", "industrial unit", "commercial space", "retail space",
    "office space", "warehouse", "shop for sale", "labour camp",
    "plot for lease", "land for lease", "factory", "showroom for sale",
]

# Property types
# Order matters — more specific types FIRST so they win over generic ones.
# Iteration: penthouse > hotel apartment > serviced apartment > villa > townhouse > duplex >
# > commercial types > land/plot > studio > apartment.
PROP_TYPE_MAP = {
    # Specific residential types (check FIRST)
    "penthouse":         ["penthouse", "pent house", "пентхаус"],
    "hotel_apartment":   ["hotel apartment", "hotel apt", "hotel residence", "hotel residences"],
    "serviced_apartment":["serviced apartment", "serviced apt", "serviced residence"],
    "villa":             ["villa", "villas", "detached villa", "independent villa", "вилла", "виллы"],
    "townhouse":         ["townhouse", "town house", "townhome", "таунхаус", "таунхауc"],
    "duplex":            ["duplex", "дуплекс"],
    # Commercial — keywords must be in specific commercial-context phrases, not casual mentions
    "hotel":             ["hotel for sale", "hotel for lease", "branded hotel", "boutique hotel"],
    "office":            ["office for sale", "office for rent", "office sale", "for sale | office",
                          "for rent | office", "office space for sale", "office space for rent", "офис на продажу"],
    "retail":            ["retail for sale", "retail for rent", "retail unit",
                          "retail space", "shop for sale", "shop for rent"],
    "warehouse":         ["warehouse for sale", "warehouse for rent", "warehouse unit", "storage facility"],
    # Land — must be in explicit plot/land offer context, not just "Plot: 1,873 sq.ft" size field
    "plot":              ["plot for sale", "plot for rent", "plot for lease", "land for sale",
                          "land for lease", "freehold plot", "building plot", "development plot",
                          "industrial plot", "commercial plot", "residential plot",
                          "земельный участок"],
    # Generic residential (LAST so they don't beat specific ones)
    "studio":            ["studio", "студия"],
    "apartment":         ["apartment", "apt", "flat", "unit", "residence", "квартира"],
}

# Views
VIEWS = [
    "full sea view", "partial sea view", "sea view",
    "burj khalifa view", "burj view", "bruj view",
    "fountain view", "full fountain view",
    "marina view", "full marina view",
    "golf view", "golf course view",
    "canal view", "water canal view",
    "palm view", "palm jumeirah view",
    "city view", "skyline view", "downtown view",
    "community view", "pool view",
    "lagoon view", "park view",
    "beach view", "ocean view",
    "creek view", "harbour view",
    "full water view", "water view",
    "garden view", "mountain view", "lake view",
    "boulevard view", "courtyard view",
    "panoramic view", "full panoramic view",
    "open view", "street view",
]

STATUS_KEYWORDS = {
    "vacant":  ["vacant", "ready to move", "ready to move in", "empty", "unoccupied"],
    "rented":  ["rented", "tenanted", "occupied", "rented out", "with tenant"],
    "offplan": ["off plan", "offplan", "off-plan", "under construction"],
    "ready":   ["ready", "completed", "handover"],
}

FURNISHING_KEYWORDS = {
    "furnished":      ["furnished", "fully furnished", "ff", "f/f"],
    "unfurnished":    ["unfurnished", "un-furnished", "uf", "u/f", "bare"],
    "semi-furnished": ["semi furnished", "semi-furnished", "sf", "s/f"],
}


# ══════════════════════════════════════════════════════════════════════════════
# TEXT CLEANUP
# ══════════════════════════════════════════════════════════════════════════════
def clean_text(text: str) -> str:
    if not text:
        return ""
    emoji_pattern = re.compile(
        "[\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
        "\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF"
        "\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
        "\u2600-\u27BF\uFE0F]+", flags=re.UNICODE)
    text = emoji_pattern.sub(" ", text)
    text = re.sub(r"[=_\-\*•·|]{3,}", " ", text)
    text = re.sub(r"[!]{2,}", "!", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text



def split_into_blocks(text: str) -> list:
    import re
    # Берём разделители из rules файла + базовые
    rule_seps = RULES.get('block_separators', [])
    separators = [r'-{4,}', r'_{4,}', r'={4,}', r'\*{4,}']
    for sep in rule_seps:
        escaped = re.escape(sep)
        if escaped not in separators:
            separators.append(escaped)
    pattern = '|'.join(separators)
    blocks = re.split(pattern, text)
    result = []
    for block in blocks:
        block = block.strip()
        if len(block) < 30:
            continue
        # Проверяем что блок похож на объявление
        bl = block.lower()
        has_price = bool(re.search(r'aed|price|sp:|\d+[mk]\b|\d{6,}', bl))
        has_prop = bool(re.search(r'bed|br|bhk|studio|villa|apartment|sqft|sq\.ft', bl))
        if has_price or has_prop:
            result.append(block)
    return result if result else [text]
def is_spam(text: str) -> bool:
    if len(text.strip()) < 25:
        return True
    tl = text.lower()
    return (any(k in tl for k in SPAM_KEYWORDS) or
            any(k in tl for k in COMMERCIAL_KEYWORDS))


# ══════════════════════════════════════════════════════════════════════════════
# DEAL TYPE
# ══════════════════════════════════════════════════════════════════════════════
# ── Market floor prices — used to validate deal_type by price plausibility ──
SALE_MIN_PRICE: dict = {
    "studio": {
        "default":                  400_000,
        "Downtown Dubai":           700_000,
        "Palm Jumeirah":          1_000_000,
        "Dubai Marina":             600_000,
        "Business Bay":             500_000,
        "Jumeirah Village Circle":  350_000,
        "Dubai Hills Estate":       600_000,
        "Emaar Beachfront":       1_000_000,
    },
    1: {
        "default":                  600_000,
        "Downtown Dubai":         1_200_000,
        "Palm Jumeirah":          1_500_000,
        "Dubai Marina":             900_000,
        "Business Bay":             800_000,
        "Jumeirah Village Circle":  550_000,
        "Dubai Hills Estate":       900_000,
        "Emaar Beachfront":       1_500_000,
    },
    2: {
        "default":                  900_000,
        "Downtown Dubai":         2_000_000,
        "Palm Jumeirah":          2_500_000,
        "Dubai Marina":           1_400_000,
        "Business Bay":           1_200_000,
        "Dubai Hills Estate":     1_500_000,
        "Emaar Beachfront":       2_500_000,
    },
    3: {
        "default":              1_400_000,
        "Downtown Dubai":       3_500_000,
        "Palm Jumeirah":        4_000_000,
        "Dubai Marina":         2_500_000,
        "Dubai Hills Estate":   2_500_000,
        "The Valley":           1_800_000,
        "DAMAC Hills":          1_500_000,
    },
    4: {
        "default":              2_500_000,
        "Palm Jumeirah":        8_000_000,
        "Dubai Hills Estate":   4_000_000,
        "The Valley":           2_500_000,
        "Arabian Ranches":      3_500_000,
    },
}

RENT_MAX_PRICE: dict = {
    "studio": 150_000,
    0:        150_000,
    1:        200_000,
    2:        350_000,
    3:        600_000,
    4:      1_000_000,
    "default": 800_000,
}


_HARD_RENT_KW_PE = [
    r'\brent\b', r'\brental\b', r'\brented\b', r'\bfor rent\b', r'\bto rent\b',
    r'\bper year\b', r'\bper month\b', r'\bper annum\b', r'\b/yr\b', r'\b/year\b',
    r'\b/month\b', r'\b/мес\b', r'\b/год\b',
    r'\bаренда\b', r'\bснять\b', r'\bсниму\b', r'\bсдам\b',
    r'\bсдается\b', r'\bсдаётся\b',
]
_HARD_SALE_KW_PE = [
    r'\bfor sale\b', r'\bselling\b', r'\bresale\b', r'\bsale price\b',
    r'\bпродажа\b', r'\bпродам\b', r'\bпродаётся\b',
    r'\bbuy\b', r'\bbuying\b',
]


def validate_deal_type_by_price(
    price: int,
    deal_type: str,
    bedrooms: Optional[int] = None,
    area: Optional[str] = None,
    text: Optional[str] = None,
) -> str:
    """
    Override deal_type using keyword rules first, then price sanity limits.
    Priority:
      1. Hard rent keywords in text → always rent
      2. Hard sale keywords in text → sale (if no rent keywords)
      3. deal_type=sale + price < 500 000 AED → rent
      4. deal_type=rent + price > 50 000 000 AED → sale
      5. Market floor/ceiling price checks (original logic)
    """
    # ── 1 & 2: keyword overrides ──────────────────────────────────────────
    if text:
        t = text.lower()
        # Rent-context guard: cheques/per month/annual rent → keep as rent
        # (a "looking for studio, 100k 4 cheques" is a RENT request)
        is_rent_context = bool(re.search(
            r'\bcheques?\b|\bper\s+month\b|/month\b|\bmonthly\b|\bper\s+year\b|/year\b|\bper\s+annum\b|\bannually\b',
            t
        ))
        # Buyer request = always sale/buy (unless rent-context)
        buyer_request_patterns = [
            r'buyer.{0,10}request',
            r'looking.{0,20}(?:buy|purchase|invest)',
            r'\bcash\s+buyer\b',
            r'\bseeking\s+(?:a\s+)?\d?\s*(?:bedroom|br|bhk|studio|villa|apartment|townhouse|plot|residence)',
            r'looking\s+for\s+(?:a\s+|my\s+)?(?:client|investor)',
            r'\bclient\s+(?:is\s+)?looking\s+(?:to\s+)?(?:buy|purchase|invest)',
            r'\bbudget\s*[:=]?\s*(?:up\s+to|aed|\$)',
        ]
        if not is_rent_context and any(re.search(p, t, re.IGNORECASE) for p in buyer_request_patterns):
            # Additional guard: skip if text clearly offers a property (selling/asking price)
            if not re.search(r'\b(?:selling\s+price|asking\s+price|sales?\s+price|sp\s*:|op\s*:|for\s+sale\b)\b', t):
                return "sale"
        if any(re.search(p, t, re.IGNORECASE) for p in _HARD_RENT_KW_PE):
            deal_type = "rent"
        elif any(re.search(p, t, re.IGNORECASE) for p in _HARD_SALE_KW_PE):
            deal_type = "sale"

    if not price or price <= 0:
        return deal_type

    # ── 3 & 4: absolute price limits ─────────────────────────────────────
    if deal_type == "sale" and price < 500_000:
        return "rent"
    if deal_type == "rent" and price > 50_000_000:
        return "sale"

    # ── 5: market floor/ceiling (original logic) ─────────────────────────
    br_key = bedrooms if bedrooms in SALE_MIN_PRICE else (
        "studio" if bedrooms == 0 else 1
    )
    br_prices = SALE_MIN_PRICE.get(br_key, SALE_MIN_PRICE[1])
    min_sale = br_prices.get(area or "", br_prices.get("default", 400_000))

    max_rent_key = bedrooms if bedrooms in RENT_MAX_PRICE else "default"
    max_rent = RENT_MAX_PRICE.get(max_rent_key, RENT_MAX_PRICE["default"])

    if price < min_sale * 0.5:   # clearly below half the minimum sale price → rent
        return "rent"
    if price > max_rent * 2:     # clearly above double the max rent → sale
        return "sale"
    return deal_type             # grey zone — keep original detection


def _deal_type_by_price_format(text: str) -> Optional[str]:
    tl = text.lower()
    if re.search(r'\d+k?\s*(?:per year|/year|/yr|per annum|annually)', tl):
        return "rent"
    if re.search(r'(?:rent|annual)\s*:\s*\d+', tl):
        return "rent"
    if re.search(r'(?:per month|/month|monthly rent)', tl):
        return "rent"
    if re.search(r'(?:price|sp|op)\s*:\s*[\d.]+\s*m', tl):
        return "sale"
    if re.search(r'payment\s*plan|handover\s*q\d', tl):
        return "sale"
    return None


def _deal_type_by_price(price: Optional[int], bedrooms: Optional[int] = None) -> Optional[str]:
    if not price or price <= 0:
        return None
    if price < 15_000:
        return None  # garbage
    if price < 500_000:
        return "rent"
    if price >= 1_000_000:
        return "sale"
    return "sale"  # 500K-1M boundary → sale


def detect_deal_type(text: str, price: Optional[int] = None,
                     bedrooms: Optional[int] = None) -> str:
    tl = text.lower()

    # Comprehensive rent signals
    rent_signals = [
        "for rent", "to rent", "rental", "rent:", "per year", "per annum",
        "/year", "/yr", "/month", "annual rent", "yearly rent", "monthly rent",
        "one cheque", "two cheques", "4 cheques", "6 cheques", "12 cheques",
        "chiller free", "no chiller", "dewa free", "tenanted", "tenant",
        "rented", "leased", "للإيجار", "аренда", "сдаётся", "сдам",
        "сдаю", "в аренду", "per month", "aed/yr", "aed/month",
    ]
    # Comprehensive sale signals
    sale_signals = [
        "for sale", "selling price", "sale price", "sp:", "op:", "asking price",
        "listed at", "mortgage", "cash price", "payment plan", "handover",
        "off plan", "offplan", "off-plan", "resale", "transfer fee", "dld fee",
        "للبيع", "продажа", "продаётся", "продам", "на продажу",
        "posthandover", "post handover", "ready to move in",
    ]

    rent_score = sum(1 for s in rent_signals if s in tl)
    sale_score = sum(1 for s in sale_signals if s in tl)

    if rent_score > sale_score:
        return "rent"
    if sale_score > rent_score:
        return "sale"

    # Tie — try price format hints
    fmt = _deal_type_by_price_format(text)
    if fmt:
        return fmt

    # Last resort: price range
    if price:
        p = _deal_type_by_price(price, bedrooms)
        if p:
            return p

    return "sale"  # default


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: EMIRATE DETECTION (direct mention)
# ══════════════════════════════════════════════════════════════════════════════
def detect_emirate_direct(text: str) -> tuple[Optional[str], float]:
    tl = text.lower()
    for emirate, aliases in EMIRATES.items():
        for alias in aliases:
            if re.search(r'\b' + re.escape(alias) + r'\b', tl):
                return emirate, 0.95
    return None, 0.0


# ══════════════════════════════════════════════════════════════════════════════
# HEADER LINE PATTERN EXTRACTION — 8 structural patterns from real messages
# ══════════════════════════════════════════════════════════════════════════════
_PROP_TYPE_TOKENS = frozenset([
    "studio", "bhk", "br", "bedroom", "apartment", "apt", "flat",
    "villa", "townhouse", "penthouse", "duplex",
])

def _match_area_by_name(text: str) -> Optional[str]:
    """
    Try to match a text fragment to a known canonical area name or AREA_ABBR.
    Returns canonical area name or None.
    """
    t = text.strip()
    tl = t.lower()

    # 1. Direct exact match on AREAS canonical name or alias
    for area_name, info in AREAS.items():
        if area_name.lower() == tl:
            return area_name
        for alias in info.get("aliases", []):
            if alias == tl:
                return area_name

    # 2. Abbreviation expansion
    t_upper = t.upper()
    if t_upper in AREA_ABBR:
        expanded = AREA_ABBR[t_upper]
        for area_name in AREAS:
            if area_name.lower() == expanded.lower():
                return area_name
        # Expansion itself (e.g. "Dubai") may not be in AREAS → return as-is
        return expanded

    return None


def extract_from_header_lines(text: str) -> dict:
    """
    Parse structural patterns from the first 6 lines:

    Pattern 1:  AREA – BUILDING        (dash / em-dash separator)
    Pattern 2:  TYPE/BUILDING          (slash, left part is prop-type)
    Pattern 2b: AREA / BUILDING        (slash, left part is area name)
    Pattern 3:  Building on line after "For Sale" / "For Rent" header
    Pattern 4:  For Sale: JLT          (colon + area abbreviation)
    Pattern 5:  BUILDING @ AREA        (@ separator)

    Returns dict with keys area, building, bedrooms (any can be None).
    Only provides hints — main pipeline still validates.
    """
    result: dict = {"area": None, "building": None, "bedrooms": None}

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    header_text = "\n".join(lines[:6])

    def _grab_br(fragment: str) -> Optional[int]:
        """Extract bedroom count from a token-rich fragment."""
        fl = fragment.lower()
        if "studio" in fl:
            return 0
        m = re.search(r'(\d)\s*(?:bhk|br|bed)\b', fl)
        return int(m.group(1)) if m else None

    # ── Pattern 4: "For Sale/Rent : AREA_CODE" ────────────────────────────────
    m = re.search(
        r'For\s+(?:Rent|Sale)\s*:\s*([A-Za-z][A-Za-z0-9\s]{1,40}?)(?:\n|/|–|—|-|$)',
        header_text, re.I)
    if m:
        area_hint = m.group(1).strip()
        matched = _match_area_by_name(area_hint)
        if matched and not result["area"]:
            result["area"] = matched

    # ── Patterns 1 + 2 + 2b + 5: per-line structural separators ─────────────
    sep_re = re.compile(
        r'^(.*?)\s*(?:–|—|-{1,2}|/|@)\s*(.*?)$', re.UNICODE)

    for line in lines[:5]:
        m = sep_re.match(line)
        if not m:
            continue
        left, right = m.group(1).strip(), m.group(2).strip()
        if not left or not right:
            continue

        left_l = left.lower()

        # Pattern 2: left part contains a property-type token  →  right = building
        if re.search(r'\d\s*(?:bhk|br|bed(?:room)?)\b|\b(?:studio|apartment|apt|flat|villa|townhouse|penthouse|duplex)\b', left_l):
            br = _grab_br(left)
            if br is not None and result["bedrooms"] is None:
                result["bedrooms"] = br
            if not result["building"]:
                result["building"] = right
            continue  # don't also try area match

        # Pattern 2b / Pattern 1: left part is an area name
        area_match = _match_area_by_name(left)
        if area_match:
            if not result["area"]:
                result["area"] = area_match
            if not result["building"]:
                result["building"] = right
            continue

        # Pattern 5 (@): right side might be the area
        if "@" in line:
            area_match_right = _match_area_by_name(right)
            if area_match_right and not result["area"]:
                result["area"] = area_match_right
            if not result["building"]:
                result["building"] = left

    # ── Pattern 3: building name on line immediately after sale/rent header ──
    HEADER_KWS = ["for sale", "for rent", "resale", "re-sale", "off plan", "off-plan"]
    for i, line in enumerate(lines[:5]):
        ll = line.lower()
        if any(k in ll for k in HEADER_KWS):
            # Look at next non-empty lines
            candidates = [l for l in lines[i + 1: i + 4] if l]
            for cand in candidates:
                # Skip if it already matched via patterns 1/2
                if result["building"] and cand.lower() == result["building"].lower():
                    break
                # Skip spec lines (have digits + unit markers)
                if re.search(r'\d+\s*(?:sqft|sq\.?ft|aed|bed|br|bhk)', cand, re.I):
                    continue
                # Skip if contains separator (already handled above)
                if re.search(r'[/–—@]', cand):
                    continue
                # Must look like a building/complex name: title-case words, <= 60 chars
                if len(cand) <= 60 and re.match(r'[A-Z]', cand):
                    if not result["building"]:
                        result["building"] = cand
                    break

    # Validate building field - clear if contains phone/email/digits
    import re as _re
    if result.get('building') and (_re.search(r'\+?\d{7,}|@|\bhttp', result['building']) or any(w in result['building'].lower() for w in ['prices are', 'net to', 'owner', 'commission', 'contact', 'whatsapp', 'call us', 'click'])):
        result['building'] = None
        result['building_conf'] = 0.0
    return result


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: AREA DETECTION → infer emirate
# ══════════════════════════════════════════════════════════════════════════════
def detect_area(text: str, known_emirate: Optional[str] = None) -> tuple[Optional[str], float, Optional[str], list]:
    """
    Returns (area_name, confidence, emirate_from_area, possible_emirates).
    possible_emirates is non-empty only for ambiguous areas.
    """
    # Expand abbreviations in text before matching
    expanded_text = text
    for abbr, full in AREA_ABBR.items():
        # Only replace standalone abbreviations (word boundaries)
        expanded_text = re.sub(r'\b' + re.escape(abbr) + r'\b', full, expanded_text, flags=re.I)

    tl = expanded_text.lower()
    # Sort by name length DESC to avoid partial matches (e.g. "Marina" before "Dubai Marina")
    sorted_areas = sorted(AREAS.items(), key=lambda x: len(x[0]), reverse=True)

    for area_name, info in sorted_areas:
        area_emirate = info.get("emirate")
        aliases = info.get("aliases", [])
        is_ambiguous = info.get("ambiguous", False)
        possible = info.get("possible", [])

        # If we know emirate and this area belongs to different emirate → skip
        if known_emirate and area_emirate and area_emirate != known_emirate:
            continue

        # Check area name and all aliases
        all_names = [area_name.lower()] + aliases
        for name in all_names:
            if re.search(r'\b' + re.escape(name) + r'\b', tl):
                if is_ambiguous:
                    return area_name, 0.60, None, possible
                conf = 0.95 if area_name.lower() in tl else 0.85
                return area_name, conf, area_emirate, []

    # Fallback: emirate-only mention (e.g. "Layla Residences, Sharjah" with no recognized district)
    # Returns area=<Emirate name>, emirate=<Emirate>, low confidence
    EMIRATE_FALLBACK = [
        ("Dubai",          ["dubai", "dxb"]),
        ("Abu Dhabi",      ["abu dhabi", "abudhabi", "auh"]),
        ("Sharjah",        ["sharjah", "shj"]),
        ("Ras Al Khaimah", ["ras al khaimah", "ras al-khaimah", "ras al khaima", "rak"]),
        ("Ajman",          ["ajman"]),
        ("Fujairah",       ["fujairah", "fuj"]),
        ("Umm Al Quwain",  ["umm al quwain", "uaq"]),
    ]
    for emirate_name, emirate_aliases in EMIRATE_FALLBACK:
        if known_emirate and emirate_name != known_emirate:
            continue
        for name in emirate_aliases:
            if re.search(r'\b' + re.escape(name) + r'\b', tl):
                return emirate_name, 0.40, emirate_name, []

    return None, 0.0, None, []


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: BUILDING DETECTION — SOURCE OF TRUTH
# If building found in BUILDINGS_DB → use its area and emirate
# ══════════════════════════════════════════════════════════════════════════════
def _is_landmark_view_reference(text_lower: str, m: re.Match) -> bool:
    """Returns True if the matched name is a 'view/near/facing' reference, not the actual building.
    e.g. 'Burj Khalifa view', '... overlooking Burj Khalifa', 'near Burj Khalifa'.
    """
    # 35 chars before
    before = text_lower[max(0, m.start() - 35): m.start()]
    after = text_lower[m.end(): m.end() + 20]
    # Pre-context markers (word that comes BEFORE the name)
    pre_markers = r'\b(?:view|views|viewing|facing|near|from|overlooking|opposite|towards?|towards|stunning|with|close\s+to|next\s+to|distance\s+from|walking\s+distance)\b\s*[a-z]*\s*$'
    if re.search(pre_markers, before):
        return True
    # Post-context markers (the name is followed by "view")
    if re.search(r'^\s*(?:view|views)\b', after):
        return True
    return False


def detect_building(text: str) -> tuple[Optional[str], float, Optional[str], Optional[str], Optional[str]]:
    """
    Returns (building_name, confidence, area, emirate, developer).
    If building found in DB → area and emirate come from DB (cross-validated).
    Landmark names ("Burj Khalifa", "Marina") in 'view' context are SKIPPED.
    """
    tl = text.lower()

    # 1. Exact match in DB — skip landmark-view references
    for bname_lower, bname_canonical in _BUILDINGS_LOWER.items():
        for m in re.finditer(r'\b' + re.escape(bname_lower) + r'\b', tl):
            if _is_landmark_view_reference(tl, m):
                continue
            bdata = BUILDINGS_DB[bname_canonical]
            return (bname_canonical, 0.95,
                    bdata.get("area"), bdata.get("emirate"), bdata.get("developer"))

    # 2. Alias match — same filter
    for alias_lower, bname_canonical in _BUILDING_ALIASES.items():
        for m in re.finditer(r'\b' + re.escape(alias_lower) + r'\b', tl):
            if _is_landmark_view_reference(tl, m):
                continue
            bdata = BUILDINGS_DB[bname_canonical]
            return (bname_canonical, 0.90,
                    bdata.get("area"), bdata.get("emirate"), bdata.get("developer"))

    # 3. Fuzzy match via known_dubai_data
    try:
        from known_dubai_data import normalize_building_name
        lines = [l.strip() for l in text.split("\n") if 4 <= len(l.strip()) <= 80]
        tokens = [t.strip() for t in re.split(r'[|,·\-\n]', text) if 4 <= len(t.strip()) <= 80]
        candidates = list(set(lines + tokens))

        for candidate in candidates:
            normalized = normalize_building_name(candidate)
            if normalized and normalized.lower() != candidate.lower():
                # Check if normalized building is in our DB
                norm_lower = normalized.lower()
                if norm_lower in _BUILDINGS_LOWER:
                    bname = _BUILDINGS_LOWER[norm_lower]
                    bdata = BUILDINGS_DB[bname]
                    return (bname, 0.82,
                            bdata.get("area"), bdata.get("emirate"), bdata.get("developer"))
                # Return normalized name without DB data
                return normalized, 0.75, None, None, None
    except Exception:
        pass

    # 4. Heuristic regex — typical "📍 X, Emirate" / "🏢 Project: X" / "X in Y" patterns
    heur = _extract_building_heuristic(text)
    if heur:
        return heur, 0.60, None, None, None

    return None, 0.0, None, None, None


# Stopwords for heuristic building extraction — area names, emirates, generic words
# that should NOT be treated as buildings.
_BUILDING_HEUR_STOPWORDS = {
    # Emirates
    "dubai", "abu dhabi", "sharjah", "ras al khaimah", "ras al-khaimah",
    "ajman", "fujairah", "umm al quwain", "rak", "uae",
    # Common generic words
    "for sale", "for rent", "hot deal", "hot offer", "distress deal", "unit for sale",
    "units for sale", "buyer request", "cash buyer", "very hot offer", "hot offers",
    "looking for", "seeking", "sale price", "selling price", "original price",
    "apartment", "villa", "townhouse", "studio", "penthouse", "office",
    "address", "location", "developer", "project", "rooms",
    "category", "area", "bedrooms", "bathrooms", "parking", "furnished",
    "availability", "balcony", "floor", "plot",
    # Pure marketing
    "luxury premium branded hotels", "branded hotels", "real estate",
}


def _is_building_stopword(s: str) -> bool:
    """Check if s is a stopword or looks like one (area name, emirate, marketing phrase)."""
    sl = s.strip().lower()
    if not sl or len(sl) < 3:
        return True
    if sl in _BUILDING_HEUR_STOPWORDS:
        return True
    # Check against AREAS keys/aliases — area names are not buildings
    for area_name, info in AREAS.items():
        if sl == area_name.lower():
            return True
        if sl in [a.lower() for a in info.get("aliases", [])]:
            return True
    return False


def _clean_building_candidate(s: str) -> Optional[str]:
    """Strip emoji/punctuation, validate, return canonical or None."""
    if not s:
        return None
    # Remove emoji and common decorative chars
    s = re.sub(r'[📍🏡🏢💰🔥✨⭐️🌊🛏🛁🚘🪑🔑🪴📐☎️📞📩‼️🟥🟨🟩‍♂️‍♀️]', '', s)
    # Remove markdown
    s = re.sub(r'\*+|_+|~+|`+', '', s)
    # If there's a ":" inside, take only the part before it (e.g. "Park Horizon: building 2" → "Park Horizon")
    if ':' in s:
        s = s.split(':', 1)[0].strip()
    if '=' in s:
        s = s.split('=', 1)[0].strip()
    # If trailing ", X" where X is shorter than 4 chars — drop the tail (e.g. "Sobha The Crest, B" → "Sobha The Crest")
    s = re.sub(r',\s*[A-Z0-9]{1,3}\s*$', '', s)
    # If em-dash, prefer the part before it (e.g. "Eden House – The Canal" → "Eden House")
    if ' – ' in s or ' — ' in s:
        s = re.split(r'\s+[–—]\s+', s)[0].strip()
    # If pipe separator with multiple segments, keep the longest/most building-like segment
    if '|' in s:
        parts = [p.strip() for p in s.split('|') if p.strip()]
        # Drop parts that look like areas, sizes, or attributes; keep the candidate
        good_parts = []
        for p in parts:
            if _is_building_stopword(p):
                continue
            if re.search(r'\b(?:\d+\s*(?:bedroom|br|bhk|bed|bath|sqft|sq\.|floor)|aed|sale|rent)\b', p, re.I):
                continue
            good_parts.append(p)
        if good_parts:
            # Pick the longest non-stopword segment
            s = max(good_parts, key=len)
        else:
            return None
    # Collapse whitespace and strip junk
    s = re.sub(r'\s+', ' ', s).strip(' ,.-–—:|')
    if not s:
        return None
    # Length: minimum 4 chars (was 3 — too short caught "1BR")
    if len(s) < 4 or len(s) > 60:
        return None
    if _is_building_stopword(s):
        return None
    # Must contain at least one letter
    if not re.search(r'[A-Za-zА-Яа-я]', s):
        return None
    # Reject bedrooms/property service fragments
    if re.search(r'\b(?:\d+\s*br|\d+\s*bhk|\d+\s*bed(?:room)?s?|property\s+type|rooms?|bedrooms?|bathrooms?|parking|furnished|availability|balcony|floor|plot|category|size|price|view|completion|developer|status|terrace|maid|wardrobe|kitchen|contact|whats\s*app|telegram)\b', s, re.I):
        return None
    # Reject marketing-style multi-word fragments
    if re.search(r'\b(?:only|deal|offer|urgent|distress|launch|renovation|sale|rent|lease|client|investor|cheques?|budget)\b', s, re.I):
        return None
    # Reject generic property-type and description words alone
    if re.search(r'\b(?:villa|townhouse|apartment|penthouse|office|studio|duplex|loft|land|spacious|layout|amenities|features|brand\s*new|vacant|rented|tenanted|ready)\b', s, re.I):
        return None
    # Reject short attribute abbreviations (OP/SP/BUA/BHK alone)
    if re.fullmatch(r'(?:op|sp|bua|bhk|aed|usd|dxb|auh|shj|rak|uae|gcc)', s, re.I):
        return None
    # Must START with a letter (uppercase or Cyrillic) — buildings start with capital, not bullet/digit/symbol
    if not re.match(r'[A-ZА-Я]', s):
        return None
    # Must not be all digits + punct
    if re.fullmatch(r'[\d\s.,\-+]+', s):
        return None
    # Filter price/size fragments
    if re.search(r'\b(aed|sqft|sq\.?\s?ft|sqm|sq\.?\s?m|m2|ft²|/month|/m|million|mln)\b', s, re.I):
        return None
    # Filter phone numbers
    if re.search(r'\+?\d{6,}', s):
        return None
    # Filter URLs/handles
    if re.search(r'https?://|@\w+', s, re.I):
        return None
    # Too many words → probably a sentence
    if len(s.split()) > 6:
        return None
    return s


def _extract_building_heuristic(text: str) -> Optional[str]:
    """
    Heuristic fallback when building is not in DB.
    Returns canonical building name or None.

    Patterns recognized:
      1) 📍 <Name>, <Emirate>       e.g. "📍 Sea la vie, Abu Dhabi"
      2) 🏢 Project: <Name>          e.g. "🏢 Project: Ellington Views I"
      3) 🏡 <Name>                   on its own line, after Area: ...
      4) (Studio|N BR|N bedroom) in <Name>   e.g. "STUDIO in DG1 Living"
      5) <Name>, <KnownArea>         e.g. "Nobles Tower, Business Bay"
    """
    if not text:
        return None

    # Skip buyer requests entirely — they don't have a "their" building
    head = text[:300].lower()
    if re.search(r'\bbuyer\s*request\b|\bcash\s*buyer\b', head):
        return None
    if re.search(r'\blooking\s+for\b.{0,40}\b(?:bedroom|br|bhk|studio|villa|apartment|townhouse|plot|residence|commercial|office|retail|warehouse)', head):
        return None
    if re.search(r'\bseeking\b.{0,30}\b(?:bedroom|br|bhk|studio|villa|apartment|townhouse|plot|commercial)', head):
        return None

    emirate_pat = r'(?:Dubai|Abu Dhabi|Sharjah|Ras\s+Al\s+Khaimah|Ajman|Fujairah|Umm\s+Al\s+Quwain)'

    # Pattern 1: 📍 <Name>, <Emirate>
    for m in re.finditer(r'📍\s*([^\n,–\-📍🏡🏢]{3,50})\s*[,–\-]\s*' + emirate_pat, text, re.IGNORECASE):
        cand = _clean_building_candidate(m.group(1))
        if cand:
            return cand

    # Pattern 2: 🏢 Project: <Name>  (also "Project:" without emoji)
    for m in re.finditer(r'(?:🏢\s*)?Project\s*[:：]\s*([^\n]{3,60})', text, re.IGNORECASE):
        cand = _clean_building_candidate(m.group(1))
        if cand:
            return cand

    # Pattern 3: 🏡 <Name> on its own line  (one short line, just a name)
    for m in re.finditer(r'🏡\s*([^\n]{3,50})', text):
        cand = _clean_building_candidate(m.group(1))
        if cand:
            return cand

    # Pattern 4: (Studio|N BR|N bedroom) in <Name>  — bounded to current line
    for m in re.finditer(r'\b(?:studio|\d+\s*(?:br|bhk|bedroom|bed))\s+in\s+([A-Z][^\n]{2,40})', text, re.IGNORECASE):
        cand = _clean_building_candidate(m.group(1))
        if cand:
            return cand

    # Pattern 5: <Name>, <KnownArea>  (line starts with name, area follows after comma)
    known_area_names = []
    for area_name, info in AREAS.items():
        known_area_names.append(re.escape(area_name))
        for a in info.get("aliases", []):
            known_area_names.append(re.escape(a))
    area_pat = '|'.join(sorted(known_area_names, key=len, reverse=True))
    if area_pat:
        for m in re.finditer(r'(?:^|\n)\s*🏡?\s*([A-Z][^\n,]{2,40})\s*,\s*(?:' + area_pat + r')\b', text, re.IGNORECASE):
            cand = _clean_building_candidate(m.group(1))
            if cand:
                return cand

    # Pattern 6: "Unit: <Building>" or "Unit: <Building>, <details>"
    for m in re.finditer(r'\bUnit\s*[:：]\s*([^\n,]{3,40})', text, re.IGNORECASE):
        cand = _clean_building_candidate(m.group(1))
        # Skip if it's just a bedroom count like "2 BDR" or "1 Bed"
        if cand and not re.match(r'^\d+\s*(?:bed|br|bhk|bdr)', cand, re.I):
            return cand

    # Pattern 7: "Location: <X>" where X is NOT a known area (i.e. it's a building)
    for m in re.finditer(r'(?:^|\n)\s*\**\s*Location\s*\**\s*[:：]\s*([^\n]{3,50})', text, re.IGNORECASE):
        val = m.group(1).strip()
        # Strip markdown
        val_clean = re.sub(r'\*+|_+', '', val).strip(' ,.-–—:|')
        if val_clean and not _is_building_stopword(val_clean):
            cand = _clean_building_candidate(val_clean)
            if cand:
                return cand

    # Pattern 8: "📍<Name>" alone on a line, where next line is property-like (bedroom/sqft/price)
    # Allow em-dash/hyphen inside the name; _clean_building_candidate trims tail.
    for m in re.finditer(r'📍\s*([^\n,]{3,50})\s*\n([^\n]+)', text):
        next_line = m.group(2).lower()
        if re.search(r'\b(?:bedroom|studio|sqft|sq\.?\s*ft|sq\.?\s*m|sqm|m2|floor|sales?\s*price|selling\s*price|price\s*:|aed)\b', next_line):
            cand = _clean_building_candidate(m.group(1))
            if cand:
                return cand

    # Pattern 9: Markdown bold header at start of text
    # "**🌊 Clearpoint 3 – Rashid Yachts & Marina by EMAAR**"  → "Clearpoint 3"
    # "**MULBERRY (Dubai Hills)**" → "MULBERRY"
    head = text[:500]
    for m in re.finditer(r'\*\*\s*([^\n*]{3,80?})\s*\*\*', head):
        raw = m.group(1)
        # Strip emoji and parenthetical
        cleaned = re.sub(r'[📍🏡🏢💰🔥✨⭐️🌊🛏🛁🚘🪑🔑🪴📐☎️📞📩‼️🟥🟨🟩🌪🌴🚨📌💥🔹🔸🟦]', '', raw)
        cleaned = re.sub(r'\s*\([^)]+\)\s*', '', cleaned)  # remove (parenthetical)
        cleaned = cleaned.strip()
        # Skip if it's a category marker ("FOR SALE", "DISTRESS DEAL", etc.) — fully uppercase short phrases
        if re.fullmatch(r'[A-Z\s\d!:\-]{3,30}', cleaned) and len(cleaned.split()) <= 3 and not re.search(r'[a-z]', cleaned):
            # Could be a brand name in caps like "MULBERRY" or a category like "DISTRESS DEAL"
            stopwords_caps = {'FOR SALE', 'FOR RENT', 'DISTRESS DEAL', 'URGENT SALE',
                              'HOT DEAL', 'HOT OFFER', 'HOT OFFERS', 'NEW LAUNCH',
                              'NEW RENOVATION', 'VERY HOT OFFER', 'CASH BUYER',
                              'BUYER REQUEST', 'OP DEAL', 'OP PRICE', 'BELOW MARKET'}
            if cleaned.upper().strip(' !:') in stopwords_caps:
                continue
        cand = _clean_building_candidate(cleaned)
        if cand:
            return cand

    return None


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: DEVELOPER DETECTION → infer emirate
# ══════════════════════════════════════════════════════════════════════════════
def detect_developer(text: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (developer_name, emirate_hint)."""
    tl = text.lower()
    for dev_name, dev_info in DEVELOPERS.items():
        aliases = dev_info.get("aliases", [])
        all_names = [dev_name.lower()] + aliases
        for name in all_names:
            if re.search(r'\b' + re.escape(name) + r'\b', tl):
                return dev_name, dev_info.get("emirate")
    return None, None


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: RESOLVE AMBIGUOUS AREA
# For areas that exist in multiple emirates (Al Nahda → Dubai or Sharjah)
# Use building, price, and other signals
# ══════════════════════════════════════════════════════════════════════════════
def resolve_ambiguous_area(text: str, area: str, possible_emirates: list,
                            building_emirate: Optional[str],
                            developer_emirate: Optional[str],
                            price: Optional[int]) -> tuple[Optional[str], float]:
    """
    Try to resolve ambiguous area to a specific emirate.
    Returns (emirate, confidence) or (None, 0) if cannot resolve.
    """
    # Building is the strongest signal
    if building_emirate and building_emirate in possible_emirates:
        return building_emirate, 0.85

    # Developer hint
    if developer_emirate and developer_emirate in possible_emirates:
        return developer_emirate, 0.70

    # Price signal: Dubai prices tend to be higher
    # Al Nahda Dubai apartments: 800k-2M, Sharjah: 300k-600k
    if price and "Dubai" in possible_emirates and "Sharjah" in possible_emirates:
        if price > 700_000:
            return "Dubai", 0.65
        elif price < 400_000:
            return "Sharjah", 0.65

    # Cannot resolve → needs manual review
    return None, 0.0


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6: NOMINATIM FALLBACK
# ══════════════════════════════════════════════════════════════════════════════
_nominatim_cache = {}
_last_nominatim_call = 0.0


def nominatim_lookup(query: str) -> Optional[dict]:
    """Free OpenStreetMap geocoding. Rate limit: 1 req/sec."""
    global _last_nominatim_call
    cache_key = query.lower().strip()
    if cache_key in _nominatim_cache:
        return _nominatim_cache[cache_key]

    elapsed = _time.time() - _last_nominatim_call
    if elapsed < 1.1:
        _time.sleep(1.1 - elapsed)

    try:
        import requests
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{query} UAE", "format": "json", "limit": 3,
                    "countrycodes": "ae", "addressdetails": 1, "accept-language": "en"},
            headers={"User-Agent": "DubaiRealEstateBot/1.0"},
            timeout=8,
        )
        _last_nominatim_call = _time.time()
        if resp.status_code != 200:
            return None

        results = resp.json()
        if not results:
            _nominatim_cache[cache_key] = None
            return None

        item = results[0]
        addr = item.get("address", {})
        area = (addr.get("suburb") or addr.get("neighbourhood") or
                addr.get("district") or addr.get("city_district") or "")
        city = addr.get("city") or addr.get("state") or ""

        emirate = "Dubai"
        for em, aliases in EMIRATES.items():
            if any(a in city.lower() for a in aliases):
                emirate = em
                break

        result = {"name": item.get("name") or query, "area": area,
                  "emirate": emirate, "confidence": 0.70}
        _nominatim_cache[cache_key] = result
        print(f"[nominatim] {query} → {area}, {emirate}")
        return result
    except Exception as e:
        print(f"[nominatim] Error '{query}': {e}")
        _last_nominatim_call = _time.time()
        return None


# ══════════════════════════════════════════════════════════════════════════════
# PROPERTY DETAILS EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════
def extract_bedrooms(text: str) -> Optional[int]:
    tl = text.lower()
    if re.search(r'\bstudio\b|\bstd\b', tl):
        return 0
    # BHK format common in South Asian postings
    m = re.search(r'(\d)\s*bhk\b', tl)
    if m:
        return int(m.group(1))
    # BR / BED / BEDROOM variants
    m = re.search(r'(\d)\s*(?:br|bed(?:room)?s?)\b', tl)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d)\s*(?:bedroom|bed)\b', tl)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)\s*(?:bdr|b/r)\b', tl)
    if m: return int(m.group(1))
    m = re.search(r'bedrooms?\s*[:\-]?\s*(\d+)', tl)
    if m: return int(m.group(1))
    m = re.search(r'(\d+)\s*bedrooms?', tl)
    if m: return int(m.group(1))
    m = re.search(r'rooms?\s*[:\-]\s*(\d+)', tl)
    if m: return int(m.group(1))
    return None
    # "Unit: X Bedroom" format
def extract_size(text: str) -> dict:
    result = {"size_sqft": None, "bua_sqft": None, "plot_sqft": None}
    def _parse_num(s: str) -> Optional[float]:
        try:
            return float(str(s).replace(",", ".").replace(" ", "").strip()) if str(s).count(",") <= 1 else float(str(s).replace(",", "").replace(" ", "").strip())
        except (ValueError, TypeError):
            return None

    def _in_range_sqft(v: Optional[float]) -> bool:
        return v is not None and 50.0 <= v <= 100_000.0

    def _sqm_to_sqft(v: float) -> float:
        return round(v * 10.764, 1)

    # ── BUA: extract first (more specific) ─────────────────────────────────
    # "BUA: 3683 sqft", "Bua 1865 sq.ft", "BUA size: 2,456 Sq. Ft.", "BUA: 1865"
    m = re.search(r'\bBUA\s*(?:size)?\s*[:\-]?\s*([\d,]+\.?\d*)\s*(?:sq\.?\s*ft|sqft|sq\.?\s*f\b|sq\.?)?', text, re.I)
    if m:
        v = _parse_num(m.group(1).replace(",", ""))
        if _in_range_sqft(v):
            result["bua_sqft"] = v
    # BUA in sqm
    m = re.search(r'\bBUA\s*(?:size)?\s*[:\-]?\s*([\d,]+\.?\d*)\s*(?:sqm|sq\.?\s*m\b|m2|sq\.?\s*m)', text, re.I)
    if m and result["bua_sqft"] is None:
        v = _parse_num(m.group(1).replace(",", ""))
        if v and 5 <= v <= 10_000:
            result["bua_sqft"] = _sqm_to_sqft(v)

    # ── Plot ────────────────────────────────────────────────────────────────
    m = re.search(r'\bPlot\s*(?:size|area)?\s*[:\-]?\s*([\d,]+\.?\d*)\s*(?:sq\.?\s*ft|sqft|sq\.?)', text, re.I)
    if m:
        v = _parse_num(m.group(1).replace(",", ""))
        if _in_range_sqft(v):
            result["plot_sqft"] = v
    m = re.search(r'\bPlot\s*(?:size|area)?\s*[:\-]?\s*([\d,]+\.?\d*)\s*(?:sqm|sq\.?\s*m\b|m2)', text, re.I)
    if m and result["plot_sqft"] is None:
        v = _parse_num(m.group(1).replace(",", ""))
        if v and 5 <= v <= 10_000:
            result["plot_sqft"] = _sqm_to_sqft(v)

    # ── Main size_sqft ─────────────────────────────────────────────────────
    # Order: SQM with label first (so "Plot Area: 75.4 SQM" doesn't get caught as sqft)
    # Then SQFT with label, then bare patterns

    # SQM with label (e.g. "Plot Area: 75.4 SQM", "Area: 130 sqm", "Size: 120 m²")
    sqm_label_patterns = [
        r'(?:Plot\s+Area|Total\s+Area|Size|Area)\s*[:=\-]?\s*([\d,]+\.?\d*)\s*(?:sqm|sq\.?\s*m\b|m2|m²)',
        # Number AFTER label  (Sq.m : 55.17, sqm: 75.4)
        r'\bsq\.?\s*m\s*[:=]\s*([\d,]+\.?\d*)',
        r'\bsqm\s*[:=]\s*([\d,]+\.?\d*)',
        r'\bSQM\s*[:=]\s*([\d,]+\.?\d*)',
    ]
    for pat in sqm_label_patterns:
        m = re.search(pat, text, re.I)
        if m:
            v = _parse_num(m.group(1).replace(",", "").replace(" ", ""))
            if v and 5 <= v <= 10_000:
                result["size_sqft"] = _sqm_to_sqft(v)
                break

    # SQFT with label (e.g. "Size: 1148 sqft", "Total Area: 654 sq.ft")
    if result["size_sqft"] is None:
        sqft_label_patterns = [
            r'(?:Plot\s+Area|Total\s+Area|Size|Area)\s*[:=\-]?\s*([\d,]+\.?\d*)\s*(?:sq\.?\s*ft|sqft|ft[²*2])',
            # Number AFTER label  (Sqft: 3880, Sq Ft 1148)
            r'\bSqft\s*[:=]\s*([\d,]+\.?\d*)',
            r'\bSq\.?\s*Ft\s*[:=]\s*([\d,]+\.?\d*)',
        ]
        for pat in sqft_label_patterns:
            m = re.search(pat, text, re.I)
            if m:
                v = _parse_num(m.group(1).replace(",", ""))
                if _in_range_sqft(v):
                    result["size_sqft"] = v
                    break

    # Bare SQFT (number directly followed by unit)
    if result["size_sqft"] is None:
        bare_sqft_patterns = [
            r'([\d,]+\.?\d*)\s*sq\.?\s*ft\b',
            r'([\d,]+\.?\d*)\s*sqft\b',
            r'([\d,]+\.?\d*)\s*ft[²*2]',
            r'([\d,]+\.?\d*)\s*SF\b',
            r'([\d,]+\.?\d*)\s*sqf\b',
            r'([\d,]+\.?\d*)\s*sq\s+f\b',
        ]
        for pat in bare_sqft_patterns:
            m = re.search(pat, text, re.I)
            if m:
                v = _parse_num(m.group(1).replace(",", ""))
                if _in_range_sqft(v):
                    result["size_sqft"] = v
                    break

    # Bare SQM (number directly followed by unit)
    if result["size_sqft"] is None:
        bare_sqm_patterns = [
            r'([\d,]+\.?\d*)\s*sqm\b',
            r'([\d,]+\.?\d*)\s*sq\.?\s*m\b',
            r'([\d,]+\.?\d*)\s*m2\b',
            r'([\d,]+\.?\d*)\s*m²',
        ]
        for pat in bare_sqm_patterns:
            m = re.search(pat, text, re.I)
            if m:
                v = _parse_num(m.group(1).replace(",", "").replace(" ", ""))
                if v and 5 <= v <= 10_000:
                    result["size_sqft"] = _sqm_to_sqft(v)
                    break

    # Fallback: if size_sqft is still None but we have BUA → use BUA
    if result["size_sqft"] is None and result["bua_sqft"] is not None:
        result["size_sqft"] = result["bua_sqft"]

    return result


def _parse_amount(s: str) -> Optional[int]:
    """Parse price strings like '1.5M', '750k', '3.2ML', '1,200,000'."""
    if not s:
        return None
    s = str(s).replace(",", "").replace(" ", "").strip().upper(); s = re.sub(r"\.(\d{3})(?=\.|$)", r"\1", s) if s.count(".") > 1 else s
    try:
        # ML or M suffix (3.2ML = 3.2M = 3,200,000)
        # Guard: if base number >= 10000 it's not "Xm" notation (e.g. "21750000 m²")
        if s.endswith("MLN") or s.endswith("ML") or s.endswith("M"):
            v = float(s.rstrip("LMN"))
            if v >= 10000:
                return None
            return int(v * 1_000_000)
        if s.endswith("K"):
            v = float(s[:-1])
            if v >= 100000:
                return None
            return int(v * 1000)
        if s.endswith("B"):
            return int(float(s[:-1]) * 1_000_000_000)
        v = int(float(s))
        return v if v > 1000 else None
    except:
        return None


def _strip_phones(text: str) -> str:
    """Remove UAE phone numbers so they can't be mistaken for prices."""
    # +971XXXXXXXXX, 00971XXXXXXXXX (international format)
    text = re.sub(r'(?<!\d)(?:\+971|00971)[\s\-]?\d[\d\s\-]{7,12}(?!\d)', ' ', text)
    # 05X-XXX-XXXX (local UAE mobile)
    text = re.sub(r'(?<!\d)0(?:50|52|54|55|56|58|2|3|4|6|7|9)\d{7}(?!\d)', ' ', text)
    # Bare 971XXXXXXXXX at word boundary (no + prefix)
    text = re.sub(r'(?<!\d)971[5][0-9]{8}(?!\d)', ' ', text)
    return text


def extract_price(text: str) -> dict:
    result = {"price": None, "currency": "AED",
              "original_price": None, "selling_price": None}

    # Strip phone numbers before any price pattern matching
    text = _strip_phones(text)
    t = text  # preserve original case for Cash regex

    # -- Original / Purchase price
    m = re.search(r'(?:op|original\s*price|purchase\s*price|sale\s*price)[\s:]*([\d,\. ]+\s*[mkb]?l?)', t, re.I)
    if m:
        result["original_price"] = _parse_amount(m.group(1))
        if result["original_price"] and not result["price"]:
            result["price"] = result["original_price"]


    m = re.search(r'AED\s*([\d\. ]+\s*M?)\s*\(CASH\)', t, re.I)
    if m:
        raw = m.group(1).strip()
        # ensure M suffix handled
        if not re.search(r'[mkb]', raw, re.I):
            # check context — "AED 4.7M (Cash)"
            cm = re.search(r'AED\s*([\d\.]+)\s*(M)\s*\(CASH\)', t, re.I)
            if cm:
                raw = cm.group(1) + cm.group(2)
        v = _parse_amount(raw)
        if v:
            result["price"] = v
            result["selling_price"] = v
            return result

    if not result["price"]:
        m = re.search(r'(?:price|asking price)\s*[:\-]?\s*([\d,\. ]+\s*(?:mln|m|k)?)', t, re.I)
        if m:
            v = _parse_amount(m.group(1))
            if v: result["price"] = v
    # ── SP / Selling price / Asking / PP ─────────────────────────────────────
    m = re.search(
        r'(?:sp|pp|selling\s*price|asking\s*price|ask)\s*:?\s*(?:aed|usd)?\s*([\d,. ]+\s*[mbk]?)',
        t, re.I)
    if m:
        v = _parse_amount(m.group(1))
        if v:
            result["selling_price"] = v
            result["price"] = v
        # Slash price: 8,000/m or 8000/month
        m = re.search(r'([\d][\d,\. ]*)/(?:m\b|mo\b|month)', t, re.I)
    # Slash price: 8,000/m or 8000/month
    m = re.search(r'([\d][\d,\. ]*)/(?:m\b|mo\b|month)', t, re.I)
    if m:
        v = _parse_amount(m.group(1).replace(" ", ""))
        if v and v > 1000:
            result["price"] = v
            return result
    if not result["price"]:
        m = re.search(r'(?:rent|for\s+rent)\s*:?\s*([\d,\.]+\s*[mkb]?l?)', t, re.I)
        if m:
            v = _parse_amount(m.group(1))
            if v and v > 10_000:
                result["price"] = v

    # ── AED X.YM (non-mortgage) ───────────────────────────────────────────────
    if not result["price"]:
        m = re.search(r'AED\s*([\d\.]+)\s*M\b', t, re.I)
        if m:
            result["price"] = int(float(m.group(1)) * 1_000_000)

    # ── X.Xmln / X.Xmillion standalone ──────────────────────────────────────
    if not result["price"]:
        m = re.search(r'([\d\.]+)\s*(?:mln|million)\b', t, re.I)
        if m:
            v = _parse_amount(m.group(1) + 'M')
            if v: result["price"] = v

    # ── Русская цена: Цена XXXXX ─────────────────────────────────────────────
    if not result["price"]:
        m = re.search(r'(?:цена|стоимость)\s*[:\-]?\s*([\d,\.\s]+)', t, re.I)
        if m:
            v = _parse_amount(m.group(1).replace(' ', ''))
            if v: result["price"] = v

    # ── Generic: number + K/M/ML ──────────────────────────────────────────────
    if not result["price"]:
        for pat in [
            r'([\d\.]+\s*[Mm][Ll])\b',     # 3.2ML
            r'([\d\.]+)\s*[Mm]\b',          # 1.5M  (separate to avoid ML clash)
            r'([\d\.]+)\s*[Kk]\b',          # 750k
            r'(?:aed\s*)?([\d,\.]+)\s*([mk])\b',
            r'(?:aed\s*)?([\d,]{6,})',
        ]:
            for m in re.finditer(pat, t, re.I):
                groups = m.groups()
                raw = "".join(g for g in groups if g)
                amount = _parse_amount(raw)
                if amount and amount > 100_000:
                    result["price"] = amount
                    break
            if result["price"]:
                break

    return result


def extract_view(text: str) -> Optional[str]:
    tl = text.lower()
    # Sort by length DESC so "full sea view" beats "sea view"
    for view in sorted(VIEWS, key=len, reverse=True):
        if view in tl:
            return view.title()
    # Generic fallback: "View: X" or "View - X" (capture next short phrase)
    m = re.search(r'\bview\s*[:\-]\s*([A-Za-z][A-Za-z\s&,/]{2,40})', text, re.I)
    if m:
        val = m.group(1).strip(' ,/&').strip()
        # Cut at conjunction or newline-like punctuation
        val = re.split(r'\s*(?:,|/|\||\n|—|–|-)\s*', val)[0].strip()
        if 3 <= len(val) <= 40 and not re.search(r'\d|sqft|sq\.|aed|price', val, re.I):
            return val.title() + (" View" if not val.lower().endswith("view") else "")
    return None


def extract_floor(text: str) -> Optional[int]:
    # "Floor: 5", "fl#7", "floor 12"
    m = re.search(r'(?:floor|fl)[:\s#]*(\d+)', text, re.I)
    if m:
        return int(m.group(1))
    # "5th floor", "23rd Floor"
    m = re.search(r'(\d+)(?:st|nd|rd|th)\s*floor', text, re.I)
    if m:
        return int(m.group(1))
    # "8 floor", "5 floor" — number + space + floor (no ordinal)
    m = re.search(r'(?<!\d)(\d+)\s+floor\b', text, re.I)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 200:
            return v
    # "23floor" — number directly attached
    m = re.search(r'(?<!\d)(\d+)floor\b', text, re.I)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 200:
            return v
    return None


def extract_unit_number(text: str) -> Optional[str]:
    m = re.search(r'(?:unit|apt|flat|#|no\.)[:\s#]*([A-Z0-9\-]+)', text, re.I)
    if m:
        val = m.group(1).strip()
        if 2 <= len(val) <= 10:
            return val
    return None


def extract_property_type(text: str, bedrooms: Optional[int] = None) -> str:
    """Returns property type code.
    Iteration order in PROP_TYPE_MAP is SIGNIFICANT — specific types are checked first.
    For multi-listing texts, parse only the FIRST BLOCK (before --- separator) to bias
    to the primary listing rather than catching a type from listing #3.
    """
    # First listing block: up to first horizontal separator or blank-blank
    first_block = re.split(r'\n\s*[-—–=]{3,}\s*\n|\n\s*\n\s*\n', text, maxsplit=1)[0]
    head = first_block[:500].lower()
    full = text.lower()

    # Pass 1: check first listing block (most reliable)
    for ptype, keywords in PROP_TYPE_MAP.items():
        for kw in keywords:
            pat = kw if kw.endswith('\\b') else r'\b' + re.escape(kw) + r'\b'
            if re.search(pat, head):
                return ptype

    # No Pass 2 — single-pass on first block. Pass 2 over full text caused false positives
    # (e.g. "Office Room" in a villa caused property_type=office).

    # Fallback
    if bedrooms == 0:
        return "studio"
    return "apartment"


def extract_status(text: str) -> Optional[str]:
    tl = text.lower()
    for status, keywords in STATUS_KEYWORDS.items():
        for kw in keywords:
            if kw in tl:
                return status
    return None


def extract_bathrooms(text: str) -> Optional[int]:
    """Returns int (1..20) or None.

    Patterns recognized:
      - "3 Bathrooms", "4 bath", "5 Bathroom" (number before label)
      - "Bathrooms: 2", "🛁 Bathrooms : 3" (label before number)
      - "2 BA" (rare abbreviation)
    """
    # Number BEFORE label: "3 Bathrooms", "4 bath", "5 Bathroom"
    m = re.search(r'(?<![\d.])(\d{1,2})\s*(?:bathroom|bath)s?\b', text, re.I)
    if m:
        v = int(m.group(1))
        if 1 <= v <= 20:
            return v
    # Label BEFORE number: "Bathrooms: 3", "Bathroom-2"
    m = re.search(r'\bbathrooms?\s*[:\-]\s*(\d{1,2})\b', text, re.I)
    if m:
        v = int(m.group(1))
        if 1 <= v <= 20:
            return v
    # "2 BA" — only if standalone, not part of word
    m = re.search(r'(?<![\d.])(\d{1,2})\s*BA\b(?![A-Za-z])', text)
    if m:
        v = int(m.group(1))
        if 1 <= v <= 20:
            return v
    return None


def extract_furnishing(text: str) -> Optional[str]:
    """Order matters: check 'semi-furnished' and 'unfurnished' BEFORE 'furnished',
    otherwise 'Unfurnished' is wrongly matched as 'furnished' (substring trap).
    """
    tl = text.lower()
    # semi-furnished (most specific)
    if re.search(r'\bsemi[\s\-]?furnished\b|\bs/f\b', tl):
        return "semi-furnished"
    # unfurnished
    if re.search(r'\bunfurnished\b|\bun[\s\-]furnished\b|\bu/f\b', tl):
        return "unfurnished"
    # furnished (must come last; use word boundary so it doesn't match inside "unfurnished")
    if re.search(r'\b(?:fully\s+)?furnished\b|\bf/f\b', tl):
        return "furnished"
    # bare (= unfurnished in real-estate parlance)
    if re.search(r'\bbare\b', tl):
        return "unfurnished"
    return None


def extract_contacts(text: str) -> dict:
    result = {"phone": None, "whatsapp": None, "agent_name": None}
    phones = re.findall(
        r'(?:\+971|00971|0)[\s\-]?(?:50|52|54|55|56|58|2|3|4|6|7|9)[\s\-]?\d{3}[\s\-]?\d{4}',
        text)
    if phones:
        cleaned = re.sub(r'[\s\-]', '', phones[0])
        if not cleaned.startswith("+"):
            cleaned = "+971" + cleaned.lstrip("0")
        result["phone"] = cleaned
        result["whatsapp"] = cleaned
    m = re.search(r'(?:contact|agent|broker|call)[:\s]+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)', text)
    if m:
        result["agent_name"] = m.group(1).strip()
    return result


# ══════════════════════════════════════════════════════════════════════════════
# DEAL QUALITY & ROI
# ══════════════════════════════════════════════════════════════════════════════
def _lookup_benchmark(building: Optional[str]) -> Optional[dict]:
    """Find PRICE_BENCHMARKS entry for building (case-insensitive, partial)."""
    if not building:
        return None
    bdata = PRICE_BENCHMARKS.get(building) or PRICE_BENCHMARKS.get(building.upper())
    if bdata:
        return bdata
    bl = building.lower()
    for k, v in PRICE_BENCHMARKS.items():
        if bl == k.lower():
            return v
    # Partial match — only if building name is long enough to be unambiguous
    if len(building) >= 6:
        for k, v in PRICE_BENCHMARKS.items():
            if bl in k.lower() or k.lower() in bl:
                return v
    return None


def compute_deal_quality(price: int, original_price: Optional[int],
                          size_sqft: Optional[float], area: Optional[str],
                          bedrooms: Optional[int],
                          building: Optional[str] = None,
                          deal_type: Optional[str] = None) -> dict:
    """Deal-quality scoring.
    Priority of market comparison:
      1) Building-level DLD median (PRICE_BENCHMARKS) — most precise
      2) District-level average (MARKET) — fallback
    """
    result = {
        "is_hot_deal": False, "deal_quality": "normal", "deal_reason": None,
        "discount_amount": None, "discount_percent": None,
        "is_below_market": False, "price_vs_market_percent": None,
        "market_avg_sqft": None, "roi_estimate": None,
        "airbnb_estimate_low": None, "airbnb_estimate_high": None,
        "investment_score": None, "market_rent_1br": None, "market_growth_pct": None,
        "benchmark_source": None,  # "dld_building" | "district"
    }
    if not price:
        return result

    # Discount from original price (works regardless of comparison source)
    if original_price and original_price > price:
        discount = original_price - price
        discount_pct = round(discount / original_price * 100, 1)
        result["discount_amount"] = discount
        result["discount_percent"] = discount_pct
        if discount_pct >= 12:
            result["deal_quality"] = "very_good"
            result["is_hot_deal"] = True
            result["deal_reason"] = f"Selling price is {discount_pct}% below original purchase price"
        elif discount_pct >= 7:
            result["deal_quality"] = "good"
            result["is_hot_deal"] = True
            result["deal_reason"] = f"Price is {discount_pct}% below original price"
        elif discount_pct >= 3:
            result["deal_quality"] = "interesting"
            result["deal_reason"] = f"Slight discount of {discount_pct}% from original price"

    # District-level market context (always populated for ROI/growth)
    mkt = MARKET.get(area, DEFAULT_MKT) if area else DEFAULT_MKT
    mkt_sqft = mkt["sqft"]
    result["market_avg_sqft"] = mkt_sqft
    result["market_rent_1br"] = mkt["rent_1br"]
    result["market_growth_pct"] = mkt["growth"]

    # PRIORITY 1: Building-level DLD benchmark (if available and we're parsing sale)
    bench = _lookup_benchmark(building)
    used_bench = False

    if (deal_type or "sale") == "sale" and bench:
        sale_med = bench.get("sale_median")
        sale_min = bench.get("sale_min")
        sale_max = bench.get("sale_max")
        sale_count = bench.get("sale_count") or 0
        # Require at least 5 DLD transactions for the benchmark to be reliable
        if sale_med and sale_count >= 5:
            price_vs_bld = round((price - sale_med) / sale_med * 100, 1)
            # Sanity: if price is so different that it must be data error, skip
            if -90 <= price_vs_bld <= 500:
                result["price_vs_market_percent"] = price_vs_bld
                result["benchmark_source"] = "dld_building"
                used_bench = True
                if price_vs_bld <= -15:
                    result["is_below_market"] = True
                    result["is_hot_deal"] = True
                    if result["deal_quality"] == "normal":
                        result["deal_quality"] = "very_good"
                        result["deal_reason"] = f"Price {abs(price_vs_bld)}% below DLD median for this building"
                elif price_vs_bld <= -8:
                    result["is_below_market"] = True
                    if result["deal_quality"] == "normal":
                        result["deal_quality"] = "good"
                        result["deal_reason"] = f"Price {abs(price_vs_bld)}% below DLD median for this building"

    # FALLBACK: district-level comparison via size_sqft × mkt_sqft
    if not used_bench and size_sqft and size_sqft > 0:
        market_value = int(size_sqft * mkt_sqft)
        price_vs_mkt = round((price - market_value) / market_value * 100, 1)
        result["price_vs_market_percent"] = price_vs_mkt
        result["benchmark_source"] = "district"
        if price_vs_mkt <= -12:
            result["is_below_market"] = True
            result["is_hot_deal"] = True
            if result["deal_quality"] == "normal":
                result["deal_quality"] = "very_good"
                result["deal_reason"] = f"Price is {abs(price_vs_mkt)}% below district average"
        elif price_vs_mkt <= -7:
            result["is_below_market"] = True
            if result["deal_quality"] == "normal":
                result["deal_quality"] = "good"
                result["deal_reason"] = f"Price is {abs(price_vs_mkt)}% below district average"

    # ROI — prefer DLD building rent_median_yearly if available, else MARKET
    rent_1br = mkt["rent_1br"]
    annual_rent_bld = bench.get("rent_median_yearly") if bench else None
    br_key = {0: "studio", 1: "1br", 2: "2br", 3: "3br"}.get(bedrooms or 1, "1br")
    if bedrooms and bedrooms >= 4:
        br_key = "4br+"
    if annual_rent_bld and (bench.get("rent_count") or 0) >= 3:
        annual_rent = int(annual_rent_bld)
    else:
        annual_rent = int(rent_1br * RENT_MULT.get(br_key, 1.0))
    if price > 0:
        roi = round(annual_rent / price * 100, 1)
        result["roi_estimate"] = roi
        result["airbnb_estimate_low"] = int(annual_rent * 1.4)
        result["airbnb_estimate_high"] = int(annual_rent * 1.7)
        score = 5.0
        if roi >= 8: score += 2
        elif roi >= 6: score += 1
        if mkt.get("growth", 4) >= 7: score += 1.5
        elif mkt.get("growth", 4) >= 5: score += 0.5
        if result["is_below_market"]: score += 1
        if result["is_hot_deal"]: score += 0.5
        result["investment_score"] = min(round(score, 1), 10.0)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# CONFIDENCE SCORE
# ══════════════════════════════════════════════════════════════════════════════
def compute_confidence(data: dict) -> tuple[float, bool, Optional[str]]:
    score = 0.0
    reasons = []

    if data.get("emirate"):
        score += data.get("emirate_confidence", 0) * 0.25
    else:
        reasons.append("emirate not detected")

    if data.get("area"):
        score += data.get("area_confidence", 0) * 0.30
    else:
        reasons.append("area not detected")

    if data.get("building"):
        score += data.get("building_confidence", 0) * 0.25
    else:
        score += 0.05

    if data.get("price"):
        score += 0.10
    else:
        reasons.append("price not found")

    if data.get("bedrooms") is not None:
        score += 0.05

    if data.get("size_sqft"):
        score += 0.05

    score = round(min(score, 1.0), 2)
    needs_review = score < 0.70 or bool(reasons)
    reason = "; ".join(reasons) if reasons else None
    return score, needs_review, reason


# ══════════════════════════════════════════════════════════════════════════════
# LISTING KEY (for deduplication)
# ══════════════════════════════════════════════════════════════════════════════
def make_listing_key(data: dict) -> Optional[str]:
    emirate = (data.get("emirate") or "").lower().replace(" ", "")
    area = (data.get("area") or "").lower().replace(" ", "")
    building = (data.get("building") or "").lower().replace(" ", "")
    unit = (data.get("unit_number") or "").lower().replace(" ", "")
    bedrooms = str(data.get("bedrooms") or "")
    size = str(int(data.get("size_sqft") or 0))
    if not (area or building):
        return None
    parts = [p for p in [emirate, area, building, unit, bedrooms, size] if p]
    return "|".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN PARSE FUNCTION — Full ТЗ pipeline
# ══════════════════════════════════════════════════════════════════════════════
def parse_message(
    text: str,
    message_id: int,
    message_date,
    chat_id: str,
    seller_username: str = None,
    image_urls: list = None,
) -> Optional[dict]:
    """
    Full ТЗ parsing pipeline:
    1. Clean text
    2. Spam check
    3. Deal type
    4. STEP 1: Emirate direct detection
    5. STEP 2: Area detection → infer emirate
    6. STEP 3: Building detection → SOURCE OF TRUTH for area+emirate
    7. STEP 4: Developer → emirate hint
    8. STEP 5: Resolve ambiguous areas
    9. STEP 6: Nominatim fallback if confidence low
    10. Extract all property details
    11. Price + deal quality
    12. Confidence score
    13. needs_manual_review
    """
    original_text = text
    clean = clean_text(text)
    
    # --- DLD pre-lookup: find building from first lines before heavy detection ---
    _dld_building = None
    _dld_area = None
    _first_lines = [l.strip() for l in text.split('\n')[:4] if l.strip() and len(l.strip()) > 3 and not any(c.isdigit() for c in l.strip()[:5])]
    for _line in _first_lines:
        _dld_r = dld_lookup(_line)
        if _dld_r:
            _dld_building = _dld_r.get('building')
            _dld_area = _dld_r.get('area')
            break

    # Step 2: Spam check
    if is_spam(clean):
        return None

    # Step 3: Deal type
    deal_type = detect_deal_type(clean)

    # ── Step 0: Header-line structural patterns ───────────────────────────────
    # Extract hints from first few lines before heavier entity detection
    header_hints = extract_from_header_lines(text)

    # ── Step 1: Emirate direct ────────────────────────────────────────────────
    emirate, emirate_conf = detect_emirate_direct(clean)

    # ── Step 3: Building (do this early — it's SOURCE OF TRUTH) ──────────────
    building, building_conf, bld_area, bld_emirate, developer_from_bld = detect_building(clean)

    # If header found a building name and main detector missed it → use header hint
    if not building and header_hints.get("building"):
        building = header_hints["building"]
        building_conf = 0.70
        bld_area = None
        bld_emirate = None
        developer_from_bld = None

    # If building gave us area/emirate → use them (high confidence)
    area = None
    area_conf = 0.0
    area_emirate = None
    possible_emirates = []

    if bld_area:
        area = bld_area
        area_conf = building_conf  # area confidence = building confidence
        area_emirate = bld_emirate

    if bld_emirate:
        if not emirate:
            emirate = bld_emirate
            emirate_conf = building_conf
        elif emirate != bld_emirate:
            # Conflict: text says Dubai but building is in Abu Dhabi
            # Trust the building database
            emirate = bld_emirate
            emirate_conf = building_conf * 0.9

    # ── Step 2: Area detection ────────────────────────────────────────────────
    if not area:
        area, area_conf, area_emirate, possible_emirates = detect_area(clean, emirate)
        if area_emirate and not emirate:
            emirate = area_emirate
            emirate_conf = area_conf * 0.9

    # ── Step 2b: Area from header hint (if still missing) ────────────────────
    if not area and header_hints.get("area"):
        h_area = header_hints["area"]
        h_area_match = _match_area_by_name(h_area)
        if h_area_match:
            # Get emirate info from AREAS dict
            h_info = AREAS.get(h_area_match, {})
            area = h_area_match
            area_conf = 0.80
            area_emirate = h_info.get("emirate")
            if area_emirate and not emirate:
                emirate = area_emirate
                emirate_conf = 0.80
        elif not emirate and h_area:
            # Abbreviation resolved to emirate name (e.g. "Dubai")
            area_conf_hint = 0.65
            _ = h_area  # keep as raw string hint for later

    # ── Step 4: Developer hint ────────────────────────────────────────────────
    developer, developer_emirate = detect_developer(clean)
    if not developer and developer_from_bld:
        developer = developer_from_bld

    if developer_emirate and not emirate:
        emirate = developer_emirate
        emirate_conf = 0.65

    # ── Step 5: Resolve ambiguous area ───────────────────────────────────────
    needs_manual = False
    review_reason = None

    if possible_emirates:
        # Extract price for signal
        temp_price_data = extract_price(clean)
        temp_price = temp_price_data.get("price")

        resolved_emirate, resolved_conf = resolve_ambiguous_area(
            clean, area, possible_emirates,
            bld_emirate, developer_emirate, temp_price
        )
        if resolved_emirate:
            emirate = resolved_emirate
            emirate_conf = resolved_conf
            area_conf = 0.70  # upgraded from 0.60
        else:
            needs_manual = True
            review_reason = f"Area '{area}' exists in multiple emirates: {possible_emirates}"

    # ── Step 6: Nominatim fallback ────────────────────────────────────────────
    # DLD lookup by first lines when building not found
    if not building:
        first_lines = [l.strip() for l in original_text.split('\n')[:3] if l.strip() and len(l.strip()) > 3]
        for line in first_lines:
            if not any(c.isdigit() for c in line[:5]):  # не начинается с цифр
                dld_r = dld_lookup(line)
                if dld_r:
                    building = dld_r.get('building')
                    if not area:
                        area = dld_r.get('area')
                    break
    if building and not area:
        dld = dld_lookup(building)
        if dld:
            area = dld.get("area") or area
    # Apply DLD pre-lookup results - DLD has priority over weak detection
    if _dld_building:
        building = _dld_building
        building_conf = 0.75
    if not area and _dld_area:
        area = _dld_area
    if building and not area and building_conf < 0.80:
        nom = nominatim_lookup(building)
        if nom:
            area = nom.get("area") or area
            area_conf = nom.get("confidence", 0.70)
            if not emirate and nom.get("emirate"):
                emirate = nom["emirate"]
                emirate_conf = 0.70

    # ── Extract all property details ──────────────────────────────────────────
    bedrooms = extract_bedrooms(clean)
    # Fallback: header-line structural hint for bedrooms (BHK patterns)
    if bedrooms is None and header_hints.get("bedrooms") is not None:
        bedrooms = header_hints["bedrooms"]
    bathrooms = extract_bathrooms(clean)
    sizes = extract_size(clean)
    view = extract_view(clean)
    floor = extract_floor(clean)
    unit_number = extract_unit_number(clean)
    prop_type = extract_property_type(clean, bedrooms)
    status = extract_status(clean)
    furnishing = extract_furnishing(clean)
    contacts = extract_contacts(original_text)

    # ── Price ─────────────────────────────────────────────────────────────────
    price_data = extract_price(clean)
    price = price_data.get("price")
    price_per_sqft = None
    if price and sizes.get("size_sqft"):
        price_per_sqft = round(price / sizes["size_sqft"], 0)

    # ── Validate deal_type against market floor prices ─────────────────────
    deal_type = validate_deal_type_by_price(price, deal_type, bedrooms, area, text=original_text)

    # ── Deal quality ──────────────────────────────────────────────────────────
    deal_analysis = compute_deal_quality(
        price=price or 0,
        original_price=price_data.get("original_price"),
        size_sqft=sizes.get("size_sqft"),
        area=area,
        bedrooms=bedrooms,
        building=building,
        deal_type=deal_type,
    )

    # ── Assemble ──────────────────────────────────────────────────────────────
    data = {
        "source": "telegram",
        "telegram_chat_id": str(chat_id),
        "telegram_message_id": message_id,
        "message_date": message_date.isoformat() if hasattr(message_date, 'isoformat') else str(message_date),
        "original_text": original_text[:2000],
        "seller_username": seller_username,
        "deal_type": deal_type,
        "property_type": prop_type,

        "emirate": emirate,
        "emirate_confidence": round(emirate_conf, 2),
        "area": area,
        "area_confidence": round(area_conf, 2),
        "building": building,
        "building_confidence": round(building_conf, 2),

        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "size_sqft": sizes.get("size_sqft"),
        "bua_sqft": sizes.get("bua_sqft"),
        "plot_sqft": sizes.get("plot_sqft"),
        "floor": floor,
        "unit_number": unit_number,
        "view": view,
        "furnishing": furnishing,
        "status": status,

        "price": price,
        "currency": "AED",
        "original_price": price_data.get("original_price"),
        "selling_price": price_data.get("selling_price"),
        "price_per_sqft": price_per_sqft,

        **deal_analysis,

        "agent_name": contacts.get("agent_name"),
        "phone": contacts.get("phone"),
        "whatsapp": contacts.get("whatsapp"),

        "has_images": bool(image_urls),
        "cover_image_url": image_urls[0] if image_urls else None,
    }

    # ── Confidence ────────────────────────────────────────────────────────────
    confidence, auto_needs_review, auto_reason = compute_confidence(data)
    data["confidence_score"] = confidence

    if needs_manual or auto_needs_review:
        data["needs_manual_review"] = True
        data["review_reason"] = review_reason or auto_reason
    else:
        data["needs_manual_review"] = False
        data["review_reason"] = None

    data["listing_key"] = make_listing_key(data)

    return data


# ══════════════════════════════════════════════════════════════════════════════
# AI CLASSIFICATION — Claude Haiku
# ══════════════════════════════════════════════════════════════════════════════
_ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Token usage stats (claude-haiku-4-5: $0.80/1M in, $0.40/1M out)
_ai_stats: dict = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

_AI_PROMPT_TEMPLATE = """\
You are a UAE real estate expert. Parse this property listing and return strict JSON only — no markdown, no explanation.

MARKET KNOWLEDGE:
Sale (AED): Studio 350K-2M | 1BR 600K-4M | 2BR 900K-8M | 3BR 1.4M-15M | 4BR+ 2.5M-50M
Rent (AED/yr): Studio 25K-100K | 1BR 40K-180K | 2BR 70K-300K | 3BR 100K-500K | 4BR+ 150K-1M

RULES:
- price < 500K and no clear sale signals → rent
- "payment plan" / "handover" / "off plan" / "mortgage" → sale
- "per year" / "per annum" / "cheques" / "tenanted" → rent
- building ≠ view ("Burj Khalifa View" → view field, building=null)
- building ≠ ownership (Freehold/Leasehold → null)
- building ≠ agency name (ignore "XYZ Properties")
- JVC=Jumeirah Village Circle, JLT=Jumeirah Lake Towers, JVT=Jumeirah Village Triangle, DCH=Dubai Creek Harbour, MBR=MBR City, DSO=Dubai Silicon Oasis

Return ONLY valid JSON:
{{"deal_type":"sale or rent","property_type":"apartment or villa or townhouse or penthouse or null","building":"name or null","district":"area or null","emirate":"Dubai or Abu Dhabi or Sharjah or Ras Al Khaimah or null","bedrooms":0,"area_sqft":null,"price":null,"view":"null","status":"vacant or rented or ready or off plan or null","furnishing":"furnished or unfurnished or semi-furnished or null","floor":null,"is_spam":false,"confidence":"high or medium or low"}}

Listing:
{text}"""


def ai_parse_listing(raw_text: str) -> Optional[dict]:
    """
    AI-классификация одного объявления через Claude Haiku.
    Возвращает dict или None при ошибке. Никогда не падает.
    """
    if not _ANTHROPIC_KEY:
        return None
    if not raw_text or len(raw_text.strip()) < 20:
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=_ANTHROPIC_KEY)

        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=350,
            messages=[{
                "role": "user",
                "content": _AI_PROMPT_TEMPLATE.format(text=raw_text[:600]),
            }],
        )

        # Track token usage
        _ai_stats["input_tokens"]  += resp.usage.input_tokens
        _ai_stats["output_tokens"] += resp.usage.output_tokens
        _ai_stats["calls"] += 1

        # Log cost every 500 calls ($0.80/1M in + $0.40/1M out for haiku)
        if _ai_stats["calls"] % 500 == 0:
            cost = (
                _ai_stats["input_tokens"]  * 0.00000080
                + _ai_stats["output_tokens"] * 0.00000040
            )
            print(
                f"[AI COST] calls={_ai_stats['calls']} "
                f"tokens={_ai_stats['input_tokens'] + _ai_stats['output_tokens']:,} "
                f"cost=${cost:.4f}"
            )

        text_out = resp.content[0].text.strip()
        text_out = re.sub(r"```json|```", "", text_out).strip()
        return json.loads(text_out)

    except Exception as e:
        print(f"[AI PARSE ERROR] {e}")
        return None


def merge_ai_with_parsed(parsed: dict, ai: dict) -> dict:
    """
    Объединяет результат AI с результатом rule-based парсера.
    AI имеет приоритет для deal_type, building, district, bedrooms, price.
    Rule-based сохраняется как fallback.
    """
    if not ai:
        return parsed

    # deal_type — AI всегда побеждает если вернул значение
    if ai.get("deal_type") in ("sale", "rent"):
        parsed["deal_type"] = ai["deal_type"]

    # Жёсткая валидация deal_type по цене (после AI)
    final_price = parsed.get("price") or ai.get("price") or 0
    if final_price > 0:
        if parsed.get("deal_type") == "sale" and final_price < 500_000:
            parsed["deal_type"] = "rent"
        elif parsed.get("deal_type") == "rent" and final_price > 30_000_000:
            parsed["deal_type"] = "sale"

    # building — AI заполняет если rule-based пропустил
    if ai.get("building") and not parsed.get("building"):
        parsed["building"] = ai["building"]

    # area — AI поле "district" → наш "area"
    if ai.get("district") and not parsed.get("area"):
        parsed["area"] = ai["district"]

    # emirate fallback
    if ai.get("emirate") and not parsed.get("emirate"):
        parsed["emirate"] = ai["emirate"]

    # bedrooms — AI заполняет если нет
    if ai.get("bedrooms") is not None and parsed.get("bedrooms") is None:
        parsed["bedrooms"] = ai["bedrooms"]

    # price fallback
    if ai.get("price") and not parsed.get("price"):
        parsed["price"] = ai["price"]

    # size_sqft fallback — с валидацией битых данных
    if ai.get("area_sqft") and not parsed.get("size_sqft"):
        sqft = ai["area_sqft"]
        final_price = parsed.get("price") or 0
        if 50 <= sqft <= 50000:
            if final_price and sqft > 0 and (final_price / sqft) > 100000:
                pass  # цена за sqft абсурдная — не сохраняем
            else:
                parsed["size_sqft"] = sqft
        # иначе битые данные — не сохраняем

    # view fallback
    if ai.get("view") and not parsed.get("view"):
        parsed["view"] = ai["view"]

    # status, furnishing, floor fallbacks
    if ai.get("status") and not parsed.get("status"):
        parsed["status"] = ai["status"]
    if ai.get("furnishing") and not parsed.get("furnishing"):
        parsed["furnishing"] = ai["furnishing"]
    if ai.get("floor") is not None and parsed.get("floor") is None:
        parsed["floor"] = ai["floor"]

    # Tag as AI-enhanced
    parsed["ai_classified"] = True
    parsed["ai_confidence"] = ai.get("confidence", "medium")

    return parsed


def extract_building_from_rules(text):
    if not text or not BUILDINGS_DB:
        return None
    text_lower = text.lower()
    # Сортируем по длине названия (длинные первыми - более точное совпадение)
    for bname, bdata in sorted(BUILDINGS_DB.items(), key=lambda x: len(x[0]), reverse=True):
        if bname.lower() in text_lower:
            return bname
        # Проверяем aliases если есть
        aliases = bdata.get('aliases', [])
        for alias in aliases:
            if alias.lower() in text_lower:
                return bname
    return None


def extract_area_from_rules(text):
    if not text or not AREAS:
        return None
    text_lower = text.lower()
    for aname, adata in sorted(AREAS.items(), key=lambda x: len(x[0]), reverse=True):
        if aname.lower() in text_lower:
            return aname
        aliases = adata.get('aliases', [])
        for alias in aliases:
            if alias.lower() in text_lower:
                return aname
    return None


def expand_abbreviations(text):
    if not text or not RULES:
        return text
    abbrevs = RULES.get('abbreviations', {})
    for abbr, full in abbrevs.items():
        import re
        text = re.sub(r'\b' + re.escape(abbr) + r'\b', full, text, flags=re.IGNORECASE)
    return text


DLD_DB_URL = "postgresql://postgres:REDACTED_ARCHIVE_DB_PASSWORD@switchback.proxy.rlwy.net:23244/railway"

def dld_lookup(building_name):
    """Lookup building in live DLD Postgres. Returns dict with building/area/avg_price or None.
    Includes AVG(actual_worth) so callers can do price comparison.
    """
    if not building_name or len(building_name) < 3:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(DLD_DB_URL, connect_timeout=5)
        cur = conn.cursor()
        # Exact match (case-insensitive)
        cur.execute("""
            SELECT building_name_en, area_name_en, AVG(actual_worth) AS avg_price, COUNT(*) AS cnt
            FROM dld_sales_unified
            WHERE UPPER(building_name_en) = UPPER(%s) AND building_name_en != ''
            GROUP BY building_name_en, area_name_en
            ORDER BY cnt DESC LIMIT 1
        """, (building_name,))
        row = cur.fetchone()
        if not row:
            # Partial match
            cur.execute("""
                SELECT building_name_en, area_name_en, AVG(actual_worth) AS avg_price, COUNT(*) AS cnt
                FROM dld_sales_unified
                WHERE building_name_en ILIKE %s AND building_name_en != ''
                GROUP BY building_name_en, area_name_en
                ORDER BY cnt DESC LIMIT 1
            """, ('%' + building_name + '%',))
            row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            return {
                "building": row[0],
                "area": row[1],
                "avg_price": float(row[2]) if row[2] else None,
                "count": int(row[3]) if row[3] else 0,
            }
    except Exception:
        pass
    return None


# Load price benchmarks
import os as _os
_BENCHMARKS_PATH = _os.path.join(_os.path.dirname(__file__), 'price_benchmarks.json')
try:
    with open(_BENCHMARKS_PATH, 'r', encoding='utf-8') as _f:
        PRICE_BENCHMARKS = json.load(_f)
    print(f'[parser] Price benchmarks loaded: {len(PRICE_BENCHMARKS)} buildings')
except:
    PRICE_BENCHMARKS = {}
    print('[parser] Price benchmarks not found')


def classify_deal_by_price(building, price):
    """
    Returns 'sale', 'rent', or None based on price vs DLD benchmarks.
    price - raw number from message (AED)
    """
    if not building or not price or not PRICE_BENCHMARKS:
        return None
    
    # Try exact match first, then case-insensitive
    bdata = PRICE_BENCHMARKS.get(building) or PRICE_BENCHMARKS.get(building.upper())
    if not bdata:
        # Try partial match
        bname_lower = building.lower()
        for k, v in PRICE_BENCHMARKS.items():
            if bname_lower in k.lower() or k.lower() in bname_lower:
                bdata = v
                break
    
    if not bdata:
        return None
    
    sale_median = bdata.get('sale_median')
    rent_median = bdata.get('rent_median_yearly')
    
    # If price is close to rent range (5k-300k AED/year)
    if rent_median and 5000 < price < 500000:
        rent_diff = abs(price - rent_median) / rent_median if rent_median else 1
        if rent_diff < 0.8:  # within 80% of median rent
            return 'rent'
    
    # If price is close to sale range (100k+ AED)
    if sale_median and price > 100000:
        sale_diff = abs(price - sale_median) / sale_median if sale_median else 1
        if sale_diff < 1.5:  # within 150% of median sale
            return 'sale'
    
    # Fallback by magnitude
    if price < 200000:
        return 'rent'
    if price > 300000:
        return 'sale'
    
    return None
