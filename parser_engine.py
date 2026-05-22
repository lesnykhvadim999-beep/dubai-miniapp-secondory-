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
    # NOTE: "AR" alone removed — слишком короткий, ловит "Ar**ea" (markdown break)
    # и "Area" во многих текстах. AR2/AR3 безопасны (с цифрой).
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
    # Whole building offerings — check FIRST so they don't get bucketed as apartment/villa
    "whole_building":    [
        "full building for sale", "full building for rent",
        "whole building for sale", "whole building for rent",
        "entire building for sale", "entire building for rent",
        "building for sale", "building for rent",
        "residential building for sale", "commercial building for sale",
        "apartment building for sale", "mixed use building for sale",
        "mixed-use building for sale",
        "tower for sale", "tower for rent",
        "целое здание", "здание целиком", "здание на продажу",
    ],
    # Specific residential types (check after whole_building)
    "penthouse":         ["penthouse", "pent house", "пентхаус"],
    "hotel_apartment":   ["hotel apartment", "hotel apt", "hotel residence", "hotel residences"],
    "serviced_apartment":["serviced apartment", "serviced apt", "serviced residence"],
    "villa":             ["villa", "villas", "detached villa", "independent villa", "вилла", "виллы"],
    "townhouse":         ["townhouse", "town house", "townhome", "таунхаус", "таунхауc"],
    "duplex":            ["duplex", "дуплекс"],
    # Commercial — keywords must be in specific commercial-context phrases, not casual mentions
    "hotel":             ["hotel for sale", "hotel for lease", "branded hotel", "boutique hotel"],
    "office":            ["office for sale", "office for rent", "office sale", "for sale | office",
                          "for rent | office", "office space for sale", "office space for rent",
                          "office unit", "office space", "office floor", "fitted office",
                          "shell and core office", "офис на продажу"],
    "retail":            ["retail for sale", "retail for rent", "retail unit",
                          "retail space", "retail opportunity", "premium retail", "prime retail",
                          "retail shop", "shop for sale", "shop for rent"],
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
    if any(k in tl for k in SPAM_KEYWORDS): return True
    if any(k in tl for k in COMMERCIAL_KEYWORDS): return True
    # ── Buyer's request / search ad — это запрос, не предложение ───────────
    # Примеры: "❌ request ❌ Looking a hotel for sale Budget 90M"
    #          "Cash buyer looking for studio in JVC"
    #          "Client looking to buy 2BR Marina up to 3M"
    # СИЛЬНЫЕ buyer-маркеры — однозначно spam, независимо от "for sale" в тексте
    strong_buyer = [
        r'❌\s*request\s*❌',
        r'\b(?:urgent\s+)?request\b.*\b(?:looking|need|require|cash|client|buyer|ready)',
        r'\b(?:client|buyer|investor)\s+(?:looking\s+(?:for|to)|wants?\s+to\s+(?:buy|invest))',
        r'\bcash\s+buyer\b',
        r'\bready\s+client\b',
        r'\blooking\s+(?:a\s+|for\s+a?\s*)?\b(?:apartment|villa|townhouse|studio|penthouse|hotel|office|plot|property|land)\b.*\bbudget\b',
        r'\bany\s+available\s+(?:apartment|villa|studio|townhouse|penthouse|property)',
        r'\bbudget\s*[:;=]\s*[\d.,]+\s*[mk-]',     # "Budget: 90M", "Budget; 600k", "Budget: 5.6 - 6m"
        r'\bbudget\s+[\d.,]+\s*[mk]',
        r'\bup\s+to\s+\d+\s*[mk]\b',                # "up to 850k" (типичный buyer request)
        r'\bany(?:one)?\s+(?:has|have|got)\s+(?:any|a)?\s*\d',
        r'\bdm\s+(?:me\s+)?with\s+(?:offers|options|listings|units)',
        r'\bany\s+offers\s+for\b',
        # "We are giving X% commission" / "buyer commission" — агентский spam
        r'\bwe\s+are\s+giving\s+\d+\s*%\s+commission',
        r'\bfull\s+buyer\s+commission\b',
        r'\b(?:above|all\s+top\s+up)\s+\d+\s*m\b.*\b(?:yours|your)\b',
        # "We have client/buyer for..." — агент запрашивает
        r'\bwe\s+have\s+(?:a\s+)?(?:client|buyer|investor)\b',
    ]
    if any(re.search(p, tl) for p in strong_buyer):
        return True
    # СЛАБЫЕ buyer-маркеры — учитываем только если нет offer-маркеров
    weak_buyer = [
        r'\b(?:looking\s+(?:for|to\s+(?:buy|invest|rent)))\b',
        r'\bclient\s+(?:wants?|needs?|requires?)\b',
        r'\bbuyer.{0,15}(?:require|need|look)',
    ]
    has_offer = bool(re.search(
        r'\b(?:selling\s+price|asking\s+price|sale\s+price|'
        r'op\s*[:=]|sp\s*[:=]|distress\s+deal|hot\s+deal|fully\s+paid|paid\s+\d+%|'
        r'handover\s+(?:q\d|in|by|on|\d{4}|ongoing|ready)|developer\s*:)\b',
        tl
    ))
    if not has_offer:
        if any(re.search(p, tl) for p in weak_buyer):
            return True
    return False


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
    r'\bsales?\s+price\b', r'\bselling\s+price\b',
    r'\bsp\s*[:\-]', r'\bop\s*[:\-]',  # SP/OP prefix usually = selling/original sale price
    r'\bpayment\s+plan\b', r'\bhandover\s+(?:q\d|in|date|by|on|\d{4})',
    r'\b(?:urgent|hot|distress)\s+sale\b', r'\bbelow\s+market\b', r'\bbelow\s+op\b',
    r'\boff[\s\-]?plan\b', r'\bdeveloper\s*:', r'\bproject\s*:',
    r'\bпродажа\b', r'\bпродам\b', r'\bпродаётся\b',
    r'\bbuy\b', r'\bbuying\b',
]


def validate_deal_type_by_price(
    price: int,
    deal_type: str,
    bedrooms: Optional[int] = None,
    area: Optional[str] = None,
    text: Optional[str] = None,
    building: Optional[str] = None,
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

    # ── 2.5: DLD benchmark classification (priority over absolute limits) ────
    # If we have a confident verdict from building-level benchmarks, trust it.
    if building:
        bench_verdict, bench_conf, _ = classify_deal_by_price(building, price)
        if bench_verdict and bench_conf >= 0.75:
            return bench_verdict

    # ── 3 & 4: absolute price limits ─────────────────────────────────────
    # Sale + low price → rent, UNLESS text has explicit sale wording
    # (cheap studios in Sharjah/Ajman can legitimately sell for 400-500k AED)
    if deal_type == "sale" and price < 500_000:
        has_explicit_sale = False
        if text:
            tl = text.lower()
            has_explicit_sale = bool(re.search(
                r'\b(?:for\s+sale|sales?\s*price|selling\s*price|sale\s*price|asking\s*price|'
                r'sp\s*[:\-]|original\s*price|op\s*[:\-]|payment\s*plan|'
                r'handover(?:\b|\s+(?:q\d|in|by|on|\d{4}|ongoing|ready))|'
                r'distress\s+deal|below\s+(?:market|op)|off[\s\-]?plan|developer\s*:|'
                r'urgent\s+sale|hot\s+sale|fully\s+paid|paid\s+\d+%|'
                r'price\s*[:\-]?\s*[\d.,]+\s*(?:m\b|mln|million)|'
                r'aed\s*[\d.,]+\s*(?:m\b|mln|million)|'
                r'\d+\s*(?:m\b|mln|million)(?!\w))\b',
                tl
            ))
        if not has_explicit_sale:
            return "rent"
        # If explicit sale wording but price < 200k, still suspicious — flip
        if price < 200_000:
            return "rent"
    if deal_type == "rent" and price > 50_000_000:
        return "sale"
    # Residential rent > 1M/year is almost always misclassified sale.
    # Genuine luxury rent in UAE caps at ~3M/yr (top Palm/DT penthouses).
    # If rent > 1M and text contains explicit sale signals → flip to sale.
    if deal_type == "rent" and price > 1_000_000 and text:
        tl = text.lower()
        if re.search(r'\b(?:for\s+sale|sales?\s*price|selling\s*price|sale\s*price|asking\s*price|sp\s*[:\-]|payment\s+plan|handover|distress\s+deal|below\s+(?:market|op)|off[\s\-]?plan|developer\s*:|aed\s*[\d.,]+\s*(?:m\b|mln|million))\b', tl):
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
    # Also match "N cheques" / "N chq" formula common in UAE rentals
    if re.search(r'\b\d+\s*(?:cheques?|chq|chk)\b', tl):
        rent_signals = rent_signals + ["_cheque_pattern_"]
        tl += " _cheque_pattern_"
    # Comprehensive sale signals
    sale_signals = [
        "for sale", "selling price", "sale price", "sp:", "op:", "asking price",
        "listed at", "mortgage", "cash price", "payment plan", "handover",
        "off plan", "offplan", "off-plan", "resale", "transfer fee", "dld fee",
        "للبيع", "продажа", "продаётся", "продам", "на продажу",
        "posthandover", "post handover",
        # NB: "ready to move in" удалено — это STATUS квартиры, не deal_type.
        # Аренда тоже бывает ready to move in. Раньше «For Rent ... Ready to Move In»
        # давало tie sale/rent → default sale.
    ]

    rent_score = sum(1 for s in rent_signals if s in tl)
    sale_score = sum(1 for s in sale_signals if s in tl)
    # Extra rent pattern: "Rent N" / "Rent: N" с явным числом
    if re.search(r'\brent\s*:?\s*\d', tl):
        rent_score += 1

    # ── Price magnitude override ────────────────────────────────────────────
    # Любая сумма > 500к AED при отсутствии явного rent-сигнала = SALE.
    # Cheapest UAE annual rent is ~25k. 500k+ is sale territory only.
    if price and price >= 500_000 and rent_score == 0:
        return "sale"
    # Price >= 1.2M — это ВСЕГДА sale, даже если есть rent-слова в тексте
    # (типичный кейс: мульти-листинг продаж с упоминанием текущих rentals).
    if price and price >= 1_200_000:
        # Но НЕ если первый параграф явно про аренду
        first_para = tl[:300]
        if not re.search(r'\bfor\s+rent\b|\bto\s+rent\b|\brental\b|\bв\s+аренду\b', first_para):
            return "sale"

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
    # Expand abbreviations in text before matching.
    # CASE-SENSITIVE: эти аббревиатуры в реальной речи всегда UPPERCASE
    # (JVC, JBR, DCH, ...). Раньше с flags=re.I мы ловили "dh" внутри
    # "downhill" и т.п. — теперь требуем точное uppercase.
    # Также требуем space/punct по краям (не markdown ** или _).
    expanded_text = text
    for abbr, full in AREA_ABBR.items():
        # `\b` matches at `**` boundary which leaks `AR` inside `Ar**ea`.
        # Use stricter: surrounded by whitespace, comma, slash, paren, or start/end.
        expanded_text = re.sub(
            r'(?<![A-Za-z0-9])' + re.escape(abbr) + r'(?![A-Za-z0-9])',
            full, expanded_text)

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
    # 80 chars before (увеличен с 35 — чтобы поймать "walking distance to X and Y")
    before = text_lower[max(0, m.start() - 80): m.start()]
    after = text_lower[m.end(): m.end() + 20]
    # Pre-context markers (word/phrase that comes BEFORE the name, может быть с
    # несколькими словами между маркером и landmark-именем).
    pre_markers = (
        r'\b(?:view|views|viewing|facing|near|from|overlooking|opposite|'
        r'towards?|stunning|close\s+to|next\s+to|distance\s+(?:from|to)|'
        r'walking\s+distance|minutes?\s+from|minutes?\s+to|drive\s+to|'
        r'beside|adjacent\s+to|right\s+next\s+to)\b'
        r'(?:\s+[a-z][a-z\s,]{0,40})?\s*(?:and\s+)?$'
    )
    if re.search(pre_markers, before):
        return True
    # Post-context markers (the name is followed by "view")
    if re.search(r'^\s*(?:view|views|area|district|community|landmark)\b', after):
        return True
    # View-list pattern: line starts with a bullet (🔹 ▪ • etc.) AND the
    # landmark is preceded by comma-separated other landmarks ("Palm, Beach, Burj Khalifa")
    # — typical "views: A, B, C" list format.
    # Check if there's a bullet in the last 60 chars before, AND comma before match.
    if re.search(r'[🔹▪•⚪▫]', before) and ',' in before[-30:]:
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
    # Directional/distance phrases starting with "To/Near/Opposite/Close to/...":
    # эти фразы — это relative location ("opp. to Miracle Gardens"), не building.
    if re.match(
        r'^(?:to |from |near |opposite |close |behind |opp[\.\s]|across |'
        r'next to |walking |minutes? |drive |around |before |after |adjacent |'
        r'beside |overlooking |facing |with\s+view |view\s+of )',
        sl):
        return True
    # Descriptor-style: "Spacious Villa", "Modern Apartment", "Luxury Penthouse",
    # "Studio for Sale", "Office for Rent", "Fully Furnished Studio" etc —
    # это типы недвижимости с одним-двумя прилагательными
    ADJ = (r'(?:spacious|luxurious|stunning|modern|brand\s+new|new|amazing|'
            r'beautiful|large|huge|massive|premium|exclusive|elegant|cozy|bright|'
            r'sunny|big|rare|unique|distress|hot|prime|cheap|affordable|'
            r'best|top|excellent|gorgeous|charming|fully|partially|semi[\s\-]?'
            r'furnished|upgraded|renovated|vacant|rented|ready|fitted|furnished|'
            r'unfurnished|partial|fully\s+furnished|semi\s+furnished|fully\s+fitted)')
    TYPE = (r'(?:villa|apartment|townhouse|penthouse|studio|duplex|plot|'
             r'office|unit|home|mansion|residence|deal|opportunity|investment|room)')
    # 1 or 2 adjectives + type
    if re.match(rf'^{ADJ}(?:\s+{ADJ})?\s+{TYPE}s?$', sl):
        return True
    if re.match(
        r'^(?:office|retail|plot|villa|apartment|townhouse|penthouse|studio|'
        r'duplex|unit|property|home|land|building|tower|hotel)\s+'
        r'(?:for\s+)?(?:sale|rent|lease|exchange)$', sl):
        return True
    # Pure property type word alone
    if sl in {'villa','apartment','townhouse','penthouse','studio','duplex',
               'plot','office','retail','property','unit','land','home',
               'mansion','residence','tower','building','complex','hotel'}:
        return True
    # «Bedroom Apartment» / «Bedroom Villa» / «Bedroom Townhouse» — typical bug
    # парсера: текст "3 Bedroom Apartment" → building="Bedroom Apartment"
    if re.match(r'^bedroom\s+(?:villa|apartment|townhouse|penthouse|studio|'
                 r'duplex|plot|office|home|mansion|residence|unit)s?$', sl):
        return True
    # Leading numbers + descriptor («2 bedroom villa», «3BR apartment»)
    if re.match(r'^\d+\s*(?:br|bdr|bedroom|bed)\s+(?:villa|apartment|townhouse|'
                 r'penthouse|studio|duplex)s?$', sl):
        return True
    # Marketing phrases / call-to-action / deal-flags
    if re.match(
        r'^(?:for\s+sale|for\s+rent|for\s+salle|hot\s+deal|hot\s+offer|distress\s+deal|'
        r'best\s+deal|best\s+price|new\s+launch|new\s+price|special\s+offer|'
        r'urgent\s+sale|exclusive\s+deal|investment\s+opportunity|'
        r'cash\s+(?:buyer|deal)|payment\s+plan|free\s+hold|freehold|'
        r'covered|for\s+serious|huge\s+(?:terrace|balcony|rooftop)|'
        r'big\s+(?:terrace|balcony|rooftop)|spacious\s+layout|'
        r'prime\s+(?:business|tower)|new\s+tower)\s*\b',
        sl):
        return True
    # Standalone area-acronyms (used as building) — these are area codes, not buildings
    if sl in {'jvc','jvt','jlt','jbr','dxb','dch','dhe','dha','dlrc','dso'}:
        return True
    # Single-feature labels ("Covered", "Huge Terrace", "Spacious") — pure descriptors
    if sl in {'covered','spacious','luxury','modern','elegant','exclusive',
               'distress','premium','prime','huge terrace','huge balcony',
               'big terrace','big balcony','spacious layout','brand new',
               'new tower','prime tower','prime business bay',
               'for serious buyers','huge rooftop terrace'}:
        return True
    # ── View / Feature description leaking into building ──────────────────
    # Если в имени есть слова view/views/stunning/degree/partly/with full и
    # это НЕ имя реального здания из DB — это феча-описание.
    feature_indicators = (
        'view', 'views', 'stunning', 'partly', 'closets', 'amenities',
        'degree', 'with full', 'with light', 'with view', 'with park',
        'kitchen', 'bathroom', 'bedroom', 'terrace', 'balcony', 'roof',
        'fittings', 'stairs', 'cement', 'parking', 'swimming', 'gym',
        'with brand new', 'walk-in', 'walk in', 'master bedroom',
        'parquet', 'flooring', 'lighting', 'fireplace', 'wardrobe',
        'home elevator', 'private pool', 'high ceiling', 'low ceiling',
    )
    sl_words = sl.split()
    if any(ind in sl for ind in feature_indicators):
        # Allowed exceptions — реальные ЗДАНИЯ которые содержат «view» в имени
        # (Marina View, Lake View, Burj Views, Park View Tower etc.)
        # Их можно идентифицировать по тому что они короткие (2-3 слова) и
        # ЗАКАНЧИВАЮТСЯ на view/views — но не «X With Y Views».
        if len(sl_words) > 3:
            return True
        # «Cement Fly Stairs» / «Partly Park» — multi-word feature
        if any(sl.startswith(p) for p in
                ('stunning ', 'partly ', 'cement ', 'with full ',
                 'with light', 'closets', 'amenities',
                 'walk-in', 'walk in')):
            return True
        # Endswith «degree views» / «with X views»
        if 'degree' in sl or 'with full' in sl:
            return True
    # Building name shouldn't contain phone digits or currency
    if re.search(r'\+?\d{6,}|aed\s*\d|\$\s*\d|€\s*\d', sl):
        return True
    # Building shouldn't contain too many words (real building 1-5 words max)
    if len(sl.split()) > 7:
        return True
    # Building shouldn't be question / call
    if '?' in sl or sl.startswith(('hello','hi ','dear','dm ','call ','contact','please')):
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
    # Aggressive emoji + variation-selector + ZWJ stripping
    # Covers: emoticons, transport/places, dingbats, supplemental symbols,
    # variation selectors (︀-️), ZWJ (‍), skin tones, etc.
    s = re.sub(
        r'[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF'
        r'‍︀-️\U0001F3FB-\U0001F3FF♀-♂⌀-⏿'
        r'←-⇿✀-➿]', '', s)
    # Specific frequently-seen decorative chars
    s = re.sub(r'[📍🏡🏢💰🔥✨⭐️🌊🛏🛁🚘🪑🔑🪴📐☎️📞📩‼️🟥🟨🟩‍♂️‍♀️👇🏼👇🏻👇🏽👇🏾👇🏿]', '', s)
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
    # Reject floor/view/price/size descriptors masquerading as building names
    if re.search(r'\b(?:high\s+floor|low\s+floor|mid(?:dle)?\s+floor|ground\s+floor|top\s+floor|penthouse\s+floor|sea\s+view|park\s+view|pool\s+view|burj\s+(?:khalifa\s+)?view|community\s+view|canal\s+view|garden\s+view|city\s+view|skyline\s+view|partial\s+view|full\s+view)\b', s, re.I):
        return None
    # Reject marketing phrases / buyer-status / channel-spam
    if re.search(r'\b(?:subscribe|channel|members?|message|please|hassle|perfect\s+for|move\s+in|investors?|end\s*users?|star\s+rating|net\s+to\s+the?\s+owner|long\s+lease|cluster|corner\s+unit|single\s+row|fully\s+upgraded)\b', s, re.I):
        return None
    # Reject if candidate IS a price-like phrase (id=21975 example "Selling Price: 120M")
    if re.search(r'\b(?:selling\s*price|sale\s*price|asking\s*price|original\s*price|sales?\s*price|\d+\s*(?:m\b|mln|million|k\b|aed))\b', s, re.I):
        return None
    # Reject 2-bedroom / 3-bathroom style alone (attribute, not building)
    if re.fullmatch(r'\d+\s*(?:bedroom|bathroom|bath|bed|br|bhk|sqft|sq\.?\s*m|m2)s?\s*', s, re.I):
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

    # Pattern 10: <KnownArea> [optional emoji/decor]
    #             <BuildingCandidate>
    #             <line containing bedroom / sqft / floor / price / view>
    # Examples:
    #   "Dubai Marina 🛥️\nSulafa Tower\n1 bedroom, 920 sqft"
    #   "Dubai Hills\n*Greenside Building*\n- 1 bedroom"
    #   "JBR Sadaf 7\n2 bedroom, 1211 sqft"  (area + building on same line)
    #   "Arjan ✅ Hot deal\nKYOTO by ORO24\n1 bedroom"
    property_signal = r'\b(?:bedroom|studio|sqft|sq\.?\s*ft|sq\.?\s*m|sqm|m2|m²|floor|price|aed|view|handover|paid|cheque|/year|/month|annual\s*rent|selling\s*price|asking|сдам|сдается|для\s+продажи)\b'
    # Build a single alternation of known areas (sorted longest-first to win greedy match)
    area_names_long_first = sorted(
        [n for area_name, info in AREAS.items()
         for n in [area_name] + info.get("aliases", [])],
        key=len, reverse=True
    )
    area_pat = '|'.join(re.escape(n) for n in area_names_long_first)
    pat10 = (
        r'(?:^|\n)\s*(?:' + area_pat + r')\b[^\n]{0,30}\n'   # area line (with optional decor/emoji)
        r'\s*([^\n]{3,70})\n'                                 # building candidate (capture)
        r'[^\n]*' + property_signal                           # next-line property signal
    )
    for m in re.finditer(pat10, head, re.IGNORECASE):
        cand = _clean_building_candidate(m.group(1))
        if cand:
            return cand

    # ── Pattern 11: "X Tower/Residences/Bay/Heights/etc." case-insensitive ─
    # Ловит "Zada tower", "Canal Front Residences", "Bayview Tower 1"
    # Trailing digit MUST be on same line (no newline in pattern).
    for m in re.finditer(
        r'\b([A-Za-z][A-Za-z0-9&\']+(?:[ \t]+[A-Za-z0-9&\']+){0,4})[ \t]+'
        r'(?:tower|towers|residence|residences|bay|villa|villas|heights|court|'
        r'place|square|apartment|apartments|mansion|mansions|hills|gardens|'
        r'crescent|hotel|views|estates?|island|park|terraces?|plaza|grove)\b'
        r'(?:[ \t]+\d{1,2}(?!\d))?',
        head, re.IGNORECASE):
        full = m.group(0).strip()
        # Title-case
        words = full.split()
        cand_str = ' '.join(w.capitalize() if (w.isupper() or w.islower())
                             and len(w) > 1 else w for w in words)
        cand = _clean_building_candidate(cand_str)
        if cand:
            return cand

    # ── Pattern 12: First Title-Case line with 2-6 capitalised words ───────
    # "ELIE SAAB A VIE at THE FIELDS D11", "Burj Binghatti Jacob & Co"
    for line in head.split('\n')[:5]:
        line_s = line.strip().strip('*_•-—|🌟🏡🏛🏢🏠🏘🚨🏗📍✨🔥💎💰 ')
        line_s = re.sub(r'\*+', '', line_s).strip()
        if not line_s or len(line_s) < 4 or len(line_s) > 70:
            continue
        words = line_s.split()
        if not (2 <= len(words) <= 7):
            continue
        cap_words = sum(1 for w in words if w and w[0].isupper())
        if cap_words >= max(2, len(words) - 1):
            ll = line_s.lower()
            # Reject category headers
            if any(b in ll for b in (
                'for sale', 'for rent', 'sale price', 'asking price',
                'distress', 'property type', 'off plan', 'off-plan',
                'available', 'sale:', 'rent:', 'new price', 'urgent sale',
                'commission', 'status:', 'price:', 'size:', 'looking for',
                'who is', 'admin', 'hello', 'hi ', 'subscribe',
            )):
                continue
            cand = _clean_building_candidate(line_s)
            if cand:
                return cand

    # ── Pattern 13: "<Building> at <KnownArea>" / "<Building> in <KnownArea>" ─
    # "ELIE SAAB A VIE at THE FIELDS"
    for m in re.finditer(
        r'\b([A-Z][A-Za-z0-9&\'\s]{3,40})\s+(?:at|in)\s+([A-Z][A-Za-z0-9&\'\s]{3,40})',
        head):
        cand = _clean_building_candidate(m.group(1))
        if cand and not _is_building_stopword(cand):
            return cand

    # ── Pattern 14: "<Building> by <Developer>"
    # "Galaxy by Binghatti", "ESPLORA by BNW", "Skyz By Danube"
    for m in re.finditer(
        r'\b([A-Z][A-Za-z0-9&\'\-\s]{2,40})\s+by\s+([A-Z][A-Za-z]+)\b',
        head, re.IGNORECASE):
        cand_raw = m.group(1).strip()
        # Title-case if all caps or all lower
        if cand_raw.isupper() or cand_raw.islower():
            cand_raw = cand_raw.title()
        cand = _clean_building_candidate(cand_raw)
        if cand and not _is_building_stopword(cand):
            return cand

    # ── Pattern 15: NUMBER + Title-Case + Suffix (e.g. "17 Icon Bay", "320 Riverside Crescent")
    for m in re.finditer(
        r'(?:^|\n)\s*(\d{1,4}\s+[A-Z][A-Za-z0-9&\'\s]{3,40}\s+'
        r'(?:Tower|Towers|Bay|Crescent|Residences?|Heights|Place|Court|Plaza))',
        head):
        cand = _clean_building_candidate(m.group(1).strip())
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
    """Bedrooms from FIRST listing block. Numeric patterns take priority over
    'studio'. All patterns are same-line bound — `\\s*` would otherwise eat
    `\\n` and capture the next line's sqft/year (e.g. "Three bedroom\\n2300 sqft"
    => bedrooms=2300). Sanity cap: 0..15.
    """
    text = _first_listing_block(text)
    tl = text.lower()
    def _ok(v):
        return v if 0 <= v <= 15 else None
    # ── European decimal: "2,5 bedroom" / "1.5 bedroom" → integer floor.
    # Это означает «2 BR + study» (полкомнаты = кабинет). Парсер раньше брал
    # 5 как BR, что ломало sqft/br ratio.
    m = re.search(r'(\d)[,.]5\s*(?:bedroom|bed\b|br\b|bdr\b|bhk)', tl)
    if m:
        v = _ok(int(m.group(1)))
        if v is not None: return v
    # Numeric patterns FIRST (higher confidence than 'studio')
    # Separator class `[ \t\-]*` allows "1-bed", "1bed", "1 bed", "1bd"
    m = re.search(r'(\d+)[ \t\-]*bhk\b', tl)
    if m:
        v = _ok(int(m.group(1)))
        if v is not None: return v
    # bd | bdr | br | bed | bedroom | beds | bedrooms — all with optional `-`/space
    m = re.search(r'(\d+)[ \t\-]*(?:bedrooms?|bdrs?|brs?|beds?|bds?)\b', tl)
    if m:
        v = _ok(int(m.group(1)))
        if v is not None: return v
    m = re.search(r'(\d+)[ \t\-]*(?:bdr|b/r)\b', tl)
    if m:
        v = _ok(int(m.group(1)))
        if v is not None: return v
    # Label-before-number: 'Bedrooms: 1' — same-line only
    m = re.search(r'\bbedrooms?[ \t]*[:\-]?[ \t]*(\d+)', tl)
    if m:
        v = _ok(int(m.group(1)))
        if v is not None: return v
    m = re.search(r'(\d+)[ \t]*bedrooms?', tl)
    if m:
        v = _ok(int(m.group(1)))
        if v is not None: return v
    # 'Rooms: 2' — require explicit separator (avoid catching "rooms\n2026")
    m = re.search(r'\brooms?[ \t]*[:\-][ \t]*(\d+)', tl)
    if m:
        v = _ok(int(m.group(1)))
        if v is not None: return v
    # Bedrooms as English words: "one bedroom" / "two bedroom" / "three bedroom"
    WORD_BR = {
        "one": 1, "single": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }
    for w, n in WORD_BR.items():
        if re.search(rf'\b{w}\s+(?:bedroom|bed|br|bhk|b/r)s?\b', tl):
            return n
    # Studio only if no numeric pattern found
    if re.search(r'\bstudio\b|\bstd\b', tl):
        return 0
    return None
    # "Unit: X Bedroom" format
def extract_size(text: str) -> dict:
    """Size from FIRST listing block (multi-listing safety).
    Different listings have different sizes — we should only extract the first one's.
    """
    text = _first_listing_block(text)
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

    # NUM pattern that supports thousands-separators: comma, space, dot
    # "1,250" / "1 250" / "18 000" / "12.500" / "1250" — все валидны
    NUM = r'(\d{1,3}(?:[\s,]\d{3})+|\d{1,6})(?:\.\d+)?'

    # SQFT with label (e.g. "Size: 1148 sqft", "Total Area: 18 000 sqft")
    if result["size_sqft"] is None:
        sqft_label_patterns = [
            rf'(?:Plot\s+Area|Total\s+Area|Size(?:\s+of\s+(?:Villa|Apartment|Townhouse))?|Area)\s*[:=\-]?\s*{NUM}\s*(?:sq\.?\s*ft|sqft|ft[²*2])',
            rf'\bSqft\s*[:=]\s*{NUM}',
            rf'\bSq\.?\s*Ft\s*[:=]\s*{NUM}',
        ]
        for pat in sqft_label_patterns:
            m = re.search(pat, text, re.I)
            if m:
                v = _parse_num(m.group(1).replace(",", "").replace(" ", ""))
                if _in_range_sqft(v):
                    result["size_sqft"] = v
                    break

    # Bare SQFT (number directly followed by unit) — теперь с поддержкой пробелов
    if result["size_sqft"] is None:
        bare_sqft_patterns = [
            rf'{NUM}\s*sq\.?\s*ft\b',
            rf'{NUM}\s*sqft\b',
            rf'{NUM}\s*ft[²*2]',
            rf'{NUM}\s*SF\b',
            rf'{NUM}\s*sqf\b',
            rf'{NUM}\s*sq\s+f\b',
        ]
        for pat in bare_sqft_patterns:
            m = re.search(pat, text, re.I)
            if m:
                v = _parse_num(m.group(1).replace(",", "").replace(" ", ""))
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
    """Parse price strings like '1.5M', '750k', '3.2ML', '1,200,000', '1,59 M' (European decimal).
    Also handles 'AED 2,760,000' / '2.4M AED' / '500 000 AED' — currency tokens stripped.
    """
    if not s:
        return None
    s = str(s).strip().upper()
    # Strip currency prefixes/suffixes BEFORE collapsing spaces (so word boundary works)
    s = re.sub(r'\bAED\b|\bUSD\b|\bEUR\b|\bДРХ\b|د\.إ', '', s, flags=re.IGNORECASE).strip()
    # Now collapse internal spaces
    s = s.replace(" ", "")
    # Strip leading punctuation (period/comma/dash) — иначе "Price. 650k" → ".650k" → 0.65k=650.
    s = s.lstrip(".,-*:")
    # European decimal handling: "1,59" → "1.59" (single comma, 1-2 digits after).
    # Multiple commas → thousand separators (US style): "1,200,000" → "1200000".
    if s.count(",") == 1 and "." not in s:
        head, tail = s.split(",")
        # Trim any unit suffix from tail to inspect length of digit part
        tail_digits = re.match(r'(\d+)', tail)
        if tail_digits and len(tail_digits.group(1)) <= 2:
            s = head + "." + tail
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", "")
    # European 1.064.000 → 1064000 (multiple dots act as thousand separator)
    if s.count(".") > 1:
        s = re.sub(r"\.(\d{3})(?=\.|$)", r"\1", s)
    try:
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
            base = s[:-1]
            # B suffix requires a decimal — "1B" is too vague (often comes from "1 BHK").
            # Real prices in billions are written as "2.93B" or "1.5 Billion".
            if "." not in base:
                return None
            v = float(base)
            if not (0.05 <= v <= 100):
                return None
            return int(v * 1_000_000_000)
        v = int(float(s))
        return v if v > 1000 else None
    except:
        return None


def _strip_phones(text: str) -> str:
    """Remove phone numbers so they can't be mistaken for prices."""
    # ANY international format: +XXX followed by 8-20 chars (digits/space/dash/paren).
    text = re.sub(r'(?<!\d)\+\s*\d[\d\s\-\(\)]{6,22}\d(?!\d)', ' ', text)
    # 00XXX international prefix
    text = re.sub(r'(?<!\d)00\d{1,3}[\s\-]?\d[\d\s\-]{7,16}(?!\d)', ' ', text)
    # 05X XXX XXXX (UAE mobile WITH spaces) — было только без пробелов
    # Catches '058 519 6704' which was being concatenated into prices.
    text = re.sub(r'(?<!\d)0(?:50|52|54|55|56|58|2|3|4|6|7|9)[\s\-]?\d{3}[\s\-]?\d{4}(?!\d)', ' ', text)
    # 50/52/54/55/56/58 XXX XXXX (mobile without leading 0/+, common in posts)
    text = re.sub(r'(?<!\d)(?:50|52|54|55|56|58)[\s\-]?\d{3}[\s\-]?\d{4}(?!\d)', ' ', text)
    # WhatsApp/phone with text marker — strip whole tail '58 519 6704' even with weird spacing
    text = re.sub(
        r'(?:whatsapp|whats\s*up|wa\.me|contact|tel|phone|call|номер|телефон)[\s:.\-]*\+?\d[\d\s\-\(\)/]{6,18}\d',
        ' ', text, flags=re.I)
    # Bare 971XXXXXXXXX at word boundary (no + prefix)
    text = re.sub(r'(?<!\d)971\s*\d[\d\s\-]{7,12}(?!\d)', ' ', text)
    # Bare long digit run >= 9 digits without M/K context (likely phone)
    text = re.sub(r'(?<![\d.,])\d{9,15}(?!\s*[mkbMKB])(?!\.\d)', ' ', text)
    return text


def extract_price(text: str) -> dict:
    """Extracts price from FIRST listing block only (multi-listing safety).
    Caps at 10 billion AED as sanity check (Dubai luxury max is ~1B).
    """
    # Use first listing block — multi-listing texts had price bleeding across
    text = _first_listing_block(text)

    result = {"price": None, "currency": "AED",
              "original_price": None, "selling_price": None}

    # Strip reference/permit/RERA/DLD numbers — these 7-9 digit IDs were being
    # picked up as price (e.g. "Reference No.: #34881961" → price=34,881,961).
    # Drop the entire line containing these markers.
    text = re.sub(
        r'(?im)^.*\b(?:reference\s+no|ref\.?\s*no|ref\s*[:#]|permit\s+no|'
        r'permit\s*[:#]|rera\s*[:#]|dld\s+permit|dld\s*[:#]|brn\s*[:#]|'
        r'license\s+no|license\s*[:#]|listed\s+by)\b.*$',
        ' ', text)
    # Also strip standalone `#<7-9 digits>` hash-ref tokens that appear inline
    text = re.sub(r'#\s*\d{6,10}\b', ' ', text)

    # Strip phone numbers (international + local) before any price pattern matching
    text = _strip_phones(text)

    # ── Strip dollar-conversion parenthesis "($387K)" / "(~$1.5M)" — это
    # пересчёт в долларах внутри объявления типа "AED 1,420,000 (~$387K)".
    # Парсер раньше брал $387K как цену.
    text = re.sub(r'\(\s*[~≈]?\s*\$\s*[\d.,]+\s*[kmKM]?\s*\)', ' ', text)

    # ── Strip "rented at/till/until X" / "rented N AED" / "rented: AED N / year"
    # Это арендный доход из текущего тенанта, не sale price.
    # Раньше брал rent (280K/year) как sale price в multi-listing с "Price: AED 6M".
    # Поддерживаем все варианты: "Rented at 75k", "Rented: AED 280,000 / year",
    # "Rented till X (240k/year)", "Rent 155K", "Rented out 58k yearly".
    text = re.sub(
        r'\brent(?:ed)?\s*(?:out\s+)?(?:at|@|till|until|for|[\-])?[\s:\-]*'
        r'(?:aed\s+)?[\d.,]+\s*[km]?\s*(?:aed)?\s*'
        r'(?:/\s*(?:year|yr|month|mo)|per\s+(?:year|month|annum)|yearly|monthly|'
        r'till\s+\d|until\s+\d)?',
        ' ', text, flags=re.I)
    # «Rented MONTH YEAR - N AED» / «Rented till MONTH-YEAR - N AED» — расширенный
    # формат с датой и тире между датой и суммой. Раньше "250 000 AED" из
    # "Rented July 2026 -250 000 AED" утекало как sale price.
    text = re.sub(
        r'\brent(?:ed)?\s+(?:till\s+|until\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|'
        r'январ|феврал|март|апрел|ма[йя]|июн|июл|авг|сент|октябр|ноябр|декабр)\w*\s+\d{4}'
        r'[\s\-\(]*[\d, ]+\s*(?:aed|k|m)?(?:\s*\))?',
        ' ', text, flags=re.I)
    # Also strip parenthesised rental "(240k/year)" / "(58k yearly)"
    text = re.sub(
        r'\(\s*[\d.,]+\s*[km]?\s*(?:aed)?\s*'
        r'/?\s*(?:year|yr|month|mo|yearly|monthly|annum)\s*\)',
        ' ', text, flags=re.I)
    # And '(OP X,XXX)' without comma-K-million suffix — likely truncated OP
    # like 'OP 2,355' that should be 2,355,000 but ambiguous → strip safer.
    text = re.sub(r'\(\s*op\s+[\d,]{1,7}\s*\)', ' ', text, flags=re.I)

    # ── Broken European thousands "N.NNN.NN" (truncated last group)
    # Real example: 'Price: 3.100.00 AED' — должно быть 3,100,000 (миллионы).
    # Парсер раньше брал 3100. Нормализуем: N.NNN.NN → N,NNN,000.
    text = re.sub(
        r'(?<![\d.])(\d)\.(\d{3})\.(\d{2})(?!\d)',
        r'\1,\2,000', text)

    # ── Strikethrough markdown ~~X~~ — это перечёркнутая (устаревшая) цена.
    # Telegram-markdown ~~X~~ / ~X~ / ~~~X~~~ означает «было X, теперь Y».
    # Использует DOTALL и {1,3} тильд чтобы покрыть варианты.
    text = re.sub(r'~{1,3}[^~]{1,200}~{1,3}', ' ', text, flags=re.S)
    # Также «Reduced from X to Y» / «From X To Y» — берём только Y.
    text = re.sub(
        r'\b(?:reduced\s+from|from)\s+(?:aed\s+)?[\d,. ]+\s*(?:k|m|aed)?\s+(?:to|→|->)\s+',
        ' ', text, flags=re.I)
    # «Selling price X ... NEW PRICE Y» — strip OLD selling price когда NEW PRICE
    # явно указана дальше. Парсер раньше брал OLD как актуальную цену.
    if re.search(r'\bnew\s+price\b', text, re.I):
        # Удаляем строку с старой "Selling price/SP X" если есть NEW PRICE дальше
        text = re.sub(
            r'\b(?:selling\s+price|sp|sale\s+price)\s*[:\-]?\s*[\d,. ]+\s*(?:aed|k|m)?',
            ' ', text, flags=re.I)

    # ── Strip payment-plan portions: "50k on handover", "X on transfer",
    # "X to the owner / X to developer". Это куски сплит-платежа, не цена.
    text = re.sub(
        r'[\d.,]+\s*[km]?\s*(?:aed)?\s*'
        r'(?:on\s+(?:handover|transfer|completion|handower)|to\s+(?:the\s+)?(?:owner|developer|builder)|'
        r'left\s+(?:on\s+)?(?:post[\s\-]?handover|to\s+pay)|remaining)',
        ' ', text, flags=re.I)
    # «PHPP\nN» / «PHPP: N» — Post-Handover Payment Plan installment, не цена
    text = re.sub(
        r'\bphpp[\s:\n]+[\d., ]+(?:\s*\([^)]*\))?', ' ', text, flags=re.I)
    text = re.sub(
        r'\bsoa[\s:\-]+\d+\s*%', ' ', text, flags=re.I)  # "SOA-75%"
    # Также: "Pay X now, Y on handover" — strip pay X now
    text = re.sub(r'\bpay\s+[\d.,]+\s*[km]?\s*(?:aed)?\s*now\b', ' ', text, flags=re.I)
    # "X each month" / "X monthly during N years" — installment, not total
    text = re.sub(
        r'[\d.,]+\s*[km]?\s*(?:aed)?\s+(?:each\s+month|monthly|per\s+month)'
        r'(?:\s+during\s+\d+\s+years?)?', ' ', text, flags=re.I)
    # Strip bedroom abbreviations — "1 BHK", "2 BR", "3 Bed" — so price regex
    # can't accidentally match "1 B" (BHK) as a billion-AED value.
    text = re.sub(r'\b(\d+)\s*(?:BHK|BR|BHRs?|BDR|B/R|BD|BED(?:ROOM)?S?|BHRM)\b',
                  r'\1 ', text, flags=re.I)
    # Strip discount-context phrases so '(130k AED discount)' or 'below op -200k'
    # don't get caught as the main price. The REAL price is elsewhere in text.
    text = re.sub(
        r'\(\s*[\d.,]+\s*(?:k|m)?\s*aed\s+discount\s*\)', ' ',
        text, flags=re.I)
    text = re.sub(
        r'\bdiscount[\s:=]+[\d.,]+\s*(?:k|m)\b', ' ', text, flags=re.I)
    text = re.sub(
        r'\bbelow\s+op(?:\s+price)?[\s:=]*[\-+]?\s*[\d.,]+\s*(?:k|m)\b', ' ',
        text, flags=re.I)
    # «AED X BELOW ORIGINAL PRICE» / «X below market» — discount, не цена
    text = re.sub(
        r'\b(?:aed\s+)?[\d.,]+\s*[km]?\s+below\s+(?:original\s+price|market|op)\b',
        ' ', text, flags=re.I)
    # «Paid X» / «Down payment X» / «NET TO OWNER X» / «to owner X» — down payment, не цена
    text = re.sub(
        r'\b(?:paid|down\s+payment|net\s+to\s+(?:owner|seller))[\s:=]+(?:aed\s+)?[\d.,]+\s*[km]?',
        ' ', text, flags=re.I)
    text = re.sub(
        r'▪️\s*paid\s*[\d,]+', ' ', text, flags=re.I)
    text = re.sub(
        r'\bsave(?:s)?[\s:=]+(?:aed\s+)?[\d.,]+\s*(?:k|m)\b', ' ',
        text, flags=re.I)
    # Service charges, DLD fees etc — should be stripped too.
    # "Service charge 15,527 AED" / "DLD 4% AED 120,000"
    text = re.sub(
        r'\bservice\s+charge[\s:=]+(?:aed\s+)?[\d,]+(?:\.\d+)?', ' ',
        text, flags=re.I)
    # DLD fee strip: ТОЛЬКО когда есть явный маркер 'fee' / '4%'.
    # Раньше регекс был жадным и съедал «DLD = 3.05» внутри «OP+DLD = 3.05 M AED»
    # (где DLD это часть условия "OP+DLD" — original price плюс DLD-комиссия).
    text = re.sub(
        r'\bdld\s+(?:fee|4%)\s*[\s:=]*(?:aed\s+)?[\d,]+(?:\.\d+)?\s*(?:aed|k|m)?',
        ' ', text, flags=re.I)
    # ── Rental income / yield mentions — это НЕ цена объекта, а доход.
    # «Rental income: AED 1.34M / Annual rental: 330,000 / Rented at 110k»
    text = re.sub(
        r'\b(?:rental?\s+income|annual\s+rental?|rented\s+(?:at|for)|rent\s+income|net\s+roi)[\s:=]+(?:aed\s+)?[\d,.\s]+\s*(?:k|m|mln|million)?',
        ' ', text, flags=re.I)
    text = re.sub(
        r'\bexpected\s+(?:roi|yield|return)[\s:=]+[\d,.~\-– ]+\s*%?',
        ' ', text, flags=re.I)
    # Strip per-sqft/per-sqm references — these are unit prices, NOT total price.
    # Examples: "1000aed per sqft", "1,300 AED/sqft", "$450 psf",
    #           "market price 1300aed per sqft", "1.5K per sqm"
    text = re.sub(
        r'[\d,\.]+\s*(?:aed|usd|eur)?\s*[/\\]?\s*(?:per\s+)?'
        r'(?:sq\.?\s*ft|sqft|psf|sq\.?\s*m|sqm|psm|кв\.?\s*м|кв\.?\s*фут)',
        ' ', text, flags=re.I)
    # Also strip area-as-meters: "74 m²" / "74 m^2" / "74 m2" — это размер не цена.
    # КРИТИЧНО: homoglyph translate превращает Cyrillic «м^2» в «m^2», и без этого
    # strip парсер брал «74 m» (через M-suffix pattern) как 74 миллиона.
    text = re.sub(
        r'(?<![\d.])\d+(?:[.,]\d+)?\s*m\s*(?:\^?2|²)',
        ' ', text, flags=re.I)
    # Также Cyrillic-equivalent m² уже convert в latin, но на всякий случай:
    text = re.sub(r'(?<![\d.])\d+(?:[.,]\d+)?\s*м[²2^]', ' ', text, flags=re.I)
    # Также фразы "X aed/m²" с любыми вариациями
    text = re.sub(
        r'\b[\d,\.]+\s*(?:aed|\$|€)\s*/?\s*m[²2]\b',
        ' ', text, flags=re.I)
    # Strip "market price/avg price/list price X" reference numbers — these are
    # not the actual sale price but a comparison value.
    text = re.sub(
        r'\bmarket\s+price[\s:=]+[\d,\.]+\s*(?:k|m|aed)?',
        ' ', text, flags=re.I)
    text = re.sub(
        r'\b(?:avg|average)\s+price[\s:=]+[\d,\.]+\s*(?:k|m|aed)?',
        ' ', text, flags=re.I)
    # ── Normalise Cyrillic suffixes (often used in RU listings) ─────────
    # 'AED 3.5М' → 'AED 3.5M', '500К' → '500K'
    text = text.replace('М', 'M').replace('м', 'm').replace('К', 'K').replace('к', 'k')
    # Cyrillic homoglyphs that look like Latin in English words —
    # «Priсe» (с=cyr) → «Price», «Lоcation» (о=cyr) → «Location»
    # Без этого `\bprice\b` regex не матчит «Priсe» и парсер пропускает цену.
    HOMOGLYPHS = str.maketrans({
        'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'у': 'y',
        'х': 'x', 'А': 'A', 'В': 'B', 'Е': 'E', 'К': 'K', 'М': 'M',
        'Н': 'H', 'О': 'O', 'Р': 'P', 'С': 'C', 'Т': 'T', 'У': 'Y',
        'Х': 'X',
    })
    text = text.translate(HOMOGLYPHS)
    # ── Mixed European format: "2.300 000" / "1.500 000" (dot + space thousands)
    # ВАЖНО: ДО space-norm — иначе пробел между группами цифр съест и весь
    # формат развалится.
    text = re.sub(
        r'\b(\d{1,3})\.(\d{3})\s+(\d{3})\b',
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}",
        text)
    # ── Broken mixed: "7.800,000" / "1.500,000" — dot + comma 3-digit groups.
    # Это часто опечатка вместо "7.800.000" — все разделители thousands.
    text = re.sub(
        r'\b(\d{1,3})\.(\d{3}),(\d{3})\b',
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}",
        text)
    # ── Broken mixed: "7,800.000" — comma + dot 3-digit groups.
    text = re.sub(
        r'\b(\d{1,3}),(\d{3})\.(\d{3})\b',
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}",
        text)
    # ── Normalise space-separated numbers ("560 000" → "560000") ────────
    # Также ловим двойные пробелы: "1 500  000" → "1500000"
    text = re.sub(
        r'(\d{1,3})(?:\s+(\d{3}))+(?!\d)',
        lambda m: re.sub(r'\s+', '', m.group(0)),
        text)
    # ── European thousand-separator with dots: "AED 3.000.000" / "230.000 AED"
    # If a number has 2+ dots with 3-digit groups, collapse them.
    # NB: используем lookahead (?!\.\d) вместо `\b` потому что `\b` после "000"
    # перед "AED" НЕ срабатывает (000 и AED оба word-chars).
    # Без слитного варианта «13.290.000AED» не нормализуется.
    text = re.sub(
        r'(?<![\d.])(\d{1,3}(?:\.\d{3}){2,})(?!\.\d)',
        lambda m: m.group(0).replace('.', ''),
        text)
    # Single-dot European thousand-sep ONLY when adjacent to AED/Dhs and YYY is 3 digits
    # AND not followed by M/K/mln suffix (those mean millions, not thousands).
    text = re.sub(
        r'(?i)(\bAED|\bDhs|درهم)\s*(\d{1,3})\.(\d{3})\b(?!\s*[mk])',
        lambda m: f"{m.group(1)} {m.group(2)}{m.group(3)}",
        text)
    text = re.sub(
        r'(?i)\b(\d{1,3})\.(\d{3})\s*(AED|Dhs|درهم)\b',
        lambda m: f"{m.group(1)}{m.group(2)} {m.group(3)}",
        text)
    t = text  # preserve original case for Cash regex

    # -- Original / Purchase price — ONLY these go to original_price
    m = re.search(r'(?:\bop\b|original\s*price|purchase\s*price)[\s:]*([\d,\. ]+\s*[mkb]?l?)', t, re.I)
    if m:
        result["original_price"] = _parse_amount(m.group(1))

    # -- Selling / Sales / Sale / Final / Asking / Price / Net — these are CURRENT
    # price. Priority order matters: специфичные label сначала, plain price в конце.
    # NB: [\s:\-]* допускает дефис как разделитель ("Selling price -3M")
    for selling_pat in [
        # NEW PRICE сначала — приоритет над OLD selling price если есть оба
        r'\bnew\s+price\s*[:\-]*\s*(?:aed\s+)?([\d,\. ]+\s*[mkb]?l?)',
        r'(?:\bsp\b|sales?\s*price|selling\s*price|sale\s*price|final\s*price|asking\s*price|net\s+price|net\s+to\s+seller)[\s:\-]*(?:aed\s+)?([\d,\. ]+\s*[mkb]?l?)',
        r'\bselling[\s:\-]+(?:aed\s+)?(\d[\d,\. ]*\s*[mkb]?l?)',
        # plain "price" но не «Op is price», «from price»
        r'(?<!is\s)(?<!from\s)(?<!op\s)\bprice[\s:\-]+(?:aed\s+)?(\d[\d,\. ]*\s*[mkb]?l?)',
    ]:
        m = re.search(selling_pat, t, re.I)
        if m:
            v = _parse_amount(m.group(1))
            if v:
                result["selling_price"] = v
                result["price"] = v
                break

    # If we still have only original_price, use it as fallback
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

    # NB: extract logic выше — мы уже пробежали все priority patterns
    # (selling/sale/asking/final/net/plain price). Если ничего не нашли —
    # дальше идут fallbacks (cash, mln, M-suffix, generic K/M etc).
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
    # BUT NOT if the match is an installment from a payment plan (e.g. "14,950/monthly"
    # in "Payment Plan: AED 14,950/monthly until 2028"). For those we want the FULL
    # sale price (already captured above via Selling/Asking/Price patterns), not the
    # monthly installment.
    has_payment_plan = bool(re.search(
        r'\b(?:payment\s+plan|installments?|instalments?|to\s+developer|to\s+seller|until\s+(?:\d{4}|noc|title|handover)|post[\s\-]?handover)\b',
        t, re.I
    ))
    if not has_payment_plan:
        m = re.search(r'([\d][\d,\. ]*)/(?:m\b|mo\b|month|yr|year)', t, re.I)
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
        import os as _os
        if _os.environ.get("DEBUG_EXTRACT_PRICE"):
            print(f"[DEBUG] text before generic: {t[:600]!r}", flush=True)
        # (pattern, min_amount) — explicit AED-suffixed prices allow rents from 20k
        patterns = [
            (r'([\d\.]+\s*[Mm][Ll])\b', 100_000),     # 3.2ML
            (r'([\d\.]+)\s*[Mm]\b', 100_000),          # 1.5M
            (r'([\d\.]+)\s*[Kk]\b', 20_000),           # 750k / 78k (rent)
            (r'(?:aed\s*)?([\d,\.]+)\s*([mk])\b', 20_000),
            # Bare number with AED suffix: explicit currency = high confidence
            (r'\b(\d{4,8})\s*(?:aed|dhs|درهم)\b', 20_000),
            (r'(?:aed|dhs|درهم)\s*(\d{4,8})\b', 20_000),
            (r'(?:aed\s*)?([\d,]{6,})', 100_000),
        ]
        for pat, min_amt in patterns:
            for m in re.finditer(pat, t, re.I):
                groups = m.groups()
                raw = "".join(g for g in groups if g)
                amount = _parse_amount(raw)
                if amount and amount > min_amt:
                    result["price"] = amount
                    break
            if result["price"]:
                break

    # Sanity cap: price > 10 billion AED is almost certainly a parsing error.
    if result["price"] and result["price"] > 10_000_000_000:
        result["price"] = None
    if result["original_price"] and result["original_price"] > 10_000_000_000:
        result["original_price"] = None
    # Sanity: 500M+ AED is extreme — only whole_hotel/plot. Drop if structure
    # is short (text < 800 chars, likely concatenation bug). Hospitals/hotels
    # usually have very long detailed descriptions.
    if result["price"] and result["price"] > 500_000_000 and len(text) < 800:
        result["price"] = None
    if result["original_price"] and result["original_price"] > 500_000_000 and len(text) < 800:
        result["original_price"] = None
    # "1070 mln" / "X mln" where X looks suspicious: X > 100 для apartment context
    # means likely typo of "1,070,000" (1.07M) — divide by 1000.
    # Apartment context = text contains "bedroom"/"studio"/"sq.ft"/"apartment".
    if result["price"] and result["price"] > 100_000_000:
        tl_check = text[:1000].lower()
        is_apt_or_studio = bool(re.search(
            r'\b(?:bedroom|studio|sq\.?\s*ft|sqft|apartment|apt)\b', tl_check))
        if is_apt_or_studio and result["price"] > 100_000_000:
            # Likely typo "1070 mln" intended "1,070,000". Reduce by 1000.
            result["price"] = result["price"] // 1000
    # General sanity caps by property context
    # Apartment max 200M (Bugatti Residences was 110M for top units)
    if result["price"] and result["price"] > 200_000_000:
        tl_check = text[:1000].lower()
        if re.search(r'\b(?:bedroom|studio|apartment|apt)\b', tl_check) and \
           not re.search(r'\bbuilding\s+for\s+sale|hotel|hospital|plot|land', tl_check):
            result["price"] = None  # corrupt

    # Currency conversion: if the source text has explicit non-AED markers
    # (USD/EUR/GBP/RUB/$/€/£/₽) AND no explicit AED/Dhs marker — convert.
    ccy = detect_currency(text)
    has_aed = bool(re.search(r'\b(?:AED|Dhs|DH|د\.إ|درهم)\b', text, re.I))
    if ccy != "AED" and not has_aed:
        result["currency"] = ccy
        if result["price"]:
            converted = convert_to_aed(result["price"], ccy)
            if converted:
                result["original_price_in_source_ccy"] = result["price"]
                result["price"] = converted
        if result["original_price"]:
            converted = convert_to_aed(result["original_price"], ccy)
            if converted:
                result["original_price"] = converted
    else:
        result["currency"] = "AED"

    return result


# ── Multi-listing split cache (text hash → first-listing chunk) ─────────
import hashlib as _hashlib
_MULTI_SPLIT_CACHE: dict = {}
_MULTI_SPLIT_MAX = 1000


def _is_likely_multi_listing(text: str) -> bool:
    """Quick heuristic: does this look like a multi-listing post?
    Triggers when 3+ price mentions OR 3+ sqft mentions OR text > 800 chars
    with 2+ blocks of price markers."""
    if not text or len(text) < 400:
        return False
    tl = text.lower()
    # Count distinct "price" markers
    price_count = (
        len(re.findall(r'\b(?:price|sp|asking|selling|aed|سعر)\s*[:=]?\s*[\d,.]+\s*[mk]?', tl, re.I))
    )
    sqft_count = len(re.findall(r'\d+\s*(?:sqft|sq\.?\s*ft|sq\.?\s*m|sqm)', tl, re.I))
    return price_count >= 3 or sqft_count >= 3


def _llm_split_first_listing(text: str, timeout: int = 12) -> Optional[str]:
    """LLM-powered first-listing extraction для multi-listing постов.
    Возвращает текст ТОЛЬКО первого объявления (где product, его цена и
    размер). None если LLM не справился — fallback на regex split.
    """
    if not text or len(text) < 200:
        return None

    # Cache by text hash
    h = _hashlib.md5(text[:2000].encode('utf-8', errors='ignore')).hexdigest()
    if h in _MULTI_SPLIT_CACHE:
        return _MULTI_SPLIT_CACHE[h]

    GROQ = os.environ.get("GROQ_API_KEY", "")
    ANTH = os.environ.get("ANTHROPIC_API_KEY", "")
    if not (GROQ or ANTH):
        return None

    snippet = text[:2500]
    prompt = (
        "You are a UAE real estate text splitter. The following text contains "
        "MULTIPLE property listings concatenated in one Telegram post. Extract "
        "ONLY the FIRST listing's text (building name + bedrooms + sqft + price). "
        "Return the EXACT verbatim text of the first listing only, NO commentary, "
        "NO markdown changes, just the chunk.\n\n"
        "Rules:\n"
        "- A new listing usually starts with a building name, location pin (📍), "
        "or new property type (Studio/1BR/Villa/etc).\n"
        "- Keep the FULL first listing including its price.\n"
        "- Stop BEFORE the second listing's building name / location pin.\n"
        "- If the text contains only ONE listing — return the whole text.\n\n"
        f"Text:\n```\n{snippet}\n```\n\n"
        "First listing only:"
    )

    text_resp = None
    try:
        import requests as _req
        if GROQ:
            r = _req.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ}",
                         "Content-Type": "application/json"},
                json={"model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
                      "max_tokens": 500,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=timeout)
            if r.status_code == 200:
                text_resp = r.json()["choices"][0]["message"]["content"].strip()
        if not text_resp and ANTH:
            r = _req.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTH, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 500,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=timeout)
            if r.status_code == 200:
                text_resp = r.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"[parser] LLM split err: {e}")
        return None

    if not text_resp or len(text_resp) < 30:
        return None

    # Cleanup possible markdown fence
    text_resp = re.sub(r'^```\w*\s*', '', text_resp)
    text_resp = re.sub(r'\s*```\s*$', '', text_resp)
    text_resp = text_resp.strip()

    # Sanity: result must be ≤ 80% of original (it's only 1 of N listings).
    # If result is 95%+ — probably LLM didn't split.
    if len(text_resp) > len(text) * 0.85:
        # Trust as single-listing
        text_resp = text

    if len(_MULTI_SPLIT_CACHE) >= _MULTI_SPLIT_MAX:
        _MULTI_SPLIT_CACHE.pop(next(iter(_MULTI_SPLIT_CACHE)))
    _MULTI_SPLIT_CACHE[h] = text_resp
    return text_resp


def _first_listing_block(text: str) -> str:
    """Return first listing's text — up to the first separator or 2+ blank lines.
    Recognises a wide variety of separators used in Telegram listings:
      ─── ━━━ ─── ═══ ⸻ ⸺ ⸺⸺ ▬▬ ━━ -------- ======== ········
      ____ underscores (long visual rules)
      ◆◆◆ ■■■ ◇◇◇ ★★★ ●●● ▪▪▪ ▫▫▫  (Telegram listings often use these)
    For single-listing texts returns the full text unchanged.

    GUARD: если ПОСЛЕ separator идёт price-banner (Asking price / Selling price /
    SP / OP / Price:), то это НЕ настоящий listing-separator, а косметический
    разделитель внутри одного объявления (банер с ценой). В этом случае
    нужно «протянуть» first block до СЛЕДУЮЩЕГО separator после price-banner.

    LLM ENHANCEMENT: для подозрительных multi-listing постов (3+ цен/sqft)
    запускаем Groq для семантического разделения, ДО regex. Кешируем результат.
    """
    # ── Стадия 0: LLM split (только для multi-listing) ───────────────
    if os.environ.get("LLM_MULTI_SPLIT", "1") != "0" and _is_likely_multi_listing(text):
        try:
            llm_chunk = _llm_split_first_listing(text)
            if llm_chunk and 50 <= len(llm_chunk) <= len(text):
                # Trust LLM split — переходим к regex с чанком
                text = llm_chunk
        except Exception as e:
            print(f"[parser] LLM split exception: {e}")
    sep_pat = (r'\n\s*(?:'
                r'[-—–=━─═▬*·_]{3,}'
                r'|[⸻⸺]+'
                r'|[◆■◇★●▪▫◽◾◻◼]{3,}'
                r'|[—–]{2,}\s*[—–]{2,}'
                r'|[🔥💎💰⭐🌟❤❗‼️🚨🏠🏡]{3,}'   # 3+ emoji-frames вокруг листингов
                r')\s*\n'
                r'|\n\s*\n\s*\n\s*\n'
                # Маркер «новый листинг» — пустая строка, затем ● или ⚫ + текст
                # с emoji-локатором или CAPS-словом (типичный bullet-style multi-listing)
                r'|\n\s*\n\s*[●⚫◆◇❌⛔🚫]\s*'
                # Маркер «новый листинг» через локатор-emoji 📍/🗺/🌍 — ТОЛЬКО когда
                # 3+ blank lines подряд (т.е. реальный разделитель листингов).
                # Просто `\n\n📍` встречается ВНУТРИ одного объявления как
                # лейбл локации / девелопера / соседств.
                r'|\n\s*\n\s*\n\s*[📍🗺🌍📌]\s*')
    parts = re.split(sep_pat, text, maxsplit=5)
    if len(parts) <= 1:
        return text

    # GUARD 1: если parts[0] — это просто HEADER (нет цены/sqft, короткий типа
    # "VILLAS" / "FOR SALE" / "EXCLUSIVE"), его надо «вклеить» в parts[1].
    # Иначе first block станет просто "VILLAS" и потеряется всё содержимое
    # первого реального объявления.
    HEADER_RE = re.compile(
        r'^[\s\W]*(?:villas?|apartments?|penthouses?|townhouses?|plots?|'
        r'offices?|retails?|studios?|hotels?|luxury\s+apartments?|'
        r'exclusive|for\s+sale|for\s+rent|hot\s+deals?|available\s+units?|'
        r'offer\s+list|distress\s+deals?|new\s+listings?|priced\s+to\s+sell|'
        r'units?\s+for\s+sale|prime\s+deals?)[\s\W]*$',
        re.I)
    while len(parts) > 1 and (
            len(parts[0].strip()) < 40 or HEADER_RE.match(parts[0].strip())):
        # parts[0] is just a category banner — merge with parts[1]
        merged = (parts[0] + '\n' + parts[1]) if parts[0].strip() else parts[1]
        parts = [merged] + parts[2:]

    # Price-banner pattern — это лейбл с ценой который идёт ПОСЛЕ visual
    # separator внутри одного объявления (типа __*Asking price 3,200,000 AED*).
    price_banner_re = re.compile(
        r'^\s*[_\*\s]*(?:asking\s+price|selling\s+price|sale\s+price|'
        r'\bprice\b|sp\b|op\b|net\s+price|final\s+price|original\s+price|'
        r'aed\s*[\d,\.]+)', re.I)
    first = parts[0]
    # Если СРАЗУ после separator идёт price-banner — берём только первую строку
    # из next part (это банер с ценой первого объявления), без того что после неё.
    if len(parts) > 1 and price_banner_re.match(parts[1]):
        # Берём только первую непустую строку второго блока (это price banner)
        banner_lines = []
        for ln in parts[1].split('\n'):
            if ln.strip():
                banner_lines.append(ln)
                # После одной непустой строки — стоп, остальное это уже след. листинг
                break
        if banner_lines:
            first = first + "\n" + banner_lines[0]
    return first


def _extract_view_core(text: str) -> Optional[str]:
    """Inner extractor — applied to first listing block, falls back to full text."""
    tl = text.lower()
    for view in sorted(VIEWS, key=len, reverse=True):
        if view in tl:
            return view.title()
    m = re.search(r'\bview\s*[:\-]\s*([A-Za-z][A-Za-z\s&,/]{2,40})', text, re.I)
    if m:
        val = m.group(1).strip(' ,/&').strip()
        val = re.split(r'\s*(?:,|/|\||\n|—|–|-)\s*', val)[0].strip()
        if 3 <= len(val) <= 40 and not re.search(r'\d|sqft|sq\.|aed|price', val, re.I):
            return val.title() + (" View" if not val.lower().endswith("view") else "")
    return None


def extract_view(text: str) -> Optional[str]:
    """View from the FIRST listing only (multi-listing safety).
    If first block has no view, return None — don't bleed view from listing #3.
    """
    block = _first_listing_block(text)
    return _extract_view_core(block)


def extract_floor(text: str) -> Optional[int]:
    """Floor from FIRST listing block only (multi-listing safety).
    Sanity cap: 0..165 (Burj Khalifa is 163, tallest residential in UAE).
    All patterns are bound to same-line whitespace so 'High floor\\n2500 sqft'
    does NOT capture 2500 as the floor.
    Также: число НЕ должно быть рядом с sq.m/sqm/sqft/m² (это размер, не этаж)."""
    text = _first_listing_block(text)
    def _ok(v):
        return v if 0 <= v <= 165 else None
    def _not_sqm(text_around: str, pos: int) -> bool:
        """Returns True если в радиусе 40 символов после числа НЕТ sq.m/sqft/m²."""
        snippet = text_around[pos:pos+40].lower()
        return not re.search(r'\bsq\.?\s*m\b|\bsqft\b|\bsq\.?\s*ft\b|\bm[²2]\b|\bкв', snippet)
    # "Floor: 5", "fl#7", "floor 12"  — same-line whitespace only (no newline)
    # 'floor' as full word — иначе ловит fl... в любом слове
    m = re.search(r'\bfloor[ \t:#]*(\d+)', text, re.I)
    if m and _not_sqm(text, m.end()):
        v = _ok(int(m.group(1)))
        if v is not None:
            return v
    # "5th floor", "23rd Floor"
    m = re.search(r'(\d+)(?:st|nd|rd|th)\s*floor', text, re.I)
    if m:
        v = _ok(int(m.group(1)))
        if v is not None:
            return v
    # "8 floor", "5 floor" — number + space + floor (no ordinal)
    m = re.search(r'(?<!\d)(\d+)\s+floor\b', text, re.I)
    if m:
        v = _ok(int(m.group(1)))
        if v is not None:
            return v
    # "23floor" — number directly attached
    m = re.search(r'(?<!\d)(\d+)floor\b', text, re.I)
    if m:
        v = _ok(int(m.group(1)))
        if v is not None:
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

    # GUARD: if first block looks like a townhouse/villa with explicit BUA/Plot dimensions
    # (e.g. "Plot Area: 1,225 Sqft - Built-up Area: 2,261 Sqft"), do NOT treat as plot
    # even if "residential plot" appears later in the multi-listing dump.
    has_townhouse_dimensions = bool(
        re.search(r'\b(?:bua|built[\s\-]?up\s+area)\s*[:\-~]?\s*[\d,.]+\s*sq', head, re.I) and
        re.search(r'\bplot\s+(?:area|size)\s*[:\-~]?\s*[\d,.]+\s*sq', head, re.I)
    )

    # Pre-strip context phrases that contain a property-type word but refer to
    # something else (view from the window, view of the community, etc).
    # Otherwise "1BR apartment with villas view" becomes property_type=villa.
    head_stripped = re.sub(
        r'\b(?:villa|villas|community|garden|park|pool|burj|golf|sea|marina|'
        r'fountain|skyline|city|canal|tower|building|park)\s+view\b',
        ' ', head, flags=re.I)
    head_stripped = re.sub(
        r'\bview\s+(?:of|to)\s+(?:the\s+)?(?:villa|villas|community|tower|park|'
        r'pool|garden|building|skyline|marina|burj|sea)\b',
        ' ', head_stripped, flags=re.I)
    # "X view from balcony"
    head_stripped = re.sub(r'\b(?:villa|villas|townhouse|townhouses|tower|building)s?\s+(?:view|nearby|next\s+to|opposite)\b',
                            ' ', head_stripped, flags=re.I)
    # "Duplex views" — описание (вид с 2 уровней), не тип объекта
    head_stripped = re.sub(r'\bduplex\s+views?\b', ' ', head_stripped, flags=re.I)
    head_stripped = re.sub(r'\btwo[\s\-]storey\s+(?:apartment|apartments)\b', ' ',
                            head_stripped, flags=re.I)

    # GUARD: "G+X building for sale" / "G+12 tower" — whole-building offer.
    # Has explicit floor count + building/tower noun.
    if re.search(r'\bg\s*\+\s*\d{1,2}\b.*\b(?:building|tower)\b.*\b(?:for\s+sale|for\s+rent|for\s+lease)\b',
                  head_stripped, re.I):
        return "whole_building"

    # GUARD: "VILLA PLOT" / "VILLA LAND" / "Residential Plot" — это PLOT, не villa.
    # Парсер сейчас матчит 'villa' первой раньше 'plot'. Override:
    if re.search(r'\bvilla\s+(?:plot|land)\b', head_stripped, re.I) or \
       re.search(r'\b(?:residential|commercial|mixed[\s\-]use|industrial)\s+plot\b', head_stripped, re.I) or \
       re.search(r'\bplot\s+for\s+sale\b.*\bg\s*\+\s*\d', head_stripped, re.I):
        if not has_townhouse_dimensions:  # townhouse override уже есть
            return "plot"

    # Pass 1: check first listing block (most reliable)
    for ptype, keywords in PROP_TYPE_MAP.items():
        # Skip "plot" if the first block has townhouse-style dimensions
        if ptype == "plot" and has_townhouse_dimensions:
            continue
        for kw in keywords:
            pat = kw if kw.endswith('\\b') else r'\b' + re.escape(kw) + r'\b'
            if re.search(pat, head_stripped):
                return ptype

    # No Pass 2 — single-pass on first block. Pass 2 over full text caused false positives
    # (e.g. "Office Room" in a villa caused property_type=office).

    # Fallback
    if bedrooms == 0:
        return "studio"
    return "apartment"


def extract_extra_info(text: str, property_type: str) -> dict:
    """Pull domain-specific extra fields from the first listing block.

    For commercial: fitted/shell/finishing, parking_spots, conference_rooms.
    For plot: freehold/leasehold, usage (residential/commercial/mixed/industrial),
              GFA, height (G+N), nearest landmarks.
    For residential: maid_room, balcony, parking_spots, payment_plan,
                     post_handover, school_distance.
    Returns a dict that can be JSON-serialised.
    """
    block = _first_listing_block(text)
    tl = block.lower()
    info: dict = {}

    if property_type in ("office", "retail", "warehouse", "hotel",
                          "hotel_apartment", "serviced_apartment"):
        # Office fit-out
        if re.search(r'\bfitted\b|\bfully\s+fitted\b', tl):
            info["fit_out"] = "fitted"
        elif re.search(r'\bshell\s+and\s+core\b|\bshell\b', tl):
            info["fit_out"] = "shell"
        elif re.search(r'\bunfurnished\b', tl):
            info["fit_out"] = "unfurnished"
        # Parking spaces
        m = re.search(r'(\d+)\s*(?:car\s+park|parking|parking\s+spaces?)', tl)
        if m:
            try: info["parking_spaces"] = int(m.group(1))
            except: pass
        # Meeting / conference rooms
        m = re.search(r'(\d+)\s*(?:meeting|conference)\s+rooms?', tl)
        if m:
            try: info["meeting_rooms"] = int(m.group(1))
            except: pass
        # Reception / kitchenette flags
        if re.search(r'\breception\b', tl): info["reception"] = True
        if re.search(r'\bkitchenette\b|\bpantry\b', tl): info["pantry"] = True

    if property_type == "plot":
        # Usage / zoning
        m = re.search(r'usage\s*[:\-]?\s*([^\n,]{3,50})', block, re.I)
        if m: info["usage"] = m.group(1).strip()
        elif re.search(r'\bresidential\b.*\bcommercial\b|\bmixed\s+use\b', tl):
            info["usage"] = "Mixed Use"
        elif re.search(r'\bresidential\b', tl): info["usage"] = "Residential"
        elif re.search(r'\bcommercial\b', tl):  info["usage"] = "Commercial"
        elif re.search(r'\bindustrial\b', tl):  info["usage"] = "Industrial"
        # Freehold / leasehold
        if re.search(r'\bfreehold\b', tl): info["tenure"] = "Freehold"
        elif re.search(r'\bleasehold\b', tl): info["tenure"] = "Leasehold"
        # GFA (Gross Floor Area)
        m = re.search(r'gfa\s*[:\-]?\s*([\d,]+\.?\d*)\s*(?:sq\.?\s*ft|sqft)', block, re.I)
        if m:
            try: info["gfa_sqft"] = int(float(m.group(1).replace(',', '')))
            except: pass
        # Height (G+N)
        m = re.search(r'(?:height\s*[:\-]?\s*)?G\s*\+\s*(\d+)', block, re.I)
        if m:
            try: info["floors"] = f"G+{int(m.group(1))}"
            except: pass

    # Common residential extras
    if property_type in ("apartment", "studio", "villa", "townhouse",
                          "penthouse", "duplex"):
        if re.search(r'\bmaid\s*[sʼ\']?\s*room\b|\b\+\s*maid\b', tl):
            info["maid_room"] = True
        if re.search(r'\bstudy(?:\s+room)?\b', tl):  info["study_room"] = True
        if re.search(r'\bbalcony\b|\bterrace\b', tl):  info["balcony"] = True
        if re.search(r'\bprivate\s+pool\b', tl):       info["private_pool"] = True
        if re.search(r'\bprivate\s+garden\b', tl):     info["private_garden"] = True
        if re.search(r'\bpayment\s+plan\b|\bpost[\s\-]?handover\b', tl):
            info["payment_plan"] = True
        m = re.search(r'(\d+)\s*(?:car\s+park|parking)', tl)
        if m:
            try: info["parking_spaces"] = int(m.group(1))
            except: pass

    return info


def extract_status(text: str) -> Optional[str]:
    """Property status. Priority: offplan > rented > vacant > ready.
    'Ready to sell/sign/deal' — seller intent, NOT property status.
    """
    tl = text.lower()
    # Off-plan markers (highest priority — supersedes "ready" mentions)
    if re.search(r'\boff[\s-]?plan\b|\bunder\s+construction\b|\bhandover\s+(?:q[1-4]|in\s+\d{4}|\d{4}|date)', tl):
        return "offplan"
    # Rented / tenanted
    if re.search(r'\b(?:rented|tenanted|with\s+tenant|leased)\b', tl):
        return "rented"
    # Vacant
    if re.search(r'\bvacant\b|\bunoccupied\b|\bvacant\s+on\s+transfer\b', tl):
        return "vacant"
    # Ready (real) — must be "ready to move in", "completed", "handed over"
    # NOT "ready to sell/sign/deal/negotiate" (that's seller intent)
    if re.search(r'\bready\s+to\s+(?:move\s+in|occupy)\b|\bcompleted\b|\bhanded\s+over\b', tl):
        return "ready"
    return None


# ── DLD canonical normalisation ──────────────────────────────────────────────
# Загружаем 4773 канонических имени зданий + 215 районов из DLD-архива.
# Используется при парсинге чтобы привести building/area к единому формату.
_DLD_CANONICAL = {"buildings": {}, "building_areas": {}, "areas": {}}
try:
    import os as _os, json as _json
    _dld_path = _os.path.join(_os.path.dirname(__file__), "dld_canonical.json")
    if _os.path.exists(_dld_path):
        with open(_dld_path, encoding="utf-8") as _f:
            _DLD_CANONICAL = _json.load(_f)
        print(f"[parser] DLD canonical: {len(_DLD_CANONICAL.get('buildings',{}))} bld, "
              f"{len(_DLD_CANONICAL.get('areas',{}))} areas")
except Exception as _e:
    print(f"[parser] DLD canonical load failed: {_e}")


def _llm_extract_building_area(text: str, timeout: int = 12) -> dict:
    """LLM fallback (Claude → Groq) для извлечения building+area из текста
    объявления когда regex-парсер не справился.
    Возвращает {"building": str|None, "area": str|None, "confidence": 0-1}.
    """
    import requests as _req
    if not text or len(text) < 30:
        return {}
    # Limit text to first 1500 chars (typical first listing block)
    snippet = text[:1500].strip()

    prompt = (
        "You are a UAE real estate data extraction expert. Extract building name "
        "and area/community/district from this listing text. Respond ONLY in valid JSON.\n\n"
        "Rules:\n"
        '- "building": the SPECIFIC building/project name (e.g. "Binghatti Nova", "Peace Lagoons", '
        '"Marina Residences"). NOT amenities like "Private Pool" or descriptions like "Studio".\n'
        '- "area": the district/community name (e.g. "JVC", "Dubai Marina", "Downtown", "DLRC", '
        '"Dubai Land", "Business Bay"). NOT a view ("Burj Khalifa view" ≠ area).\n'
        "- If MULTIPLE listings in text — extract only the FIRST one's building/area.\n"
        "- If you cannot find a specific building name, set it to null.\n"
        '- Set confidence 0-1 based on how clearly the info is stated.\n\n'
        f"Text:\n```\n{snippet}\n```\n\n"
        'Output (JSON only, no markdown): {"building": "...", "area": "...", "confidence": 0.9}'
    )

    # Try Claude first (env vars)
    ANTH = os.environ.get("ANTHROPIC_API_KEY", "")
    GROQ = os.environ.get("GROQ_API_KEY", "")
    text_resp = None

    if ANTH:
        try:
            r = _req.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTH, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001",
                      "max_tokens": 200,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=timeout)
            if r.status_code == 200:
                text_resp = r.json()["content"][0]["text"].strip()
        except Exception as e:
            print(f"[parser_llm] Claude err: {e}")

    if not text_resp and GROQ:
        try:
            r = _req.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ}",
                         "Content-Type": "application/json"},
                json={"model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
                      "max_tokens": 200,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=timeout)
            if r.status_code == 200:
                text_resp = r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"[parser_llm] Groq err: {e}")

    if not text_resp:
        return {}

    # Extract JSON from response
    try:
        # Strip markdown code-fences if present
        cleaned = re.sub(r'```(?:json)?\s*', '', text_resp).rstrip('` \n')
        m = re.search(r'\{.*?\}', cleaned, re.S)
        if not m:
            return {}
        obj = _json.loads(m.group(0))
        return {
            "building": (obj.get("building") or "").strip() or None,
            "area": (obj.get("area") or "").strip() or None,
            "confidence": float(obj.get("confidence") or 0),
        }
    except Exception as e:
        print(f"[parser_llm] JSON parse err: {e}")
        return {}


def normalize_via_dld(building: Optional[str], area: Optional[str]) -> tuple:
    """Возвращает (canonical_building, canonical_area). Если DLD-словарь
    содержит ключ — берём оттуда. Иначе возвращаем оригинал.

    NB: после DLD normalize применяем DLD_TO_FRIENDLY чтобы заменить
    официальные DLD-имена (Al Barsha South Fourth) на привычные пользователю
    (Jumeirah Village Circle). Юзеры не знают официальных DLD-имён.
    """
    new_bld, new_area = building, area
    if building:
        canon = _DLD_CANONICAL.get("buildings", {}).get(building.strip().lower())
        if canon:
            new_bld = canon
            # Из DLD также подставляем area для этого здания
            bld_area = _DLD_CANONICAL.get("building_areas", {}).get(building.strip().lower())
            if bld_area:
                new_area = bld_area
    if not new_area and area:
        canon = _DLD_CANONICAL.get("areas", {}).get(area.strip().lower())
        if canon:
            new_area = canon
    elif new_area:
        canon = _DLD_CANONICAL.get("areas", {}).get(new_area.strip().lower())
        if canon:
            new_area = canon
    # ── Convert DLD official → friendly name (final layer) ─────────────
    if new_area:
        new_area = _DLD_TO_FRIENDLY.get(new_area, new_area)
    return new_bld, new_area


# ─── BUILDING → AREA INFERENCE CACHE ─────────────────────────────────────
# Building name → known area. Кеш для каждого building чтобы не дёргать LLM
# каждый раз. Заполняется из DLD + DB + Groq.
_BUILDING_AREA_CACHE: dict = {}


def infer_area_from_building(building: str, db_dsn: str = None) -> Optional[str]:
    """Trying 3 sources в каскаде:
    1. DLD canonical building→area mapping (4773 buildings)
    2. Существующие DB records с тем же building (majority area)
    3. Groq LLM с вопросом «in which Dubai district is X building?»
    Результат кешируется в _BUILDING_AREA_CACHE.
    """
    if not building:
        return None
    key = building.strip().lower()
    if key in _BUILDING_AREA_CACHE:
        return _BUILDING_AREA_CACHE[key]

    # Step 1: DLD canonical
    dld_area = _DLD_CANONICAL.get("building_areas", {}).get(key)
    if dld_area:
        # Convert DLD official → friendly
        friendly = _DLD_TO_FRIENDLY.get(dld_area, dld_area)
        _BUILDING_AREA_CACHE[key] = friendly
        return friendly

    # Step 2: DB majority — if we have 5+ records of this building with non-null
    # area, take the most common one.
    if not db_dsn:
        db_dsn = os.environ.get("DATABASE_URL", "")
    if db_dsn:
        try:
            import psycopg2 as _pg
            _conn = _pg.connect(db_dsn, connect_timeout=5)
            _cur = _conn.cursor()
            _cur.execute("""SELECT area, COUNT(*) AS n FROM listings
                WHERE LOWER(building)=%s AND area IS NOT NULL
                GROUP BY area ORDER BY n DESC LIMIT 1""", (key,))
            row = _cur.fetchone()
            _cur.close(); _conn.close()
            if row and row[1] >= 5:  # need ≥ 5 confirmations
                _BUILDING_AREA_CACHE[key] = row[0]
                return row[0]
        except Exception as _e:
            print(f"[parser] DB infer_area err: {_e}")

    # Step 3: Groq LLM — one-off lookup
    GROQ = os.environ.get("GROQ_API_KEY", "")
    ANTH = os.environ.get("ANTHROPIC_API_KEY", "")
    if GROQ or ANTH:
        prompt = (
            f"You are a UAE real estate database. In which Dubai (or other UAE emirate) "
            f"district/community is the building '{building}' located? "
            f"Return ONLY a JSON object with a single 'area' field. "
            f'Examples: {{"area": "Jumeirah Village Circle"}} or {{"area": "Dubai Marina"}}. '
            f"If you don't know with confidence, return: {{\"area\": null}}.\n\n"
            f"Use the FAMILIAR user-known name (JVC, Marina, Downtown, etc.), "
            f"NOT DLD official names like 'Al Barsha South Fourth'."
        )
        try:
            import requests as _req
            text = None
            if ANTH:
                r = _req.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": ANTH, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": "claude-haiku-4-5-20251001", "max_tokens": 80,
                          "messages": [{"role": "user", "content": prompt}]},
                    timeout=10)
                if r.status_code == 200:
                    text = r.json()["content"][0]["text"].strip()
            if not text and GROQ:
                r = _req.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ}",
                             "Content-Type": "application/json"},
                    json={"model": "llama-3.3-70b-versatile",
                          "max_tokens": 80,
                          "messages": [{"role": "user", "content": prompt}]},
                    timeout=10)
                if r.status_code == 200:
                    text = r.json()["choices"][0]["message"]["content"].strip()
            if text:
                cleaned = re.sub(r'```(?:json)?\s*', '', text).rstrip('` \n')
                m = re.search(r'\{[^{}]*\}', cleaned, re.S)
                if m:
                    obj = _json.loads(m.group(0))
                    area = obj.get("area")
                    if area and isinstance(area, str) and len(area) >= 3:
                        # Apply friendly mapping just in case
                        friendly = _DLD_TO_FRIENDLY.get(area, area)
                        _BUILDING_AREA_CACHE[key] = friendly
                        return friendly
        except Exception as _e:
            print(f"[parser] LLM infer_area err: {_e}")

    _BUILDING_AREA_CACHE[key] = None
    return None


# DLD official area names → user-friendly names.
# Юзеры ищут «JVC», не «Al Barsha South Fourth». Конвертируем при выводе.
_DLD_TO_FRIENDLY = {
    "Al Barsha South Fourth":            "Jumeirah Village Circle",
    "Al Barsha South Third":             "Jumeirah Village Triangle",
    "Al Barsha South Second":            "Jumeirah Village Triangle",
    "Marsa Dubai":                       "Dubai Marina",
    "Hadaeq Sheikh Mohammed Bin Rashid": "MBR City",
    "Al Khairan First":                  "Dubai Creek Harbour",
    "Al Khairan Second":                 "Dubai Creek Harbour",
    "Wadi Al Safa 2":                    "Dubai Hills Estate",
    "Wadi Al Safa 3":                    "Dubai Hills Estate",
    "Wadi Al Safa 5":                    "Dubai Hills Estate",
    "Wadi Al Safa 7":                    "Dubai Hills Estate",
    "Burj Khalifa":                      "Downtown Dubai",
    "Jabal Ali First":                   "Jebel Ali",
    "Saih Shuaib 2":                     "Dubai Investments Park",
    "Madinat Al Mataar":                 "Dubai South",
    "Marsa Al Arab":                     "Sufouh",
    "Trade Center Second":               "DIFC",
    "Trade Center First":                "DIFC",
    "Mirdif":                            "Mirdiff Hills",
    "Al Yufrah 1":                       "Town Square",
    "Al Yufrah 2":                       "Town Square",
    "Al Thanyah Fifth":                  "Jumeirah Lake Towers",
    "Al Thanyah Fourth":                 "Jumeirah Lake Towers",
    "Al Thanyah Third":                  "Jumeirah Lake Towers",
}


def extract_offplan(text: str) -> bool:
    """Returns True if text contains off-plan / under-construction markers."""
    t = _first_listing_block(text).lower()
    patterns = [
        r"\boff[\s\-]?plan\b",
        r"\bunder\s+construction\b",
        r"\bnew\s+launch\b",
        r"\bpre[\s\-]?launch\b",
        r"\bpost[\s\-]?handover\b",
        r"\bhandover\s+(?:in\s+)?20\d\d\b",
        r"\bpayment\s+plan\b.*\bto\s+(?:developer|builder)\b",
        r"\b(?:q[1-4]\s*[\-/]?\s*20\d\d)\b",
    ]
    for p in patterns:
        if re.search(p, t):
            return True
    return False


def extract_handover_date(text: str) -> Optional[str]:
    """Returns ISO date string YYYY-MM-DD for handover, or None.
    Recognizes 'Q3 2027', 'handover 2026', 'handover Dec 2025', '12/2025'."""
    t = _first_listing_block(text)
    # Q1-Q4 YYYY
    m = re.search(r'\bq([1-4])\s*[\-/]?\s*(20\d\d)\b', t, re.I)
    if m:
        q = int(m.group(1)); yr = int(m.group(2))
        month = (q-1)*3 + 1
        return f"{yr:04d}-{month:02d}-01"
    # handover Mon YYYY
    months = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
              "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
    m = re.search(r'\bhandover\s+(?:in\s+)?(' + "|".join(months) + r')\w*\s*(20\d\d)\b', t, re.I)
    if m:
        mon = months[m.group(1).lower()[:3]]
        yr  = int(m.group(2))
        return f"{yr:04d}-{mon:02d}-01"
    # handover YYYY
    m = re.search(r'\bhandover\s+(?:in\s+)?(20\d\d)\b', t, re.I)
    if m:
        return f"{int(m.group(1)):04d}-12-31"
    return None


# Currency conversion rates → AED (approximate, refreshed weekly)
_FX_TO_AED = {
    "USD": 3.67, "EUR": 4.00, "GBP": 4.65, "RUB": 0.040,
    "AED": 1.0, "DHS": 1.0, "DH": 1.0,
}


def convert_to_aed(amount: float, currency: str) -> Optional[int]:
    rate = _FX_TO_AED.get((currency or "AED").upper())
    if not rate:
        return None
    return int(amount * rate)


def detect_currency(text: str) -> str:
    """Returns currency code from text, default AED.
    Использует word-boundary матч — раньше `'RUB' in t` ловил 'Ruby'
    (Binghatti Ruby) и переводил цену в AED по курсу RUB → 0.04× от
    реальной цены (675K RUB → 27K AED).
    """
    if not text: return "AED"
    for ccy in ("USD", "EUR", "GBP", "RUB"):
        if re.search(r'\b' + ccy + r'\b', text, re.I):
            return ccy
    # Symbol-based (тоже с word-boundary не нужно — символы не часть слова)
    if "$" in text:  return "USD"
    if "€" in text:  return "EUR"
    if "£" in text:  return "GBP"
    if "₽" in text:  return "RUB"
    return "AED"


def extract_bathrooms(text: str) -> Optional[int]:
    """Returns int (1..20) or None. From FIRST listing block (multi-listing safety).

    Patterns recognized:
      - "3 Bathrooms", "4 bath", "5 Bathroom" (number before label)
      - "Bathrooms: 2", "🛁 Bathrooms : 3" (label before number)
      - "2 BA" (rare abbreviation)
    """
    text = _first_listing_block(text)
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
    Uses FIRST listing block to avoid bleed across multi-listing texts.
    """
    text = _first_listing_block(text)
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

    # ── MULTI-LISTING SAFETY ─────────────────────────────────────────────────
    # КРИТИЧНО: clean_text() удаляет разделители и переводы строк —
    # в clean уже нельзя найти границы первого листинга.
    # Берём first_block из ОРИГИНАЛЬНОГО текста, потом чистим только этот блок.
    first_block_raw = _first_listing_block(text)
    first_block = clean_text(first_block_raw)

    # Step 3: Deal type — из first_block чтобы не подхватить "for rent" из объекта #3
    deal_type = detect_deal_type(first_block)

    # ── Step 0: Header-line structural patterns ───────────────────────────────
    # Extract hints from first few lines before heavier entity detection
    header_hints = extract_from_header_lines(first_block)

    # ── Step 1: Emirate direct ────────────────────────────────────────────────
    emirate, emirate_conf = detect_emirate_direct(first_block)

    # ── Step 3: Building (do this early — it's SOURCE OF TRUTH) ──────────────
    building, building_conf, bld_area, bld_emirate, developer_from_bld = detect_building(first_block)

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
        area, area_conf, area_emirate, possible_emirates = detect_area(first_block, emirate)
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
    # ВСЕ структурные поля парсим из first_block — иначе склеиваем данные
    # из разных объектов в multi-listing.
    bedrooms = extract_bedrooms(first_block)
    # Fallback: header-line structural hint for bedrooms (BHK patterns)
    if bedrooms is None and header_hints.get("bedrooms") is not None:
        bedrooms = header_hints["bedrooms"]
    bathrooms = extract_bathrooms(first_block)
    sizes = extract_size(first_block)
    view = extract_view(first_block)
    floor = extract_floor(first_block)
    unit_number = extract_unit_number(first_block)
    prop_type = extract_property_type(first_block, bedrooms)
    extra_info = extract_extra_info(first_block, prop_type)
    status = extract_status(first_block)
    furnishing = extract_furnishing(first_block)
    contacts = extract_contacts(original_text)
    is_off_plan   = extract_offplan(first_block)
    handover_date = extract_handover_date(first_block)

    # ── Price ─────────────────────────────────────────────────────────────────
    price_data = extract_price(first_block)
    price = price_data.get("price")
    price_per_sqft = None
    if price and sizes.get("size_sqft"):
        price_per_sqft = round(price / sizes["size_sqft"], 0)

    # ── Validate deal_type against market floor prices ─────────────────────
    deal_type = validate_deal_type_by_price(price, deal_type, bedrooms, area, text=original_text, building=building)

    # ── Sanity floor: residential rent < 20k / sale < 200k = это служебка ──
    residential_types = {"apartment","studio","villa","townhouse","penthouse","duplex"}
    if price and prop_type in residential_types:
        if deal_type == "rent" and price < 20_000:
            print(f"[parser] DROP rent price={price} (likely service charge)")
            price = None
            price_per_sqft = None
        elif deal_type == "sale" and price < 200_000:
            print(f"[parser] DROP sale price={price} (likely service charge / mis-parse)")
            price = None
            price_per_sqft = None

    # ── KEYWORD-BASED reclassification (strict text-content check) ──────────
    # Парсер должен ВИДЕТЬ ключевое слово в тексте, иначе reclass на правильное.
    tl_head = first_block[:500].lower()
    has_kw = {
        "villa":          "villa" in tl_head or "mansion" in tl_head,
        "townhouse":      "townhouse" in tl_head or "town house" in tl_head,
        "penthouse":      "penthouse" in tl_head or "pent house" in tl_head,
        "studio":         "studio" in tl_head,
        "duplex":         "duplex" in tl_head,
        "plot":           "plot" in tl_head or "land for sale" in tl_head[:300],
        "office":         "office" in tl_head,
        "retail":         "retail" in tl_head or "shop for" in tl_head,
        "whole_building": any(k in tl_head for k in (
            "whole building","full building","building for sale","tower for sale",
            "entire building","residential building")),
    }
    has_plot_bua = bool(re.search(r'\bplot\s*[:]\s*\d', tl_head)
                         and re.search(r'\bbua\s*[:]\s*\d', tl_head))

    # PENTHOUSE без "penthouse" в тексте → реклассификация
    if prop_type == "penthouse" and not has_kw["penthouse"]:
        if has_kw["whole_building"]:
            prop_type = "whole_building"
        elif has_kw["plot"] or "gfa" in tl_head:
            prop_type = "plot"
        elif (has_kw["villa"] or "mansion" in tl_head) or has_plot_bua:
            prop_type = "villa"
        else:
            prop_type = "apartment"

    # WHOLE_BUILDING без явных ключевых слов → реклассификация
    if prop_type == "whole_building" and not has_kw["whole_building"]:
        if "full floor" in tl_head and has_kw["office"]:
            prop_type = "office"
        elif (("land" in tl_head[:200] or "plot" in tl_head[:200]) and "gfa" in tl_head):
            prop_type = "plot"
        elif has_plot_bua and (has_kw["villa"] or "bedroom" in tl_head):
            prop_type = "villa"
        elif has_kw["villa"]:
            prop_type = "villa"

    # VILLA без "villa"/"mansion" → реклассификация
    if prop_type == "villa" and not has_kw["villa"]:
        if has_kw["townhouse"]:
            prop_type = "townhouse"
        elif has_kw["apartment"] if False else "apartment" in tl_head:
            prop_type = "apartment"

    # TOWNHOUSE без "townhouse" → реклассификация
    if prop_type == "townhouse" and not has_kw["townhouse"]:
        if has_kw["villa"]:
            prop_type = "villa"
        elif "apartment" in tl_head:
            prop_type = "apartment"

    # PLOT — building always NULL (земля не имеет building, только community)
    if prop_type == "plot" and building:
        building = None
        building_conf = 0.0

    # PLOT — bedrooms not meaningful (плот — это земля, не квартира).
    # Текст вроде «5 bedroom villa plot» означает «участок под виллу на 5 BR»,
    # но ещё не построено. Bedrooms должен быть NULL для plot.
    if prop_type == "plot" and bedrooms is not None:
        bedrooms = None

    # WHOLE_BUILDING — bedrooms тоже не имеет смысла (это всё здание)
    if prop_type == "whole_building" and bedrooms is not None:
        bedrooms = None

    # building == area (case-insensitive) → удалить building, это area-как-building bug
    if building and area and building.strip().lower() == area.strip().lower():
        building = None
        building_conf = 0.0

    # Final stopword check на extracted building (на случай если detect_building не отсек)
    if building and _is_building_stopword(building):
        building = None
        building_conf = 0.0

    # ── Building post-clean (Round 6 rules) ────────────────────────────────
    if building:
        bl = building.strip().lower()
        # Hard-NULL noise words
        if bl in {'year','month','day','week','price','sale','rent','studio',
                   'apartment','villa','townhouse','plot','land','unit','flat',
                   '1 bedroom','2 bedroom','3 bedroom','4 bedroom','5 bedroom',
                   'bedroom','available','vacant','furnished','unfurnished',
                   'high floor','low floor','mid floor','top floor',
                   'two villas','three villas','multiple units','units for sale',
                   'one unit','total apartments','floors total apartments',
                   'distress deal','hot deal','investor deal','flip sale',
                   'last transaction','all available apartments','all available',
                   'available units','units available','available now',
                   'urgent sale','best layout','best price','best deal','good deal',
                   'new listing','fresh listing','op price','sp price',
                   'asking price','selling price','sale price','final price',
                   'offer price','starting price','with maid','plus maid',
                   'maid room',"maid's room","maids room",
                   # Month names (часто захватываются из «vacant in March»)
                   'january','february','march','april','may','june','july',
                   'august','september','october','november','december',
                   # Marketing / status phrases that get mis-parsed as building
                   'best payment plan in business bay','best payment plan',
                   'not covered','i am covered','i\'m covered','direct',
                   'built','built in wardrobes','built-in wardrobes',
                   'ready to move in','ready to move','vacant on transfer',
                   'price drop','price reduction','price update','price reduced',
                   'mortgage accepted','cash payment','direct from owner',
                   'below market','below market price','below original',
                   'distress','distress deal','distress price',
                   # Area-name leak (когда area попадает в building):
                   'damac lagoon','damac lagoons','damac hills','damac hills 2',
                   'creek harbour','creek beach','sobha hartland','palm jumeirah',
                   'dubai marina','business bay','downtown','jumeirah'}:
            building = None
            building_conf = 0.0
        # Starts with digit + bedroom-noun
        elif re.match(r'^\d+[\s\-]*(?:bedroom|bed|br|bdr|bhk)s?\b', bl):
            building = None
            building_conf = 0.0
        # Plot Area / GFA / BUA phrases
        elif re.search(r'\b(?:plot\s+area|sqft\s+area|bua|gfa|total\s+apartments)\b', bl):
            building = None
            building_conf = 0.0
        else:
            cleaned = building
            cleaned = re.sub(r'^(?:apartment|studio|villa|townhouse|penthouse|'
                              r'duplex|office|flat|unit)\s+in\s+', '', cleaned, flags=re.I)
            cleaned = re.sub(r'^[^\w\d]+', '', cleaned).strip()
            cleaned = re.sub(r'\s*[-–—]\s*plot\s*$', '', cleaned, flags=re.I)
            cleaned = re.sub(r'\s+plot\s*$', '', cleaned, flags=re.I)
            # Strip trailing " Area" / " Type" / " Property" / " Unit" — лейблы
            # которые парсер схватил вместе с building name.
            cleaned = re.sub(r'\s+(?:area|type|property|unit|apartment|villa|townhouse)\s*$',
                              '', cleaned, flags=re.I).strip()
            # Strip leading «Studio | X» / «1 BR | X» — type-token prefix
            cleaned = re.sub(r'^(?:studio|\d+\s*br|apartment|villa|townhouse|penthouse|duplex)\s*\|\s*',
                              '', cleaned, flags=re.I).strip()
            # Strip trailing parenthetical area like "(Damac Hills 2)" если короткая
            cleaned = re.sub(r'\s*\([^)]{3,30}\)\s*$', '', cleaned).strip()
            if area:
                pat = re.compile(r'\s*[-–—]\s*' + re.escape(area) + r'\s*$', re.I)
                cleaned = pat.sub('', cleaned).strip()
            cleaned = cleaned.strip(' -—–|*')
            if not cleaned or len(cleaned) < 3:
                building = None
                building_conf = 0.0
            else:
                building = cleaned

    # ── Property type sanity by price/size — переклассификация ───────────────
    # Villa в Дубае реально не дешевле 2M AED. Если price<2M + type=villa,
    # это не villa (парсер угадал по 'villas view' или из multi-listing).
    if prop_type == "villa" and price:
        if deal_type == "sale" and price < 2_000_000:
            prop_type = "apartment"
        if deal_type == "rent" and price < 30_000:
            prop_type = "apartment"
    if prop_type == "townhouse" and price:
        if deal_type == "sale" and price < 800_000:
            prop_type = "apartment"
        if deal_type == "rent" and price < 30_000:
            prop_type = "apartment"
    # Studio не может быть > 3M AED sale или иметь bedrooms >= 2
    if prop_type == "studio":
        if (bedrooms or 0) >= 2:
            prop_type = "apartment"
        elif (bedrooms or 0) == 1:
            prop_type = "apartment"
        elif deal_type == "sale" and price and price > 3_000_000:
            prop_type = "apartment"
        elif sizes.get("size_sqft") and sizes["size_sqft"] > 1200:
            prop_type = "apartment"
    # Penthouse: real penthouse >= 2BR + sqft >= 1500
    if prop_type == "penthouse":
        if (bedrooms or 0) <= 1 and sizes.get("size_sqft") and sizes["size_sqft"] < 1500:
            prop_type = "apartment"

    # ── Size sanity — sqft > 6000 для apartment вероятно мусор ───────────────
    if prop_type == "apartment" and sizes.get("size_sqft") and sizes["size_sqft"] > 6000:
        # Не сразу обнуляем — может быть legitimate penthouse, переклассифицируем
        prop_type = "penthouse"
    if sizes.get("size_sqft") and sizes["size_sqft"] > 50_000:
        # Чистый мусор — sqft > 50k явно неправильный
        sizes["size_sqft"] = None
        price_per_sqft = None

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
        # GUARD: plot/whole_building почти всегда sale (rent для земли в UAE — крайняя редкость).
        # Multi-listing posts с rent-сигналами могли неверно классифицировать plot как rent.
        "deal_type": "sale" if prop_type in ("plot", "whole_building") else deal_type,
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
        "is_off_plan": is_off_plan,
        "handover_date": handover_date,
        "extra_info": extra_info if extra_info else None,

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

    # ── DLD normalisation — приводим building/area к каноническому имени ───
    canon_bld, canon_area = normalize_via_dld(data.get("building"), data.get("area"))
    if canon_bld and canon_bld != data.get("building"):
        data["building"] = canon_bld
    if canon_area and canon_area != data.get("area"):
        data["area"] = canon_area

    # ── Building → area cross-check ─────────────────────────────────────
    # Если есть building, проверяем что area совпадает с тем где building
    # реально находится. Используем 3 источника: DLD → DB majority → Groq.
    # Cron-friendly: вызывается ТОЛЬКО когда area null OR явно странный
    # (multi-listing leak).
    if data.get("building"):
        # 1. Если area отсутствует — заполняем через infer
        if not data.get("area"):
            inferred = infer_area_from_building(data["building"])
            if inferred:
                data["area"] = inferred
                data["area_confidence"] = 0.85
                data["area_source"] = "inferred"
        # 2. Если area есть — проверяем DLD building→area, переписываем если есть
        else:
            bld_key = data["building"].strip().lower()
            dld_area = _DLD_CANONICAL.get("building_areas", {}).get(bld_key)
            if dld_area:
                friendly = _DLD_TO_FRIENDLY.get(dld_area, dld_area)
                if data["area"].strip().lower() != friendly.strip().lower():
                    # Conflict — DLD wins (это source of truth)
                    data["area"] = friendly
                    data["area_confidence"] = 0.95
                    data["area_source"] = "dld_override"

    # ── Stage 3: LLM fallback — если building всё ещё NULL, спросим Claude/Groq.
    # Лимит: только для текста ≥80 chars (иначе шум). Если LLM нашёл — пробуем
    # снова DLD-normalize. Если LLM не уверен (confidence < 0.5) — игнорим.
    use_llm = (not data.get("building")
               and text and len(text.strip()) >= 80
               and os.environ.get("LLM_BUILDING_FALLBACK", "1") != "0")
    if use_llm:
        try:
            llm = _llm_extract_building_area(text)
            if llm.get("building") and llm.get("confidence", 0) >= 0.5:
                llm_bld = llm["building"]
                llm_area = llm.get("area")
                # Validate via DLD
                canon_bld2, canon_area2 = normalize_via_dld(llm_bld, llm_area)
                data["building"] = canon_bld2 or llm_bld
                if canon_area2 and not data.get("area"):
                    data["area"] = canon_area2
                elif llm_area and not data.get("area"):
                    data["area"] = llm_area
                data["building_confidence"] = llm["confidence"]
                data["llm_used"] = True
        except Exception as _e:
            print(f"[parser] LLM fallback err: {_e}")

    # ── Quality gate: NULL building → отправить в audit (не публикуем) ─────
    # Пользователь договорился: лучше меньше но качественных объявлений.
    # Если building всё ещё пустой даже после LLM — audit.
    if not data.get("building") or not str(data.get("building")).strip():
        data["needs_manual_review"] = True
        data["auto_audit"] = True
        existing_reason = data.get("review_reason") or ""
        data["review_reason"] = (existing_reason + "; no_building").lstrip("; ")

    # ── Sanity-limit длины: building > 100 chars / area > 100 chars = мусор
    if data.get("building") and len(data["building"]) > 100:
        data["building"] = None
    if data.get("area") and len(data["area"]) > 100:
        data["area"] = None

    # ── Strict validator — помечаем подозрительные сразу в audit ───────────
    audit_reasons = _validate_listing_strict(data)
    if audit_reasons:
        data["needs_manual_review"] = True
        data["review_reason"] = "; ".join(audit_reasons)
        # Не отправляем сразу в audit (is_audit=TRUE) — это сделает flag_audit cron.
        # Но помечаем review_reason для админ-обзора.

    data["listing_key"] = make_listing_key(data)

    return data


def _validate_listing_strict(data: dict) -> list:
    """Возвращает список причин подозрительности или [] если всё ок.
    Логика синхронизирована с DB-валидатором — те же эвристики."""
    reasons = []
    bld = data.get("building") or ""
    area = data.get("area") or ""
    deal = data.get("deal_type") or "sale"
    pt = data.get("property_type") or "apartment"
    br = data.get("bedrooms")
    sqft = (data.get("size_sqft") or 0) or (data.get("bua_sqft") or 0)
    price = data.get("price") or 0
    text = (data.get("original_text") or "")[:1000].lower()

    # Building checks
    if bld:
        if len(bld) > 60:
            reasons.append("building_too_long")
        if re.search(r'\b(?:view|deal|vacant|rented|brand new|fully|distress|hot|asking)\b',
                      bld, re.I):
            reasons.append("building_descriptor")
        if re.search(r'\d{4,}', bld):
            reasons.append("building_has_digits")
        # Building не упомянуто в первом блоке текста
        if text and bld.lower() not in text[:600]:
            if bld.lower() in text:
                reasons.append("building_not_in_first_block")

    # Price plausibility
    residential = {"apartment","villa","townhouse","penthouse","studio","duplex"}

    # АБСОЛЮТНЫЕ полы независимо от bedrooms (защита от мусора):
    # - rent < 15000 AED/год = невозможно (минимум в UAE ~25k)
    # - sale < 200000 AED = невозможно (минимум в UAE ~350k)
    if price and pt in residential:
        if deal == "rent" and price < 15000:
            reasons.append(f"rent_absurd_low_{price}")
        elif deal == "sale" and price < 200000:
            reasons.append(f"sale_absurd_low_{price}")

    # Контекстные полы (с учётом bedrooms)
    if price and br is not None and br > 0 and pt in residential:
        if deal == "rent":
            min_rent = {1:40000,2:60000,3:90000,4:120000}.get(br, 30000)
            if price < min_rent * 0.5:
                reasons.append(f"rent_too_low_{price}")
        elif deal == "sale":
            min_sale = {1:500000,2:900000,3:1500000,4:2500000}.get(br, 350000)
            if price < min_sale * 0.4:
                reasons.append(f"sale_too_low_{price}")

    # Sqft-based plausibility for rent: < 8 AED/sqft/year = почти точно битая цена
    if price and sqft and sqft > 200 and deal == "rent" and pt in residential:
        psf = price / sqft
        if psf < 8:
            reasons.append(f"rent_psf_absurd_{int(psf)}")
    # Sqft-based for sale: < 150 AED/sqft = битая цена
    if price and sqft and sqft > 200 and deal == "sale" and pt in residential:
        psf = price / sqft
        if psf < 150:
            reasons.append(f"sale_psf_absurd_{int(psf)}")

    # Sqft plausibility
    if sqft and br is not None and br > 0:
        if sqft < br * 200:
            reasons.append(f"sqft_too_small_{sqft}")

    # Type vs text contradictions
    if text:
        if pt == "studio" and re.search(r'\b[1-9]\s*(?:br|bedroom|bhk)\b', text):
            reasons.append("studio_with_NBR")
        if pt == "villa" and "villa" not in text[:200] and sqft and sqft < 2000:
            reasons.append("villa_no_word")
        if pt == "penthouse" and "penthouse" not in text[:300]:
            if not (br and br >= 3 and sqft and sqft > 2000):
                reasons.append("penthouse_no_word")

    return reasons


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
    Decide sale vs rent purely on benchmark distance.
    Returns ('sale'|'rent'|None, confidence 0..1, reason).
    Conservative: returns None unless clearly one or the other.
    """
    if not building or not price or not PRICE_BENCHMARKS:
        return None, 0.0, None

    bdata = _lookup_benchmark(building)
    if not bdata:
        return None, 0.0, None

    sale_med = bdata.get('sale_median')
    rent_med = bdata.get('rent_median_yearly')
    sale_count = bdata.get('sale_count') or 0
    # rent_count not yet in JSON, estimate via presence
    has_rent = bool(rent_med)

    # Need at least decent sale stats to use sale benchmark
    if sale_count < 5:
        sale_med = None

    # Distance ratios (smaller = closer)
    sale_dist = abs(price - sale_med) / sale_med if sale_med else 999
    rent_dist = abs(price - rent_med) / rent_med if has_rent else 999

    # Case A: rent benchmark available — compare both
    if has_rent and sale_med:
        # Price strongly favors rent: close to rent_med, far from sale_med
        if rent_dist < 0.6 and sale_dist > 0.7:
            return 'rent', 0.85, f"price ~{price} close to rent median {rent_med}, far from sale median {sale_med}"
        # Price strongly favors sale
        if sale_dist < 0.6 and rent_dist > 5:
            return 'sale', 0.85, f"price ~{price} close to sale median {sale_med}, far from rent median {rent_med}"
        # Mid range — let other logic decide
        return None, 0.0, None

    # Case B: only sale benchmark available
    if sale_med:
        # If price is within 60% of sale median, very likely sale
        if sale_dist < 0.6:
            return 'sale', 0.75, f"price close to sale median {sale_med}"
        # If price is way below sale median (< 5% of it), almost certainly rent
        if price < sale_med * 0.05 and price < 500_000:
            return 'rent', 0.80, f"price << sale median {sale_med} → rent"
        # If price between 5-15% of sale median — gray zone, let absolute limits decide
        if price < sale_med * 0.15 and price < 800_000:
            return 'rent', 0.65, f"price < 15% of sale median → likely rent"
        return None, 0.0, None

    # Case C: only rent benchmark available
    if has_rent:
        if rent_dist < 0.6:
            return 'rent', 0.75, f"price close to rent median {rent_med}"
        # Way above rent median → sale
        if price > rent_med * 10 and price > 500_000:
            return 'sale', 0.75, f"price >> rent median {rent_med} → sale"

    return None, 0.0, None
