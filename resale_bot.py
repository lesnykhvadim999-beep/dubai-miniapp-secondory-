"""
resale_bot.py — Dubai Resale Intelligence Bot v5
All fixes applied:
1. Rent filter working
2. Add listing wizard with moderation
3. Full seller data in lead bot
4. Market data summary from DB
5. Dynamic area selection from market_data
6. Photo via Bot API
7. sendMediaGroup for multiple photos
8. Stats with searches/views counters
"""

import os, re, time, json, threading, requests
from datetime import datetime, timezone

from db_schema import (
    init_db, search_listings, get_listing_by_id,
    get_listing_images, get_price_history, save_user, save_lead,
    get_full_stats, get_conn,
)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("RESALE_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID       = 353806371
LEAD_BOT_URL   = "https://t.me/dubai_fpr_lead_bot"
LEAD_BOT_TOKEN = os.environ.get("LEAD_BOT_TOKEN", "REDACTED_LEAD_BOT_TOKEN")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
API            = f"https://api.telegram.org/bot{BOT_TOKEN}"
PER_PAGE       = 10

# ── Logo ─────────────────────────────────────────────────────────────────────
_LOGO_FILE_ID_PATH = os.path.join(os.path.dirname(__file__), "logo_file_id.txt")
_LOGO_JPG_PATH     = os.path.join(os.path.dirname(__file__), "logo.jpg")
_logo_file_id: str = ""  # cached in memory after first load


def _load_logo_file_id() -> str:
    """Read logo file_id from DB (_auth_kv table)."""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT v FROM _auth_kv WHERE k='logo_file_id'")
            row = cur.fetchone()
        conn.close()
        return row["v"] if row and row["v"] else ""
    except Exception as e:
        print(f"[logo] read error: {e}")
    return ""


def _save_logo_file_id(fid: str):
    """Persist file_id to DB (_auth_kv table)."""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO _auth_kv(k,v) VALUES('logo_file_id',%s) "
                "ON CONFLICT(k) DO UPDATE SET v=%s",
                (fid, fid)
            )
        conn.commit()
        conn.close()
        print(f"[logo] Saved file_id to DB: {fid[:30]}...")
    except Exception as e:
        print(f"[logo] save error: {e}")


def _upload_logo_once() -> str:
    """
    Upload logo.jpg to Telegram once, return file_id.
    Sends to ADMIN_ID chat as a silent message.
    """
    global _logo_file_id
    if not os.path.exists(_LOGO_JPG_PATH):
        print("[logo] logo.jpg not found — skipping upload")
        return ""
    if not BOT_TOKEN:
        return ""
    try:
        with open(_LOGO_JPG_PATH, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                files={"photo": ("logo.jpg", f, "image/jpeg")},
                data={"chat_id": str(ADMIN_ID), "disable_notification": "true",
                      "caption": "✅ Logo uploaded"},
                timeout=30,
            )
        data = resp.json()
        if data.get("ok"):
            fid = data["result"]["photo"][-1]["file_id"]
            _logo_file_id = fid
            _save_logo_file_id(fid)
            print(f"[logo] Uploaded successfully, file_id={fid[:30]}...")
            return fid
        else:
            print(f"[logo] Upload failed: {data.get('description')}")
    except Exception as e:
        print(f"[logo] Upload error: {e}")
    return ""


def get_logo_file_id() -> str:
    """Return logo file_id (from cache, disk, or upload)."""
    global _logo_file_id
    if _logo_file_id:
        return _logo_file_id
    # Try disk
    fid = _load_logo_file_id()
    if fid:
        _logo_file_id = fid
        return fid
    # Upload once
    return _upload_logo_once()


def send_welcome_with_logo(cid: int, uid: int):
    """
    Send welcome message with logo on top + language selector in bottom bar.
    Photo+caption with reply keyboard attached directly — single message,
    keyboard always shows. (Раньше was 2 messages с zero-width-space, и иногда
    Telegram не показывал клавиатуру если 2-е сообщение «пустое».)
    """
    welcome_text = _t(uid, "welcome")
    fid = get_logo_file_id()
    lang_kb = kb_lang_reply()

    if fid:
        try:
            resp = _api("sendPhoto",
                        chat_id=cid,
                        photo=fid,
                        caption=welcome_text[:1024],
                        parse_mode="Markdown",
                        reply_markup=lang_kb)
            if resp.get("ok"):
                print(f"[logo] Welcome sent with logo to {cid}")
                return
            print(f"[logo] sendPhoto failed: {resp.get('description')} — fallback to text")
        except Exception as e:
            print(f"[logo] sendPhoto error: {e} — fallback to text")

    # Fallback — text only with keyboard
    _send(cid, welcome_text, kb=lang_kb)

# ── Translations ──────────────────────────────────────────────────────────────
T = {
"en": {
    "welcome": (
        "𝗗𝘂𝗯𝗮𝗶 𝗥𝗲𝗮𝗹 𝗘𝘀𝘁𝗮𝘁𝗲 𝗜𝗻𝘁𝗲𝗹𝗹𝗶𝗴𝗲𝗻𝗰𝗲\n"
        "Your private UAE real estate advisor.\n"
        "10,000+ verified listings · ROI · Hot deals\n\n"
        "Select your language ⬇️"
    ),
    "lang_set": "English selected",
    "main_menu": "────────────────────\n  UAE PROPERTY SEARCH\n────────────────────",
    "btn_ai":       "✦  AI Property Advisor",
    "btn_filter":   "Search by Filters",
    "btn_hot":      "Hot Deals",
    "btn_new":      "New Listings",
    "btn_budget":   "Search by Budget",
    "btn_area":     "Search by Area",
    "btn_building": "Search by Building",
    "btn_lang":     "Language",
    "btn_add":      "List My Property",
    # Bottom reply-keyboard (persistent main menu) — by deal_type categories
    "rbtn_buy":         "🏠 Buy",
    "rbtn_rent":        "🔑 Rent",
    "rbtn_commercial":  "🏢 Commercial",
    "rbtn_plot":        "🌱 Land",
    "rbtn_hot":         "🔥 Hot Deals",
    "rbtn_new":         "🆕 New",
    "rbtn_ai":          "✦ AI Assistant",
    "rbtn_add":         "➕ List Property",
    "rbtn_lang":        "🌐 Language",
    "rbtn_home":        "🏠 Main Menu",
    # Results navigation (bottom bar)
    "rbtn_more":        "▶ Show more",
    "rbtn_change_deal": "← Transaction Type",
    "rbtn_back":        "← Back",
    # Emirate wizard buttons
    "em_dubai":     "🇦🇪 Dubai",
    "em_abudhabi":  "🏛 Abu Dhabi",
    "em_rak":       "🌴 Ras Al Khaimah",
    "em_sharjah":   "⛵ Sharjah",
    "em_any":       "🌍 All UAE",
    # Property type buttons
    "pt_apt_btn":     "🏢 Apartment",
    "pt_villa_btn":   "🏖 Villa",
    "pt_town_btn":    "🏘 Townhouse",
    "pt_pent_btn":    "👑 Penthouse",
    "pt_studio_btn":  "✨ Studio",
    "pt_duplex_btn":  "🔷 Duplex",
    "pt_office_btn":  "🏢 Office",
    "pt_retail_btn":  "🛍 Retail",
    "pt_warehouse_btn":"📦 Warehouse",
    "pt_hotel_btn":   "🏨 Hotel",
    "pt_building_btn":"🏗 Whole Building",
    "pt_any_btn":     "🔍 Any type",
    # Bedroom buttons
    "br_studio_btn":  "✨ Studio",
    "br_1_btn":       "🛏 1 BR",
    "br_2_btn":       "🛏 2 BR",
    "br_3_btn":       "🛏 3 BR",
    "br_4p_btn":      "🛏 4+ BR",
    "br_any_btn":     "🔍 Any",
    # Budget any
    "b_any_btn":      "💰 Any budget",
    # Listing card labels
    "card_price_request": "Price on request",
    "card_per_year":   "/year",
    "card_for_rent":   "For Rent",
    "card_for_sale":   "For Sale",
    "card_below_mkt":  "below market",
    "card_below_op":   "below original price",
    "card_roi":        "ROI",
    "card_per_year_short": "/yr",
    "card_score":      "Investment score",
    "card_floor":      "Floor",
    "card_bua":        "BUA",
    "card_plot":       "Plot",
    "card_completion": "Handover",
    "card_developer":  "Developer",
    "ft_studio":       "Studio",
    "ft_br":           "BR",
    "ft_bath":         "BA",
    "emirate_q":  "Select Emirate",
    "e_dubai":    "Dubai",
    "e_abudhabi": "Abu Dhabi",
    "e_rak":      "Ras Al Khaimah",
    "e_sharjah":  "Sharjah",
    "e_any":      "All UAE",
    "deal_q":  "Transaction Type",
    "d_sale":  "For Sale",
    "d_rent":  "For Rent",
    "d_any":   "Any",
    "prop_q":   "Property Type",
    "pt_apt":   "Apartment",
    "pt_villa": "Villa",
    "pt_town":  "Townhouse",
    "pt_pent":  "Penthouse",
    "pt_any":   "Any Type",
    "budget_q": "Select Budget",
    "b_u1":  "Under 1M AED",
    "b_12":  "1M – 2M AED",
    "b_25":  "2M – 5M AED",
    "b_5p":  "5M+ AED",
    "b_any": "Any Budget",
    "rent_budget_q": "Annual Rent Budget",
    "rb_u100": "Under 100K AED/yr",
    "rb_100200": "100K – 200K AED/yr",
    "rb_200p": "200K+ AED/yr",
    "br_q":     "Bedrooms",
    "br_studio":"Studio",
    "br_1":     "1 BR",
    "br_2":     "2 BR",
    "br_3":     "3 BR",
    "br_4p":    "4 BR+",
    "br_any":   "Any",
    "area_q":     "Select Area",
    "bld_q":      "Type building name",
    "search_hint":"Or type any request in chat",
    "no_results": "────────────────────\n  No properties found\n────────────────────\nTry adjusting your filters.",
    "btn_more":    "Show more  ·  {n} remaining",
    "btn_back":    "← Back",
    "btn_menu":    "Main Menu",
    "btn_book":    "Request Consultation",
    "btn_similar": "Similar Properties",
    "btn_analysis":"Investment Analysis",
    "btn_send":    "Send to Client",
    "btn_all_in_bld": "🏢 All units in this building",
    "btn_fav_add":   "❤️ Save",
    "btn_fav_rem":   "💔 Remove",
    "btn_map":       "🗺 Map",
    "btn_compare":   "⚖️ Compare",
    "btn_photos":    "📸 All photos",
    "btn_watch_add": "⭐ Watch",
    "btn_watch_rem": "⭐ Stop watching",
    "rbtn_favs":     "❤️ Saved",
    "rbtn_alerts":   "🔔 Alerts",
    "watch_added":     "⭐ Added to your watchlist. /watch — view list.",
    "watch_removed":   "Removed from watchlist.",
    "watch_empty":     "Your watchlist is empty. Tap ⭐ on any listing to start watching.",
    "watch_title":     "──── ⭐ YOUR WATCHLIST ────",
    "rbtn_compare":  "⚖️ Compare ({n})",
    "favs_empty":    "No saved properties yet. Tap ❤️ on any listing.",
    "favs_title":    "──── ❤️ SAVED PROPERTIES ────",
    "alerts_empty":  "No active alerts.\nRun a search → tap «Create alert» on results.",
    "alerts_title":  "──── 🔔 PRICE ALERTS ────",
    "alert_created": "✅ Alert created. We'll notify you about new matches.",
    "alert_deleted": "Alert removed.",
    "rbtn_create_alert": "🔔 Create alert",
    "compare_empty": "Cart is empty. Tap ⚖️ on listings to compare.",
    "compare_added": "✅ Added to compare ({n}/3).",
    "compare_full":  "⚠ Compare cart full (3). Open ⚖️ Compare to clear.",
    "compare_title": "──── ⚖️ COMPARISON ────",
    "compare_clear": "🗑 Clear",
    "ai_start": "────────────────────\n  AI PROPERTY ADVISOR\n────────────────────\n\nI'll find the perfect property\nbased on your goals.\n\nLet's begin:",
    "ai_goal_q":    "What is your goal?",
    "ai_invest":    "💼 Investment",
    "ai_live":      "🏠 To Live In",
    "ai_holiday":   "🏖 Holiday Home",
    "ai_unsure":    "🤔 Not Sure Yet",
    "ai_commercial":"🏢 Commercial",
    "ai_land":      "🌱 Land / Plot",
    "ai_commercial_q": "What type of commercial property?",
    "ai_inv_q":       "Investment Strategy",
    "ai_inv_longterm":"Long-term Rental",
    "ai_inv_airbnb":  "Short-term · Airbnb",
    "ai_inv_resale":  "Resale · Flip",
    "ai_inv_growth":  "Capital Growth",
    "ai_life_q":       "Preferred Lifestyle",
    "ai_l_downtown":   "City Centre",
    "ai_l_sea":        "By the Sea",
    "ai_l_family":     "Family Area",
    "ai_l_premium":    "Premium Lifestyle",
    "ai_l_nature":     "Nature & Quiet",
    "ai_l_business":   "Near Business Hubs",
    "ai_analyzing": "────────────────────\nAnalyzing market data\nFinding best matches...\n────────────────────",
    "ai_result": "────────────────────\n  AI RECOMMENDATIONS\n────────────────────\n\nBased on your goals:",
    "searching": "Searching...",
    "contact_sent": "────────────────────\nRequest sent\n\nVadim will contact you\nshortly.\n────────────────────",
    # Add listing wizard
    "add_start": "────────────────────\n  LIST YOUR PROPERTY\n────────────────────\n\nLet's add your property\nstep by step.",
    "add_deal_q": "Sale or Rent?",
    "add_emirate_q": "Select Emirate",
    "add_area_q": "Select Area",
    "add_building_q": "Building name\n(type in chat)",
    "add_type_q": "Property Type",
    "add_br_q": "Bedrooms",
    "add_size_q": "Size in sqft\n(type in chat, or Skip)",
    "add_floor_q": "Floor number\n(type a number, or Skip)\nExample: 12",
    "add_unit_q": "Unit number\n(type in chat, or Skip)\nExample: 1206",
    "add_description_q": "Additional details / description\n(type any free text, or Skip)\n\nE.g. parking, balcony, school nearby, renovated, fitted office, freehold plot, etc.",
    "add_price_q": "Price in AED\n(type in chat)\nExample: 1500000 or 1.5M",
    "add_status_q": "Status",
    "add_status_vacant": "Vacant",
    "add_status_rented": "Rented",
    "add_furn_q": "Furnishing",
    "add_furn_yes": "Furnished",
    "add_furn_no": "Unfurnished",
    "add_furn_semi": "Semi-furnished",
    "add_view_q": "View",
    "add_contact_q": "Your contact\n(phone or @username)",
    "add_photo_q": "Send photos\n(up to 10)\nOr tap Skip",
    "add_skip": "Skip",
    "add_done": "────────────────────\n✅ Listing submitted!\n\nWe'll review and add it\nwithin 24 hours.\n────────────────────",
    "add_cancelled": "Cancelled.",
    "add_cancel": "Cancel",
    "moderation_new": "🏠 NEW LISTING FOR REVIEW\n\n",
    "mod_approve": "✅ Approve",
    "mod_reject": "❌ Reject",
    "mod_approved": "✅ Approved — added to database",
    "mod_rejected": "❌ Rejected",
    "stats_title":       "ADMIN STATISTICS",
    "stats_total":       "Total listings",
    "stats_hot":         "Hot deals",
    "stats_review":      "Needs review",
    "stats_pending":     "Pending moderate",
    "stats_by_emirate":  "BY EMIRATE",
    "stats_by_quality":  "BY QUALITY",
    "stats_today":       "TODAY",
    "stats_new":         "New listings",
    "stats_dupes":       "Duplicates",
    "stats_today_hot":   "Hot deals",
    "stats_users":       "USERS",
    "stats_total_users": "Total users",
    "stats_active":      "Active today",
    "stats_searches":    "Searches today",
    "stats_views":       "Views today",
    "stats_leads_today": "Leads today",
    "stats_leads_week":  "Leads this week",
    "stats_last_sync":   "Last sync",
    "area_custom_btn":    "✏️  Enter area name",
    "area_custom_q":      "Type the area or district name:",
    "wiz_area_q":         "🏙 *Choose location*\n\nType area name (full or abbreviation: Marina, JVC, DT, Palm, JBR…)\nor tap «Any location» to skip.",
    "wiz_area_any":       "🌍 Any location",
    "wiz_area_match":     "Matching areas — tap one:",
    "wiz_area_nomatch":   "_No area matched «{q}». Try shorter spelling or tap «Any location»._",
    "wiz_bld_q":          "🏢 *Choose building*\n\nType building name (full or part: Burj, Aykon, Sobha One, Sidra…)\nor tap «Any building» to skip.",
    "wiz_bld_any":        "🏢 Any building",
    "wiz_bld_match":      "Matching buildings — tap one:",
    "wiz_bld_nomatch":    "_No building matched «{q}». Try shorter spelling or tap «Any building»._",
    "area_custom_none":   "No properties found for \"{text}\". Try a different area name.",
    "add_area_custom_btn": "✏️  Enter custom area",
    "add_area_custom_q":   "Type your area name:",
    "deal_type_q":        "What are you looking for?",
    "d_any_deal":         "✨  All — Sale & Rent",
    "stats_by_deal":      "BY DEAL TYPE",
    "stats_sale_cnt":     "For Sale",
    "stats_rent_cnt":     "For Rent",
    "stats_sale_avg":     "Avg sale price",
    "stats_rent_avg":     "Avg rent/year",
},
"ru": {
    "welcome": (
        "𝗡𝗘𝗗𝗩𝗜𝗭𝗛𝗜𝗠𝗢𝗦𝗧 𝗢𝗔𝗘\n"
        "────────────────────\n"
        "Ваш личный советник\n"
        "по рынку недвижимости ОАЭ.\n\n"
        "10 000+ проверенных объектов\n"
        "Инвестиционный анализ · ROI · Аренда\n"
        "Горячие предложения ниже рынка\n\n"
        "Дубай · Абу-Даби · РАК · Шарджа\n"
        "────────────────────\n"
        "Выберите язык"
    ),
    "lang_set": "Язык: Русский",
    "main_menu": "────────────────────\n  ПОИСК НЕДВИЖИМОСТИ\n────────────────────",
    "btn_ai":       "✦  AI Подбор объекта",
    "btn_filter":   "Поиск по фильтрам",
    "btn_hot":      "Горячие предложения",
    "btn_new":      "Новые объявления",
    "btn_budget":   "Поиск по бюджету",
    "btn_area":     "Поиск по району",
    "btn_building": "Поиск по зданию",
    "btn_lang":     "Язык",
    "btn_add":      "Разместить объект",
    # Bottom reply-keyboard (persistent main menu) — by deal_type categories
    "rbtn_buy":         "🏠 Купить",
    "rbtn_rent":        "🔑 Снять",
    "rbtn_commercial":  "🏢 Коммерция",
    "rbtn_plot":        "🌱 Земля",
    "rbtn_hot":         "🔥 Горячие",
    "rbtn_new":         "🆕 Новые",
    "rbtn_ai":          "✦ AI Помощник",
    "rbtn_add":         "➕ Разместить",
    "rbtn_lang":        "🌐 Язык",
    "rbtn_home":        "🏠 Главное меню",
    # Results navigation (bottom bar)
    "rbtn_more":        "▶ Показать ещё",
    "rbtn_change_deal": "← Тип сделки",
    "rbtn_back":        "← Назад",
    # Emirate wizard buttons
    "em_dubai":     "🇦🇪 Дубай",
    "em_abudhabi":  "🏛 Абу-Даби",
    "em_rak":       "🌴 Рас-эль-Хайма",
    "em_sharjah":   "⛵ Шарджа",
    "em_any":       "🌍 Все ОАЭ",
    # Property type buttons
    "pt_apt_btn":     "🏢 Апартаменты",
    "pt_villa_btn":   "🏖 Вилла",
    "pt_town_btn":    "🏘 Таунхаус",
    "pt_pent_btn":    "👑 Пентхаус",
    "pt_studio_btn":  "✨ Студия",
    "pt_duplex_btn":  "🔷 Дуплекс",
    "pt_office_btn":  "🏢 Офис",
    "pt_retail_btn":  "🛍 Ритейл",
    "pt_warehouse_btn":"📦 Склад",
    "pt_hotel_btn":   "🏨 Отель",
    "pt_building_btn":"🏗 Целое здание",
    "pt_any_btn":     "🔍 Любой тип",
    # Bedroom buttons
    "br_studio_btn":  "✨ Студия",
    "br_1_btn":       "🛏 1 спальня",
    "br_2_btn":       "🛏 2 спальни",
    "br_3_btn":       "🛏 3 спальни",
    "br_4p_btn":      "🛏 4+ спален",
    "br_any_btn":     "🔍 Любое",
    # Budget any
    "b_any_btn":      "💰 Любой бюджет",
    # Listing card labels
    "card_price_request": "Цена по запросу",
    "card_per_year":   "/год",
    "card_for_rent":   "В аренду",
    "card_for_sale":   "В продажу",
    "card_below_mkt":  "ниже рынка",
    "card_below_op":   "ниже исходной цены",
    "card_roi":        "ROI",
    "card_per_year_short": "/год",
    "card_score":      "Инвест-оценка",
    "card_floor":      "Этаж",
    "card_bua":        "BUA",
    "card_plot":       "Участок",
    "card_completion": "Сдача",
    "card_developer":  "Застройщик",
    "ft_studio":       "Студия",
    "ft_br":           "сп.",
    "ft_bath":         "вс.",
    "emirate_q":  "Выберите эмират",
    "e_dubai":    "Дубай",
    "e_abudhabi": "Абу-Даби",
    "e_rak":      "Рас-эль-Хайма",
    "e_sharjah":  "Шарджа",
    "e_any":      "Все ОАЭ",
    "deal_q":  "Тип сделки",
    "d_sale":  "Продажа",
    "d_rent":  "Аренда",
    "d_any":   "Любой",
    "prop_q":   "Тип недвижимости",
    "pt_apt":   "Апартаменты",
    "pt_villa": "Вилла",
    "pt_town":  "Таунхаус",
    "pt_pent":  "Пентхаус",
    "pt_any":   "Любой тип",
    "budget_q": "Выберите бюджет",
    "b_u1":  "До 1M AED",
    "b_12":  "1M – 2M AED",
    "b_25":  "2M – 5M AED",
    "b_5p":  "5M+ AED",
    "b_any": "Любой бюджет",
    "rent_budget_q": "Бюджет аренды в год",
    "rb_u100": "До 100K AED/год",
    "rb_100200": "100K – 200K AED/год",
    "rb_200p": "200K+ AED/год",
    "br_q":     "Количество спален",
    "br_studio":"Студия",
    "br_1":     "1 спальня",
    "br_2":     "2 спальни",
    "br_3":     "3 спальни",
    "br_4p":    "4+",
    "br_any":   "Любое",
    "area_q":     "Выберите район",
    "bld_q":      "Введите название здания",
    "search_hint":"Или напишите запрос в чате",
    "no_results": "────────────────────\n  Объектов не найдено\n────────────────────\nПопробуйте изменить фильтры.",
    "btn_more":    "Показать ещё  ·  {n} осталось",
    "btn_back":    "← Назад",
    "btn_menu":    "Главное меню",
    "btn_book":    "Оставить заявку",
    "btn_similar": "Похожие объекты",
    "btn_analysis":"Инвестиционный анализ",
    "btn_send":    "Отправить клиенту",
    "btn_all_in_bld": "🏢 Все объекты в этом доме",
    "btn_fav_add":   "❤️ В избранное",
    "btn_fav_rem":   "💔 Убрать",
    "btn_map":       "🗺 На карте",
    "btn_compare":   "⚖️ Сравнить",
    "btn_photos":    "📸 Все фото",
    "btn_watch_add": "⭐ Следить",
    "btn_watch_rem": "⭐ Не следить",
    "rbtn_favs":     "❤️ Избранное",
    "rbtn_alerts":   "🔔 Уведомления",
    "watch_added":     "⭐ Добавлено в watchlist. /watch — список подписок.",
    "watch_removed":   "Удалено из watchlist.",
    "watch_empty":     "Watchlist пуст. Нажмите ⭐ на карточке, чтобы начать следить.",
    "watch_title":     "──── ⭐ ВАШ WATCHLIST ────",
    "rbtn_compare":  "⚖️ Сравнить ({n})",
    "favs_empty":    "Список избранного пуст. Нажмите ❤️ на любом объявлении.",
    "favs_title":    "──── ❤️ ИЗБРАННОЕ ────",
    "alerts_empty":  "Активных уведомлений нет.\nЗапустите поиск → нажмите «Создать уведомление».",
    "alerts_title":  "──── 🔔 УВЕДОМЛЕНИЯ О ЦЕНАХ ────",
    "alert_created": "✅ Уведомление создано. Сообщим о новых подходящих объектах.",
    "alert_deleted": "Уведомление удалено.",
    "rbtn_create_alert": "🔔 Создать уведомление",
    "compare_empty": "Корзина пуста. Нажмите ⚖️ на объявлении.",
    "compare_added": "✅ Добавлено в сравнение ({n}/3).",
    "compare_full":  "⚠ В сравнении уже 3 объекта. Очистите чтобы добавить.",
    "compare_title": "──── ⚖️ СРАВНЕНИЕ ────",
    "compare_clear": "🗑 Очистить",
    "ai_start": "────────────────────\n  AI ПОДБОР ОБЪЕКТА\n────────────────────\n\nНайду идеальный объект\nпод ваши цели.\n\nНачнём:",
    "ai_goal_q":    "Цель покупки?",
    "ai_invest":    "💼 Инвестиция",
    "ai_live":      "🏠 Для жизни",
    "ai_holiday":   "🏖 Для отдыха",
    "ai_unsure":    "🤔 Не уверен",
    "ai_commercial":"🏢 Коммерция",
    "ai_land":      "🌱 Земля / Участок",
    "ai_commercial_q": "Какой тип коммерческой недвижимости?",
    "ai_inv_q":       "Стратегия инвестиций",
    "ai_inv_longterm":"Долгосрочная аренда",
    "ai_inv_airbnb":  "Краткосрочная · Airbnb",
    "ai_inv_resale":  "Перепродажа",
    "ai_inv_growth":  "Рост капитала",
    "ai_life_q":       "Образ жизни",
    "ai_l_downtown":   "Центр города",
    "ai_l_sea":        "У моря",
    "ai_l_family":     "Семейный район",
    "ai_l_premium":    "Премиальный lifestyle",
    "ai_l_nature":     "Природа и тишина",
    "ai_l_business":   "Рядом с бизнесом",
    "ai_analyzing": "────────────────────\nАнализирую рынок\nПодбираю лучшие варианты...\n────────────────────",
    "ai_result": "────────────────────\n  AI РЕКОМЕНДАЦИИ\n────────────────────\n\nПо вашим критериям:",
    "searching": "Поиск...",
    "contact_sent": "────────────────────\nЗаявка отправлена\n\nВадим свяжется с вами\nв ближайшее время.\n────────────────────",
    "add_start": "────────────────────\n  РАЗМЕСТИТЬ ОБЪЕКТ\n────────────────────\n\nДобавим ваш объект\nшаг за шагом.",
    "add_deal_q": "Продажа или аренда?",
    "add_emirate_q": "Выберите эмират",
    "add_area_q": "Выберите район",
    "add_building_q": "Название здания\n(напишите в чате)",
    "add_type_q": "Тип недвижимости",
    "add_br_q": "Количество спален",
    "add_size_q": "Площадь в кв. футах\n(напишите в чате или Пропустить)\nПример: 642",
    "add_floor_q": "Этаж\n(число или Пропустить)\nПример: 12",
    "add_unit_q": "Номер юнита\n(напишите в чате или Пропустить)\nПример: 1206",
    "add_description_q": "Дополнительная информация / описание\n(любой свободный текст или Пропустить)\n\nНапример: парковка, балкон, школа рядом, ремонт, оборудованный офис, фрихолд, и т.д.",
    "add_price_q": "Цена в AED\n(напишите в чате)\nПример: 1500000 или 1.5M",
    "add_status_q": "Статус",
    "add_status_vacant": "Свободно",
    "add_status_rented": "Сдано",
    "add_furn_q": "Меблировка",
    "add_furn_yes": "Меблировано",
    "add_furn_no": "Без мебели",
    "add_furn_semi": "Частично",
    "add_view_q": "Вид из окна",
    "add_contact_q": "Ваш контакт\n(телефон или @username)",
    "add_photo_q": "Отправьте фото\n(до 10 штук)\nИли нажмите Пропустить",
    "add_skip": "Пропустить",
    "add_done": "────────────────────\n✅ Объект отправлен!\n\nМы проверим и добавим\nв течение 24 часов.\n────────────────────",
    "add_cancelled": "Отменено.",
    "add_cancel": "Отмена",
    "moderation_new": "🏠 НОВЫЙ ОБЪЕКТ НА МОДЕРАЦИЮ\n\n",
    "mod_approve": "✅ Одобрить",
    "mod_reject": "❌ Отклонить",
    "mod_approved": "✅ Одобрено — добавлено в базу",
    "mod_rejected": "❌ Отклонено",
    "stats_title":       "СТАТИСТИКА АДМИНА",
    "stats_total":       "Всего объектов",
    "stats_hot":         "Горячих сделок",
    "stats_review":      "Требует проверки",
    "stats_pending":     "На модерации",
    "stats_by_emirate":  "ПО ЭМИРАТУ",
    "stats_by_quality":  "ПО КАЧЕСТВУ",
    "stats_today":       "СЕГОДНЯ",
    "stats_new":         "Новых объектов",
    "stats_dupes":       "Дубликатов",
    "stats_today_hot":   "Горячих сделок",
    "stats_users":       "ПОЛЬЗОВАТЕЛИ",
    "stats_total_users": "Всего пользователей",
    "stats_active":      "Активных сегодня",
    "stats_searches":    "Поисков сегодня",
    "area_custom_btn":    "✏️  Ввести свой район",
    "area_custom_q":      "Напишите название района или района:",
    "wiz_area_q":         "🏙 *Выберите район*\n\nНапишите название (полностью или сокращение: Marina, JVC, DT, Palm, JBR, Бизнес-Бей…)\nили нажмите «Любой район» чтобы пропустить.",
    "wiz_area_any":       "🌍 Любой район",
    "wiz_area_match":     "Подходящие районы — нажмите:",
    "wiz_area_nomatch":   "_Район «{q}» не найден. Попробуйте сократить или нажмите «Любой район»._",
    "wiz_bld_q":          "🏢 *Выберите здание*\n\nНапишите название (полностью или часть: Burj, Aykon, Sobha One, Sidra…)\nили нажмите «Любое здание» чтобы пропустить.",
    "wiz_bld_any":        "🏢 Любое здание",
    "wiz_bld_match":      "Подходящие здания — нажмите:",
    "wiz_bld_nomatch":    "_Здание «{q}» не найдено. Попробуйте сократить или нажмите «Любое здание»._",
    "area_custom_none":   "По запросу \"{text}\" ничего не найдено. Попробуйте другое название.",
    "add_area_custom_btn": "✏️  Ввести свой район",
    "add_area_custom_q":   "Напишите название района:",
    "stats_views":       "Просмотров сегодня",
    "stats_leads_today": "Лидов сегодня",
    "stats_leads_week":  "Лидов за неделю",
    "stats_last_sync":   "Последняя синхронизация",
    "deal_type_q":        "Что вас интересует?",
    "d_any_deal":         "✨  Всё — Продажа и Аренда",
    "stats_by_deal":      "ТИП СДЕЛКИ",
    "stats_sale_cnt":     "Продажа",
    "stats_rent_cnt":     "Аренда",
    "stats_sale_avg":     "Ср. цена продажи",
    "stats_rent_avg":     "Ср. аренда/год",
},
"ar": {
    "welcome": (
        "𝗘𝗤𝗔𝗥𝗔𝗧 𝗔𝗟-𝗜𝗠𝗔𝗥𝗔𝗧\n"
        "────────────────────\n"
        "مستشارك العقاري الخاص\n"
        "لسوق الإمارات.\n\n"
        "+10,000 عقار موثق\n"
        "تحليل استثماري · عائد الإيجار\n"
        "أفضل الصفقات\n\n"
        "دبي · أبوظبي · رأس الخيمة · الشارقة\n"
        "────────────────────\n"
        "اختر لغتك"
    ),
    "lang_set": "اللغة: العربية",
    "main_menu": "────────────────────\n  بحث العقارات\n────────────────────",
    "btn_ai":       "✦  مستشار AI العقاري",
    "btn_filter":   "البحث بالفلاتر",
    "btn_hot":      "أفضل الصفقات",
    "btn_new":      "الإعلانات الجديدة",
    "btn_budget":   "البحث بالميزانية",
    "btn_area":     "البحث بالمنطقة",
    "btn_building": "البحث بالمبنى",
    "btn_lang":     "اللغة",
    "btn_add":      "إضافة عقار",
    # Admin statistics (Arabic translations)
    "stats_title":       "إحصائيات الإدارة",
    "stats_total":       "إجمالي العقارات",
    "stats_hot":         "صفقات ساخنة",
    "stats_review":      "بحاجة للمراجعة",
    "stats_pending":     "قيد الاعتماد",
    "stats_by_emirate":  "حسب الإمارة",
    "stats_by_quality":  "حسب الجودة",
    "stats_today":       "اليوم",
    "stats_new":         "إعلانات جديدة",
    "stats_dupes":       "نسخ مكررة",
    "stats_today_hot":   "صفقات ساخنة",
    "stats_users":       "المستخدمون",
    "stats_total_users": "إجمالي المستخدمين",
    "stats_active":      "نشطون اليوم",
    "stats_searches":    "بحوث اليوم",
    "stats_views":       "مشاهدات اليوم",
    "stats_leads_today": "طلبات اليوم",
    "stats_leads_week":  "طلبات الأسبوع",
    "stats_last_sync":   "آخر مزامنة",
    # Bottom reply-keyboard (persistent main menu) — by deal_type categories
    "rbtn_buy":         "🏠 شراء",
    "rbtn_rent":        "🔑 إيجار",
    "rbtn_commercial":  "🏢 تجاري",
    "rbtn_plot":        "🌱 أرض",
    "rbtn_hot":         "🔥 صفقات",
    "rbtn_new":         "🆕 جديد",
    "rbtn_ai":          "✦ مساعد AI",
    "rbtn_add":         "➕ إضافة عقار",
    "rbtn_lang":        "🌐 اللغة",
    "rbtn_home":        "🏠 القائمة الرئيسية",
    # Results navigation (bottom bar)
    "rbtn_more":        "▶ عرض المزيد",
    "rbtn_change_deal": "← نوع الصفقة",
    "rbtn_back":        "← رجوع",
    # Emirate wizard buttons
    "em_dubai":     "🇦🇪 دبي",
    "em_abudhabi":  "🏛 أبوظبي",
    "em_rak":       "🌴 رأس الخيمة",
    "em_sharjah":   "⛵ الشارقة",
    "em_any":       "🌍 جميع الإمارات",
    # Property type buttons
    "pt_apt_btn":     "🏢 شقة",
    "pt_villa_btn":   "🏖 فيلا",
    "pt_town_btn":    "🏘 تاون هاوس",
    "pt_pent_btn":    "👑 بنتهاوس",
    "pt_studio_btn":  "✨ استوديو",
    "pt_duplex_btn":  "🔷 دوبلكس",
    "pt_office_btn":  "🏢 مكتب",
    "pt_retail_btn":  "🛍 ريتيل",
    "pt_warehouse_btn":"📦 مستودع",
    "pt_hotel_btn":   "🏨 فندق",
    "pt_building_btn":"🏗 مبنى كامل",
    "pt_any_btn":     "🔍 أي نوع",
    # Bedroom buttons
    "br_studio_btn":  "✨ استوديو",
    "br_1_btn":       "🛏 غرفة واحدة",
    "br_2_btn":       "🛏 غرفتان",
    "br_3_btn":       "🛏 3 غرف",
    "br_4p_btn":      "🛏 4+ غرف",
    "br_any_btn":     "🔍 أي عدد",
    # Budget any
    "b_any_btn":      "💰 أي ميزانية",
    # Listing card labels
    "card_price_request": "السعر عند الطلب",
    "card_per_year":   "/سنة",
    "card_for_rent":   "للإيجار",
    "card_for_sale":   "للبيع",
    "card_below_mkt":  "أقل من السوق",
    "card_below_op":   "أقل من السعر الأصلي",
    "card_roi":        "العائد",
    "card_per_year_short": "/سنة",
    "card_score":      "تقييم الاستثمار",
    "card_floor":      "الطابق",
    "card_bua":        "المساحة المبنية",
    "card_plot":       "الأرض",
    "card_completion": "التسليم",
    "card_developer":  "المطور",
    "ft_studio":       "استوديو",
    "ft_br":           "غرفة",
    "ft_bath":         "حمام",
    "emirate_q":  "اختر الإمارة",
    "e_dubai":    "دبي",
    "e_abudhabi": "أبوظبي",
    "e_rak":      "رأس الخيمة",
    "e_sharjah":  "الشارقة",
    "e_any":      "كل الإمارات",
    "deal_q":  "نوع الصفقة",
    "d_sale":  "للبيع",
    "d_rent":  "للإيجار",
    "d_any":   "الكل",
    "prop_q":   "نوع العقار",
    "pt_apt":   "شقة",
    "pt_villa": "فيلا",
    "pt_town":  "تاون هاوس",
    "pt_pent":  "بنتهاوس",
    "pt_any":   "الكل",
    "budget_q": "الميزانية",
    "b_u1":  "أقل من 1M AED",
    "b_12":  "1M – 2M AED",
    "b_25":  "2M – 5M AED",
    "b_5p":  "5M+ AED",
    "b_any": "أي ميزانية",
    "rent_budget_q": "ميزانية الإيجار سنوياً",
    "rb_u100": "أقل من 100K AED/سنة",
    "rb_100200": "100K – 200K AED/سنة",
    "rb_200p": "200K+ AED/سنة",
    "br_q":     "عدد الغرف",
    "br_studio":"استوديو",
    "br_1":     "غرفة",
    "br_2":     "غرفتان",
    "br_3":     "3 غرف",
    "br_4p":    "4+",
    "br_any":   "الكل",
    "area_q":     "اختر المنطقة",
    "bld_q":      "اكتب اسم المبنى",
    "search_hint":"أو اكتب طلبك",
    "no_results": "────────────────────\n  لا توجد نتائج\n────────────────────\nجرب تغيير الفلاتر.",
    "btn_more":    "عرض المزيد  ·  {n} متبقي",
    "btn_back":    "← رجوع",
    "btn_menu":    "القائمة الرئيسية",
    "btn_book":    "طلب استشارة",
    "btn_similar": "عقارات مشابهة",
    "btn_analysis":"التحليل الاستثماري",
    "btn_send":    "إرسال للعميل",
    "btn_all_in_bld": "🏢 جميع الوحدات في هذا المبنى",
    "btn_fav_add":   "❤️ حفظ",
    "btn_fav_rem":   "💔 إزالة",
    "btn_map":       "🗺 الخريطة",
    "btn_compare":   "⚖️ مقارنة",
    "btn_photos":    "📸 كل الصور",
    "btn_watch_add": "⭐ متابعة",
    "btn_watch_rem": "⭐ إلغاء المتابعة",
    "rbtn_favs":     "❤️ المحفوظة",
    "rbtn_alerts":   "🔔 التنبيهات",
    "watch_added":     "⭐ تمت الإضافة إلى قائمة المتابعة. /watch — عرض القائمة.",
    "watch_removed":   "تمت إزالته من قائمة المتابعة.",
    "watch_empty":     "قائمة المتابعة فارغة. اضغط ⭐ على أي إعلان لبدء المتابعة.",
    "watch_title":     "──── ⭐ قائمة المتابعة ────",
    "rbtn_compare":  "⚖️ مقارنة ({n})",
    "favs_empty":    "لا توجد عقارات محفوظة. اضغط ❤️ على أي إعلان.",
    "favs_title":    "──── ❤️ المحفوظة ────",
    "alerts_empty":  "لا توجد تنبيهات نشطة.\nشغّل بحثاً ثم اضغط «إنشاء تنبيه».",
    "alerts_title":  "──── 🔔 تنبيهات الأسعار ────",
    "alert_created": "✅ تم إنشاء التنبيه.",
    "alert_deleted": "تم حذف التنبيه.",
    "rbtn_create_alert": "🔔 إنشاء تنبيه",
    "compare_empty": "السلة فارغة.",
    "compare_added": "✅ تم الإضافة ({n}/3).",
    "compare_full":  "⚠ السلة ممتلئة (3).",
    "compare_title": "──── ⚖️ مقارنة ────",
    "compare_clear": "🗑 مسح",
    "ai_start": "────────────────────\n  مستشار AI العقاري\n────────────────────\n\nسأجد العقار المثالي\nلأهدافك.\n\nلنبدأ:",
    "ai_goal_q":  "ما هدفك؟",
    "ai_invest":  "💼 استثمار",
    "ai_live":    "🏠 للسكن",
    "ai_holiday": "🏖 منزل إجازة",
    "ai_unsure":  "🤔 لست متأكداً",
    "ai_commercial":"🏢 تجاري",
    "ai_land":      "🌱 أرض / قطعة",
    "ai_commercial_q": "ما نوع العقار التجاري؟",
    "ai_inv_q":       "استراتيجية الاستثمار",
    "ai_inv_longterm":"إيجار طويل الأمد",
    "ai_inv_airbnb":  "إيجار قصير · Airbnb",
    "ai_inv_resale":  "إعادة البيع",
    "ai_inv_growth":  "نمو رأس المال",
    "ai_life_q":     "نمط الحياة",
    "ai_l_downtown": "وسط المدينة",
    "ai_l_sea":      "على البحر",
    "ai_l_family":   "منطقة عائلية",
    "ai_l_premium":  "نمط حياة مميز",
    "ai_l_nature":   "طبيعة وهدوء",
    "ai_l_business": "قرب مراكز الأعمال",
    "ai_analyzing": "────────────────────\nجاري تحليل السوق\nالبحث عن الأفضل...\n────────────────────",
    "ai_result": "────────────────────\n  توصيات AI\n────────────────────\n\nوفق معاييرك:",
    "searching": "جاري البحث...",
    "contact_sent": "────────────────────\nتم إرسال الطلب\n\nسيتواصل معك فاديم\nقريباً.\n────────────────────",
    "add_start": "────────────────────\n  إضافة عقار\n────────────────────\n\nدعنا نضيف عقارك\nخطوة بخطوة.",
    "add_deal_q": "بيع أم إيجار؟",
    "add_emirate_q": "اختر الإمارة",
    "add_area_q": "اختر المنطقة",
    "add_building_q": "اسم المبنى\n(اكتب في المحادثة)",
    "add_type_q": "نوع العقار",
    "add_br_q": "عدد الغرف",
    "add_size_q": "المساحة بالقدم المربع\n(اكتب أو تخطّى)",
    "add_floor_q": "رقم الطابق\n(اكتب رقم أو تخطّى)\nمثال: 12",
    "add_unit_q": "رقم الوحدة\n(اكتب أو تخطّى)\nمثال: 1206",
    "add_description_q": "تفاصيل إضافية / وصف\n(نص حر أو تخطّى)\n\nمثل: موقف، شرفة، مدرسة قريبة، مجدد، مكتب مجهز، الخ.",
    "add_price_q": "السعر بالدرهم",
    "add_status_q": "الحالة",
    "add_status_vacant": "شاغر",
    "add_status_rented": "مؤجر",
    "add_furn_q": "التأثيث",
    "add_furn_yes": "مفروش",
    "add_furn_no": "غير مفروش",
    "add_furn_semi": "نصف مفروش",
    "add_view_q": "الإطلالة",
    "add_contact_q": "معلومات التواصل",
    "add_photo_q": "أرسل الصور\n(حتى 10)\nأو اضغط تخطى",
    "add_skip": "تخطى",
    "add_done": "────────────────────\n✅ تم إرسال العقار!\n\nسنراجعه ونضيفه\nخلال 24 ساعة.\n────────────────────",
    "add_cancelled": "تم الإلغاء.",
    "add_cancel": "إلغاء",
    "moderation_new": "🏠 عقار جديد للمراجعة\n\n",
    "mod_approve": "✅ موافقة",
    "mod_reject": "❌ رفض",
    "mod_approved": "✅ تمت الموافقة",
    "mod_rejected": "❌ مرفوض",
    "area_custom_btn":    "✏️  أدخل اسم المنطقة",
    "area_custom_q":      "اكتب اسم المنطقة أو الحي:",
    "wiz_area_q":         "🏙 *اختر المنطقة*\n\nاكتب اسم المنطقة (كامل أو اختصار: Marina, JVC, DT, Palm…)\nأو اضغط «أي منطقة» لتخطي.",
    "wiz_area_any":       "🌍 أي منطقة",
    "wiz_area_match":     "المناطق المطابقة — اضغط:",
    "wiz_area_nomatch":   "_لم تتطابق منطقة مع «{q}»._",
    "wiz_bld_q":          "🏢 *اختر المبنى*\n\nاكتب اسم المبنى (كامل أو جزء: Burj, Aykon…)\nأو اضغط «أي مبنى» لتخطي.",
    "wiz_bld_any":        "🏢 أي مبنى",
    "wiz_bld_match":      "المباني المطابقة — اضغط:",
    "wiz_bld_nomatch":    "_لم يتطابق مبنى مع «{q}»._",
    "area_custom_none":   "لا نتائج لـ \"{text}\". جرب اسماً آخر.",
    "add_area_custom_btn": "✏️  أدخل منطقتك",
    "add_area_custom_q":   "اكتب اسم المنطقة:",
    "deal_type_q":        "ماذا تبحث عن؟",
    "d_any_deal":         "✨  الكل — بيع وإيجار",
    "stats_by_deal":      "نوع الصفقة",
    "stats_sale_cnt":     "للبيع",
    "stats_rent_cnt":     "للإيجار",
    "stats_sale_avg":     "متوسط سعر البيع",
    "stats_rent_avg":     "متوسط الإيجار/سنة",
},
}

# ── State ─────────────────────────────────────────────────────────────────────
user_states = {}
user_lang   = {}
# Add listing state
add_states  = {}
# Admin panel state
admin_states = {}   # uid → {queue, idx, edits, edit_field, edit_qid}

import threading
# v53: централизованный error logger → push в intel DB
try:
    import error_logger as _err_logger
except Exception:
    _err_logger = None

# Investment engine + PDF integration
try:
    from investment_engine import compute_investment_score, build_conclusion_text
    INVEST_OK = True
except ImportError:
    INVEST_OK = False
try:
    from pdf_report import generate_pdf
    PDF_OK = True
except ImportError:
    PDF_OK = False

# v51 PERF: module-level imports to avoid repeated import cost in hot path
try:
    from parser_engine import _lookup_benchmark, MARKET
    _PE_OK = True
except ImportError:
    _PE_OK = False
    _lookup_benchmark = lambda x: None
    MARKET = {}
try:
    from report_api import get_market_metrics, get_building_benchmark
    _RA_OK = True
except Exception:
    _RA_OK = False
    get_market_metrics = lambda x: None
    get_building_benchmark = lambda x: None
try:
    import antispam as _antispam_mod
except Exception:
    _antispam_mod = None


def _send_pdf(cid, uid, listing):
    """Generate investment PDF for a listing + send as document."""
    try:
        # v51 PERF: use module-level imports (was lazy in hot path)
        dld_b = _lookup_benchmark(listing.get("building")) if listing.get("building") else None
        mkt = None
        try:
            if listing.get("area"):
                mkt = get_market_metrics(listing["area"])
            if listing.get("building"):
                better_b = get_building_benchmark(listing["building"])
                if better_b:
                    dld_b = dict(dld_b or {})
                    for _k, _v in better_b.items():
                        if _v is not None:
                            dld_b[_k] = _v
        except Exception as _e:
            print(f"[ecosystem] report_api skip ({listing.get('id')}): {_e}")
        if mkt is None and listing.get("area"):
            mkt = MARKET.get(listing["area"])
        score = compute_investment_score(listing, dld_b, mkt)
        lang = _get_lang(uid)

        # v_pdf2: общий брендовый Vadim Realty PDF (10 страниц).
        pdf_path = None
        try:
            from vadim_pdf import generate_pdf_report
            payload = {
                "name": listing.get("building") or listing.get("area") or "Listing",
                "title": listing.get("building") or "Property",
                "area": listing.get("area"),
                "emirate": listing.get("emirate"),
                "avg_price": listing.get("price"),
                "price_per_m2": (listing.get("price_per_sqft") or 0) * 10.764 if listing.get("price_per_sqft") else None,
                "area_m2": (listing.get("size_sqft") or 0) * 0.0929 if listing.get("size_sqft") else None,
                "yield": (mkt or {}).get("yield") if mkt else None,
                "growth_yoy": (mkt or {}).get("growth_yoy") if mkt else None,
                "deals": (dld_b or {}).get("deals") if dld_b else None,
                "description": f"{listing.get('bedrooms','?')} BR · {listing.get('property_type','')} · score {score}",
                "signals": [
                    f"Investment score: {score}" if score else None,
                    f"ROI estimate: {listing.get('roi_estimate')}%" if listing.get('roi_estimate') else None,
                    f"Discount vs market: {listing.get('discount_percent')}%" if listing.get('discount_percent') else None,
                ],
            }
            payload["signals"] = [s for s in payload["signals"] if s]
            pdf_path = generate_pdf_report("listing", payload, lang=lang)
        except Exception as _e:
            print(f"[resale] vadim_pdf failed, fallback to legacy: {_e}")
            pdf_path = generate_pdf(listing, score, lang=lang)

        if not pdf_path or not os.path.exists(pdf_path):
            _send(cid, {"en":"PDF failed","ru":"PDF не создан","ar":"فشل"}.get(lang,"PDF failed"))
            return
        bld = (listing.get("building") or listing.get("area") or "Property")
        filename = f"investment-{listing.get('id', 'report')}.pdf"
        caption = {
            "en": f"📄 Investment Report — {bld}",
            "ru": f"📄 Инвест-отчёт — {bld}",
            "ar": f"📄 تقرير الاستثمار — {bld}",
        }.get(lang, bld)
        with open(pdf_path, "rb") as f:
            requests.post(
                f"{API}/sendDocument",
                data={"chat_id": cid, "caption": caption[:1000]},
                files={"document": (filename, f, "application/pdf")},
                timeout=60,
            )
        try: os.remove(pdf_path)
        except Exception: pass
    except Exception as e:
        print(f"[bot] _send_pdf: {e}")


def _get_lang(uid):
    return user_lang.get(uid, "en")


def _t(uid, key, **kw):
    lang = user_lang.get(uid, "en")
    txt  = T.get(lang, T["en"]).get(key, T["en"].get(key, key))
    return txt.format(**kw) if kw else txt

def gs(uid):
    if uid not in user_states:
        user_states[uid] = {"filters": {}, "results": [], "page": 0,
                            "ai_step": 0, "ai_data": {}, "waiting": None,
                            "default_deal": None}
    return user_states[uid]

def _reset(uid):
    dd = user_states.get(uid, {}).get("default_deal")  # preserve across resets
    user_states[uid] = {"filters": {}, "results": [], "page": 0,
                        "ai_step": 0, "ai_data": {}, "waiting": None,
                        "default_deal": dd}

# ── Telegram helpers ──────────────────────────────────────────────────────────
def _api(method, **kw):
    try:
        r = requests.post(f"{API}/{method}", json=kw, timeout=10)
        return r.json()
    except Exception as e:
        print(f"[bot] {method}: {e}")
        return {}

def _send(cid, text, kb=None):
    p = {"chat_id": cid, "text": text, "parse_mode": "Markdown"}
    if kb: p["reply_markup"] = kb
    return _api("sendMessage", **p)

def _edit(cid, mid, text, kb=None):
    p = {"chat_id": cid, "message_id": mid, "text": text, "parse_mode": "Markdown"}
    if kb: p["reply_markup"] = kb
    return _api("editMessageText", **p)

def _photo(cid, photo, caption, kb=None):
    p = {"chat_id": cid, "photo": photo, "caption": caption[:1024], "parse_mode": "Markdown"}
    if kb: p["reply_markup"] = kb
    return _api("sendPhoto", **p)

def _media_group(cid, file_ids: list, caption: str):
    """Send multiple photos as album."""
    if not file_ids:
        return
    media = []
    for i, fid in enumerate(file_ids[:10]):
        item = {"type": "photo", "media": fid}
        if i == 0:
            item["caption"] = caption[:1024]
            item["parse_mode"] = "Markdown"
        media.append(item)
    try:
        requests.post(f"{API}/sendMediaGroup",
                      json={"chat_id": cid, "media": media}, timeout=15)
    except Exception as e:
        print(f"[bot] media_group: {e}")

def _get_file_url(file_id: str) -> str:
    """Get direct photo URL via Bot API."""
    try:
        r = requests.get(f"{API}/getFile", params={"file_id": file_id}, timeout=10)
        data = r.json()
        if data.get("ok"):
            path = data["result"]["file_path"]
            return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
    except:
        pass
    return file_id

_ACKED_CB_IDS: set = set()
_ACKED_CB_ORDER: list = []
_ACK_MAX_KEEP = 5000

def _answer(cbid, text=None):
    """ACK a callback query. Safe to call multiple times — only the FIRST call
    actually sends ACK. Optional `text` displays a toast to the user (only
    works on the first ACK). Without this de-dup the second ACK silently
    fails with 'query is too old' and the toast is lost."""
    if not cbid or cbid in _ACKED_CB_IDS:
        return
    _ACKED_CB_IDS.add(cbid)
    _ACKED_CB_ORDER.append(cbid)
    if len(_ACKED_CB_ORDER) > _ACK_MAX_KEEP:
        old = _ACKED_CB_ORDER.pop(0)
        _ACKED_CB_IDS.discard(old)
    kwargs = {"callback_query_id": cbid}
    if text:
        kwargs["text"] = text
    _api("answerCallbackQuery", **kwargs)

def _btn(label, data):
    return {"text": label, "callback_data": data}

def _url_btn(label, url):
    return {"text": label, "url": url}

def _kb(*rows):
    """Premium keyboard builder. Each row is a list of button dicts.
    Rules: 1 btn = full-width, 2 btns = 50/50, 3 btns = 33/33/33.
    Universal/Back buttons always get their own full-width row."""
    return {"inline_keyboard": list(rows)}


def _reply_kb(rows, persistent=True):
    """Bottom reply keyboard — stays visible under the message input.
    rows: list of lists of label strings."""
    return {
        "keyboard": [[{"text": label} for label in row] for row in rows],
        "resize_keyboard": True,
        "is_persistent": persistent,
    }


def _reply_remove():
    """Hide the reply keyboard."""
    return {"remove_keyboard": True}


def kb_main_reply(uid):
    """v54 UX (Layla follow-up): «🌐 Язык» убран из меню — теперь только команда /language.
    v53: bottom menu compressed to 4 rows (was 6).
    Частые действия наверху, редкие (Add, Lang) — через команды /add /language.
    Hot/New объединены в одну строку с AI — это quick-access shortcuts.
    Хендлеры на rbtn_add / rbtn_lang сохранены для обратной совместимости.
    """
    return _reply_kb([
        [_t(uid, "rbtn_buy"),        _t(uid, "rbtn_rent")],
        [_t(uid, "rbtn_commercial"), _t(uid, "rbtn_plot")],
        [_t(uid, "rbtn_hot"),        _t(uid, "rbtn_new"),   _t(uid, "rbtn_ai")],
        [_t(uid, "rbtn_favs"),       _t(uid, "rbtn_alerts")],
    ])


# Property type groups used for category filters
RESIDENTIAL_TYPES = ["apartment", "studio", "villa", "townhouse", "penthouse", "duplex"]
COMMERCIAL_TYPES  = ["office", "retail", "warehouse", "hotel", "hotel_apartment", "serviced_apartment", "whole_building"]
LAND_TYPES        = ["plot"]


# ── Wizard reply-keyboards (each step replaces the bottom bar) ───────────────
def kb_reply_emirate(uid):
    return _reply_kb([
        [_t(uid, "em_dubai"),    _t(uid, "em_abudhabi")],
        [_t(uid, "em_rak"),      _t(uid, "em_sharjah")],
        [_t(uid, "em_any")],
        [_t(uid, "rbtn_home")],
    ])


def kb_reply_proptype_residential(uid):
    return _reply_kb([
        [_t(uid, "pt_apt_btn"),    _t(uid, "pt_villa_btn")],
        [_t(uid, "pt_town_btn"),   _t(uid, "pt_pent_btn")],
        [_t(uid, "pt_studio_btn"), _t(uid, "pt_duplex_btn")],
        [_t(uid, "pt_any_btn")],
        [_t(uid, "rbtn_home")],
    ])


def kb_reply_proptype_commercial(uid):
    return _reply_kb([
        [_t(uid, "pt_office_btn"),    _t(uid, "pt_retail_btn")],
        [_t(uid, "pt_warehouse_btn"), _t(uid, "pt_hotel_btn")],
        [_t(uid, "pt_building_btn")],
        [_t(uid, "pt_any_btn")],
        [_t(uid, "rbtn_home")],
    ])


def kb_reply_bedrooms(uid):
    return _reply_kb([
        [_t(uid, "br_studio_btn"), _t(uid, "br_1_btn"), _t(uid, "br_2_btn")],
        [_t(uid, "br_3_btn"),      _t(uid, "br_4p_btn"), _t(uid, "br_any_btn")],
        [_t(uid, "rbtn_home")],
    ])


def kb_reply_area_input(uid):
    """Bottom keyboard для шага «введите район»."""
    return _reply_kb([
        [_t(uid, "wiz_area_any")],
        [_t(uid, "rbtn_home")],
    ])


def kb_reply_results(uid, has_more=False):
    """Bottom reply keyboard shown AFTER a results batch."""
    rows = []
    if has_more:
        rows.append([_t(uid, "rbtn_more")])
    rows.append([_t(uid, "rbtn_create_alert")])
    rows.append([_t(uid, "rbtn_change_deal"), _t(uid, "rbtn_back")])
    rows.append([_t(uid, "rbtn_home")])
    return _reply_kb(rows)


def kb_reply_ai_goal(uid):
    return _reply_kb([
        [_t(uid, "ai_invest"),     _t(uid, "ai_live")],
        [_t(uid, "ai_holiday"),    _t(uid, "ai_unsure")],
        [_t(uid, "ai_commercial"), _t(uid, "ai_land")],
        [_t(uid, "rbtn_home")],
    ])


def kb_reply_ai_invest(uid):
    return _reply_kb([
        [_t(uid, "ai_inv_longterm"), _t(uid, "ai_inv_airbnb")],
        [_t(uid, "ai_inv_resale"),   _t(uid, "ai_inv_growth")],
        [_t(uid, "rbtn_home")],
    ])


def kb_reply_ai_life(uid):
    return _reply_kb([
        [_t(uid, "ai_l_downtown"), _t(uid, "ai_l_sea")],
        [_t(uid, "ai_l_family"),   _t(uid, "ai_l_premium")],
        [_t(uid, "ai_l_nature"),   _t(uid, "ai_l_business")],
        [_t(uid, "rbtn_home")],
    ])


def kb_reply_ai_commtype(uid):
    return _reply_kb([
        [_t(uid, "pt_office_btn"),    _t(uid, "pt_retail_btn")],
        [_t(uid, "pt_warehouse_btn"), _t(uid, "pt_hotel_btn")],
        [_t(uid, "pt_building_btn")],
        [_t(uid, "pt_any_btn")],
        [_t(uid, "rbtn_home")],
    ])


def kb_reply_budget(uid, is_rent=False, is_commercial=False, is_plot=False):
    """Budget selection in the BOTTOM reply keyboard (was inline before)."""
    any_btn = _t(uid, "b_any_btn")
    home    = _t(uid, "rbtn_home")
    if is_rent:
        return _reply_kb([
            ["≤ 60k AED",  "60–100k"],
            ["100–200k",   "200–500k"],
            ["500k+ AED",  any_btn],
            [home],
        ])
    if is_commercial:
        return _reply_kb([
            ["≤ 1M AED",   "1–5M"],
            ["5–20M",      "20–100M"],
            ["100M+ AED",  any_btn],
            [home],
        ])
    if is_plot:
        return _reply_kb([
            ["≤ 5M AED",   "5–20M"],
            ["20–50M",     "50–100M"],
            ["100M+ AED",  any_btn],
            [home],
        ])
    # Default: sale residential
    return _reply_kb([
        ["≤ 1M AED",   "1–2M"],
        ["2–3M",       "3–5M"],
        ["5–10M",      "10–25M"],
        ["25M+ AED",   any_btn],
        [home],
    ])


# Budget reply-button text → (min, max) AED range. Same range may map to multiple
# button labels (e.g. "≤ 1M AED" in residential vs commercial).
BUDGET_BUTTONS = {
    # Sale residential
    "≤ 1M AED":  (None,        1_000_000),
    "1–2M":      (1_000_000,   2_000_000),
    "2–3M":      (2_000_000,   3_000_000),
    "3–5M":      (3_000_000,   5_000_000),
    "5–10M":     (5_000_000,  10_000_000),
    "10–25M":    (10_000_000, 25_000_000),
    "25M+ AED":  (25_000_000,        None),
    # Rent
    "≤ 60k AED":  (None,    60_000),
    "60–100k":    (60_000, 100_000),
    "100–200k":   (100_000, 200_000),
    "200–500k":   (200_000, 500_000),
    "500k+ AED":  (500_000,    None),
    # Commercial
    "1–5M":       (1_000_000,   5_000_000),
    "5–20M":      (5_000_000,  20_000_000),
    "20–100M":    (20_000_000, 100_000_000),
    "100M+ AED":  (100_000_000,       None),
    # Plot
    "≤ 5M AED":   (None,      5_000_000),
    "20–50M":     (20_000_000, 50_000_000),
    "50–100M":    (50_000_000, 100_000_000),
}


# Canonical key → (translation_key, filter_value) mapping
# We look up which canonical button was pressed across ALL languages.
EMIRATE_KEYS = {
    "em_dubai":    "Dubai",
    "em_abudhabi": "Abu Dhabi",
    "em_rak":      "Ras Al Khaimah",
    "em_sharjah":  "Sharjah",
    "em_any":      None,
}
PROPTYPE_KEYS = {
    "pt_apt_btn":      "apartment",
    "pt_villa_btn":    "villa",
    "pt_town_btn":     "townhouse",
    "pt_pent_btn":     "penthouse",
    "pt_studio_btn":   "studio",
    "pt_duplex_btn":   "duplex",
    "pt_office_btn":   "office",
    "pt_retail_btn":   "retail",
    "pt_warehouse_btn":"warehouse",
    "pt_hotel_btn":    "hotel",
    "pt_building_btn": "whole_building",
    "pt_any_btn":      None,
}
BEDROOM_KEYS = {
    "br_studio_btn": 0,
    "br_1_btn":      1,
    "br_2_btn":      2,
    "br_3_btn":      3,
    "br_4p_btn":     99,
    "br_any_btn":    None,
}


# ── Smart area search ────────────────────────────────────────────────────────
_AREA_CACHE = {"data": None, "expires": 0}

def _load_all_areas() -> list:
    """Возвращает list of dicts: {name, emirate, aliases:[..]}.
    Кэш на 1 час — areas/listings не меняются часто."""
    import time as _time
    now = _time.time()
    if _AREA_CACHE["data"] and _AREA_CACHE["expires"] > now:
        return _AREA_CACHE["data"]
    out: dict = {}   # name → {name, emirate, aliases}
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            # 1. Базовый список — areas table (с aliases)
            cur.execute("SELECT name, emirate, aliases FROM areas WHERE name IS NOT NULL")
            for r in cur.fetchall():
                aliases = list(r.get("aliases") or [])
                out[r["name"]] = {
                    "name": r["name"],
                    "emirate": r.get("emirate"),
                    "aliases": aliases,
                }
            # 2. Дополнить — все уникальные area из listings (если их нет в areas)
            cur.execute("""
                SELECT DISTINCT area, emirate FROM listings
                WHERE is_active = TRUE AND area IS NOT NULL
                  AND (is_audit IS NULL OR is_audit = FALSE)
            """)
            for r in cur.fetchall():
                if r["area"] and r["area"] not in out:
                    out[r["area"]] = {
                        "name": r["area"],
                        "emirate": r.get("emirate"),
                        "aliases": [],
                    }
        conn.close()
    except Exception as e:
        print(f"[area_search] DB error: {e}")
    items = list(out.values())
    _AREA_CACHE["data"] = items
    _AREA_CACHE["expires"] = now + 3600
    return items


# v110: master-zone canonical search (единая функция для всех ботов).
# Используется чтобы при вводе "JVC" / "Marina" / "Al Hebiah First"
# resale-bot сразу понимал, что речь о master-zone "Jumeirah Village Circle"
# и матчил листинги по её каноническому имени, а не по DLD sub-area.
try:
    from area_search import search_area_canonical as _v110_canonical_search
except Exception as _e:
    print(f"[resale_bot] v110 area_search unavailable: {_e}")
    _v110_canonical_search = None


def _master_zone_aliases(q: str) -> list:
    """Если query разрешается в master-zone, возвращает список её алиасов
    (master_zone + display_en + display_ru). Используется как первый фильтр
    в search_areas_by_query, чтобы JVC / Marina / Al Hebiah First попадали
    в одну сгруппированную область."""
    if not _v110_canonical_search:
        return []
    try:
        rows = _v110_canonical_search(q, limit=1) or []
        if rows and rows[0].get("kind") == "master_zone":
            r = rows[0]
            out = []
            for k in ("master_zone", "display_en", "display_ru"):
                v = r.get(k)
                if v and v not in out:
                    out.append(v)
            return out
    except Exception as _e:
        print(f"[resale_bot] master_zone alias err: {_e}")
    return []


def search_areas_by_query(q: str, emirate: str = None, limit: int = 8) -> list:
    """Возвращает top-N matching areas. Логика:
       0. v110: master-zone canonical (JVC → Jumeirah Village Circle и его алиасы) — топ
       1. Точное совпадение name / alias (case-insensitive)
       2. startswith — следующие
       3. contains — последние
    Если emirate задан — фильтруем."""
    if not q or len(q.strip()) < 1:
        return []
    qn = q.strip().lower()
    items = _load_all_areas()
    if emirate:
        items = [i for i in items if not i.get("emirate") or i["emirate"] == emirate]

    # v110: 0. master-zone alias-set
    mz_aliases = [a.lower() for a in _master_zone_aliases(q)]

    mz_matches, exact, starts, contains = [], [], [], []
    for item in items:
        names_to_check = [item["name"]] + list(item.get("aliases") or [])
        names_lower = [n.lower() for n in names_to_check if n]
        if mz_aliases and any(n in mz_aliases for n in names_lower):
            mz_matches.append(item); continue
        if qn in names_lower:
            exact.append(item); continue
        if any(n.startswith(qn) for n in names_lower):
            starts.append(item); continue
        if any(qn in n for n in names_lower):
            contains.append(item)
    # Дедуп — name уникален
    seen, result = set(), []
    for it in mz_matches + exact + starts + contains:
        if it["name"] in seen: continue
        seen.add(it["name"]); result.append(it)
        if len(result) >= limit:
            break
    return result


# ── Smart BUILDING search ────────────────────────────────────────────────────
_BUILDING_CACHE = {"data": None, "expires": 0}


def _load_all_buildings() -> list:
    """Возвращает list of dicts: {name, area, emirate, listings_count}.
    Источник — DISTINCT building из listings (auto-grow: новые здания подхватываются
    автоматически при следующем перечитывании кэша).
    Кэш 1 час."""
    import time as _time
    now = _time.time()
    if _BUILDING_CACHE["data"] and _BUILDING_CACHE["expires"] > now:
        return _BUILDING_CACHE["data"]
    out: dict = {}   # name → {name, area, emirate, count}
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            # buildings table (если непустая — приоритет)
            cur.execute("""
                SELECT name, area, emirate, aliases FROM buildings
                WHERE name IS NOT NULL
            """)
            for r in cur.fetchall():
                out[r["name"]] = {
                    "name":    r["name"],
                    "area":    r.get("area"),
                    "emirate": r.get("emirate"),
                    "aliases": list(r.get("aliases") or []),
                    "count":   0,
                }
            # Дополнить из listings (DISTINCT building) — auto-grow
            cur.execute("""
                SELECT building, area, emirate, COUNT(*) AS n
                FROM listings WHERE is_active = TRUE
                  AND building IS NOT NULL AND LENGTH(TRIM(building)) >= 4
                  AND (is_audit IS NULL OR is_audit = FALSE)
                GROUP BY building, area, emirate
            """)
            for r in cur.fetchall():
                name = r["building"]
                if not name: continue
                if name in out:
                    out[name]["count"] = out[name].get("count", 0) + int(r["n"])
                    if not out[name].get("area") and r.get("area"):
                        out[name]["area"] = r["area"]
                    if not out[name].get("emirate") and r.get("emirate"):
                        out[name]["emirate"] = r["emirate"]
                else:
                    out[name] = {
                        "name":    name,
                        "area":    r.get("area"),
                        "emirate": r.get("emirate"),
                        "aliases": [],
                        "count":   int(r["n"]),
                    }
        conn.close()
    except Exception as e:
        print(f"[building_search] DB error: {e}")
    items = list(out.values())
    # Сортируем по count DESC чтобы популярные были в топе
    items.sort(key=lambda x: x.get("count", 0), reverse=True)
    _BUILDING_CACHE["data"] = items
    _BUILDING_CACHE["expires"] = now + 3600
    return items


def search_buildings_by_query(q: str, emirate: str = None, area: str = None,
                              limit: int = 8) -> list:
    """Возвращает top-N matching buildings.
       Логика: exact match → startswith → contains.
       При совпадениях — по count DESC (популярные первыми).
       Если emirate / area задан — СТРОГИЙ фильтр (без NULL).
       Раньше: items без area тоже проходили → юзер в JVC видел
       Binghatti из других районов. Теперь только точное совпадение.

       v_unified: если локальная база listings ничего не вернула — fallback
       в канонический поиск по DLD-архиву (mv_building_12m_summary) через
       building_search.search_building_canonical. Так пользователь НИКОГДА
       не получает пустой ответ — даже на опечатки и редкие здания.
    """
    if not q or len(q.strip()) < 1:
        return []
    qn = q.strip().lower()
    items = _load_all_buildings()
    if emirate:
        # Эмират — поддерживаем без значения (для тех зданий где emirate не определён,
        # но area из эмирата подходит)
        items = [i for i in items if not i.get("emirate") or i["emirate"] == emirate]
    if area:
        # СТРОГО — только здания из этого района
        items = [i for i in items if i.get("area") == area]

    exact, starts, contains = [], [], []
    for item in items:
        names_to_check = [item["name"]] + list(item.get("aliases") or [])
        names_lower = [n.lower() for n in names_to_check if n]
        if qn in names_lower:
            exact.append(item); continue
        if any(n.startswith(qn) for n in names_lower):
            starts.append(item); continue
        if any(qn in n for n in names_lower):
            contains.append(item)
    seen, result = set(), []
    for it in exact + starts + contains:
        if it["name"] in seen: continue
        seen.add(it["name"]); result.append(it)
        if len(result) >= limit:
            break
    if result:
        return result

    # ── DLD fallback: если в локальных listings ничего — спрашиваем архив ──
    try:
        from building_search import search_building_canonical as _dld_search
        dld_rows = _dld_search(q, limit=limit, include_popular_fallback=True) or []
    except Exception as _e:
        print(f"[building_search] DLD fallback error: {_e}")
        dld_rows = []
    # Если выбран area-фильтр — отсекаем здания не из этого района (master_zone тоже учитываем).
    if area:
        a_low = area.lower()
        dld_rows = [r for r in dld_rows
                    if (r.get("area_name") or "").lower() == a_low
                    or (r.get("master_zone") or "").lower() == a_low]
    suggestions = []
    for r in dld_rows:
        suggestions.append({
            "name":    r["name"],
            "area":    r.get("master_zone") or r.get("area_name"),
            "emirate": emirate or "Dubai",
            "aliases": [],
            "count":   r.get("deals_12m") or 0,
            "is_dld_suggest": True,
            "kind":    r.get("kind"),
        })
    return suggestions[:limit]


def kb_reply_building_input(uid):
    """Bottom keyboard для шага «введите здание»."""
    return _reply_kb([
        [_t(uid, "wiz_bld_any")],
        [_t(uid, "rbtn_home")],
    ])


# AI Assistant flow — bottom reply keyboard buttons
AI_GOAL_KEYS = {
    "ai_invest":     "invest",
    "ai_live":       "live",
    "ai_holiday":    "holiday",
    "ai_unsure":     "unsure",
    "ai_commercial": "commercial",
    "ai_land":       "land",
}
AI_INVEST_KEYS = {
    "ai_inv_longterm": "longterm",
    "ai_inv_airbnb":   "airbnb",
    "ai_inv_resale":   "resale",
    "ai_inv_growth":   "growth",
}
AI_LIFE_KEYS = {
    "ai_l_downtown": "downtown",
    "ai_l_sea":      "sea",
    "ai_l_family":   "family",
    "ai_l_premium":  "premium",
    "ai_l_nature":   "nature",
    "ai_l_business": "business",
}
AI_COMMTYPE_KEYS = {
    "pt_office_btn":    "office",
    "pt_retail_btn":    "retail",
    "pt_warehouse_btn": "warehouse",
    "pt_hotel_btn":     "hotel",
    "pt_building_btn":  "whole_building",
    "pt_any_btn":       "any",
}


def _wizard_match(text, key_map):
    """Find which canonical key the text matches across all 3 languages."""
    if not text:
        return None, False
    for canonical_key, value in key_map.items():
        for lang_strings in T.values():
            if lang_strings.get(canonical_key) == text:
                return value, True
    return None, False


def is_main_menu_text(text: str):
    """Returns the rbtn_* key if `text` matches a MAIN MENU label in any
    language. Wizard-only keys (rbtn_more / rbtn_back / rbtn_change_deal /
    rbtn_create_alert) ИСКЛЮЧЕНЫ — они обрабатываются dispatch_wizard_button."""
    if not text: return None
    MAIN_MENU_KEYS = {
        "rbtn_buy", "rbtn_rent", "rbtn_commercial", "rbtn_plot",
        "rbtn_hot", "rbtn_new", "rbtn_ai", "rbtn_add",
        "rbtn_favs", "rbtn_alerts", "rbtn_lang", "rbtn_home",
    }
    for lang_code, strings in T.items():
        for k, v in strings.items():
            if k in MAIN_MENU_KEYS and v == text:
                return k
    return None

# ── Keyboards ─────────────────────────────────────────────────────────────────
def kb_lang():
    """Inline keyboard for language selection — kept for backward compat
    (when language is changed from main menu)."""
    return _kb(
        [_btn("🇬🇧  English",  "lang|en")],
        [_btn("🇷🇺  Русский",  "lang|ru")],
        [_btn("🇦🇪  العربية", "lang|ar")],
    )


def kb_lang_reply():
    """Bottom reply keyboard for language selection — used at /start welcome."""
    return _reply_kb([
        ["🇬🇧 English"],
        ["🇷🇺 Русский"],
        ["🇦🇪 العربية"],
    ])


# Mapping reply-keyboard language buttons to lang codes
LANG_BUTTONS = {
    "🇬🇧 English":  "en",
    "🇷🇺 Русский":  "ru",
    "🇦🇪 العربية": "ar",
}

def kb_main(uid):
    return _kb(
        [_btn(_t(uid, "btn_ai"),       "ai|start")],
        [_btn(_t(uid, "btn_filter"),   "menu|filter"),  _btn(_t(uid, "btn_hot"),      "menu|hot")],
        [_btn(_t(uid, "btn_budget"),   "menu|budget"),  _btn(_t(uid, "btn_area"),     "menu|area")],
        [_btn(_t(uid, "btn_new"),      "menu|new"),     _btn(_t(uid, "btn_building"), "menu|building")],
        [_btn(_t(uid, "btn_add"),      "add|start"),    _btn(_t(uid, "btn_lang"),     "menu|lang")],
    )

def kb_emirate(uid):
    return _kb(
        [_btn(_t(uid, "e_dubai"),    "em|Dubai"),           _btn(_t(uid, "e_abudhabi"), "em|Abu Dhabi")],
        [_btn(_t(uid, "e_rak"),      "em|Ras Al Khaimah"),  _btn(_t(uid, "e_sharjah"),  "em|Sharjah")],
        [_btn(_t(uid, "e_any"),      "em|any")],
        [_btn(_t(uid, "rbtn_home"),  "menu|main")],
    )

def kb_deal(uid):
    return _kb(
        [_btn(_t(uid, "d_sale"),   "deal|sale"),  _btn(_t(uid, "d_rent"), "deal|rent")],
        [_btn(_t(uid, "d_any"),    "deal|any")],
        [_btn(_t(uid, "rbtn_home"), "menu|main")],
    )

def kb_proptype(uid):
    """Residential property types only — commercial/plot are accessed via main menu category."""
    return _kb(
        [_btn(_t(uid, "pt_apt"),   "pt|apartment"), _btn(_t(uid, "pt_villa"), "pt|villa")],
        [_btn(_t(uid, "pt_town"),  "pt|townhouse"), _btn(_t(uid, "pt_pent"), "pt|penthouse")],
        [_btn(_t(uid, "pt_any"),   "pt|any")],
        [_btn(_t(uid, "rbtn_home"), "menu|main")],
    )

def kb_commercial_type(uid):
    """Commercial sub-types when user picked Commercial from main menu."""
    return _kb(
        [_btn("🏢 Office",     "pt|office"),     _btn("🛍 Retail",     "pt|retail")],
        [_btn("📦 Warehouse",  "pt|warehouse"),  _btn("🏨 Hotel",      "pt|hotel")],
        [_btn(_t(uid, "pt_building_btn"), "pt|whole_building")],
        [_btn(_t(uid, "pt_any"), "pt|any")],
        [_btn(_t(uid, "rbtn_home"), "menu|main")],
    )

def kb_budget(uid, is_rent=False, is_commercial=False, is_plot=False):
    """Dynamic budget ranges based on actual DB distribution.
    - Sale residential: median 2.9M, 75pct 7M, 95pct 35M
    - Rent residential: median 165k, 75pct 400k, 90pct 1M
    - Commercial: huge spread 145k–920M
    - Plot: 2.25M–510M
    """
    if is_rent:
        return _kb(
            [_btn("≤ 60k AED",     "bud|r_u60"),   _btn("60–100k",     "bud|r_60100")],
            [_btn("100–200k",      "bud|r_100200"),_btn("200–500k",    "bud|r_200500")],
            [_btn("500k+ AED",     "bud|r_500p"),  _btn(_t(uid, "b_any"), "bud|any")],
            [_btn(_t(uid, "rbtn_home"), "menu|main")],
        )
    if is_commercial:
        return _kb(
            [_btn("≤ 1M AED",      "bud|c_u1"),    _btn("1–5M",        "bud|c_15")],
            [_btn("5–20M",         "bud|c_520"),   _btn("20–100M",     "bud|c_20100")],
            [_btn("100M+ AED",     "bud|c_100p"),  _btn(_t(uid, "b_any"), "bud|any")],
            [_btn(_t(uid, "rbtn_home"), "menu|main")],
        )
    if is_plot:
        return _kb(
            [_btn("≤ 5M AED",      "bud|p_u5"),    _btn("5–20M",       "bud|p_520")],
            [_btn("20–50M",        "bud|p_2050"),  _btn("50–100M",     "bud|p_50100")],
            [_btn("100M+ AED",     "bud|p_100p"),  _btn(_t(uid, "b_any"), "bud|any")],
            [_btn(_t(uid, "rbtn_home"), "menu|main")],
        )
    # Default: residential sale (most common case)
    return _kb(
        [_btn("≤ 1M AED",      "bud|u1"),     _btn("1–2M",       "bud|1-2")],
        [_btn("2–3M",          "bud|2-3"),    _btn("3–5M",       "bud|3-5")],
        [_btn("5–10M",         "bud|5-10"),   _btn("10–25M",     "bud|10-25")],
        [_btn("25M+ AED",      "bud|25p"),    _btn(_t(uid, "b_any"), "bud|any")],
        [_btn(_t(uid, "rbtn_home"), "menu|main")],
    )

def kb_bedrooms(uid):
    return _kb(
        [_btn(_t(uid, "br_studio"), "br|0"), _btn(_t(uid, "br_1"), "br|1"), _btn(_t(uid, "br_2"), "br|2")],
        [_btn(_t(uid, "br_3"),      "br|3"), _btn(_t(uid, "br_4p"), "br|4p"), _btn(_t(uid, "br_any"), "br|any")],
        [_btn(_t(uid, "rbtn_home"), "menu|main")],
    )

POPULAR_AREAS = [
    "Downtown Dubai", "Business Bay", "Dubai Marina",
    "Palm Jumeirah", "Jumeirah Village Circle", "Dubai Hills Estate",
    "Dubai Creek Harbour", "Jumeirah Beach Residence", "MBR City",
    "Al Marjan Island", "Yas Island", "Al Reem Island",
]

def kb_areas(uid):
    rows = [[_btn(a, f"area|{a}")] for a in POPULAR_AREAS]
    rows.append([_btn(_t(uid, "area_custom_btn"), "area|_custom_")])
    rows.append([_btn(_t(uid, "rbtn_home"), "menu|main")])
    return {"inline_keyboard": rows}

# ── Format helpers ────────────────────────────────────────────────────────────
def _fmt(price):
    if not price: return "-"
    return f"{int(price):,} AED".replace(",", " ")




def _fmt_size(sqft):
    if not sqft: return ""
    sqm = round(sqft * 0.0929)
    return f"{int(sqft):,} sqft  ·  {sqm} m²".replace(",", " ")

def _fmt_br(br):
    if br is None: return ""
    return "Studio" if br == 0 else f"{br} BR"

def _sep():
    return "────────────────────"

# ── Market data helpers ───────────────────────────────────────────────────────

# v107 read-model: pre-aggregated DLD stats (≤50ms vs raw scan)
try:
    import dxb_stats_client as _dxb_stats
    _DXB_STATS_OK = True
except Exception as _e:
    print(f"[resale_bot] dxb_stats_client unavailable: {_e}", flush=True)
    _dxb_stats = None
    _DXB_STATS_OK = False


# user-facing area → возможные DLD canonical имена для read-model
# v128: расширен на основе coverage-аудита от 2026-05-24 (см. _coverage_audit.py).
# Цель: покрыть top-20 ранее непокрытых районов через sub-area aggregation.
_RB_AREA_CANDIDATES = {
    "Dubai Marina":      ["Dubai Marina", "Marsa Dubai"],
    "Marina":            ["Marsa Dubai", "Dubai Marina"],
    "Downtown Dubai":    ["Burj Khalifa", "Downtown Dubai"],
    "Downtown":          ["Burj Khalifa", "Downtown Dubai"],
    "Dubai Hills":       ["Hadaeq Sheikh Mohammed Bin Rashid",
                            "Dubai Hills Estate"],
    "Dubai Hills Estate":["Hadaeq Sheikh Mohammed Bin Rashid",
                            "Dubai Hills Estate"],
    "Business Bay":      ["Business Bay"],
    "JVC":               ["Jumeirah Village Circle", "JVC"],
    "Jumeirah Village Circle": ["Jumeirah Village Circle", "JVC"],
    "JVT":               ["Jumeirah Village Triangle", "JVT"],
    "Jumeirah Village Triangle": ["Jumeirah Village Triangle", "JVT"],
    # v124 fix: DLD-канон с "s" (Lakes) + физический sub-area Al Thanyah Fifth
    "JLT":               ["Jumeirah Lakes Towers", "Jumeirah Lake Towers",
                            "Al Thanyah Fifth"],
    "Jumeirah Lake Towers": ["Jumeirah Lakes Towers", "Jumeirah Lake Towers",
                              "Al Thanyah Fifth"],
    "Palm Jumeirah":     ["Palm Jumeirah"],
    "MBR City":          ["MBR City", "Wadi Al Safa 3", "Wadi Al Safa 6", "Wadi Al Safa 7"],
    "Dubai Creek Harbour":["Dubai Creek Harbour", "Al Jaddaf"],
    "Creek Harbour":     ["Dubai Creek Harbour", "Al Jaddaf"],
    "Creek Beach":       ["Dubai Creek Harbour", "Al Jaddaf"],
    "Dubai South":       ["Madinat Al Mataar", "Dubai South"],
    "Al Furjan":         ["Al Furjan", "Jabal Ali First"],
    "Arjan":             ["Arjan", "Al Barsha South Fourth"],
    "Silicon Oasis":     ["Dubai Silicon Oasis", "Silicon Oasis"],
    "Sports City":       ["Dubai Sports City", "Sports City"],
    "DIFC":              ["DIFC", "Zaabeel Second"],
    # v128: Meydan в DLD дробится на Meydan One / Meydan Avenue + историч. Nad Al Sheba
    "Meydan":            ["Meydan", "Meydan One", "Meydan Avenue", "Nad Al Sheba"],
    "Al Barsha":         ["Al Barsha First", "Al Barsha South Fourth"],
    # ── v128 NEW ALIAS BUCKETS (top uncovered areas, по DLD-маппингу) ──
    "Wadi Al Safa":      ["Wadi Al Safa 2", "Wadi Al Safa 3", "Wadi Al Safa 4",
                          "Wadi Al Safa 5", "Wadi Al Safa 6", "Wadi Al Safa 7"],
    "Maritime City":     ["Dubai Maritime City", "Maritime City"],
    "Studio City":       ["Dubai Studio City", "Studio City"],
    "Dubai Studio City": ["Dubai Studio City", "Studio City"],
    "Dubai Production City": ["Dubai Production City", "IMPZ"],
    "International City": ["International City Ph 1", "International City Ph 2 & 3",
                            "Warsan First"],
    "Arabian Ranches":   ["Arabian Ranches I", "Arabian Ranches II", "Arabian Ranches III",
                            "Wadi Al Safa 5", "Wadi Al Safa 7"],
    "Arabian Ranches 1": ["Arabian Ranches I", "Wadi Al Safa 5"],
    "Arabian Ranches 2": ["Arabian Ranches II"],
    "Arabian Ranches 3": ["Arabian Ranches III"],
    "Dubai Investment Park": ["Dubai Investment Park First", "Dubai Investment Park Second"],
    "DIP":               ["Dubai Investment Park First", "Dubai Investment Park Second"],
    "Bluewaters":        ["Bluewaters", "Bluewaters Island"],
    "Bluewaters Island": ["Bluewaters", "Bluewaters Island"],
    "JBR":               ["Jumeirah Beach Residence", "Marsa Dubai"],
    "Jumeirah Beach Residence": ["Jumeirah Beach Residence", "Marsa Dubai"],
    "Al Jaddaf":         ["Al Jaddaf", "Dubai Creek Harbour"],
    "MBR":               ["MBR City", "Wadi Al Safa 3", "Wadi Al Safa 6", "Wadi Al Safa 7"],
    "District One":      ["District One", "MBR City", "Nad Al Sheba"],
    "Mohammed Bin Rashid City": ["MBR City", "Wadi Al Safa 3", "Wadi Al Safa 6", "Wadi Al Safa 7"],
    "Town Square":       ["Al Yelayiss 2", "Town Square"],
    "Tilal Al Ghaf":     ["Tilal Al Ghaf"],
    "DAMAC Hills":       ["DAMAC Hills", "Al Hebiah Fourth", "Al Hebiah Third"],
    "DAMAC Hills 2":     ["DAMAC Hills 2", "Al Yufrah 1", "Al Yufrah 2"],
    "Akoya":             ["DAMAC Hills 2", "Al Yufrah 1", "Al Yufrah 2"],
    "Mudon":             ["Al Hebiah Sixth", "Mudon"],
    "Sobha Hartland":    ["Sobha Hartland", "Nad Al Sheba", "Wadi Al Safa 3"],
    "The Valley":        ["The Valley", "Al Yelayiss 1"],
    "Reem":              ["Al Reem", "Wadi Al Safa 6"],
    "Remraam":           ["Remraam", "Al Hebiah Fifth"],
    "Discovery Gardens": ["Discovery Gardens", "Jebel Ali First"],
    "IMPZ":              ["Dubai Production City", "IMPZ"],
    "Motor City":        ["Motor City", "Al Hebiah Third"],
    "Liwan":             ["Liwan", "Al Mizhar Third"],
    "Al Mizhar":         ["Al Mizhar First", "Al Mizhar Second", "Al Mizhar Third"],
    # v128 part-2 — off-plan / new launches
    "Dubai Islands":     ["Palm Deira", "Deira", "Dubai Islands"],
    "Madinat Jumeirah":  ["Umm Suqeim Third", "Umm Suqeim Second", "Madinat Jumeirah"],
    "Madinat Jumeirah Living": ["Umm Suqeim Third", "Madinat Jumeirah"],
    "Madinat Hind":      ["Madinat Hind 1", "Madinat Hind 3", "Madinat Hind 4"],
    "Dubailand":         ["Wadi Al Safa 5", "Wadi Al Safa 6", "Wadi Al Safa 7",
                          "Al Hebiah Fourth", "Al Yelayiss 1", "Al Yelayiss 2"],
    "Warsan":            ["Al Warsan First", "Al Warsan Second", "Al Warsan Third",
                          "Warsan Fourth"],
    "International City Phase 2": ["International City Ph 2 & 3", "Warsan First"],
    "Emaar Beachfront":  ["Marsa Dubai", "Dubai Marina"],
    "Madinat Al Mataar": ["Madinat Al Mataar"],
    "World Islands":     ["World Islands"],
    "Jumeirah Islands":  ["Jumeirah Islands"],
}


# v128: дополнительный кэш alias-ов из read-model.master_project_zones
# (single fetch, lazy, TTL 1ч). Снимает необходимость хардкодить весь словарь.
_MPZ_CACHE = {"data": None, "ts": 0.0}
_MPZ_TTL_SEC = 3600

def _mpz_aliases(area_key_lc: str):
    """Возвращает list[area-name lower] из master_project_zones, если найден.
    Безопасно: при ошибке/отсутствии — пустой список.
    """
    import time as _t
    if not _DXB_STATS_OK or not area_key_lc:
        return []
    now = _t.time()
    if not _MPZ_CACHE["data"] or (now - _MPZ_CACHE["ts"]) > _MPZ_TTL_SEC:
        try:
            with _dxb_stats._conn().cursor() as cur:
                cur.execute("""
                    SELECT LOWER(display_en), LOWER(area_key), dld_master_project_names
                    FROM master_project_zones
                """)
                rows = cur.fetchall()
            cache = {}
            for disp, ak, names in rows:
                bucket = []
                for n in (names or []):
                    if n: bucket.append(n.strip().lower())
                # доступ как по display, так и по area_key
                for key in {disp, ak}:
                    if key:
                        cache[key] = list(set(bucket))
            _MPZ_CACHE["data"] = cache
            _MPZ_CACHE["ts"] = now
        except Exception as _e:
            print(f"[resale_bot] mpz fetch err: {_e}", flush=True)
            _MPZ_CACHE["data"] = {}
            _MPZ_CACHE["ts"] = now
    return _MPZ_CACHE["data"].get(area_key_lc, [])


def _rb_area_candidates(area: str):
    if not area:
        return []
    out = list(_RB_AREA_CANDIDATES.get(area, []))
    # v128: подмешиваем алиасы из master_project_zones по lower(area)
    out.extend(_mpz_aliases(area.strip().lower()))
    if area not in out:
        out.append(area)
    out.extend([area.upper(), area.title()])
    seen, uniq = set(), []
    for n in out:
        k = (n or "").strip().lower()
        if k and k not in seen:
            seen.add(k); uniq.append(n)
    return uniq


def get_area_aggregate(area: str) -> dict | None:
    """v107: возвращает агрегат района из read-model (dxb_stats).

    Returns dict с полями:
        avg_price, top_quartile_price, avg_price_psf, top_quartile_psf,
        yoy_growth_pct, yoy_growth_top_pct, avg_rental_yield_pct,
        top_rental_yield_pct, deals, rent_avg_price.

    None если read-model не настроена / нет данных по району.
    """
    if not _DXB_STATS_OK or not area:
        return None
    import time as _time_aa
    _t_aa = _time_aa.perf_counter()
    best_sale, best_rent = None, None
    # v111: один SQL по всем кандидатам сразу — было 4 * N round-trip.
    candidates = _rb_area_candidates(area)
    keys = []
    seen = set()
    for n in candidates:
        k = (n or "").strip().lower()
        if k and k not in seen:
            seen.add(k); keys.append(k)
    batch = {}
    if keys:
        try:
            sql = """
                SELECT area_key, area_name, property_type, deal_type,
                       deals, avg_price, avg_price_psf,
                       top_quartile_price, top_quartile_psf,
                       yoy_growth_pct, yoy_growth_top_pct,
                       avg_rental_yield_pct, top_rental_yield_pct
                FROM mv_area_12m_summary
                WHERE area_key=ANY(%s) AND rooms='all'
                  AND property_type IN ('apartment','all')
                  AND deal_type IN ('sale','rent')
            """
            c = _dxb_stats._conn()
            with c.cursor() as cur:
                cur.execute(sql, (keys,))
                rows = cur.fetchall()
            tmp = {}
            for r in rows:
                ak, anm, pt, dt_, deals, ap, apsf, tqp, tpsf, gy, gyt, yy, yyt = r
                slot = tmp.setdefault(ak, {"sale": {}, "rent": {}})
                target = "sale" if dt_ == "sale" else "rent"
                d = {
                    "area_name": anm, "deals": int(deals or 0),
                    "avg_price": float(ap) if ap else None,
                    "avg_price_psf": float(apsf) if apsf else None,
                    "top_quartile_price": float(tqp) if tqp else None,
                    "top_quartile_psf": float(tpsf) if tpsf else None,
                    "yoy_growth_pct": float(gy) if gy is not None else None,
                    "yoy_growth_top_pct": float(gyt) if gyt is not None else None,
                    "avg_rental_yield_pct": float(yy) if yy is not None else None,
                    "top_rental_yield_pct": float(yyt) if yyt is not None else None,
                }
                slot[target].setdefault(pt, d)
            for ak, slot in tmp.items():
                batch[ak] = {
                    "sale": slot["sale"].get("apartment") or slot["sale"].get("all"),
                    "rent": slot["rent"].get("apartment") or slot["rent"].get("all"),
                }
        except Exception as _ex:
            print(f"[resale_bot] batch_area err: {_ex}", flush=True)
            batch = {}
    for nm in candidates:
        slot = batch.get((nm or "").strip().lower()) or {}
        s = slot.get("sale"); r = slot.get("rent")
        if s and (s.get("deals") or 0) >= 3:
            if best_sale is None or s["deals"] > (best_sale.get("deals") or 0):
                best_sale = s
        if r and (r.get("deals") or 0) >= 3:
            if best_rent is None or r["deals"] > (best_rent.get("deals") or 0):
                best_rent = r
    _dt_aa = (_time_aa.perf_counter() - _t_aa) * 1000
    print(f"[LAT] R1_get_area_aggregate: {_dt_aa:.0f}ms area={str(area)[:40]!r} cands={len(candidates)}", flush=True)
    if _dt_aa > 500:
        print(f"[LAT_SLOW] R1_get_area_aggregate: {_dt_aa:.0f}ms", flush=True)
    if not best_sale and not best_rent:
        return None
    # v124 fix: yield-колонки в mv_area_12m_summary заполняются в
    # backfill_rental_yield на строках sale (sale.avg_rental_yield_pct), а
    # rent-строки имеют NULL. Берём yield prefer-from-sale, fallback-rent.
    out = {
        "deals":                   (best_sale or {}).get("deals"),
        "avg_price":               (best_sale or {}).get("avg_price"),
        "top_quartile_price":      (best_sale or {}).get("top_quartile_price"),
        "avg_price_psf":           (best_sale or {}).get("avg_price_psf"),
        "top_quartile_psf":        (best_sale or {}).get("top_quartile_psf"),
        "yoy_growth_pct":          (best_sale or {}).get("yoy_growth_pct"),
        "yoy_growth_top_pct":      (best_sale or {}).get("yoy_growth_top_pct"),
        "avg_rental_yield_pct":    ((best_sale or {}).get("avg_rental_yield_pct")
                                    or (best_rent or {}).get("avg_rental_yield_pct")),
        "top_rental_yield_pct":    ((best_sale or {}).get("top_rental_yield_pct")
                                    or (best_rent or {}).get("top_rental_yield_pct")),
        "rent_avg_price":          (best_rent or {}).get("avg_price"),
    }
    return out


def get_market_summary(area: str, strategy: str = None) -> str:
    """Market summary текст. v107: сначала пробуем dxb_stats read-model, потом fallback на market_data."""
    # ── v107 fast path: read-model ─────────────────────────────────────────
    agg = get_area_aggregate(area) if area else None
    if agg and (agg.get("deals") or 0) >= 5:
        lines = [f"\n{_sep()}\n  MARKET INSIGHT  ·  {area.upper()}\n{_sep()}"]
        roi_top = agg.get("top_rental_yield_pct")
        roi_avg = agg.get("avg_rental_yield_pct")
        gr_avg  = agg.get("yoy_growth_pct")
        gr_top  = agg.get("yoy_growth_top_pct")
        avg_p   = agg.get("avg_price")
        top_p   = agg.get("top_quartile_price")
        rent_yr = agg.get("rent_avg_price")
        if roi_avg:
            lines.append(f"  ROI              {round(float(roi_avg),1)}% yearly")
        if roi_top and (not roi_avg or roi_top > roi_avg):
            lines.append(f"  Premium ROI      up to {round(float(roi_top),1)}%")
        if gr_avg is not None:
            trend = "↑" if gr_avg > 0 else "↓"
            lines.append(f"  Annual growth    {trend} {abs(round(float(gr_avg),1))}%")
        if gr_top is not None and (gr_avg is None or gr_top > gr_avg):
            lines.append(f"  Premium growth   up to {round(float(gr_top),1)}%")
        if avg_p:
            lines.append(f"  Avg deal         {_fmt(float(avg_p))}")
        if top_p:
            lines.append(f"  Premium segment  {_fmt(float(top_p))}")
        if rent_yr:
            lines.append(f"  Avg annual rent  {_fmt(float(rent_yr))}/year")
        if agg.get("deals"):
            lines.append(f"  Yearly deals     {int(agg['deals'])}")
        lines.append("  _source: DLD aggregates_")
        lines.append(_sep())
        return "\n".join(lines)

    # ── fallback: market_data (legacy путь) ───────────────────────────────
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM market_data
                WHERE LOWER(area) = LOWER(%s)
                ORDER BY data_month DESC LIMIT 1
            """, (area,))
            m = cur.fetchone()
        conn.close()

        if not m:
            return ""

        roi      = m.get("avg_roi")
        growth   = m.get("yoy_change_pct")
        demand   = m.get("demand_score")
        liq      = m.get("liquidity_score")
        rent_1br = m.get("avg_rent_1br")
        txns     = m.get("transactions_count")

        lines = [f"\n{_sep()}\n  MARKET INSIGHT  ·  {area.upper()}\n{_sep()}"]

        if roi:
            lines.append(f"  ROI              {roi}% yearly")
        if growth:
            trend = "↑" if growth > 0 else "↓"
            lines.append(f"  Annual growth    {trend} {abs(growth)}%")
        if demand:
            d_label = "Very High" if demand >= 9 else "High" if demand >= 7 else "Moderate"
            lines.append(f"  Demand           {d_label}  ({demand}/10)")
        if liq:
            l_label = "Fast" if liq >= 8 else "Good" if liq >= 6 else "Moderate"
            lines.append(f"  Liquidity        {l_label}  ({liq}/10)")
        if rent_1br:
            lines.append(f"  Avg rent 1BR     {_fmt(rent_1br)}/year")
        if txns:
            lines.append(f"  Monthly deals    {txns}")

        lines.append(_sep())
        return "\n".join(lines)
    except:
        return ""


def get_best_areas_from_db(strategy: str, emirate: str = None, limit: int = 3) -> list:
    """Get best areas dynamically by strategy.

    v128: ПРИОРИТЕТ read-model (live DLD aggregates через mv_area_12m_summary):
    - airbnb / longterm: топ по rental yield (top_rental_yield_pct)
    - growth:            топ по YoY-росту (yoy_growth_top_pct)
    - resale:            топ по числу сделок (ликвидность) + рост
    - default:           топ по deals
    Fallback: легаси market_data (статический seed), warning в логи если упало туда.
    """
    # ── v128 fast path: read-model ────────────────────────────────────────
    if _DXB_STATS_OK:
        try:
            if strategy in ("airbnb", "longterm"):
                order = "top_rental_yield_pct DESC NULLS LAST, deals DESC"
            elif strategy == "growth":
                order = "yoy_growth_top_pct DESC NULLS LAST, deals DESC"
            elif strategy == "resale":
                order = "deals DESC, yoy_growth_top_pct DESC NULLS LAST"
            else:
                order = "deals DESC"
            sql = f"""
                SELECT area_name, deals,
                       avg_rental_yield_pct, top_rental_yield_pct,
                       yoy_growth_pct, yoy_growth_top_pct
                FROM mv_area_12m_summary
                WHERE rooms='all' AND property_type='apartment' AND deal_type='sale'
                  AND area_name IS NOT NULL
                  AND deals >= 30
                ORDER BY {order}
                LIMIT %s
            """
            with _dxb_stats._conn().cursor() as cur:
                cur.execute(sql, (limit * 3,))  # запас для dedup
                rows = cur.fetchall()
            # display map: DLD canonical → user-facing (через _RB_AREA_CANDIDATES inverse)
            inverse = {}
            for user_name, dld_list in _RB_AREA_CANDIDATES.items():
                for d in dld_list:
                    inverse.setdefault(d.strip().lower(), user_name)
            out, seen = [], set()
            for r in rows:
                dld_name = (r[0] or "").strip()
                pretty = inverse.get(dld_name.lower(), dld_name)
                if pretty in seen:
                    continue
                seen.add(pretty)
                out.append(pretty)
                if len(out) >= limit:
                    break
            if out:
                return out
        except Exception as _e:
            print(f"[resale_bot] get_best_areas_from_db read-model err: {_e}", flush=True)

    # ── fallback: legacy market_data (статический seed) ───────────────────
    print(f"[resale_bot] WARN: get_best_areas_from_db fallback to market_data (strategy={strategy})", flush=True)
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            data_month = datetime.now().strftime("%Y-%m")
            base_q = """
                SELECT DISTINCT ON (area) area, emirate,
                    avg_roi, demand_score, liquidity_score,
                    yoy_change_pct, avg_rent_1br
                FROM market_data
                WHERE data_month = %s
            """
            params = [data_month]

            if emirate:
                base_q += " AND emirate = %s"
                params.append(emirate)

            # Sort by strategy
            if strategy == "airbnb":
                base_q += " ORDER BY area, demand_score DESC, avg_roi DESC"
            elif strategy == "longterm":
                base_q += " ORDER BY area, avg_roi DESC, liquidity_score DESC"
            elif strategy == "resale":
                base_q += " ORDER BY area, liquidity_score DESC, yoy_change_pct DESC"
            elif strategy == "growth":
                base_q += " ORDER BY area, yoy_change_pct DESC, demand_score DESC"
            else:
                base_q += " ORDER BY area, demand_score DESC"

            cur.execute(f"SELECT * FROM ({base_q}) t ORDER BY demand_score DESC LIMIT %s",
                       params + [limit])
            rows = cur.fetchall()
        conn.close()
        return [r["area"] for r in rows] if rows else []
    except:
        return []

# ── Client card ────────────────────────────────────────────────────────────────
SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━"


def _fmt_br_local(uid, br):
    """Localized bedroom display."""
    if br is None: return ""
    if br == 0:    return _t(uid, "ft_studio")
    return f"{br} {_t(uid, 'ft_br')}"


def format_card(listing, uid, rank=None):
    """Professional listing card. Logical order:
       1. Title:   🏢 Building
       2. Location: 📍 Area · Emirate
       ─
       3. Price:   💰 amount + AED/m²
       ─
       4. Specs:   🛏 BR · 🛁 BA · 📐 Size · 🏗 Floor
       5. Extras:  🌅 View · 🛋 Furn · 🔑 Status
       ─
       6. Analytics: market diff / discount / ROI / score
    """
    emirate   = listing.get("emirate") or ""
    area      = listing.get("area") or ""
    building  = listing.get("building") or ""
    br        = listing.get("bedrooms")
    bath      = listing.get("bathrooms")
    size      = listing.get("size_sqft")
    floor     = listing.get("floor")
    view      = listing.get("view")
    status    = listing.get("status")
    furn      = listing.get("furnishing")
    deal_type = listing.get("deal_type", "sale")
    prop_type = listing.get("property_type") or ""
    price     = listing.get("price")
    ppf       = listing.get("price_per_sqft")
    pct       = listing.get("price_vs_market_percent")
    disc      = listing.get("discount_percent")
    roi       = listing.get("roi_estimate")
    score     = listing.get("investment_score")
    bua       = listing.get("bua_sqft")
    plot      = listing.get("plot_sqft")

    lines = []

    # 1. Building (title)
    if building:
        lines.append(f"🏢 *{building}*")

    # 2. Location: Area · Emirate
    loc_parts = [p for p in [area, emirate] if p and p != "UAE"]
    if loc_parts:
        lines.append("📍 " + "  ·  ".join(loc_parts))
    elif not building:
        lines.append("🌍 UAE")

    lines.append(SEPARATOR)

    # 3. Price block
    if price:
        p_str = f"💰 *{_fmt(price)}*"
        if deal_type == "rent":
            p_str += f"  {_t(uid, 'card_per_year')}"
        lines.append(p_str)
        # AED/m² breakdown only for sale (rent has yearly amount as-is)
        if deal_type == "sale":
            if ppf:
                lines.append(f"📐 {int(ppf * 10.764):,} AED/m²".replace(",", " "))
            elif size and size > 0:
                sqm = size * 0.0929
                if sqm > 0:
                    lines.append(f"📐 {int(price / sqm):,} AED/m²".replace(",", " "))
    else:
        lines.append(f"💰 _{_t(uid, 'card_price_request')}_")

    lines.append(SEPARATOR)

    # 4. Specs: BR · BA · Size · Floor — single line
    spec_parts = []
    br_str = _fmt_br_local(uid, br)
    if br_str:
        spec_parts.append(f"🛏 {br_str}")
    if bath is not None:
        spec_parts.append(f"🛁 {bath} {_t(uid, 'ft_bath')}")
    if size:
        spec_parts.append(f"📐 {_fmt_size(size)}")
    if floor is not None:
        spec_parts.append(f"🏗 {_t(uid, 'card_floor')} {floor}")
    if spec_parts:
        lines.append("  ·  ".join(spec_parts))

    # 4b. BUA / Plot (villa/townhouse extra info)
    bp_parts = []
    if bua and bua != size:
        bp_parts.append(f"{_t(uid, 'card_bua')} {_fmt_size(bua)}")
    if plot:
        bp_parts.append(f"{_t(uid, 'card_plot')} {_fmt_size(plot)}")
    if bp_parts:
        lines.append("📏 " + "  ·  ".join(bp_parts))

    # 5. Extras: View · Furnishing · Status
    extras = []
    if view:   extras.append(f"🌅 {view}")
    if furn:   extras.append(f"🛋 {furn.title()}")
    if status: extras.append(f"🔑 {status.title()}")
    if extras:
        lines.append("  ·  ".join(extras))

    # 5b. Extra info / description (parser-extracted JSON, optional)
    extra = listing.get("extra_info")
    if extra:
        if isinstance(extra, str):
            try:
                import json as _json
                extra = _json.loads(extra)
            except: extra = {}
        if isinstance(extra, dict) and extra:
            extra_bits = []
            if extra.get("fit_out"):       extra_bits.append(f"Fit-out: {extra['fit_out']}")
            if extra.get("usage"):         extra_bits.append(f"Usage: {extra['usage']}")
            if extra.get("tenure"):        extra_bits.append(extra['tenure'])
            if extra.get("gfa_sqft"):      extra_bits.append(f"GFA: {extra['gfa_sqft']:,} sqft")
            if extra.get("floors"):        extra_bits.append(extra['floors'])
            if extra.get("parking_spaces"): extra_bits.append(f"🚗 {extra['parking_spaces']} parking")
            if extra.get("meeting_rooms"): extra_bits.append(f"📋 {extra['meeting_rooms']} mtg rooms")
            if extra.get("maid_room"):     extra_bits.append("🛏 Maid room")
            if extra.get("study_room"):    extra_bits.append("📚 Study")
            if extra.get("balcony"):       extra_bits.append("🌿 Balcony")
            if extra.get("private_pool"):  extra_bits.append("🏊 Private pool")
            if extra.get("private_garden"): extra_bits.append("🌳 Private garden")
            if extra.get("payment_plan"):  extra_bits.append("💳 Payment plan")
            if extra.get("reception"):     extra_bits.append("Reception")
            if extra.get("pantry"):        extra_bits.append("Kitchenette")
            if extra_bits:
                lines.append("ℹ️ " + "  ·  ".join(extra_bits))

    # User-entered description (from /add wizard)
    desc = listing.get("description")
    if desc and isinstance(desc, str) and desc.strip():
        lines.append(f"📝 _{desc[:200].strip()}_")

    # 6. Analytics block (separator + items)
    analytics = []
    # Show "below market" ONLY if reasonable range (-3% .. -70%).
    # Anything > -70% means our parsed price is junk (service charge, fee, etc.) —
    # don't broadcast nonsense like "99.8% below market".
    if pct is not None and -70 < pct < -3:
        analytics.append(f"📉 {abs(round(pct, 1))}% {_t(uid, 'card_below_mkt')}")
    if disc and 5 <= disc < 80:
        analytics.append(f"🏷 {disc}% {_t(uid, 'card_below_op')}")
    if roi and deal_type == "sale" and roi < 30:    # ROI > 30%/yr тоже мусор
        analytics.append(f"📈 {_t(uid, 'card_roi')} {roi}%{_t(uid, 'card_per_year_short')}")
    if score:
        analytics.append(f"⭐ {score}/10  ·  {_t(uid, 'card_score')}")
    # v107: sравнение с премиум-сегментом района из read-model
    try:
        _lp = listing.get("price") or 0
        if deal_type == "sale" and _lp and area and _DXB_STATS_OK:
            _agg = get_area_aggregate(area)
            if _agg:
                _top = _agg.get("top_quartile_price")
                if _top and float(_top) > 0 and _lp < float(_top):
                    _diff = round((1 - _lp / float(_top)) * 100, 1)
                    if 1 < _diff < 90:
                        analytics.append(f"👑 {_diff}% below premium segment")
    except Exception:
        pass
    if analytics:
        lines.append(SEPARATOR)
        lines.extend(analytics)

    # ── INVESTMENT ANALYSIS — verdict, KPIs, vs bank, risk ─────────────────
    if INVEST_OK:
        try:
            # v51 PERF: module-level imports
            dld_b = _lookup_benchmark(listing.get("building")) if listing.get("building") else None
            mkt   = None
            try:
                if listing.get("area"):
                    mkt = get_market_metrics(listing["area"])
                if listing.get("building"):
                    better_b = get_building_benchmark(listing["building"])
                    if better_b:
                        dld_b = dict(dld_b or {})
                        for _k, _v in better_b.items():
                            if _v is not None:
                                dld_b[_k] = _v
            except Exception as _e2:
                print(f"[ecosystem] report_api skip ({listing.get('id')}): {_e2}")
            if mkt is None and listing.get("area"):
                mkt = MARKET.get(listing["area"])
            inv   = compute_investment_score(listing, dld_b, mkt)
            lang  = _get_lang(uid)
            lines.append("")
            lines.append(build_conclusion_text(listing, inv, lang))
        except Exception as _e:
            print(f"[bot] invest section: {_e}")

    return "\n".join(lines)


def format_detail(listing, uid):
    lines = []
    emirate   = listing.get("emirate") or "UAE"
    area      = listing.get("area") or ""
    building  = listing.get("building") or ""
    deal_type = listing.get("deal_type", "sale")

    lines.append(_sep())
    if building and area:
        loc = f"  {building.upper()}  ·  {area.upper()}"
        if emirate: loc += f"  ·  {emirate}"
    elif area:
        loc = f"  {area.upper()}"
        if emirate: loc += f"  ·  {emirate}"
    else:
        loc = f"  {emirate.upper()}"
    lines.append(loc)
    lines.append(_sep())

    br    = listing.get("bedrooms")
    size  = listing.get("size_sqft")
    view  = listing.get("view")
    floor = listing.get("floor")
    stat  = listing.get("status")
    furn  = listing.get("furnishing")
    ptype = listing.get("property_type", "").title()

    # ── Price — always visible ──────────────────────────────────────────────
    price          = listing.get("price")
    price_per_sqft = listing.get("price_per_sqft")
    lines.append("")
    if price:
        lines.append(f"💰 *{_fmt(price)}*")
        if price_per_sqft:
            sqm_price = int(price_per_sqft * 10.764)
            lines.append(f"📐 {sqm_price:,} AED/m²".replace(",", " "))
        elif size and size > 0:
            sqm = size * 0.0929
            if sqm > 0:
                lines.append(f"📐 {int(price / sqm):,} AED/m²".replace(",", " "))
    else:
        lines.append("💰 Цена по запросу")

    lines.append("")
    if deal_type == "rent": lines.append(f"  Type        For Rent")
    if ptype:  lines.append(f"  Property    {ptype}")
    if br is not None: lines.append(f"  Bedrooms    {_fmt_br(br)}")
    if size:   lines.append(f"  Size        {_fmt_size(size)}")
    if floor:  lines.append(f"  Floor       {floor}")
    if view:   lines.append(f"  View        {view}")
    if stat:   lines.append(f"  Status      {stat.title()}")
    if furn:   lines.append(f"  Furnishing  {furn.title()}")
    if listing.get("is_off_plan"):
        hd = listing.get("handover_date")
        hd_s = f" · Handover {hd}" if hd else ""
        lines.append(f"  Stage       Off-plan{hd_s}")
    # Extra info (commercial / plot / residential domain-specific)
    extra = listing.get("extra_info") or {}
    if isinstance(extra, dict) and extra:
        for k, v in extra.items():
            if v in (None, "", False): continue
            label = k.replace("_", " ").title()
            val   = "✓" if v is True else str(v)
            lines.append(f"  {label:11} {val}")
    # User-submitted description
    desc = listing.get("description")
    if desc:
        lines.append("")
        lines.append("  Description")
        # Wrap at ~50 chars
        for chunk in [desc[i:i+50] for i in range(0, len(desc), 50)][:6]:
            lines.append(f"  {chunk}")

    roi   = listing.get("roi_estimate")
    rent  = listing.get("market_rent_1br")
    alow  = listing.get("airbnb_estimate_low")
    ahigh = listing.get("airbnb_estimate_high")
    growth= listing.get("market_growth_pct")
    score = listing.get("investment_score")
    dq    = listing.get("deal_quality", "normal")
    pct   = listing.get("price_vs_market_percent")
    disc  = listing.get("discount_percent")

    if deal_type == "sale":
        lines.append("")
        lines.append(_sep())
        lines.append("  INVESTMENT ANALYSIS")
        lines.append(_sep())
        if score:  lines.append(f"  Score       {score} / 10")
        if roi:    lines.append(f"  ROI         {roi}% yearly")
        if rent:   lines.append(f"  Long-term   {_fmt(rent)} / year")
        if alow and ahigh:
            lines.append(f"  Airbnb      {_fmt(alow)} – {_fmt(ahigh)} / year")
        if growth: lines.append(f"  Area growth {growth}% annually")

    if dq in ("very_good", "good", "interesting"):
        lines.append("")
        lines.append(_sep())
        if dq == "very_good":   lines.append("  ▸ VERY GOOD DEAL")
        elif dq == "good":      lines.append("  ▸ GOOD DEAL")
        else:                   lines.append("  ▸ INTERESTING OFFER")
        # Sanity: hide nonsense like "99.8% below market" (artefact of bad parsing).
        if pct and -70 < pct < 0:
            lines.append(f"  {abs(round(pct,1))}% below market average")
        if disc and 3 <= disc < 80:
            lines.append(f"  {disc}% below original price")

    # Market insight from DB
    if area:
        mkt = get_market_summary(area)
        if mkt: lines.append(mkt)

    lines.append(_sep())
    return "\n".join(lines)


def format_admin(listing):
    price    = listing.get("price")
    orig     = listing.get("original_price")
    seller   = listing.get("seller_username") or "—"
    phone    = listing.get("phone") or "—"
    whatsapp = listing.get("whatsapp") or phone
    agent    = listing.get("agent_name") or "—"
    source   = listing.get("telegram_chat_id") or "—"
    msg_id   = listing.get("telegram_message_id") or "—"
    msg_link = f"https://t.me/{source.lstrip('@')}/{msg_id}" if source and msg_id else "—"

    return (
        f"🔐 *ADMIN — #{listing.get('id')}*\n\n"
        f"💰 Price: *{_fmt(price)}*\n"
        f"🏷 Original: {_fmt(orig)}\n"
        f"👤 Seller: @{seller}\n"
        f"📞 Seller Phone: {phone}\n"
        f"📱 WhatsApp: {whatsapp}\n"
        f"👔 Agent: {agent}\n"
        f"📢 Source: {source}\n"
        f"🔗 Message: {msg_link}"
    )

# ── Lead sending ──────────────────────────────────────────────────────────────
def send_lead_to_bot(uid, uname, fname, lang, listing_id):
    listing = get_listing_by_id(listing_id)
    if not listing: return

    now   = datetime.utcnow().strftime("%d.%m.%Y %H:%M UTC")
    udisp = f"@{uname}" if uname else fname or str(uid)
    lang_labels = {"en": "English", "ru": "Русский", "ar": "العربية"}

    area     = listing.get("area") or listing.get("emirate") or "UAE"
    building = listing.get("building") or "—"
    br       = _fmt_br(listing.get("bedrooms"))
    size     = _fmt_size(listing.get("size_sqft")) if listing.get("size_sqft") else "—"
    view     = listing.get("view") or "—"
    score    = listing.get("investment_score")
    roi      = listing.get("roi_estimate")
    price    = listing.get("price")
    orig     = listing.get("original_price")
    seller   = listing.get("seller_username") or "—"
    phone    = listing.get("phone") or "—"
    whatsapp = listing.get("whatsapp") or phone
    agent    = listing.get("agent_name") or "—"
    source   = listing.get("telegram_chat_id") or "—"
    msg_id   = listing.get("telegram_message_id") or "—"
    msg_link = f"https://t.me/{source.lstrip('@')}/{msg_id}" if source != "—" and msg_id != "—" else "—"

    text = (
        f"🏠 *NEW LEAD — Resale Property*\n\n"
        f"👤 Client: {udisp}\n"
        f"🆔 ID: `{uid}`\n"
        f"🌐 Language: {lang_labels.get(lang, lang)}\n"
        f"🕐 {now}\n\n"
        f"{_sep()}\n  PROPERTY\n{_sep()}\n"
        f"📍 {area}\n"
        f"🏢 {building}\n"
        f"🛏 {br}  ·  {size}\n"
        f"🌅 {view}\n"
    )
    if score: text += f"⭐️ Score: {score}/10\n"
    if roi:   text += f"📈 ROI: {roi}%\n"

    text += (
        f"\n{_sep()}\n  INTERNAL DATA\n{_sep()}\n"
        f"💰 Price: *{_fmt(price)}*\n"
    )
    if orig: text += f"🏷 Original: {_fmt(orig)}\n"
    text += (
        f"👤 Seller: @{seller}\n"
        f"📞 Phone: {phone}\n"
        f"📱 WhatsApp: {whatsapp}\n"
        f"👔 Agent: {agent}\n"
        f"📢 Source: {source}\n"
        f"🔗 Message: {msg_link}\n"
        f"{_sep()}"
    )

    try:
        requests.post(
            f"https://api.telegram.org/bot{LEAD_BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[lead] {e}")

# ── Add listing wizard ────────────────────────────────────────────────────────
ADD_STEPS = [
    "deal", "emirate", "area", "building", "type",
    "bedrooms", "size", "floor", "unit", "price",
    "status", "furnishing", "view", "description",
    "contact", "photos"
]

ADD_VIEWS = [
    "Sea View", "Burj Khalifa View", "Fountain View",
    "Marina View", "Golf View", "Canal View",
    "City View", "Community View", "Park View", "No View"
]

ADD_AREAS_DUBAI = [
    "Downtown Dubai", "Business Bay", "Dubai Marina",
    "Palm Jumeirah", "Jumeirah Village Circle", "Dubai Hills Estate",
    "Dubai Creek Harbour", "Jumeirah Beach Residence", "MBR City",
    "Al Furjan", "DAMAC Hills", "Sobha Hartland",
]
ADD_AREAS_AD = ["Yas Island", "Saadiyat Island", "Al Reem Island", "Al Raha"]
ADD_AREAS_RAK = ["Al Marjan Island", "Mina Al Arab", "Al Hamra Village"]
ADD_AREAS_SHJ = ["Al Zahia", "Aljada"]


ADD_EMIRATES = ["🇦🇪  Dubai", "🕌  Abu Dhabi", "🏝  Ras Al Khaimah", "🏙  Sharjah"]
ADD_EMIRATE_MAP = {
    "🇦🇪  Dubai": "Dubai", "🕌  Abu Dhabi": "Abu Dhabi",
    "🏝  Ras Al Khaimah": "Ras Al Khaimah", "🏙  Sharjah": "Sharjah",
}


def _reply_with_cancel(uid, rows):
    """Append [Cancel] to bottom of any /add reply keyboard."""
    rows = list(rows) + [[_t(uid, "add_cancel")]]
    return _reply_kb(rows)


def _reply_with_skip_cancel(uid):
    return _reply_kb([[_t(uid, "add_skip")], [_t(uid, "add_cancel")]])


def start_add_listing(cid, uid, mid=None):
    """/add wizard entry — now in BOTTOM reply keyboard."""
    add_states[uid] = {"step": 0, "data": {}, "photos": []}
    text = _t(uid, "add_start") + "\n\n" + _t(uid, "add_deal_q")
    kb = _reply_with_cancel(uid, [[_t(uid, "d_sale"), _t(uid, "d_rent")]])
    _send(cid, text, kb)


def add_next_step(cid, uid):
    s = add_states.get(uid, {})
    step = s.get("step", 0)
    data = s.get("data", {})

    if step >= len(ADD_STEPS):
        submit_listing(cid, uid)
        return

    current = ADD_STEPS[step]

    if current == "emirate":
        kb = _reply_with_cancel(uid, [
            [ADD_EMIRATES[0], ADD_EMIRATES[1]],
            [ADD_EMIRATES[2], ADD_EMIRATES[3]],
        ])
        _send(cid, _t(uid, "add_emirate_q"), kb)

    elif current == "area":
        emirate = data.get("emirate", "Dubai")
        areas = {
            "Dubai": ADD_AREAS_DUBAI,
            "Abu Dhabi": ADD_AREAS_AD,
            "Ras Al Khaimah": ADD_AREAS_RAK,
            "Sharjah": ADD_AREAS_SHJ,
        }.get(emirate, ADD_AREAS_DUBAI)
        # 2-column layout
        rows = []
        for i in range(0, len(areas), 2):
            rows.append(areas[i:i+2])
        rows.append([_t(uid, "add_area_custom_btn")])
        kb = _reply_with_cancel(uid, rows)
        _send(cid, _t(uid, "add_area_q"), kb)

    elif current == "building":
        add_states[uid]["waiting_text"] = "building"
        _send(cid, _t(uid, "add_building_q"),
              _reply_with_cancel(uid, []))

    elif current == "type":
        kb = _reply_with_cancel(uid, [
            [_t(uid, "pt_apt"),  _t(uid, "pt_villa")],
            [_t(uid, "pt_town"), _t(uid, "pt_pent")],
        ])
        _send(cid, _t(uid, "add_type_q"), kb)

    elif current == "bedrooms":
        kb = _reply_with_cancel(uid, [
            [_t(uid, "br_studio"), _t(uid, "br_1"), _t(uid, "br_2")],
            [_t(uid, "br_3"),      _t(uid, "br_4p")],
        ])
        _send(cid, _t(uid, "add_br_q"), kb)

    elif current == "size":
        add_states[uid]["waiting_text"] = "size"
        _send(cid, _t(uid, "add_size_q"), _reply_with_skip_cancel(uid))

    elif current == "floor":
        add_states[uid]["waiting_text"] = "floor"
        _send(cid, _t(uid, "add_floor_q"), _reply_with_skip_cancel(uid))

    elif current == "unit":
        add_states[uid]["waiting_text"] = "unit"
        _send(cid, _t(uid, "add_unit_q"), _reply_with_skip_cancel(uid))

    elif current == "description":
        add_states[uid]["waiting_text"] = "description"
        _send(cid, _t(uid, "add_description_q"), _reply_with_skip_cancel(uid))

    elif current == "price":
        add_states[uid]["waiting_text"] = "price"
        _send(cid, _t(uid, "add_price_q"), _reply_with_cancel(uid, []))

    elif current == "status":
        kb = _reply_with_cancel(uid, [
            [_t(uid, "add_status_vacant"), _t(uid, "add_status_rented")],
        ])
        _send(cid, _t(uid, "add_status_q"), kb)

    elif current == "furnishing":
        kb = _reply_with_cancel(uid, [
            [_t(uid, "add_furn_yes"),  _t(uid, "add_furn_no")],
            [_t(uid, "add_furn_semi")],
        ])
        _send(cid, _t(uid, "add_furn_q"), kb)

    elif current == "view":
        rows = []
        for i in range(0, len(ADD_VIEWS), 2):
            rows.append(ADD_VIEWS[i:i+2])
        _send(cid, _t(uid, "add_view_q"), _reply_with_cancel(uid, rows))

    elif current == "contact":
        add_states[uid]["waiting_text"] = "contact"
        _send(cid, _t(uid, "add_contact_q"), _reply_with_cancel(uid, []))

    elif current == "photos":
        add_states[uid]["waiting_text"] = "photos"
        _send(cid, _t(uid, "add_photo_q"), _reply_with_skip_cancel(uid))


def _label_all_langs(key):
    """Return the labels of a translation key across all 3 languages."""
    return [T[l].get(key) for l in ("en", "ru", "ar") if T[l].get(key)]


def dispatch_add_button(cid, uid, text):
    """Handle a press of a reply-keyboard button inside the /add wizard.
    Returns True if the text matched a button and was handled."""
    s = add_states.get(uid)
    if not s:
        return False

    # Universal — Cancel
    if text in _label_all_langs("add_cancel"):
        add_states.pop(uid, None)
        _send(cid, _t(uid, "add_cancelled"), kb_main_reply(uid))
        return True

    step = s.get("step", 0)
    if step >= len(ADD_STEPS):
        return False
    current = ADD_STEPS[step]

    # Universal — Skip (only on skippable steps)
    if text in _label_all_langs("add_skip"):
        if current in ("size", "floor", "unit", "description"):
            s["waiting_text"] = None
            s["step"] += 1
            add_states[uid] = s
            add_next_step(cid, uid)
            return True
        if current == "photos":
            s["step"] = len(ADD_STEPS)
            add_states[uid] = s
            submit_listing(cid, uid)
            return True

    def _advance(field, value):
        s["data"][field] = value
        s["waiting_text"] = None
        s["step"] += 1
        add_states[uid] = s
        add_next_step(cid, uid)
        return True

    # Step 0 — deal
    if current == "deal":
        if text in _label_all_langs("d_sale"): return _advance("deal", "sale")
        if text in _label_all_langs("d_rent"): return _advance("deal", "rent")

    if current == "emirate":
        if text in ADD_EMIRATE_MAP:
            return _advance("emirate", ADD_EMIRATE_MAP[text])

    if current == "area":
        emirate = s.get("data", {}).get("emirate", "Dubai")
        areas = {
            "Dubai": ADD_AREAS_DUBAI, "Abu Dhabi": ADD_AREAS_AD,
            "Ras Al Khaimah": ADD_AREAS_RAK, "Sharjah": ADD_AREAS_SHJ,
        }.get(emirate, ADD_AREAS_DUBAI)
        if text in areas:
            return _advance("area", text)
        if text in _label_all_langs("add_area_custom_btn"):
            s["waiting_text"] = "custom_area"
            add_states[uid] = s
            _send(cid, _t(uid, "add_area_custom_q"), _reply_with_cancel(uid, []))
            return True

    if current == "type":
        for k, v in [("pt_apt","apartment"), ("pt_villa","villa"),
                     ("pt_town","townhouse"), ("pt_pent","penthouse")]:
            if text in _label_all_langs(k):
                return _advance("type", v)

    if current == "bedrooms":
        for k, v in [("br_studio",0),("br_1",1),("br_2",2),("br_3",3),("br_4p",4)]:
            if text in _label_all_langs(k):
                return _advance("bedrooms", v)

    if current == "status":
        if text in _label_all_langs("add_status_vacant"): return _advance("status", "vacant")
        if text in _label_all_langs("add_status_rented"): return _advance("status", "rented")

    if current == "furnishing":
        for k, v in [("add_furn_yes","furnished"),("add_furn_no","unfurnished"),
                     ("add_furn_semi","semi-furnished")]:
            if text in _label_all_langs(k):
                return _advance("furnishing", v)

    if current == "view":
        if text in ADD_VIEWS:
            return _advance("view", text)

    return False


def submit_listing(cid, uid):
    s = add_states.get(uid, {})
    data = s.get("data", {})
    photos = s.get("photos", [])

    # Format moderation message for admin
    lang = user_lang.get(uid, "en")
    uname = ""

    text = (
        f"🏠 *NEW LISTING FOR MODERATION*\n\n"
        f"👤 From: {uid}\n"
        f"📅 {datetime.utcnow().strftime('%d.%m.%Y %H:%M UTC')}\n\n"
        f"{_sep()}\n"
        f"  {data.get('deal', 'sale').upper()}  ·  {data.get('type', 'apartment').upper()}\n"
        f"{_sep()}\n\n"
        f"📍 {data.get('area', '—')}, {data.get('emirate', '—')}\n"
        f"🏢 {data.get('building', '—')}\n"
        f"🛏 {_fmt_br(data.get('bedrooms'))}  ·  {data.get('size', '—')} sqft\n"
        f"💰 {data.get('price', '—')}\n"
        f"🔑 {data.get('status', '—')}  ·  {data.get('furnishing', '—')}\n"
        f"🌅 {data.get('view', '—')}\n"
        f"📞 {data.get('contact', '—')}\n"
        f"📸 Photos: {len(photos)}\n"
        f"{_sep()}"
    )

    # Store pending listing in DB for moderation
    listing_json = json.dumps({**data, "uid": uid, "photos": photos})
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pending_listings (uid, data) VALUES (%s, %s) RETURNING id",
                (uid, listing_json)
            )
            pending_id = cur.fetchone()["id"]
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[add] DB error: {e}")
        pending_id = 0

    # Send to admin for moderation
    kb = _kb(
        [_btn("✅  Approve", f"mod|approve|{pending_id}"),
         _btn("❌  Reject",  f"mod|reject|{pending_id}")],
    )

    if photos:
        photo_urls = [_get_file_url(p) for p in photos[:10]]
        try:
            _media_group(ADMIN_ID, photo_urls, text)
            _send(ADMIN_ID, "Use buttons to moderate:", kb)
        except:
            _send(ADMIN_ID, text, kb)
    else:
        _send(ADMIN_ID, text, kb)

    # Notify user — return to bottom main menu
    _send(cid, _t(uid, "add_done"), kb_main_reply(uid))
    add_states.pop(uid, None)


# ── Search ────────────────────────────────────────────────────────────────────
def _track(uid, action):
    """Track user activity."""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET searches_count = searches_count + 1, last_seen = NOW() WHERE telegram_id = %s",
                (uid,)
            )
        conn.commit()
        conn.close()
    except:
        pass


def do_search(uid, extra=None):
    s = gs(uid)
    filters = dict(s.get("filters", {}))
    if extra: filters.update(extra)
    # Apply default deal preference when no explicit deal_type filter is set
    if "deal_type" not in filters and s.get("default_deal"):
        filters["deal_type"] = s["default_deal"]
    # SAFETY NET: if user came from the wizard via a category button
    # (Купить / Снять / Коммерция / Земля) we expect the corresponding
    # filter to be set. If it has been silently dropped somewhere in the
    # flow, recover it from the wizard hint so SALE queries never leak
    # rent / commercial / plot listings (and vice versa).
    wiz_deal = s.get("wizard_deal_hint")
    if wiz_deal and "deal_type" not in filters:
        filters["deal_type"] = wiz_deal
    wiz_pt_not_in = s.get("wizard_pt_not_in_hint")
    if wiz_pt_not_in and "property_type_not_in" not in filters \
       and "property_type_in" not in filters \
       and "property_type" not in filters:
        filters["property_type_not_in"] = wiz_pt_not_in
    wiz_pt_in = s.get("wizard_pt_in_hint")
    if wiz_pt_in and "property_type_in" not in filters \
       and "property_type" not in filters:
        filters["property_type_in"] = wiz_pt_in
    wiz_pt = s.get("wizard_pt_hint")
    if wiz_pt and "property_type" not in filters \
       and "property_type_in" not in filters:
        filters["property_type"] = wiz_pt
    results, total = search_listings(filters, limit=PER_PAGE * 5)
    s["results"] = results
    s["total"]   = total
    s["page"]    = 0
    _track(uid, "search")
    print(f"[SEARCH] filters={filters} total={total}")
    return results


def send_results(cid, uid, mid=None):
    s       = gs(uid)
    results = s.get("results", [])
    page    = s.get("page", 0)
    total   = s.get("total", 0)

    if not results:
        # При 0 results — предлагаем расширить фильтры (убрать самый строгий)
        filters = s.get("filters", {})
        relax_buttons = []
        if filters.get("building"):
            relax_buttons.append([_btn(f"❌ Без здания: «{filters['building'][:20]}»",
                                        "relax|building")])
        if filters.get("min_price") or filters.get("max_price"):
            relax_buttons.append([_btn("💰 Без ограничения бюджета", "relax|budget")])
        if filters.get("bedrooms") is not None:
            relax_buttons.append([_btn("🛏 Без фильтра спален", "relax|bedrooms")])
        if filters.get("area"):
            relax_buttons.append([_btn(f"📍 Без района: «{filters['area'][:20]}»",
                                        "relax|area")])
        relax_buttons.append([_btn(_t(uid, "btn_menu"), "menu|main")])
        kb = _kb(*relax_buttons)
        # Текст с подсказкой что фильтры можно ослабить
        hint_text = _t(uid, "no_results")
        if any(filters.get(k) for k in ("building","area","bedrooms","min_price","max_price")):
            hint_text += "\n\n_Попробуйте убрать один из фильтров ниже:_"
        if mid: _edit(cid, mid, hint_text, kb)
        else:   _send(cid, hint_text, kb)
        return

    start = page * PER_PAGE
    end   = min(start + PER_PAGE, len(results))
    items = results[start:end]

    deal_type = gs(uid).get("filters", {}).get("deal_type", "sale")
    type_label = "FOR RENT" if deal_type == "rent" else "FOR SALE"
    header = f"{_sep()}\n  {total} PROPERTIES  ·  {type_label}\n{_sep()}"
    if mid: _edit(cid, mid, header)
    else:   _send(cid, header)

    # v52 PERF: batch-load favorites for this page (1 SQL instead of N)
    try:
        from db_schema import is_favorited_batch as _is_fav_batch
        page_lids = [lst.get("id") for lst in items if lst.get("id")]
        _fav_set = _is_fav_batch(uid, page_lids)
    except Exception:
        _fav_set = set()

    for i, lst in enumerate(items, start=start+1):
        text = format_card(lst, uid, rank=i)
        lid  = lst.get("id") or lst["id"]
        try:
            fav_now = lid in _fav_set
        except Exception:
            fav_now = False
        fav_label = _t(uid, "btn_fav_rem") if fav_now else _t(uid, "btn_fav_add")
        has_building = bool(lst.get("building"))
        # v54 UX (Layla): «Оставить заявку» (btn_book) убрана с карточки списка —
        # cluttering CTA. Кнопка остаётся на детальном экране (btn_analysis →
        # «Инвестиционный анализ»), где сохранена _url_btn lead-bot (см. ~строку 3251).
        # Callback `book|{lid}` handler не удалён — обратная совместимость.
        kb_rows = [
            [_btn(_t(uid, "btn_analysis"), f"detail|{lid}")],
            [_btn(fav_label,               f"fav|{lid}"),    _btn(_t(uid, "btn_compare"), f"cmp|{lid}")],
            [_btn(_t(uid, "btn_map"),      f"map|{lid}"),    _btn(_t(uid, "btn_photos"),  f"photos|{lid}")],
        ]
        if has_building:
            kb_rows.append([_btn(_t(uid, "btn_all_in_bld"), f"allbld|{lid}")])
        kb_rows.append([_btn(_t(uid, "btn_similar"),  f"similar|{lid}"), _btn(_t(uid, "btn_send"),   f"send|{lid}")])
        if PDF_OK:
            pdf_lbl = {"en":"📄 Investment PDF","ru":"📄 Инвест-отчёт PDF",
                       "ar":"📄 تقرير PDF"}.get(_get_lang(uid), "📄 Investment PDF")
            kb_rows.append([_btn(pdf_lbl, f"pdf|{lid}")])
        kb = _kb(*kb_rows)
        # Send with photos (file_id stored directly from Bot API upload)
        images = get_listing_images(lid) if lid else []
        photo_sent = False
        if images:
            for img in images[:1]:
                if img.startswith("tg://"):   # legacy invalid format — skip
                    break
                try:
                    _photo(cid, img, text, kb)   # img IS a Bot API file_id
                    time.sleep(0.3)
                    save_lead(uid, "", lid, "view")
                    photo_sent = True
                except Exception:
                    pass
        if photo_sent:
            continue

        cover = lst.get("cover_image_url")
        if cover and not cover.startswith("tg://"):
            try:
                _photo(cid, cover, text, kb)   # cover is a Bot API file_id
                time.sleep(0.3)
                save_lead(uid, "", lid, "view")
                continue
            except Exception:
                pass

        _send(cid, text, kb)
        save_lead(uid, "", lid, "view")
        time.sleep(0.3)

    remaining = len(results) - end
    if remaining > 0:
        s["page"] += 1
    # Mark wizard state so dispatch_wizard_button can route navigation presses
    s["wizard"] = "results"
    s["results_has_more"] = remaining > 0
    footer = _sep()
    if remaining > 0:
        footer += f"\n\n{_t(uid, 'btn_more', n=remaining)}"
    _send(cid, footer, kb_reply_results(uid, has_more=remaining > 0))


# ── LLM с fallback Claude → Groq → None ─────────────────────────────────────
# Claude — премиум для AI-аргументации (если кредиты есть).
# Groq — бесплатный fallback (Llama 3.3 70B, OpenAI-compatible API).
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL   = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")


def _llm_call(prompt: str, max_tokens: int = 600, timeout: int = 20) -> str | None:
    """Универсальный LLM-вызов. Сначала Claude, при ошибке/нет ключа → Groq.
    Returns текст ответа или None если оба упали."""
    # 1) Claude (если есть key и credits)
    if ANTHROPIC_KEY:
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001",
                      "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=timeout,
            )
            if resp.status_code == 200:
                return resp.json()["content"][0]["text"].strip()
            # 400 = balance too low / 401 = invalid key → fallback на Groq
            print(f"[llm] Claude HTTP {resp.status_code}: {resp.text[:120]}, falling back to Groq")
        except Exception as e:
            print(f"[llm] Claude error: {e}, falling back to Groq")

    # 2) Groq (бесплатный fallback)
    if GROQ_API_KEY:
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": GROQ_MODEL,
                      "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=timeout,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
            print(f"[llm] Groq HTTP {resp.status_code}: {resp.text[:120]}")
        except Exception as e:
            print(f"[llm] Groq error: {e}")

    return None


def claude_translate(text, target_lang="en"):
    """Translate any text to target_lang via LLM (Claude → Groq fallback)."""
    if not text: return None
    lang_full = {"en": "English", "ru": "Russian", "ar": "Arabic"}.get(target_lang, "English")
    prompt = (
        f"Translate the following UAE real estate listing to {lang_full}. "
        f"Preserve numbers, prices, and proper names. Return ONLY the "
        f"translated text, no preface:\n\n{text[:1500]}"
    )
    return _llm_call(prompt, max_tokens=600, timeout=15)


def claude_parse(text, lang="en"):
    """Use LLM to parse a free-form real estate query into filters.
    Supports EN/RU/AR with slang ("у моря", "семейный район", "за моллом").
    Uses Claude → Groq fallback chain."""
    if not (ANTHROPIC_KEY or GROQ_API_KEY): return {}
    prompt = (
        "You are a UAE real estate query parser. The user can write in English, "
        "Russian, or Arabic, with slang, typos, and lifestyle hints. Return ONLY "
        "valid JSON — no prose.\n\n"
        "Schema (use null for any missing field):\n"
        "{\n"
        '  "emirate": "Dubai|Abu Dhabi|Sharjah|Ras Al Khaimah|null",\n'
        '  "area": "string|null  (e.g. Dubai Marina, JBR, Downtown, Palm)",\n'
        '  "building": "string|null",\n'
        '  "deal_type": "sale|rent|null",\n'
        '  "property_type": "apartment|villa|townhouse|penthouse|studio|duplex|office|retail|warehouse|hotel|whole_building|plot|null",\n'
        '  "bedrooms": "int (0=studio)|null",\n'
        '  "min_price": "int AED|null",\n'
        '  "max_price": "int AED|null",\n'
        '  "view": "sea|burj|fountain|marina|golf|park|city|null",\n'
        '  "furnishing": "furnished|unfurnished|semi-furnished|null",\n'
        '  "hot_only": "true|false|null",\n'
        '  "sort": "best_deals|newest|price_asc|null"\n'
        "}\n\n"
        "Slang mapping examples:\n"
        '  "у моря / sea view / пляж" → view: "sea"\n'
        '  "семья / family-friendly / с детьми" → area: "Dubai Hills Estate" or "JVC"\n'
        '  "тихий район / quiet" → area: "Dubai Hills Estate" or "The Springs"\n'
        '  "премиум / люкс / luxury" → area: "Downtown Dubai" or "Palm Jumeirah"\n'
        '  "для аренды / для сдачи / Airbnb" → deal_type: "sale" (intent to buy for rent)\n'
        '  "снять / арендовать / rent" → deal_type: "rent"\n'
        '  "до 5 миллионов / до 5М / under 5M" → max_price: 5000000\n'
        '  "от 2М / over 2M / больше 2 миллионов" → min_price: 2000000\n'
        '  "выгодно / hot deals / ниже рынка" → hot_only: true\n'
        '  "новые / свежие / newest" → sort: "newest"\n\n'
        f'Query ({lang}): "{text}"\n'
        "JSON:"
    )
    raw = _llm_call(prompt, max_tokens=400, timeout=4)  # v131: cap hot-path LLM at 4s
    if not raw:
        return {}
    try:
        # Extract JSON object (may be wrapped in code fences / extra text)
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m: return {}
        parsed = json.loads(m.group())
        # Clean nulls and falsy
        out = {}
        for k, v in parsed.items():
            if v is None or v == "" or v == "null":
                continue
            if k in ("min_price", "max_price", "bedrooms") and isinstance(v, str):
                try: v = int(re.sub(r'[^\d]', '', v))
                except: continue
            out[k] = v
        return out
    except Exception as e:
        print(f"[claude_parse] {e}")
        return {}


def parse_nl(text, lang="en"):
    from parser_engine import detect_area, detect_emirate_direct
    filters = {}
    tl = text.lower()

    emirate, _ = detect_emirate_direct(text)
    if emirate: filters["emirate"] = emirate

    area, _, aem, _ = detect_area(text)
    if area:
        filters["area"] = area
        if aem and not filters.get("emirate"): filters["emirate"] = aem

    if any(k in tl for k in ["rent", "аренда", "إيجار", "for rent", "to rent"]):
        filters["deal_type"] = "rent"
    elif any(k in tl for k in ["sale", "продаж", "بيع", "for sale", "buy"]):
        filters["deal_type"] = "sale"

    if "studio" in tl or "студ" in tl: filters["bedrooms"] = 0
    else:
        m = re.search(r'(\d)\s*(?:br|bed|спал|غرف)', tl)
        if m: filters["bedrooms"] = int(m.group(1))

    for pat, view in [("море|sea|ocean|بحر", "sea view"),
                      ("burj|бурдж", "burj view"),
                      ("fountain|фонтан", "fountain view"),
                      ("marina|марин", "marina view")]:
        if re.search(pat, tl): filters["view"] = view; break

    m = re.search(r'до\s*(\d+(?:\.\d+)?)\s*[мm]', tl)
    if m: filters["max_price"] = float(m.group(1))
    m = re.search(r'(\d+(?:\.\d+)?)\s*m\b', tl)
    if m and "max_price" not in filters: filters["max_price"] = float(m.group(1))

    # Rent budget
    m = re.search(r'(\d+(?:\.\d+)?)\s*k\s*(?:aed|per year|в год)?', tl)
    if m and filters.get("deal_type") == "rent":
        filters["max_price"] = float(m.group(1)) * 1000 / 1_000_000

    if any(k in tl for k in ["hot", "deal", "выгод", "скидк", "below market", "дешев"]):
        filters["hot_only"] = True

    filters.setdefault("sort", "best_deals")

    # v131(perf): LLM enrich ONLY if regex failed to extract structured fields.
    # Hot-path target <500ms — LLM call (~3-8s) запрещён если regex уже нашёл
    # area/deal_type/max_price. Для сложных free-text запросов где regex даёт
    # пусто — короткий timeout=3s и Claude fills only missing slots.
    _has_structured = any(filters.get(k) for k in ("area", "deal_type", "max_price", "bedrooms", "view"))
    if (not _has_structured) and len(text.split()) >= 3 and ANTHROPIC_KEY:
        cf = claude_parse(text, lang)
        if cf:
            for k, v in cf.items():
                if k not in filters and v is not None:
                    filters[k] = v

    filters.setdefault("sort", "best_deals")
    return filters


# ── AI Advisor ────────────────────────────────────────────────────────────────
def run_ai_recommend(cid, uid):
    """Execute the AI Assistant recommendation based on collected ai_data.
    Called after user picks budget in the reply-keyboard AI flow."""
    s = gs(uid)
    ai = s.get("ai_data", {})
    goal      = ai.get("goal", "invest")
    strategy  = ai.get("strategy", "longterm")
    lifestyle = ai.get("lifestyle", "downtown")
    comm_type = ai.get("comm_type")

    extra_filters = {}
    if goal == "invest":
        areas = get_best_areas_from_db(strategy)
        if not areas:
            fallback = {
                "airbnb":   ["Downtown Dubai", "Dubai Marina", "Palm Jumeirah", "Jumeirah Beach Residence"],
                "longterm": ["Jumeirah Village Circle", "Dubai Hills Estate", "Business Bay"],
                "resale":   ["Downtown Dubai", "Dubai Marina", "Palm Jumeirah"],
                "growth":   ["Dubai Creek Harbour", "MBR City", "Dubai Hills Estate"],
            }
            areas = fallback.get(strategy, ["Downtown Dubai", "Business Bay"])
    elif goal == "live":
        lifestyle_map = {
            "sea":      ["Dubai Marina", "Jumeirah Beach Residence", "Palm Jumeirah", "Emaar Beachfront", "Bluewaters Island"],
            "downtown": ["Downtown Dubai", "Business Bay", "DIFC", "City Walk"],
            "family":   ["Dubai Hills Estate", "Jumeirah Village Circle", "Meydan", "Arabian Ranches", "The Springs"],
            "premium":  ["Palm Jumeirah", "Downtown Dubai", "Bluewaters Island", "Jumeirah Golf Estates", "Emirates Hills"],
            "nature":   ["Dubai Hills Estate", "The Valley", "Tilal Al Ghaf", "Mudon", "Dubailand"],
            "business": ["Business Bay", "DIFC", "Downtown Dubai", "Dubai Marina"],
        }
        areas = lifestyle_map.get(lifestyle, ["Downtown Dubai", "Dubai Marina"])
    elif goal == "commercial":
        comm_areas = {
            "office":    ["Business Bay", "DIFC", "Downtown Dubai", "Sheikh Zayed Road"],
            "retail":    ["Dubai Marina", "Downtown Dubai", "City Walk", "JBR"],
            "warehouse": ["Al Quoz", "Dubai Investment Park", "Jebel Ali"],
            "hotel":     ["Palm Jumeirah", "Downtown Dubai", "JBR"],
            "any":       ["Business Bay", "DIFC", "Downtown Dubai"],
        }
        areas = comm_areas.get(comm_type, ["Business Bay", "DIFC"])
        if comm_type and comm_type != "any":
            extra_filters["property_type_in"] = [comm_type]
        else:
            extra_filters["property_type_in"] = COMMERCIAL_TYPES
    elif goal == "land":
        areas = ["Dubai South", "Dubai Investment Park", "Al Furjan", "Tilal Al Ghaf", "MBR City"]
        extra_filters["property_type"] = "plot"
    else:
        areas = ["Downtown Dubai", "Dubai Marina", "Jumeirah Village Circle"]

    summary_text = ""
    if areas:
        mkt = get_market_summary(areas[0], strategy)
        if mkt:
            summary_text = mkt

    filters = dict(s.get("filters", {}))
    filters.update(extra_filters)
    # Apply budget collected during AI flow
    if ai.get("min_price"): filters["min_price"] = ai["min_price"]
    if ai.get("max_price"): filters["max_price"] = ai["max_price"]

    # v131(perf): parallel area search via ThreadPoolExecutor.
    # Раньше: 5 sequential SQL roundtrips × ~80ms = ~400ms.
    # Теперь: max(5 parallel) ≈ 80-100ms, целевой <500ms end-to-end.
    import time as _t_perf
    _t0 = _t_perf.perf_counter()
    best = []
    try:
        from concurrent.futures import ThreadPoolExecutor
        def _one(area):
            r, _tot = search_listings({**filters, "area": area, "sort": "best_deals"}, limit=3)
            return r
        with ThreadPoolExecutor(max_workers=5) as _pool:
            for r in _pool.map(_one, areas[:5]):
                best.extend(r)
    except Exception as _e:
        print(f"[ai_recommend] parallel search failed: {_e} — fallback sequential", flush=True)
        for area in areas[:5]:
            r, _tot = search_listings({**filters, "area": area, "sort": "best_deals"}, limit=3)
            best.extend(r)
    if not best:
        best, _tot = search_listings({**filters, "sort": "best_deals"}, limit=10)

    _dt_ms = int((_t_perf.perf_counter() - _t0) * 1000)
    print(f"[LAT] ai_recommend_search: {_dt_ms}ms ({len(best)} results, {len(areas[:5])} areas)", flush=True)

    s["results"] = best; s["total"] = len(best); s["page"] = 0
    header = _t(uid, "ai_result") + summary_text
    _send(cid, header)
    send_results(cid, uid)


def show_ai_start(cid, uid, mid=None):
    """AI Assistant entry — now uses BOTTOM reply keyboard (not inline)."""
    s = gs(uid)
    s["ai_step"] = 1
    s["ai_data"] = {}
    s["wizard"] = "ai_goal"
    text = _t(uid, "ai_start") + "\n\n" + _t(uid, "ai_goal_q")
    _send(cid, text, kb_reply_ai_goal(uid))


def handle_ai(cid, uid, mid, parts):
    s = gs(uid); ai = s.get("ai_data", {})
    if len(parts) < 2: return
    action = parts[1]

    if action == "goal":
        goal = parts[2] if len(parts) > 2 else "unsure"
        ai["goal"] = goal; s["ai_data"] = ai
        if goal == "invest":
            text = _t(uid, "ai_inv_q")
            kb = _kb(
                [_btn(_t(uid, "ai_inv_longterm"), "ai|strategy|longterm"), _btn(_t(uid, "ai_inv_airbnb"), "ai|strategy|airbnb")],
                [_btn(_t(uid, "ai_inv_resale"),   "ai|strategy|resale"),   _btn(_t(uid, "ai_inv_growth"), "ai|strategy|growth")],
                [_btn("🔍  Find Properties", "ai|recommend")],
            )
        elif goal == "live":
            text = _t(uid, "ai_life_q")
            kb = _kb(
                [_btn(_t(uid, "ai_l_downtown"), "ai|lifestyle|downtown"), _btn(_t(uid, "ai_l_sea"),      "ai|lifestyle|sea")],
                [_btn(_t(uid, "ai_l_family"),   "ai|lifestyle|family"),   _btn(_t(uid, "ai_l_premium"),  "ai|lifestyle|premium")],
                [_btn(_t(uid, "ai_l_nature"),   "ai|lifestyle|nature"),   _btn(_t(uid, "ai_l_business"), "ai|lifestyle|business")],
                [_btn("🔍  Find Properties", "ai|recommend")],
            )
        elif goal == "commercial":
            # Commercial sub-type wizard
            text = _t(uid, "ai_commercial_q")
            kb = _kb(
                [_btn(_t(uid, "pt_office_btn"),    "ai|commtype|office"),
                 _btn(_t(uid, "pt_retail_btn"),    "ai|commtype|retail")],
                [_btn(_t(uid, "pt_warehouse_btn"), "ai|commtype|warehouse"),
                 _btn(_t(uid, "pt_hotel_btn"),     "ai|commtype|hotel")],
                [_btn(_t(uid, "pt_any_btn"),       "ai|commtype|any")],
                [_btn("🔍  Find Properties", "ai|recommend")],
            )
        elif goal == "land":
            # Land/plot — go straight to budget
            ai["land_only"] = True; s["ai_data"] = ai
            _edit(cid, mid, _t(uid, "budget_q"), kb_budget(uid, is_plot=True))
            return
        else:
            _edit(cid, mid, _t(uid, "budget_q"), kb_budget(uid))
            return
        _edit(cid, mid, text, kb)

    elif action == "commtype":
        ai["comm_type"] = parts[2] if len(parts) > 2 else "any"
        s["ai_data"] = ai
        _edit(cid, mid, _t(uid, "budget_q"), kb_budget(uid, is_commercial=True))

    elif action == "strategy":
        ai["strategy"] = parts[2] if len(parts) > 2 else "mixed"
        s["ai_data"] = ai
        _edit(cid, mid, _t(uid, "budget_q"), kb_budget(uid))

    elif action == "lifestyle":
        ai["lifestyle"] = parts[2] if len(parts) > 2 else "downtown"
        s["ai_data"] = ai
        _edit(cid, mid, _t(uid, "budget_q"), kb_budget(uid))

    elif action == "recommend":
        _edit(cid, mid, _t(uid, "ai_analyzing"))
        time.sleep(1)

        goal      = ai.get("goal", "invest")
        strategy  = ai.get("strategy", "longterm")
        lifestyle = ai.get("lifestyle", "downtown")
        comm_type = ai.get("comm_type")

        # Set up filters by goal
        extra_filters = {}
        # Dynamic areas from market_data
        if goal == "invest":
            areas = get_best_areas_from_db(strategy)
            if not areas:
                fallback = {
                    "airbnb":   ["Downtown Dubai", "Dubai Marina", "Palm Jumeirah", "Jumeirah Beach Residence"],
                    "longterm": ["Jumeirah Village Circle", "Dubai Hills Estate", "Business Bay"],
                    "resale":   ["Downtown Dubai", "Dubai Marina", "Palm Jumeirah"],
                    "growth":   ["Dubai Creek Harbour", "MBR City", "Dubai Hills Estate"],
                }
                areas = fallback.get(strategy, ["Downtown Dubai", "Business Bay"])
        elif goal == "live":
            lifestyle_map = {
                "sea":      ["Dubai Marina", "Jumeirah Beach Residence", "Palm Jumeirah", "Emaar Beachfront", "Bluewaters Island"],
                "downtown": ["Downtown Dubai", "Business Bay", "DIFC", "City Walk"],
                "family":   ["Dubai Hills Estate", "Jumeirah Village Circle", "Meydan", "Arabian Ranches", "The Springs"],
                "premium":  ["Palm Jumeirah", "Downtown Dubai", "Bluewaters Island", "Jumeirah Golf Estates", "Emirates Hills"],
                "nature":   ["Dubai Hills Estate", "The Valley", "Tilal Al Ghaf", "Mudon", "Dubailand"],
                "business": ["Business Bay", "DIFC", "Downtown Dubai", "Dubai Marina"],
            }
            areas = lifestyle_map.get(lifestyle, ["Downtown Dubai", "Dubai Marina"])
        elif goal == "commercial":
            # Best commercial areas by sub-type
            comm_areas = {
                "office":    ["Business Bay", "DIFC", "Downtown Dubai", "Sheikh Zayed Road"],
                "retail":    ["Dubai Marina", "Downtown Dubai", "City Walk", "JBR"],
                "warehouse": ["Al Quoz", "Dubai Investment Park", "Jebel Ali"],
                "hotel":     ["Palm Jumeirah", "Downtown Dubai", "JBR"],
                "any":       ["Business Bay", "DIFC", "Downtown Dubai"],
            }
            areas = comm_areas.get(comm_type, ["Business Bay", "DIFC"])
            if comm_type and comm_type != "any":
                extra_filters["property_type_in"] = [comm_type]
            else:
                extra_filters["property_type_in"] = COMMERCIAL_TYPES
        elif goal == "land":
            # Plot search across hot plot areas
            areas = ["Dubai South", "Dubai Investment Park", "Al Furjan", "Tilal Al Ghaf", "MBR City"]
            extra_filters["property_type"] = "plot"
        else:
            areas = ["Downtown Dubai", "Dubai Marina", "Jumeirah Village Circle"]

        # Generate market summary for top area
        summary_text = ""
        if areas:
            mkt = get_market_summary(areas[0], strategy)
            if mkt:
                summary_text = mkt

        filters = dict(s.get("filters", {}))
        filters.update(extra_filters)
        best = []
        for area in areas[:5]:
            r, _ = search_listings({**filters, "area": area, "sort": "best_deals"}, limit=3)
            best.extend(r)
        if not best:
            best, _ = search_listings({**filters, "sort": "best_deals"}, limit=10)

        s["results"] = best; s["total"] = len(best); s["page"] = 0

        header = _t(uid, "ai_result") + summary_text
        _edit(cid, mid, header)
        send_results(cid, uid)


# ── Detail view ───────────────────────────────────────────────────────────────
def show_detail(cid, uid, mid, lid):
    listing = get_listing_by_id(lid)
    if not listing:
        _edit(cid, mid, "Property not found."); return
    # v49 AUDIT FIX: hide audit_flagged listings from user-facing detail view
    # (admin queue uses separate code path)
    if listing.get("status") == "audit_flagged" and uid != ADMIN_ID:
        _edit(cid, mid, "Listing currently under review. Try another one or use search.")
        return

    save_lead(uid, "", lid, "view")
    text     = format_detail(listing, uid)
    lead_url = f"{LEAD_BOT_URL}?start=resale_{lid}"
    from db_schema import is_favorited as _is_fav
    try:
        fav_now = _is_fav(uid, lid)
    except Exception:
        fav_now = False
    fav_label = _t(uid, "btn_fav_rem") if fav_now else _t(uid, "btn_fav_add")
    # v55: watchlist toggle button — подписка на этот конкретный listing
    try:
        from db_schema import is_watching as _is_watch
        watch_now = _is_watch(uid, "listing", str(lid))
    except Exception:
        watch_now = False
    watch_label = _t(uid, "btn_watch_rem") if watch_now else _t(uid, "btn_watch_add")
    lang_user = user_lang.get(uid, "en")
    has_building = bool(listing.get("building"))
    kb_rows = [
        [_url_btn(_t(uid, "btn_book"), lead_url)],
        [_btn(fav_label,              f"fav|{lid}"),    _btn(_t(uid, "btn_compare"), f"cmp|{lid}")],
        [_btn(watch_label,            f"watch|{lid}")],
        [_btn(_t(uid, "btn_map"),     f"map|{lid}"),    _btn(_t(uid, "btn_photos"),  f"photos|{lid}")],
        [_btn("🌐 Translate",         f"translate|{lid}|{lang_user}")],
    ]
    # All-in-building button — только если есть building
    if has_building:
        kb_rows.append([_btn(_t(uid, "btn_all_in_bld"), f"allbld|{lid}")])
    kb_rows.append([_btn(_t(uid, "btn_similar"), f"similar|{lid}"), _btn(_t(uid, "btn_back"), "results|back")])
    # v47 ECOSYSTEM cross-nav: subtle marketing — leads пользователя в смежные боты
    # когда он смотрит конкретный listing (highly intent moment).
    # v55: расширили payload до from_resale_utm_resale_card_detail для UTM tracking.
    kb_rows.append([
        _url_btn("🏗 Новостройки от застройщика",
                 "https://t.me/dubai_projects_monitor_bot?start=from_resale_utm_resale_card_detail"),
    ])
    kb_rows.append([
        _url_btn("📊 ROI калькулятор",
                 "https://t.me/dubai_roi_fpr_bot?start=from_resale_utm_resale_card_detail"),
        _url_btn("📈 Аналитика района",
                 "https://t.me/Analitik_price_bot?start=from_resale_utm_resale_card_detail"),
    ])
    kb_rows.append([_btn(_t(uid, "btn_menu"), "menu|main")])
    kb = _kb(*kb_rows)

    # Try to show photos
    images = get_listing_images(lid)
    if images:
        urls = []
        for img in images[:10]:
            url = _get_file_url(img) if not img.startswith("http") else img
            if url: urls.append(url)
        if len(urls) > 1:
            try:
                _media_group(cid, urls, text[:1024])
                _send(cid, _sep(), kb)
                if cid != ADMIN_ID:
                    _api("sendMessage", chat_id=ADMIN_ID,
                         text=format_admin(dict(listing)), parse_mode="Markdown")
                return
            except:
                pass
        if urls:
            try:
                _photo(cid, urls[0], text[:1024], kb)
                if cid != ADMIN_ID:
                    _api("sendMessage", chat_id=ADMIN_ID,
                         text=format_admin(dict(listing)), parse_mode="Markdown")
                return
            except:
                pass

    _edit(cid, mid, text, kb)
    if cid != ADMIN_ID:
        _api("sendMessage", chat_id=ADMIN_ID,
             text=format_admin(dict(listing)), parse_mode="Markdown")


# ── Stats ─────────────────────────────────────────────────────────────────────
def show_stats(cid, uid):
    if uid != ADMIN_ID:
        _send(cid, "Access denied."); return

    try:
        s = get_full_stats()
    except Exception as e:
        _send(cid, f"⚠️ Stats error: {e}")
        return

    def _fmt_m(v):
        if not v or v == 0: return "—"
        if v >= 1_000_000: return f"{v/1_000_000:.1f}M AED"
        if v >= 1_000:     return f"{v/1_000:.0f}K AED"
        return f"{v:,} AED"

    def _fmt_dt(dt):
        if not dt: return "—"
        try: return dt.strftime("%d.%m.%Y %H:%M")
        except: return str(dt)

    # Extra DB queries (active users, views, searches)
    active_today = views_today = searches_today = 0
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM users WHERE last_seen >= NOW() - INTERVAL '24 hours'")
            active_today = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM leads WHERE created_at >= NOW() - INTERVAL '24 hours' AND action='view'")
            views_today = cur.fetchone()["cnt"]
            cur.execute("SELECT COALESCE(SUM(searches_count),0) as cnt FROM users WHERE last_seen >= NOW() - INTERVAL '24 hours'")
            searches_today = cur.fetchone()["cnt"]
        conn.close()
    except Exception as e:
        print(f"[stats] extra query error: {e}")

    by_em = "\n".join(f"  {em or 'Unknown':<22}{cnt}" for em, cnt in s.get("by_emirate", {}).items())
    by_q  = "\n".join(f"  {q:<22}{cnt}" for q, cnt in s.get("by_quality", {}).items())

    channel_lines = []
    for ch, info in s.get("by_channel", {}).items():
        last = _fmt_dt(info.get("last"))
        lid  = info.get("last_id") or 0
        channel_lines.append(f"  {ch[:20]:<22}msg {lid}  ({last})")
    by_channel = "\n".join(channel_lines)

    corrupt = s.get("corrupt_prices", 0)
    corrupt_warn = f"\n  ⚠️  Corrupt prices:      {corrupt}" if corrupt else ""

    # Audit category counts
    audit_total = audit_buckets_lines = ""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM listings WHERE is_active=TRUE AND is_audit=TRUE")
            audit_total = cur.fetchone()["cnt"]
            cur.execute("""
                SELECT split_part(audit_reason, '_', 1) || '_' || split_part(audit_reason, '_', 2) AS bucket,
                       COUNT(*) AS n
                FROM listings WHERE is_active=TRUE AND is_audit=TRUE
                GROUP BY bucket ORDER BY n DESC LIMIT 5
            """)
            ab = cur.fetchall()
        conn.close()
        audit_buckets_lines = "\n".join(f"    {b['bucket']:<22}{b['n']}" for b in ab)
    except Exception:
        audit_total = 0
        audit_buckets_lines = ""

    text = (
        f"{_sep()}\n  СТАТИСТИКА АДМИНА  ·  Dubai Resale Intelligence\n{_sep()}\n\n"

        f"  Всего объектов:        {s['total']}\n"
        f"  В продаже:             {s['sale_total']}  ({s['sale_clean']} чистых)\n"
        f"  В аренде:              {s['rent_total']}  ({s['rent_clean']} чистых)\n"
        f"  Горячих сделок:        {s['hot_deals']}\n"
        f"  Ниже рынка:            {s['below_market']}\n"
        f"  Требует проверки:      {s['needs_review']}\n"
        f"  Очередь модерации:     {s['review_queue']}\n"
        f"  Ожидает одобрения:     {s['pending']}\n"
        f"  Зданий в БД:           {s['buildings_count']}\n"
        f"  Районов:               {s['areas_count']}\n"
        f"  Каналов парсится:      {s['groups_count']}"
        f"{corrupt_warn}\n\n"

        f"{_sep()}\n  АУДИТ (скрытые от пользователей)\n{_sep()}\n"
        f"  Всего в аудите:        {audit_total}\n"
        f"  Топ причин:\n{audit_buckets_lines}\n\n"

        f"{_sep()}\n  АНАЛИТИКА\n{_sep()}\n"
        f"  Средняя цена продажи:  {_fmt_m(s['avg_sale_price'])}\n"
        f"  Средняя аренда/год:    {_fmt_m(s['avg_rent_price'])}\n"
        f"  Средняя цена/sqft:     {int(s['avg_price_sqft']) if s['avg_price_sqft'] else '—'} AED\n"
        f"  Средний ROI:           {s['avg_roi']}%\n\n"

        f"{_sep()}\n  АКТИВНОСТЬ\n{_sep()}\n"
        f"  Сегодня (Дубай):       {s['today_listings']}\n"
        f"  Вчера:                 {s['yesterday_listings']}\n"
        f"  Эта неделя:            {s['week_listings']}\n"
        f"  Этот месяц:            {s['month_listings']}\n\n"

        f"{_sep()}\n  СИНХРОНИЗАЦИЯ СЕГОДНЯ\n{_sep()}\n"
        f"  Новых добавлено:       {s['today_new']}\n"
        f"  Дубликатов:            {s['today_dupes']}\n"
        f"  Горячих найдено:       {s['today_hot']}\n"
        f"  Ошибок:                {s['today_errors']}\n"
        f"  Запусков синхр.:       {s['syncs_today']}\n"
        f"  Последняя синхр.:      {_fmt_dt(s['last_sync'])}\n\n"

        f"{_sep()}\n  КАНАЛЫ\n{_sep()}\n"
        f"{by_channel}\n\n"

        f"{_sep()}\n  ПО ЭМИРАТАМ\n{_sep()}\n"
        f"{by_em}\n\n"

        f"{_sep()}\n  ПО КАЧЕСТВУ СДЕЛКИ\n{_sep()}\n"
        f"{by_q}\n\n"

        f"{_sep()}\n  ПОЛЬЗОВАТЕЛИ\n{_sep()}\n"
        f"  Всего пользователей:   {s['users_total']}\n"
        f"  Активных сегодня:      {active_today}\n"
        f"  Поисков сегодня:       {searches_today}\n"
        f"  Просмотров сегодня:    {views_today}\n"
        f"  Заявок сегодня:        {s['leads_today']}\n"
        f"  Заявок за неделю:      {s['leads_week']}\n"
        f"{_sep()}"
    )
    _send(cid, f"`{text}`")

    # ── Cross-bot conversions (v55) ─────────────────────────────────────────
    try:
        from db_schema import get_cross_bot_stats
        cbj = get_cross_bot_stats(days=7)
        if cbj:
            lines = ["🔀 *Cross-bot conversions* (last 7d):\n"]
            for r in cbj[:25]:
                lines.append(
                    f"  `{r['from_bot']:>10} → {r['to_bot']:<10}`  "
                    f"{r['n']}  (users {r['users']})"
                )
            _send(cid, "\n".join(lines))
        else:
            _send(cid, "🔀 *Cross-bot conversions*: пока нет данных за 7 дней.")
    except Exception as e:
        print(f"[stats] cbj err: {e}", flush=True)


# ── Main menu ─────────────────────────────────────────────────────────────────
def show_deal_type_menu(cid, uid, mid=None):
    text = _t(uid, "deal_type_q")
    kb = _kb(
        [_btn("🏠  " + _t(uid, "d_sale"), "default_deal|sale")],
        [_btn("🔑  " + _t(uid, "d_rent"), "default_deal|rent")],
        [_btn(_t(uid, "d_any_deal"),       "default_deal|any")],
    )
    if mid: _edit(cid, mid, text, kb)
    else:   _send(cid, text, kb)


def show_main(cid, uid, mid=None):
    """Always sends a fresh message with persistent bottom keyboard.
    If an old inline-menu message is given, delete it to keep the chat clean.

    B019 fix: persistent=True wizard reply-keyboards не всегда заменяются новой
    persistent reply-keyboard (Telegram-баг, особенно iOS). Сначала шлём transient
    "✓" с remove_keyboard, удаляем его — это сбрасывает текущую wizard-клавиатуру
    на клиенте — потом отправляем main menu с правильной клавиатурой.
    Pattern из channel-bot v132.7 (commit fc6b0ee)."""
    if mid:
        try: _api("deleteMessage", chat_id=cid, message_id=mid)
        except: pass
    try:
        tmp = _api("sendMessage", chat_id=cid, text="✓",
                   reply_markup={"remove_keyboard": True})
        tmp_mid = ((tmp or {}).get("result") or {}).get("message_id")
        if tmp_mid:
            _api("deleteMessage", chat_id=cid, message_id=tmp_mid)
    except Exception as _e:
        print(f"[resale_bot] B019 main-menu reply-kb clear failed: {_e}", flush=True)
    _send(cid, _t(uid, "main_menu"), kb_main_reply(uid))


def _parse_payload(payload: str):
    """Парсит deep-link payload и возвращает (from_bot, utm_source, utm_campaign, utm_content).

    Поддерживает 2 формата:
      • легаси: from_<botname>[_<id>]   → from_hub / from_offplan_3190 / from_resale
      • новый: ...<legacy>...utm_<source>_<campaign>[_<content>]
        пример: from_offplan_3190_utm_resale_card_share
                → from_bot=offplan, utm_source=resale, utm_campaign=card_share

    Также поддерживает префиксы legacy lead-bot: resale-, proj-, roi-, area-, bld-.
    """
    if not payload:
        return ("unknown", None, None, None)
    p = payload.strip()

    # Разрезаем по '_utm_' если есть extended UTM
    utm_source = utm_campaign = utm_content = None
    if "_utm_" in p:
        head, _, tail = p.partition("_utm_")
        utm_parts = tail.split("_")
        if len(utm_parts) >= 1: utm_source   = utm_parts[0] or None
        if len(utm_parts) >= 2: utm_campaign = utm_parts[1] or None
        if len(utm_parts) >= 3: utm_content  = "_".join(utm_parts[2:]) or None
        p = head

    # Определяем from_bot
    from_bot = "unknown"
    if p.startswith("from_"):
        # from_hub / from_resale / from_offplan_3190 / from_roi / from_analytics
        tail = p[len("from_"):]
        first = tail.split("_", 1)[0] if tail else ""
        # map alias offplan→channel
        mapping = {"offplan": "channel", "channel": "channel",
                   "resale": "resale", "hub": "hub", "roi": "roi",
                   "analytics": "analytics", "dld": "analytics",
                   "lead": "lead"}
        from_bot = mapping.get(first.lower(), first.lower() or "unknown")
    elif p.startswith("proj-") or p.startswith("proj_") or p.startswith("proj|"):
        from_bot = "channel"
    elif p.startswith("resale-") or p.startswith("resale_"):
        from_bot = "resale"
    elif p.startswith("roi-") or p.startswith("roi_"):
        from_bot = "roi"
    elif p.startswith("area-") or p.startswith("area_") or \
         p.startswith("bld-") or p.startswith("bld_"):
        from_bot = "analytics"
    return (from_bot, utm_source, utm_campaign, utm_content)


def show_watchlist(cid, uid):
    """Show user's active watchlists with /unwatch_N commands."""
    try:
        from db_schema import get_user_watchlists
        rows = get_user_watchlists(uid)
    except Exception as e:
        _send(cid, f"⚠ Watchlist error: {e}")
        return
    if not rows:
        _send(cid, _t(uid, "watch_empty"), kb_main_reply(uid))
        return
    lines = [_t(uid, "watch_title"), f"  {len(rows)} items", ""]
    for r in rows:
        wt = r.get("watch_type") or "?"
        wv = r.get("watch_value") or "—"
        freq = r.get("notification_freq") or "daily"
        icon = {"area": "📍", "building": "🏢", "developer": "🏗",
                "price_range": "💰", "listing": "🏠"}.get(wt, "•")
        # для listing — попытаемся показать здание/район
        extra = ""
        if wt == "listing" and wv.isdigit():
            try:
                lst = get_listing_by_id(int(wv))
                if lst:
                    extra = f" — {lst.get('building') or lst.get('area') or '—'}"
            except Exception:
                pass
        lines.append(f"{icon} `{wt}`: {wv}{extra}  · {freq}    /unwatch_{r['id']}")
    _send(cid, "\n".join(lines), kb_main_reply(uid))


def show_favorites(cid, uid):
    from db_schema import get_user_favorites
    rows = get_user_favorites(uid)
    if not rows:
        _send(cid, _t(uid, "favs_empty"), kb_main_reply(uid))
        return
    _send(cid, _t(uid, "favs_title") + f"\n  {len(rows)} items")
    s = gs(uid)
    s["results"] = [dict(r) for r in rows]
    s["total"]   = len(rows)
    s["page"]    = 0
    send_results(cid, uid)


def show_alerts(cid, uid):
    from db_schema import get_user_alerts
    rows = get_user_alerts(uid)
    if not rows:
        _send(cid, _t(uid, "alerts_empty"), kb_main_reply(uid))
        return
    text = _t(uid, "alerts_title") + "\n\n"
    for r in rows:
        parts = []
        if r.get("deal_type"): parts.append(r["deal_type"].upper())
        if r.get("property_type"): parts.append(r["property_type"])
        if r.get("area"): parts.append(r["area"])
        elif r.get("emirate"): parts.append(r["emirate"])
        if r.get("bedrooms") is not None: parts.append(f"{r['bedrooms']}BR")
        if r.get("min_price") or r.get("max_price"):
            mn = r.get("min_price"); mx = r.get("max_price")
            if mn and mx:    parts.append(f"{mn//1000}k–{mx//1000}k")
            elif mx:         parts.append(f"≤{mx//1000}k")
            elif mn:         parts.append(f"≥{mn//1000}k")
        text += f"• {' · '.join(parts) or 'Any'}    /alert_del_{r['id']}\n"
    _send(cid, text, kb_main_reply(uid))


def show_compare(cid, uid):
    """Render the user's compare cart — up to 3 listings side-by-side."""
    s = gs(uid)
    cart = s.get("compare", [])
    if not cart:
        _send(cid, _t(uid, "compare_empty"), kb_main_reply(uid))
        return
    items = [get_listing_by_id(lid) for lid in cart]
    items = [dict(x) for x in items if x]
    if not items:
        s["compare"] = []
        _send(cid, _t(uid, "compare_empty"), kb_main_reply(uid))
        return
    lines = [_t(uid, "compare_title"), ""]
    def _fmt_price(p, dt):
        if not p: return "—"
        if dt == "rent": return f"{p:,} AED/yr"
        if p >= 1_000_000: return f"{p/1_000_000:.2f}M AED"
        return f"{p:,} AED"
    for i, lst in enumerate(items, 1):
        psf = (lst.get("price") or 0) / (lst.get("size_sqft") or 1) if lst.get("size_sqft") else 0
        lines.append(f"#{i}  {lst.get('building') or '—'}")
        lines.append(f"   📍 {lst.get('area') or '—'}, {lst.get('emirate') or '—'}")
        lines.append(f"   🛏 {lst.get('bedrooms') if lst.get('bedrooms') is not None else '—'} BR  "
                     f"·  {int(lst.get('size_sqft') or 0)} sqft")
        lines.append(f"   💰 {_fmt_price(lst.get('price'), lst.get('deal_type'))}"
                     f"  ·  {int(psf)} AED/sqft" if psf else f"   💰 {_fmt_price(lst.get('price'), lst.get('deal_type'))}")
        lines.append(f"   🌅 {lst.get('view') or '—'}")
        lines.append("")
    lines.append("/compare_clear — clear cart")
    _send(cid, "\n".join(lines), kb_main_reply(uid))


def create_alert_from_filters(cid, uid):
    """Create a price alert from the user's current search filters."""
    from db_schema import add_price_alert
    f = dict(gs(uid).get("filters", {}))
    # Strip non-alertable keys
    clean = {k: f.get(k) for k in
             ("deal_type","property_type","emirate","area","bedrooms","min_price","max_price")}
    add_price_alert(uid, clean)
    _send(cid, _t(uid, "alert_created"), kb_main_reply(uid))


def dispatch_main_button(cid, uid, rkey):
    """Dispatches a press of a bottom reply-keyboard button to the right flow.
    Each category sets the appropriate filter so search results stay within it.
    Now uses reply keyboards (bottom bar) for emirate/proptype/bedrooms wizard steps."""
    if rkey == "rbtn_buy":
        _reset(uid)
        gs(uid)["filters"]["deal_type"] = "sale"
        gs(uid)["filters"]["property_type_not_in"] = COMMERCIAL_TYPES + LAND_TYPES
        # Wizard hints — recovered by do_search if filters are dropped en route
        gs(uid)["wizard_deal_hint"] = "sale"
        gs(uid)["wizard_pt_not_in_hint"] = COMMERCIAL_TYPES + LAND_TYPES
        gs(uid)["wizard"] = "emirate"
        _send(cid, _t(uid, "emirate_q"), kb_reply_emirate(uid))
    elif rkey == "rbtn_rent":
        _reset(uid)
        gs(uid)["filters"]["deal_type"] = "rent"
        gs(uid)["filters"]["property_type_not_in"] = COMMERCIAL_TYPES + LAND_TYPES
        gs(uid)["wizard_deal_hint"] = "rent"
        gs(uid)["wizard_pt_not_in_hint"] = COMMERCIAL_TYPES + LAND_TYPES
        gs(uid)["wizard"] = "emirate"
        _send(cid, _t(uid, "emirate_q"), kb_reply_emirate(uid))
    elif rkey == "rbtn_commercial":
        _reset(uid)
        gs(uid)["filters"]["property_type_in"] = COMMERCIAL_TYPES
        gs(uid)["wizard_pt_in_hint"] = COMMERCIAL_TYPES
        gs(uid)["wizard"] = "emirate"
        _send(cid, _t(uid, "emirate_q"), kb_reply_emirate(uid))
    elif rkey == "rbtn_plot":
        _reset(uid)
        gs(uid)["filters"]["property_type"] = "plot"
        gs(uid)["wizard_pt_hint"] = "plot"
        gs(uid)["wizard"] = "emirate"
        _send(cid, _t(uid, "emirate_q"), kb_reply_emirate(uid))
    elif rkey == "rbtn_hot":
        # Сохраняем существующие фильтры (если пользователь уже выбрал Аренда —
        # покажем горячие аренды). Иначе — по умолчанию SALE.
        existing = dict(gs(uid).get("filters", {}))
        existing["hot_only"] = True
        existing["sort"] = "best_deals"
        if "deal_type" not in existing:
            existing["deal_type"] = "sale"
        gs(uid)["filters"] = existing
        _send(cid, _t(uid, "searching")); do_search(uid); send_results(cid, uid)
    elif rkey == "rbtn_new":
        existing = dict(gs(uid).get("filters", {}))
        existing["sort"] = "newest"
        if "deal_type" not in existing:
            existing["deal_type"] = "sale"
        gs(uid)["filters"] = existing
        _send(cid, _t(uid, "searching")); do_search(uid); send_results(cid, uid)
    elif rkey == "rbtn_ai":
        show_ai_start(cid, uid)
    elif rkey == "rbtn_add":
        start_add_listing(cid, uid)
    elif rkey == "rbtn_lang":
        # Bottom reply keyboard for language change too
        _send(cid, "🌐  Select your language / Выберите язык / اختر لغتك",
              kb_lang_reply())
    elif rkey == "rbtn_home":
        show_main(cid, uid)
    elif rkey == "rbtn_favs":
        show_favorites(cid, uid)
    elif rkey == "rbtn_alerts":
        show_alerts(cid, uid)


def dispatch_wizard_button(cid, uid, text):
    """Handle wizard reply-keyboard button presses (emirate, property_type, bedrooms).
    Works across all 3 languages via _wizard_match (canonical key lookup).
    Returns True if the text matched a wizard button and was handled."""
    state = gs(uid)
    wizard = state.get("wizard")
    # Make sure filters is a real reference inside state so mutations persist.
    # Previously `state.get("filters", {})` could return a *new* empty dict that
    # would never be written back if state["filters"] was missing — leading to
    # silently lost deal_type/property_type filters in the wizard flow.
    filters = state.setdefault("filters", {})

    # Emirate step
    if wizard == "emirate":
        em, matched = _wizard_match(text, EMIRATE_KEYS)
        if matched:
            if em:
                filters["emirate"] = em
            if filters.get("property_type") == "plot":
                state["wizard"] = "budget"
                _send(cid, _t(uid, "budget_q"), kb_reply_budget(uid, is_plot=True))
            elif filters.get("property_type_in"):
                state["wizard"] = "proptype"
                _send(cid, _t(uid, "prop_q"), kb_reply_proptype_commercial(uid))
            else:
                state["wizard"] = "proptype"
                _send(cid, _t(uid, "prop_q"), kb_reply_proptype_residential(uid))
            return True

    # Property type step
    if wizard == "proptype":
        pt, matched = _wizard_match(text, PROPTYPE_KEYS)
        if matched:
            if pt:
                if pt in COMMERCIAL_TYPES:
                    filters["property_type_in"] = [pt]
                else:
                    filters["property_type"] = pt
                    filters.pop("property_type_in", None)
            is_comm = bool(filters.get("property_type_in"))
            is_plot = filters.get("property_type") == "plot"
            is_rent = filters.get("deal_type") == "rent"
            if is_plot or is_comm:
                state["wizard"] = "budget"
                _send(cid, _t(uid, "rent_budget_q" if is_rent else "budget_q"),
                      kb_reply_budget(uid, is_rent=is_rent, is_commercial=is_comm, is_plot=is_plot))
            else:
                state["wizard"] = "bedrooms"
                _send(cid, _t(uid, "br_q"), kb_reply_bedrooms(uid))
            return True

    # Bedrooms step
    if wizard == "bedrooms":
        br, matched = _wizard_match(text, BEDROOM_KEYS)
        if matched:
            if br is not None:
                filters["bedrooms"] = br
            state["wizard"] = "budget"
            is_rent = filters.get("deal_type") == "rent"
            _send(cid, _t(uid, "rent_budget_q" if is_rent else "budget_q"),
                  kb_reply_budget(uid, is_rent=is_rent))
            return True

    # Results navigation (bottom reply keyboard after a result batch)
    if wizard == "results":
        more_labels   = [T[l]["rbtn_more"]         for l in ("en","ru","ar")]
        change_labels = [T[l]["rbtn_change_deal"]  for l in ("en","ru","ar")]
        back_labels   = [T[l]["rbtn_back"]         for l in ("en","ru","ar")]
        alert_labels  = [T[l]["rbtn_create_alert"] for l in ("en","ru","ar")]
        if text in more_labels:
            send_results(cid, uid)
            return True
        if text in alert_labels:
            create_alert_from_filters(cid, uid)
            return True
        if text in change_labels:
            state["filters"].pop("deal_type", None)
            state["default_deal"] = None
            state["wizard"] = None
            show_main(cid, uid)
            return True
        if text in back_labels:
            state["wizard"] = None
            show_main(cid, uid)
            return True

    # AI Assistant — goal step
    if wizard == "ai_goal":
        goal, matched = _wizard_match(text, AI_GOAL_KEYS)
        if matched and goal:
            ai = state.setdefault("ai_data", {})
            ai["goal"] = goal
            if goal == "invest":
                state["wizard"] = "ai_invest"
                _send(cid, _t(uid, "ai_inv_q"), kb_reply_ai_invest(uid))
            elif goal == "live":
                state["wizard"] = "ai_life"
                _send(cid, _t(uid, "ai_life_q"), kb_reply_ai_life(uid))
            elif goal == "commercial":
                state["wizard"] = "ai_commtype"
                _send(cid, _t(uid, "ai_commercial_q"), kb_reply_ai_commtype(uid))
            elif goal == "land":
                ai["land_only"] = True
                state["wizard"] = "ai_recommend"
                _send(cid, _t(uid, "budget_q"), kb_reply_budget(uid, is_plot=True))
            else:
                # holiday / unsure → straight to budget then recommend
                state["wizard"] = "ai_recommend"
                _send(cid, _t(uid, "budget_q"), kb_reply_budget(uid))
            return True

    # AI Assistant — investment strategy step
    if wizard == "ai_invest":
        strat, matched = _wizard_match(text, AI_INVEST_KEYS)
        if matched and strat:
            ai = state.setdefault("ai_data", {})
            ai["strategy"] = strat
            state["wizard"] = "ai_recommend"
            _send(cid, _t(uid, "budget_q"), kb_reply_budget(uid))
            return True

    # AI Assistant — lifestyle step
    if wizard == "ai_life":
        lf, matched = _wizard_match(text, AI_LIFE_KEYS)
        if matched and lf:
            ai = state.setdefault("ai_data", {})
            ai["lifestyle"] = lf
            state["wizard"] = "ai_recommend"
            _send(cid, _t(uid, "budget_q"), kb_reply_budget(uid))
            return True

    # AI Assistant — commercial subtype step
    if wizard == "ai_commtype":
        ct, matched = _wizard_match(text, AI_COMMTYPE_KEYS)
        if matched and ct:
            ai = state.setdefault("ai_data", {})
            ai["comm_type"] = ct
            state["wizard"] = "ai_recommend"
            _send(cid, _t(uid, "budget_q"), kb_reply_budget(uid, is_commercial=True))
            return True

    # AI Assistant — budget step → recommend
    if wizard == "ai_recommend":
        ai = state.setdefault("ai_data", {})
        # Apply budget filter (uses same buttons as regular budget step)
        if text == _t(uid, "b_any_btn") or text in BUDGET_BUTTONS:
            if text in BUDGET_BUTTONS:
                mn, mx = BUDGET_BUTTONS[text]
                if mn is not None: ai["min_price"] = mn
                if mx is not None: ai["max_price"] = mx
            state["wizard"] = None
            _send(cid, _t(uid, "ai_analyzing"), kb_main_reply(uid))
            run_ai_recommend(cid, uid)
            return True

    # Budget step (bottom reply keyboard)
    if wizard == "budget":
        # "Any" → no min/max
        if text == _t(uid, "b_any_btn"):
            # Skip budget, go to area input
            state["wizard"] = "area_input"
            _send(cid, _t(uid, "wiz_area_q"), kb_reply_area_input(uid))
            return True
        if text in BUDGET_BUTTONS:
            mn, mx = BUDGET_BUTTONS[text]
            if mn is not None: filters["min_price"] = mn
            if mx is not None: filters["max_price"] = mx
            # Next step — area input with smart suggestions
            state["wizard"] = "area_input"
            _send(cid, _t(uid, "wiz_area_q"), kb_reply_area_input(uid))
            return True

    # Area input step — текстовый ввод с автоподсказками
    if wizard == "area_input":
        # Skip — переходим к building шагу без area filter
        if text == _t(uid, "rbtn_home"):
            state["wizard"] = None
            show_main(cid, uid)
            return True
        if text == _t(uid, "wiz_area_any"):
            state["wizard"] = "building_input"
            _send(cid, _t(uid, "wiz_bld_q"), kb_reply_building_input(uid))
            return True
        # Поиск по введённому тексту
        emirate = filters.get("emirate")
        matches = search_areas_by_query(text, emirate=emirate, limit=10)
        if not matches:
            _send(cid, _t(uid, "wiz_area_nomatch").replace("{q}", text),
                  kb_reply_area_input(uid))
            return True
        # ВСЕГДА показываем suggestions (даже при 1 матче) — юзер сверяется + skip option
        rows = []
        for it in matches:
            label = it["name"]
            if it.get("aliases"):
                label = f"{it['name']}  ({it['aliases'][0]})"
            rows.append([_btn(label, f"pickarea|{it['name']}")])
        rows.append([_btn(_t(uid, "wiz_area_any"), "pickarea|__any__")])
        _send(cid, _t(uid, "wiz_area_match"),
              {"inline_keyboard": rows})
        return True

    # ── Building input step (smart search, auto-grow по listings DB) ─────────
    if wizard == "building_input":
        if text == _t(uid, "rbtn_home"):
            state["wizard"] = None
            show_main(cid, uid)
            return True
        if text == _t(uid, "wiz_bld_any"):
            state["wizard"] = None
            _send(cid, _t(uid, "searching"), kb_main_reply(uid))
            do_search(uid)
            send_results(cid, uid)
            return True
        emirate = filters.get("emirate")
        area = filters.get("area")
        matches = search_buildings_by_query(text, emirate=emirate, area=area, limit=10)
        if not matches:
            _send(cid, _t(uid, "wiz_bld_nomatch").replace("{q}", text),
                  kb_reply_building_input(uid))
            return True
        # ВСЕГДА показываем suggestions (даже при 1 матче) — юзер может проверить
        # что попало в фильтр + видит фактический count объявлений в каждом.
        is_suggest = any(it.get("is_dld_suggest") for it in matches)
        rows = []
        for it in matches:
            cnt = it.get("count", 0)
            label = it["name"]
            if it.get("is_dld_suggest"):
                # подсказка из DLD-архива: показываем район + кол-во сделок
                area_lbl = it.get("area") or ""
                if area_lbl:
                    label = f"{it['name']}  · {area_lbl}"
                if cnt > 0:
                    label = f"{label} · {cnt} сделок"
            elif cnt > 0:
                label = f"{it['name']}  · {cnt}"
            rows.append([_btn(label, f"pickbld|{it['name']}")])
        rows.append([_btn(_t(uid, "wiz_bld_any"), "pickbld|__any__")])
        header = _t(uid, "wiz_bld_match")
        if is_suggest:
            # «У нас нет объявлений в этом здании, но возможно вы имели в виду:»
            header = "💡 Возможно, вы имели в виду (из DLD-архива):"
        _send(cid, header, {"inline_keyboard": rows})
        return True

    return False


# ── Admin Panel ───────────────────────────────────────────────────────────────
def needs_review_check(listing):
    price = listing.get("price") or 0
    sqft  = listing.get("size_sqft") or 0
    deal  = listing.get("deal_type") or "sale"
    if not listing.get("building"):             return True, "нет здания"
    if not listing.get("area"):                 return True, "нет района"
    if not price or price <= 0:                 return True, "нет цены"
    if deal == "sale" and price < 100_000:      return True, f"цена низкая: {price}"
    if deal == "sale" and price > 100_000_000:  return True, f"цена высокая: {price}"
    if deal == "rent" and price < 5_000:        return True, f"аренда низкая: {price}"
    if deal == "rent" and price > 5_000_000:    return True, f"аренда высокая: {price}"
    if sqft and sqft < 100:                     return True, f"площадь мала: {sqft:.0f}"
    if sqft and sqft > 50_000:                  return True, f"площадь велика: {sqft:.0f}"
    if deal == "sale" and sqft > 0:
        psf = price / sqft
        if psf < 200:    return True, f"цена/sqft низкая: {psf:.0f}"
        if psf > 15_000: return True, f"цена/sqft высокая: {psf:.0f}"
    return False, None


def get_review_queue():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT rq.id, rq.listing_id, rq.reason,
                       l.area, l.building, l.emirate, l.bedrooms,
                       l.size_sqft, l.price, l.deal_type, l.original_text
                FROM review_queue rq
                JOIN listings l ON l.id = rq.listing_id
                WHERE rq.status = 'pending' AND l.is_active = TRUE
                ORDER BY rq.created_at ASC
            """)
            rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"[admin] get_review_queue error: {e}")
        return []


def _fmt_review_item(item, idx, total, edits):
    building = edits.get("building", item.get("building") or "❌")
    area     = edits.get("area",     item.get("area")     or "❌")
    bedrooms = edits.get("bedrooms", item.get("bedrooms"))
    price    = edits.get("price",    item.get("price"))
    sqft     = item.get("size_sqft")
    deal     = item.get("deal_type") or "sale"
    orig     = (item.get("original_text") or "")[:200]
    price_str = f"{price:,} AED".replace(",", " ") if price else "❌"
    sqft_str  = f"{int(sqft)} sqft" if sqft else "❌"
    br_str    = str(bedrooms) if bedrooms is not None else "❌"
    lines = [
        "────────────────────",
        f"⚠️  ПРОВЕРКА  [{idx+1} из {total}]",
        "",
        f"🔴 Причина: {item.get('reason', '')}",
        "",
        f"📍 Район:   {area}",
        f"🏢 Здание:  {building}",
        f"🛏 Спальни: {br_str}",
        f"📐 Площадь: {sqft_str}",
        f"💰 Цена:    {price_str}",
        f"🔑 Тип:     {deal}",
        "",
        "Оригинал:",
        f'"{orig}"',
        "────────────────────",
    ]
    return "\n".join(lines)


def show_review_item(cid, uid, idx, mid=None):
    state = admin_states.get(uid, {})
    queue = state.get("queue")
    if queue is None:
        queue = get_review_queue()
        admin_states[uid] = {"queue": queue, "idx": 0, "edits": {}}
    if not queue:
        text = "✅ Очередь проверки пуста"
        kb   = _kb([_btn("← Меню", "admin|menu")])
        if mid: _edit(cid, mid, text, kb)
        else:   _send(cid, text, kb)
        return
    idx   = max(0, min(idx, len(queue) - 1))
    admin_states[uid]["idx"] = idx
    item  = queue[idx]
    edits = admin_states[uid].get("edits", {})
    total = len(queue)
    qid   = item["id"]
    prev_i = idx - 1 if idx > 0 else idx
    next_i = idx + 1 if idx < total - 1 else idx
    text = _fmt_review_item(item, idx, total, edits)
    kb = _kb(
        [_btn("✏️ Здание",   f"admin|edit|building|{qid}"),
         _btn("✏️ Район",    f"admin|edit|area|{qid}")],
        [_btn("✏️ Цену",     f"admin|edit|price|{qid}"),
         _btn("✏️ Спальни",  f"admin|edit|bedrooms|{qid}")],
        [_btn("✅ Сохранить", f"admin|save|{qid}"),
         _btn("🚫 Удалить",   f"admin|del|{qid}")],
        [_btn(f"← {prev_i+1}" if idx > 0 else "←",         f"admin|review|{prev_i}"),
         _btn(f"{idx+1}/{total}",                            "admin|noop"),
         _btn(f"{next_i+1} →" if idx < total-1 else "→",   f"admin|review|{next_i}")],
        [_btn("← Меню", "admin|menu")],
    )
    if mid: _edit(cid, mid, text, kb)
    else:   _send(cid, text, kb)


def show_admin_menu(cid, uid, mid=None):
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM review_queue WHERE status='pending'")
            count = cur.fetchone()["cnt"]
        conn.close()
    except:
        count = 0
    text = "────────────────────\n🔐  АДМИН ПАНЕЛЬ\n────────────────────"
    kb = _kb(
        [_btn("📊  Статистика",               "admin|stats")],
        [_btn(f"⚠️  На проверке ({count})",   "admin|review|0")],
        [_btn("🔍  Сканировать базу",          "admin|scan")],
        [_btn("⚙️  Управление",               "admin|manage")],
    )
    if mid: _edit(cid, mid, text, kb)
    else:   _send(cid, text, kb)


def ai_classify_deal(raw_text: str, price: int, bedrooms, area: str) -> str | None:
    """Single listing deal_type classification via Claude Haiku (raw requests, ~10 tokens out)."""
    if not ANTHROPIC_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 10,
                "messages": [{
                    "role": "user",
                    "content": (
                        f"UAE real estate expert. Is this listing for SALE or RENT?\n\n"
                        f"Price: {price} AED\nBedrooms: {bedrooms}\nArea: {area}\n\n"
                        f"Sale minimums: Studio 350K+, 1BR 600K+, 2BR 900K+, 3BR 1.4M+\n"
                        f"Rent max/yr: Studio 100K, 1BR 180K, 2BR 300K, 3BR 500K\n\n"
                        f"Text: {(raw_text or '')[:300]}\n\n"
                        f"Reply ONLY: sale or rent"
                    ),
                }],
            },
            timeout=10,
        )
        if resp.status_code == 200:
            answer = resp.json()["content"][0]["text"].strip().lower()
            return "rent" if "rent" in answer else "sale"
        if resp.status_code == 400 and "credit" in resp.text.lower():
            raise RuntimeError("credit_balance_low")
        return None
    except RuntimeError:
        raise
    except Exception as e:
        print(f"[AI classify] {e}")
        return None


_RESCAN_SALE_KW = [
    r'\bfor sale\b', r'\bresale\b', r'\bselling\b', r'\bfor sell\b',
    r'\bpayment plan\b', r'\bhandover\b', r'\boff.?plan\b', r'\bdown payment\b',
    r'\bmortgage\b', r'\bpurchase\b', r'\bbuying\b', r'\blaunch price\b',
    r'\bROI\b', r'\byield\b', r'\bcapital gain\b', r'\bpost handover\b',
    r'\bdeveloper\b', r'\bprice:\s*aed\b', r'\bask(?:ing)? price\b', r'\bsale price\b',
    r'продаж', r'\bпродам\b', r'\bпродается\b', r'\bкупить\b',
    r'\brented\b.*\btill\b', r'\brented\b.*\b\d{1,3}[km]\b',
    r'\binvestment\b', r'\bnet lease\b.*\bbuild', r'\bprice\s+\d',
]
_RESCAN_RENT_KW = [
    r'\bfor rent\b', r'\bto rent\b', r'\bin rent\b', r'\brental\b',
    r'\bper (?:year|month|annum|yr|mo)\b', r'\b/yr\b', r'\b/year\b', r'\b/month\b',
    r'\bannual(?:ly)? rent\b', r'\bmonthly rent\b', r'\brent(?:ing)? out\b',
    r'\bleasing\b', r'\bfor lease\b', r'\btenants?\b',
    r'\bсдается\b', r'\bсниму\b', r'\bсдам\b', r'\bаренда\b',
    r'\bp\.?a\.\b', r'\bper annum\b', r'yearly.*aed', r'aed.*yearly',
    r'\d+k?\s*/\s*year',
]


def _keyword_classify(txt: str):
    """Fast keyword-based deal_type: returns 'sale', 'rent', or None (ambiguous)."""
    import re as _re
    t = (txt or "").lower()
    sh = any(_re.search(p, t, _re.IGNORECASE) for p in _RESCAN_SALE_KW)
    rh = any(_re.search(p, t, _re.IGNORECASE) for p in _RESCAN_RENT_KW)
    if sh and not rh:
        return "sale"
    if rh and not sh:
        return "rent"
    return None


# Hard rent signals — any one match forces deal_type=rent regardless of AI
_HARD_RENT_KW = [
    r'\brent\b', r'\brental\b', r'\brented\b', r'\bfor rent\b', r'\bto rent\b',
    r'\bper year\b', r'\bper month\b', r'\bper annum\b', r'\b/yr\b', r'\b/year\b',
    r'\b/month\b', r'\b/мес\b', r'\b/год\b',
    r'\barenда\b', r'\bаренда\b', r'\bснять\b', r'\bсниму\b', r'\bсдам\b',
    r'\bсдается\b', r'\bсдаётся\b',
]
# Hard sale signals
_HARD_SALE_KW = [
    r'\bfor sale\b', r'\bselling\b', r'\bresale\b', r'\bsale price\b',
    r'\bproдажа\b', r'\bпродажа\b', r'\bпродам\b', r'\bпродаётся\b',
    r'\bbuy\b', r'\bbuying\b',
]


def _hard_validate_deal_type(txt: str, price, deal_type: str) -> str:
    """
    Post-AI override rules applied to EVERY parsed listing.
    Priority order:
      1. Rent keywords in text → always rent
      2. Sale keywords in text → sale (only if no rent keywords won)
      3. deal_type=sale + price < 500 000 AED → rent
      4. deal_type=rent + price > 50 000 000 AED → sale
    """
    t = (txt or "").lower()

    rent_hit = any(re.search(p, t, re.IGNORECASE) for p in _HARD_RENT_KW)
    if rent_hit:
        return "rent"

    sale_hit = any(re.search(p, t, re.IGNORECASE) for p in _HARD_SALE_KW)
    if sale_hit:
        deal_type = "sale"

    # Price sanity limits
    try:
        p = int(price or 0)
        if p > 0:
            if deal_type == "sale" and p < 500_000:
                return "rent"
            if deal_type == "rent" and p > 50_000_000:
                return "sale"
    except (TypeError, ValueError):
        pass

    return deal_type


def ai_rescan_deal_types(cid: int):
    """Background: keyword-first then AI deal_type fix for the grey zone 200K–2M AED."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, original_text, price, bedrooms, area, deal_type
            FROM listings
            WHERE price BETWEEN 200000 AND 2000000
              AND original_text IS NOT NULL
            ORDER BY id
        """)
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    total = len(rows)
    _send(cid, f"📊 Found {total} listings in grey zone 200K–2M AED\n⏳ Running keyword pass first…")

    kw_rent = kw_sale = fixed_rent = fixed_sale = errors = 0

    conn2 = get_conn()
    conn2.autocommit = False

    ambiguous = []
    with conn2.cursor() as c:
        for row in rows:
            kw = _keyword_classify(row["original_text"])
            if kw is not None:
                if kw != row["deal_type"]:
                    c.execute("UPDATE listings SET deal_type=%s WHERE id=%s", (kw, row["id"]))
                    if kw == "rent":
                        kw_rent += 1
                    else:
                        kw_sale += 1
            else:
                ambiguous.append(row)
        conn2.commit()

    conn2.close()
    _send(cid,
          f"✅ Keyword pass: →rent={kw_rent} →sale={kw_sale}\n"
          f"🤖 AI pass: {len(ambiguous)} ambiguous listings…")

    for i, row in enumerate(ambiguous):
        try:
            result = ai_classify_deal(
                row["original_text"],
                row["price"],
                row["bedrooms"],
                row["area"] or "",
            )
            if result and result != row["deal_type"]:
                conn3 = get_conn()
                with conn3.cursor() as c:
                    c.execute("UPDATE listings SET deal_type=%s WHERE id=%s",
                              (result, row["id"]))
                conn3.commit(); conn3.close()
                if result == "rent":
                    fixed_rent += 1
                else:
                    fixed_sale += 1

            if i > 0 and i % 50 == 0:
                _send(cid, f"⏳ AI {i}/{len(ambiguous)}… →rent={fixed_rent} →sale={fixed_sale}")

            time.sleep(0.2)

        except RuntimeError as e:
            if "credit" in str(e):
                _send(cid, "❌ Anthropic balance too low — top up at console.anthropic.com")
                return
        except Exception as e:
            errors += 1
            print(f"[airescan] id={row['id']} err={e}")

    _send(cid,
          f"✅ *AI deal_type rescan complete*\n\n"
          f"Grey zone total: {total}\n"
          f"Keyword →rent: {kw_rent}  →sale: {kw_sale}\n"
          f"AI →rent: {fixed_rent}  →sale: {fixed_sale}\n"
          f"AI errors: {errors}\n\n"
          f"Send /stats to see updated breakdown")


def ai_full_parse(raw_text: str, current_price, current_deal: str,
                  current_building, current_area, current_bedrooms):
    """Full AI parse of one listing — returns dict or None."""
    if not ANTHROPIC_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 200,
                "messages": [{
                    "role": "user",
                    "content": (
                        "UAE real estate expert. Parse this listing.\n\n"
                        "PRICE RULES — apply first:\n"
                        "- Studio SALE min 350,000 AED\n"
                        "- 1BR SALE min 550,000 AED\n"
                        "- 2BR SALE min 800,000 AED\n"
                        "- 3BR SALE min 1,200,000 AED\n"
                        "- 4BR+ SALE min 2,000,000 AED\n"
                        "If price is clearly below minimum → it's RENT.\n\n"
                        "SALE signals: for sale, payment plan, handover, mortgage, "
                        "off plan, resale, SP:\n"
                        "RENT signals: for rent, per year, per annum, cheques, "
                        "tenanted, rental, lease\n\n"
                        "BEDROOMS — extract integer, MANDATORY if any mention found:\n"
                        "  studio / Studio / STUDIO / студия / студио → 0\n"
                        "  1BR / 1 BR / 1bed / 1 bed / 1bedroom / 1 bedroom / "
                        "1-bedroom / 1 спальня / однокомнатная → 1\n"
                        "  2BR / 2 BR / 2bed / 2 bedroom / 2-bedroom / "
                        "2 спальни / двухкомнатная → 2\n"
                        "  3BR / 3 bedroom / 3 спальни → 3\n"
                        "  4BR / 4 bedroom / 4 спальни → 4\n"
                        "  5BR / 5 bedroom / 5 спален → 5\n"
                        "  6BR+ / 6 bedroom+ → 6\n"
                        "  Any digit before 'bed', 'BR', 'bedroom', 'спальн', "
                        "'комнат' → use that digit.\n"
                        "  If text says 'studio' in any language → bedrooms=0.\n"
                        "  If NO bedroom mention at all → null.\n\n"
                        f"Current data:\n"
                        f"- Price: {current_price} AED\n"
                        f"- Deal: {current_deal}\n"
                        f"- Building: {current_building or 'unknown'}\n"
                        f"- Area: {current_area or 'unknown'}\n"
                        f"- Bedrooms: {current_bedrooms}\n\n"
                        f"Original listing text:\n{(raw_text or '')[:500]}\n\n"
                        'Return ONLY valid JSON (no markdown, no extra text):\n'
                        '{"deal_type":"sale or rent",'
                        '"building":"exact building name or null",'
                        '"area":"Dubai/UAE area name or null",'
                        '"bedrooms":integer_or_null,'
                        '"price":integer_or_null,'
                        '"is_spam":true_or_false}'
                    ),
                }],
            },
            timeout=15,
        )
        if resp.status_code != 200:
            if resp.status_code == 400 and "credit" in resp.text.lower():
                raise RuntimeError("credit_balance_low")
            return None
        raw = resp.json()["content"][0]["text"].strip()
        # Extract first {...} block robustly — ignore any text before/after
        m = re.search(r'\{[^{}]*\}', raw, re.DOTALL)
        if not m:
            print(f"[ai_full_parse] no JSON object in: {raw[:80]}")
            return None
        obj = json.loads(m.group())
        # Ensure it's a dict, not a list
        if not isinstance(obj, dict):
            return None
        # Apply hard validation rules over AI result
        if "deal_type" in obj:
            obj["deal_type"] = _hard_validate_deal_type(
                raw_text, current_price, obj["deal_type"]
            )
        return obj
    except RuntimeError:
        raise
    except Exception as e:
        print(f"[ai_full_parse] {e}")
        return None


def full_ai_rescan(cid: int):
    """Background: full AI re-parse of every listing that has original_text."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, original_text, price, deal_type,
                   building, area, bedrooms
            FROM listings
            WHERE original_text IS NOT NULL AND original_text != ''
            ORDER BY id
        """)
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    total = len(rows)
    _send(cid, f"📊 Найдено {total} объявлений с оригинальным текстом\n⏳ Запускаю…")

    stats = {
        "deal_fixed": 0, "building_fixed": 0, "area_fixed": 0,
        "bedrooms_fixed": 0, "price_fixed": 0,
        "spam_hidden": 0, "errors": 0,
    }

    conn2 = get_conn()
    conn2.autocommit = False

    for i, row in enumerate(rows):
        if i > 0 and i % 100 == 0:
            _send(cid,
                  f"⏳ {i}/{total} обработано…\n"
                  f"deal={stats['deal_fixed']} bld={stats['building_fixed']} "
                  f"area={stats['area_fixed']} br={stats['bedrooms_fixed']} "
                  f"price={stats['price_fixed']} spam={stats['spam_hidden']}")

        # ── keyword pre-check for deal_type (free, no API) ─────────────────
        kw_deal = _keyword_classify(row["original_text"])

        try:
            result = ai_full_parse(
                raw_text=row["original_text"],
                current_price=row["price"],
                current_deal=row["deal_type"],
                current_building=row["building"],
                current_area=row["area"],
                current_bedrooms=row["bedrooms"],
            )

            if not result:
                stats["errors"] += 1
                time.sleep(0.2)
                continue

            # ── Spam → deactivate (never hard-delete) ──────────────────────
            if result.get("is_spam"):
                with conn2.cursor() as c:
                    c.execute("UPDATE listings SET is_active=FALSE WHERE id=%s",
                              (row["id"],))
                stats["spam_hidden"] += 1
                if i % 50 == 0:
                    conn2.commit()
                time.sleep(0.2)
                continue

            updates = {}

            # deal_type: keyword wins if unambiguous, else trust AI
            ai_deal = result.get("deal_type")
            final_deal = kw_deal or ai_deal
            if final_deal and final_deal != row["deal_type"]:
                updates["deal_type"] = final_deal
                stats["deal_fixed"] += 1

            # building: fill only if currently empty
            if result.get("building") and not row["building"]:
                updates["building"] = result["building"]
                stats["building_fixed"] += 1

            # area: fill only if currently empty
            if result.get("area") and not row["area"]:
                updates["area"] = result["area"]
                stats["area_fixed"] += 1

            # bedrooms: fill only if currently NULL
            if result.get("bedrooms") is not None and row["bedrooms"] is None:
                updates["bedrooms"] = result["bedrooms"]
                stats["bedrooms_fixed"] += 1

            # price: fill only if currently NULL/zero
            if result.get("price") and not row["price"]:
                updates["price"] = result["price"]
                stats["price_fixed"] += 1

            if updates:
                set_clause = ", ".join(f"{k}=%s" for k in updates)
                with conn2.cursor() as c:
                    c.execute(
                        f"UPDATE listings SET {set_clause} WHERE id=%s",
                        list(updates.values()) + [row["id"]],
                    )

            if i % 50 == 0:
                conn2.commit()

            time.sleep(0.2)

        except RuntimeError as e:
            if "credit" in str(e):
                conn2.commit()
                conn2.close()
                _send(cid, "❌ Кончились кредиты Anthropic — пополни на console.anthropic.com")
                return
        except Exception as e:
            stats["errors"] += 1
            print(f"[fullrescan] id={row['id']} err={e}")

    conn2.commit()
    conn2.close()

    _send(cid,
          f"✅ *ПОЛНЫЙ ПЕРЕСМОТР ЗАВЕРШЁН*\n\n"
          f"Обработано: {total}\n\n"
          f"Исправлено:\n"
          f"🔄 Deal type: {stats['deal_fixed']}\n"
          f"🏢 Здание: {stats['building_fixed']}\n"
          f"📍 Район: {stats['area_fixed']}\n"
          f"🛏 Спальни: {stats['bedrooms_fixed']}\n"
          f"💰 Цена: {stats['price_fixed']}\n"
          f"🚫 Спам скрыт: {stats['spam_hidden']}\n"
          f"❌ Ошибок: {stats['errors']}\n\n"
          f"Напиши /stats чтобы увидеть итог",
          parse_mode="Markdown")


def run_scan_bg(cid, uid, mid):
    try:
        _edit(cid, mid, "🔍 Сканирование базы...\nЭто может занять несколько минут.")
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, building, area, price, deal_type, size_sqft "
                "FROM listings WHERE is_active = TRUE"
            )
            all_rows = [dict(r) for r in cur.fetchall()]
        conn.close()

        conn2 = get_conn()
        with conn2.cursor() as cur2:
            cur2.execute("SELECT listing_id FROM review_queue WHERE status='pending'")
            existing_ids = {r["listing_id"] for r in cur2.fetchall()}
        conn2.close()

        problems      = []
        reasons_count = {}
        already_had   = 0

        for row in all_rows:
            flag, reason = needs_review_check(row)
            if not flag:
                continue
            problems.append((row["id"], reason))
            rkey = reason.split(":")[0].strip()
            reasons_count[rkey] = reasons_count.get(rkey, 0) + 1
            if row["id"] in existing_ids:
                already_had += 1

        new_items = [(lid, reason) for lid, reason in problems if lid not in existing_ids]

        if new_items:
            conn3 = get_conn()
            with conn3.cursor() as cur3:
                for lid, reason in new_items:
                    cur3.execute(
                        "INSERT INTO review_queue (listing_id, reason) "
                        "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (lid, reason)
                    )
            conn3.commit()
            conn3.close()

        lines = [
            "✅ Сканирование завершено", "",
            f"Проверено: {len(all_rows):,} объявлений".replace(",", " "),
            f"Проблемных: {len(problems):,}".replace(",", " "),
            f"Добавлено в очередь: {len(new_items)} ({already_had} уже были)",
            "",
        ]
        for reason, cnt in sorted(reasons_count.items(), key=lambda x: -x[1]):
            lines.append(f"  {reason}: {cnt}")

        total_queue = len(problems)
        kb = _kb(
            [_btn(f"⚠️  К очереди ({total_queue})", "admin|review|0")],
            [_btn("← Меню", "admin|menu")],
        )
        _edit(cid, mid, "\n".join(lines), kb)
    except Exception as e:
        print(f"[scan] error: {e}")
        try: _edit(cid, mid, f"❌ Ошибка сканирования:\n{e}")
        except: pass


# ── Callback handler ──────────────────────────────────────────────────────────
def handle_cb(cb):
    cbid = cb["id"]
    cid  = cb["message"]["chat"]["id"]
    mid  = cb["message"]["message_id"]
    uid  = cb["from"]["id"]
    data = cb.get("data", "")
    # v112 централизованный tracking callbacks → bot_users
    try:
        from bot_user_tracker import track_user_async
        track_user_async(
            telegram_id=uid,
            username=cb["from"].get("username", ""),
            first_name=cb["from"].get("first_name", ""),
            language=cb["from"].get("language_code", ""),
            action="callback",
        )
    except Exception:
        pass
    # v51 ANTISPAM (module-level, callback flood guard)
    if _antispam_mod:
        try:
            r = _antispam_mod.is_spam(uid, "", is_admin=(uid == ADMIN_ID))
            if r and r in ("flood_blocked", "blacklisted"):
                _answer(cbid)
                return
        except Exception:
            pass
    _answer(cbid)
    if "|" not in data: return
    parts  = data.split("|")
    action = parts[0]

    # ── Add listing callbacks ─────────────────────────────────────────────────
    if action == "add":
        sub = parts[1] if len(parts) > 1 else ""
        if sub == "start":
            start_add_listing(cid, uid, mid)
            return
        if sub == "cancel":
            add_states.pop(uid, None)
            _edit(cid, mid, _t(uid, "add_cancelled"),
                  _kb([_btn(_t(uid, "btn_menu"), "menu|main")]))
            return
        if sub == "skip_photos":
            s = add_states.get(uid, {})
            s["step"] = len(ADD_STEPS)
            add_states[uid] = s
            submit_listing(cid, uid)
            return
        if sub == "skip_field":
            # Generic skip for size/floor/unit/description steps — leave field empty, advance
            s = add_states.get(uid, {})
            s["waiting_text"] = None
            s["step"] = s.get("step", 0) + 1
            add_states[uid] = s
            add_next_step(cid, uid)
            return

        val = parts[2] if len(parts) > 2 else ""
        s = add_states.get(uid)
        if not s: return

        if sub == "area" and val == "_custom_":
            add_states[uid]["waiting_text"] = "custom_area"
            _send(cid, _t(uid, "add_area_custom_q"),
                  _kb([_btn(_t(uid, "add_cancel"), "add|cancel")]))
            return

        step_map = {
            "deal": "deal", "emirate": "emirate", "area": "area",
            "type": "type", "br": "bedrooms", "status": "status",
            "furn": "furnishing", "view": "view",
        }
        if sub in step_map:
            s["data"][step_map[sub]] = val
            s["step"] += 1
            add_states[uid] = s
            add_next_step(cid, uid)
        return

    # ── Moderation callbacks ──────────────────────────────────────────────────
    if action == "mod":
        sub = parts[1] if len(parts) > 1 else ""
        pid = int(parts[2]) if len(parts) > 2 else 0
        if uid != ADMIN_ID: return

        try:
            conn = get_conn()
            with conn.cursor() as cur:
                if sub == "approve":
                    cur.execute("SELECT data FROM pending_listings WHERE id=%s", (pid,))
                    row = cur.fetchone()
                    if row:
                        data = json.loads(row["data"])
                        # Save to listings table
                        from db_schema import upsert_listing
                        from parser_engine import make_listing_key
                        listing_data = {
                            "source": "user_submitted",
                            "telegram_chat_id": str(data.get("uid", "")),
                            "deal_type": data.get("deal", "sale"),
                            "property_type": data.get("type", "apartment"),
                            "emirate": data.get("emirate"),
                            "area": data.get("area"),
                            "building": data.get("building"),
                            "bedrooms": data.get("bedrooms"),
                            "size_sqft": float(data.get("size", 0) or 0),
                            "status": data.get("status"),
                            "furnishing": data.get("furnishing"),
                            "view": data.get("view"),
                            "phone": data.get("contact"),
                            "emirate_confidence": 0.95,
                            "area_confidence": 0.95,
                            "building_confidence": 0.90,
                            "confidence_score": 0.92,
                            "needs_manual_review": False,
                        }
                        # Parse price
                        price_str = str(data.get("price", "0")).lower().replace(",", "")
                        try:
                            if "m" in price_str:
                                listing_data["price"] = int(float(price_str.replace("m","")) * 1_000_000)
                            elif "k" in price_str:
                                listing_data["price"] = int(float(price_str.replace("k","")) * 1_000)
                            else:
                                listing_data["price"] = int(float(price_str))
                        except:
                            pass
                        listing_data["listing_key"] = make_listing_key(listing_data)
                        upsert_listing(listing_data)
                    cur.execute("UPDATE pending_listings SET status='approved' WHERE id=%s", (pid,))
                    _edit(cid, mid, "✅ Approved — added to database")
                    # Notify user
                    try:
                        conn2 = get_conn()
                        with conn2.cursor() as c2:
                            c2.execute("SELECT uid FROM pending_listings WHERE id=%s", (pid,))
                            r = c2.fetchone()
                            if r:
                                _send(r["uid"], "✅ Your listing has been approved and added to the database!")
                        conn2.close()
                    except:
                        pass
                else:
                    cur.execute("UPDATE pending_listings SET status='rejected' WHERE id=%s", (pid,))
                    _edit(cid, mid, "❌ Rejected")
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[mod] {e}")
        return

    # ── Main callbacks ────────────────────────────────────────────────────────
    if action == "lang":
        user_lang[uid] = parts[1]; _reset(uid)
        # Show main menu (persistent bottom keyboard) — categories define deal_type
        # so we don't ask "are you looking for sale or rent" anymore.
        try: _api("deleteMessage", chat_id=cid, message_id=mid)
        except: pass
        _send(cid, _t(uid, "main_menu"), kb_main_reply(uid))

    elif action == "default_deal":
        val = parts[1]  # "sale", "rent", "any"
        gs(uid)["default_deal"] = val if val != "any" else None
        show_main(cid, uid, mid)

    elif action == "menu":
        sub = parts[1]
        if sub == "main":     show_main(cid, uid, mid)
        elif sub == "lang":   _edit(cid, mid, "Select language:", kb_lang())
        elif sub == "filter": _reset(uid); _edit(cid, mid, _t(uid, "emirate_q"), kb_emirate(uid))
        elif sub == "hot":
            # По умолчанию из главного меню — только продажа (rent должен быть
            # выбран явно через wizard «Аренда»).
            _reset(uid); gs(uid)["filters"] = {"hot_only": True, "sort": "best_deals",
                                                 "deal_type": "sale"}
            _edit(cid, mid, _t(uid, "searching")); do_search(uid); send_results(cid, uid, mid)
        elif sub == "new":
            _reset(uid); gs(uid)["filters"] = {"sort": "newest", "deal_type": "sale"}
            _edit(cid, mid, _t(uid, "searching")); do_search(uid); send_results(cid, uid, mid)
        elif sub == "budget":
            _reset(uid); _edit(cid, mid, _t(uid, "budget_q"), kb_budget(uid))
        elif sub == "area":
            _reset(uid); _edit(cid, mid, _t(uid, "area_q"), kb_areas(uid))
        elif sub == "building":
            _reset(uid)
            gs(uid)["waiting"] = "building"
            _edit(cid, mid, _t(uid, "bld_q"),
                  _kb([_btn(_t(uid, "btn_back"), "menu|main")]))

    elif action == "ai":
        if parts[1] == "start": show_ai_start(cid, uid, mid)
        else: handle_ai(cid, uid, mid, parts)

    elif action == "em":
        gs(uid)["filters"]["emirate"] = None if parts[1] == "any" else parts[1]
        f = gs(uid)["filters"]
        # Skip wizard steps that the main-menu category has already set
        if f.get("property_type") == "plot":
            # Plot category: skip deal/property — go to budget
            _edit(cid, mid, _t(uid, "budget_q"), kb_budget(uid, is_rent=False))
        elif f.get("property_type_in"):
            # Commercial category: pick sub-type
            _edit(cid, mid, _t(uid, "prop_q"), kb_commercial_type(uid))
        elif f.get("deal_type") and f.get("property_type_not_in"):
            # Buy/Rent category: pick residential sub-type
            _edit(cid, mid, _t(uid, "prop_q"), kb_proptype(uid))
        else:
            # Old flow (no category preset): ask deal_type
            _edit(cid, mid, _t(uid, "deal_q"), kb_deal(uid))

    elif action == "deal":
        val = parts[1]
        if val != "any": gs(uid)["filters"]["deal_type"] = val
        _edit(cid, mid, _t(uid, "prop_q"), kb_proptype(uid))

    elif action == "pt":
        val = parts[1]
        if val != "any": gs(uid)["filters"]["property_type"] = val
        is_rent = gs(uid)["filters"].get("deal_type") == "rent"
        _edit(cid, mid, _t(uid, "rent_budget_q" if is_rent else "budget_q"),
              kb_budget(uid, is_rent))

    elif action == "bud":
        val = parts[1]
        # Comprehensive budget map for all categories
        bmap = {
            # Sale residential (default)
            "u1":     (None,        1_000_000),
            "1-2":    (1_000_000,   2_000_000),
            "2-3":    (2_000_000,   3_000_000),
            "3-5":    (3_000_000,   5_000_000),
            "5-10":   (5_000_000,  10_000_000),
            "10-25":  (10_000_000, 25_000_000),
            "25p":    (25_000_000,       None),
            # Legacy keys (kept for backward compat with old inline buttons)
            "2-5":    (2_000_000,   5_000_000),
            "5p":     (5_000_000,        None),
            # Rent
            "r_u60":     (None,    60_000),
            "r_60100":   (60_000, 100_000),
            "r_u100":    (None,   100_000),
            "r_100200":  (100_000, 200_000),
            "r_200500":  (200_000, 500_000),
            "r_200p":    (200_000,    None),
            "r_500p":    (500_000,    None),
            # Commercial
            "c_u1":      (None,     1_000_000),
            "c_15":      (1_000_000, 5_000_000),
            "c_520":     (5_000_000, 20_000_000),
            "c_20100":   (20_000_000, 100_000_000),
            "c_100p":    (100_000_000, None),
            # Plot
            "p_u5":      (None,     5_000_000),
            "p_520":     (5_000_000, 20_000_000),
            "p_2050":    (20_000_000, 50_000_000),
            "p_50100":   (50_000_000, 100_000_000),
            "p_100p":    (100_000_000, None),
        }
        if val in bmap:
            mn, mx = bmap[val]
            if mn: gs(uid)["filters"]["min_price"] = mn
            if mx: gs(uid)["filters"]["max_price"] = mx
        # Next step: bedrooms (residential) or skip to search (commercial/plot)
        f = gs(uid)["filters"]
        if f.get("property_type") == "plot" or f.get("property_type_in"):
            # Commercial/plot — go straight to search
            _edit(cid, mid, _t(uid, "searching"))
            do_search(uid)
            send_results(cid, uid)
        else:
            _edit(cid, mid, _t(uid, "br_q"), kb_bedrooms(uid))

    elif action == "br":
        v = parts[1]
        if v == "0":    gs(uid)["filters"]["bedrooms"] = 0
        elif v == "4p": gs(uid)["filters"]["bedrooms"] = 99
        elif v != "any": gs(uid)["filters"]["bedrooms"] = int(v)
        _edit(cid, mid, _t(uid, "searching"))
        do_search(uid); send_results(cid, uid, mid)

    elif action == "area":
        if parts[1] == "_custom_":
            gs(uid)["waiting"] = "custom_area"
            _edit(cid, mid, _t(uid, "area_custom_q"),
                  _kb([_btn(_t(uid, "btn_back"), "menu|main")]))
        else:
            gs(uid)["filters"]["area"] = parts[1]
            _edit(cid, mid, _t(uid, "br_q"), kb_bedrooms(uid))

    elif action == "detail":
        show_detail(cid, uid, mid, int(parts[1]))

    elif action == "book":
        lid   = int(parts[1]) if len(parts) > 1 else 0
        uname = cb["from"].get("username", "")
        fname = cb["from"].get("first_name", "")
        lang  = user_lang.get(uid, "en")
        save_lead(uid, uname, lid, "book")
        send_lead_to_bot(uid, uname, fname, lang, lid)
        _edit(cid, mid, _t(uid, "contact_sent"),
              _kb([_btn(_t(uid, "btn_menu"), "menu|main")]))

    elif action == "send":
        lid = int(parts[1]) if len(parts) > 1 else 0
        listing = get_listing_by_id(lid)
        if listing:
            text = format_card(dict(listing), uid)
            _send(cid, text)

    elif action == "pdf":
        lid = int(parts[1]) if len(parts) > 1 else 0
        listing = get_listing_by_id(lid)
        if not listing:
            # ACK was already sent at the top — show via normal message
            _send(cid, "Not found.")
            return
        ack = {"en": "📄 Generating PDF…", "ru": "📄 Готовлю PDF…",
                "ar": "📄 جاري التحضير…"}.get(_get_lang(uid), "📄 Generating PDF…")
        # Send as message (toast won't fire — ACK already sent at top of handle_cb)
        _send(cid, ack)
        threading.Thread(target=_send_pdf, args=(cid, uid, dict(listing)),
                          daemon=True).start()

    elif action == "similar":
        lid = int(parts[1]) if len(parts) > 1 else 0
        listing = get_listing_by_id(lid)
        if listing:
            gs(uid)["filters"] = {
                "area": listing.get("area"),
                "bedrooms": listing.get("bedrooms"),
                "deal_type": listing.get("deal_type", "sale"),
                "sort": "best_deals",
            }
            _edit(cid, mid, _t(uid, "searching"))
            do_search(uid); send_results(cid, uid, mid)

    elif action == "filter":
        if parts[1] == "deal_type_reset":
            gs(uid)["filters"].pop("deal_type", None)
            gs(uid)["default_deal"] = None
            show_deal_type_menu(cid, uid, mid)

    elif action == "results":
        if parts[1] == "more": send_results(cid, uid)
        elif parts[1] == "back": show_main(cid, uid, mid)

    # ── Favorites ─────────────────────────────────────────────────────────────
    # ── All units in this building ──────────────────────────────────────
    # Сбрасывает budget/bedrooms фильтры и показывает все объекты в здании.
    elif action == "allbld":
        lid = int(parts[1]) if len(parts) > 1 else 0
        listing = get_listing_by_id(lid)
        if not listing or not listing.get("building"):
            _api("answerCallbackQuery", callback_query_id=cb["id"],
                 text="No building info", show_alert=True)
            return
        s = gs(uid)
        bld = listing["building"]
        # Сброс фильтров — оставляем только building + deal_type того же объекта
        new_filters = {
            "building": bld,
            "deal_type": listing.get("deal_type", "sale"),
            "sort": "best_deals",
        }
        # Сохраняем emirate если есть (часто нужно для дедупа buildings с одним именем)
        if listing.get("emirate"):
            new_filters["emirate"] = listing["emirate"]
        s["filters"] = new_filters
        s["wizard"] = None
        _send(cid, f"🏢 Все объекты в *{bld}*", kb_main_reply(uid))
        do_search(uid)
        send_results(cid, uid)

    # ── Relax filter — убрать конкретный фильтр и заново поискать ──────
    elif action == "relax":
        which = parts[1] if len(parts) > 1 else ""
        s = gs(uid)
        if which == "building":
            s["filters"].pop("building", None)
        elif which == "area":
            s["filters"].pop("area", None)
        elif which == "bedrooms":
            s["filters"].pop("bedrooms", None)
        elif which == "budget":
            s["filters"].pop("min_price", None)
            s["filters"].pop("max_price", None)
        _edit(cid, mid, _t(uid, "searching"))
        do_search(uid)
        send_results(cid, uid, mid)

    # ── Area picker — выбор района из suggestions → переход к building ──────
    elif action == "pickarea":
        chosen = parts[1] if len(parts) > 1 else "__any__"
        s = gs(uid)
        if chosen != "__any__":
            s["filters"]["area"] = chosen
        # После area переходим к building выбору
        s["wizard"] = "building_input"
        _send(cid, _t(uid, "wiz_bld_q"), kb_reply_building_input(uid))

    # ── Building picker — выбор здания из suggestions → запуск поиска ───────
    elif action == "pickbld":
        chosen = parts[1] if len(parts) > 1 else "__any__"
        s = gs(uid)
        if chosen != "__any__":
            s["filters"]["building"] = chosen
        s["wizard"] = None
        _send(cid, _t(uid, "searching"), kb_main_reply(uid))
        do_search(uid)
        send_results(cid, uid)

    # ── Watchlist toggle (v55) ────────────────────────────────────────────────
    elif action == "watch":
        lid = int(parts[1]) if len(parts) > 1 else 0
        try:
            from db_schema import (add_watchlist, remove_watchlist,
                                    is_watching, get_user_watchlists)
            if is_watching(uid, "listing", str(lid)):
                # удалить из watchlist по listing_value
                rows = [r for r in get_user_watchlists(uid)
                        if r.get("watch_type") == "listing"
                        and r.get("watch_value") == str(lid)]
                for r in rows:
                    remove_watchlist(uid, r["id"])
                _api("answerCallbackQuery", callback_query_id=cb["id"],
                     text=_t(uid, "watch_removed"))
            else:
                # listing watchlist + сохраняем филтры из listing
                lst = get_listing_by_id(lid) or {}
                filters = {
                    "area":          lst.get("area"),
                    "building":      lst.get("building"),
                    "bedrooms":      lst.get("bedrooms"),
                    "deal_type":     lst.get("deal_type"),
                    "property_type": lst.get("property_type"),
                    "price":         lst.get("price"),
                }
                add_watchlist(uid, "listing", str(lid),
                              filters=filters, freq="daily")
                _api("answerCallbackQuery", callback_query_id=cb["id"],
                     text=_t(uid, "watch_added"))
        except Exception as e:
            print(f"[watch] toggle fail uid={uid} lid={lid}: {e}", flush=True)
            _api("answerCallbackQuery", callback_query_id=cb["id"],
                 text="⚠ Watchlist error.")

    elif action == "fav":
        lid = int(parts[1]) if len(parts) > 1 else 0
        from db_schema import add_favorite, remove_favorite, is_favorited
        if is_favorited(uid, lid):
            remove_favorite(uid, lid)
            _api("answerCallbackQuery", callback_query_id=cb["id"],
                 text=_t(uid, "btn_fav_rem"))
        else:
            add_favorite(uid, lid)
            save_lead(uid, "", lid, "save")
            _api("answerCallbackQuery", callback_query_id=cb["id"],
                 text=_t(uid, "btn_fav_add"))

    # ── Compare cart ──────────────────────────────────────────────────────────
    elif action == "cmp":
        lid = int(parts[1]) if len(parts) > 1 else 0
        cart = gs(uid).setdefault("compare", [])
        if lid in cart:
            cart.remove(lid)
            _api("answerCallbackQuery", callback_query_id=cb["id"],
                 text="Removed from compare")
        elif len(cart) >= 3:
            _api("answerCallbackQuery", callback_query_id=cb["id"],
                 text=_t(uid, "compare_full"), show_alert=True)
        else:
            cart.append(lid)
            _api("answerCallbackQuery", callback_query_id=cb["id"],
                 text=_t(uid, "compare_added", n=len(cart)))

    # ── Map link ──────────────────────────────────────────────────────────────
    elif action == "map":
        lid = int(parts[1]) if len(parts) > 1 else 0
        listing = get_listing_by_id(lid)
        if listing:
            building = listing.get("building") or ""
            area     = listing.get("area") or ""
            emirate  = listing.get("emirate") or "Dubai"
            q = ", ".join(x for x in [building, area, emirate, "UAE"] if x)
            url = f"https://www.google.com/maps/search/?api=1&query={requests.utils.quote(q)}"
            _send(cid, f"🗺 {q}\n\n{url}")

    # ── Auto-translate listing description via Claude ─────────────────────────
    elif action == "translate":
        lid = int(parts[1]) if len(parts) > 1 else 0
        target_lang = parts[2] if len(parts) > 2 else user_lang.get(uid, "en")
        listing = get_listing_by_id(lid)
        if listing and listing.get("original_text"):
            translated = claude_translate(listing["original_text"], target_lang)
            if translated:
                _send(cid, f"🌐 *Translation ({target_lang.upper()})*\n\n{translated}")
            else:
                _api("answerCallbackQuery", callback_query_id=cb["id"],
                     text="Translation unavailable", show_alert=True)
        else:
            _api("answerCallbackQuery", callback_query_id=cb["id"],
                 text="No text to translate", show_alert=True)

    # ── Photo carousel (all photos) ───────────────────────────────────────────
    elif action == "photos":
        lid = int(parts[1]) if len(parts) > 1 else 0
        images = get_listing_images(lid) or []
        urls = [img for img in images if not img.startswith("tg://")]
        if not urls:
            _api("answerCallbackQuery", callback_query_id=cb["id"],
                 text="No photos available", show_alert=True)
        else:
            try:
                _media_group(cid, urls[:10])
                _api("answerCallbackQuery", callback_query_id=cb["id"])
            except Exception:
                _api("answerCallbackQuery", callback_query_id=cb["id"],
                     text="Failed to send photos", show_alert=True)

    # ── Admin callbacks ───────────────────────────────────────────────────────
    elif action == "admin":
        if uid != ADMIN_ID: return
        sub = parts[1] if len(parts) > 1 else ""

        if sub == "menu":
            show_admin_menu(cid, uid, mid)

        elif sub == "stats":
            show_stats(cid, uid)

        elif sub == "review":
            idx = int(parts[2]) if len(parts) > 2 else 0
            existing = admin_states.get(uid, {})
            if existing.get("queue") is None or idx == 0:
                # Fresh open or explicit reset to start — reload queue
                admin_states[uid] = {"queue": get_review_queue(), "idx": idx, "edits": {}}
            else:
                # Navigation within existing session — preserve queue & edits
                admin_states[uid]["idx"] = idx
            show_review_item(cid, uid, idx, mid)

        elif sub == "noop":
            pass

        elif sub == "edit":
            field = parts[2] if len(parts) > 2 else ""
            qid   = int(parts[3]) if len(parts) > 3 else 0
            admin_states.setdefault(uid, {})["edit_field"] = field
            admin_states[uid]["edit_qid"] = qid
            labels = {"building": "здание", "area": "район",
                      "price": "цену", "bedrooms": "кол-во спален"}
            _send(cid, f"✏️ Введите {labels.get(field, field)}:")

        elif sub == "save":
            qid   = int(parts[2]) if len(parts) > 2 else 0
            state = admin_states.get(uid, {})
            edits = state.get("edits", {})
            queue = state.get("queue", [])
            item  = next((q for q in queue if q["id"] == qid), None)
            if not item:
                _send(cid, "❌ Элемент не найден"); return
            if not edits:
                _send(cid, "ℹ️ Нечего сохранять — сначала отредактируйте поля"); return
            lid = item["listing_id"]
            try:
                conn = get_conn()
                set_clauses = ", ".join(f"{f} = %s" for f in edits)
                vals = list(edits.values()) + [lid]
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE listings SET {set_clauses}, updated_at=NOW() WHERE id=%s",
                        vals
                    )
                    cur.execute(
                        "UPDATE review_queue SET status='approved', reviewed_at=NOW() WHERE id=%s",
                        (qid,)
                    )
                conn.commit()
                conn.close()
                new_queue = [q for q in queue if q["id"] != qid]
                new_idx   = min(state.get("idx", 0), max(0, len(new_queue) - 1))
                admin_states[uid] = {"queue": new_queue, "idx": new_idx, "edits": {}}
                _edit(cid, mid, "✅ Сохранено")
                if new_queue:
                    show_review_item(cid, uid, new_idx)
                else:
                    _send(cid, "✅ Очередь пуста",
                          _kb([_btn("← Меню", "admin|menu")]))
            except Exception as e:
                print(f"[admin save] {e}")
                _edit(cid, mid, f"❌ Ошибка: {e}")

        elif sub == "del":
            qid   = int(parts[2]) if len(parts) > 2 else 0
            state = admin_states.get(uid, {})
            queue = state.get("queue", [])
            item  = next((q for q in queue if q["id"] == qid), None)
            if not item:
                _send(cid, "❌ Элемент не найден"); return
            lid = item["listing_id"]
            try:
                conn = get_conn()
                with conn.cursor() as cur:
                    cur.execute("UPDATE listings SET is_active=FALSE WHERE id=%s", (lid,))
                    cur.execute(
                        "UPDATE review_queue SET status='rejected', reviewed_at=NOW() WHERE id=%s",
                        (qid,)
                    )
                conn.commit()
                conn.close()
                new_queue = [q for q in queue if q["id"] != qid]
                new_idx   = min(state.get("idx", 0), max(0, len(new_queue) - 1))
                admin_states[uid] = {"queue": new_queue, "idx": new_idx, "edits": {}}
                _edit(cid, mid, "🚫 Удалено")
                if new_queue:
                    show_review_item(cid, uid, new_idx)
                else:
                    _send(cid, "✅ Очередь пуста",
                          _kb([_btn("← Меню", "admin|menu")]))
            except Exception as e:
                print(f"[admin del] {e}")
                _edit(cid, mid, f"❌ Ошибка: {e}")

        elif sub == "scan":
            t = threading.Thread(target=run_scan_bg, args=(cid, uid, mid), daemon=True)
            t.start()

        elif sub == "manage":
            text = (
                "────────────────────\n"
                "⚙️  УПРАВЛЕНИЕ\n"
                "────────────────────\n\n"
                "Источник: @flipluxproperty\n"
                "Статус парсера: активен (Railway)\n"
            )
            kb = _kb(
                [_btn("🔄 Запустить парсер", "admin|parse")],
                [_btn("← Назад",             "admin|menu")],
            )
            _edit(cid, mid, text, kb)

        elif sub == "parse":
            from telethon_parser import run_parser_thread
            run_parser_thread(backfill=False)
            _edit(cid, mid, "⚙️ Парсер запущен (инкрементальный)",
                  _kb([_btn("← Меню", "admin|menu")]))


# ── Message handler ───────────────────────────────────────────────────────────
def handle_msg(msg):
    text  = msg.get("text", "").strip()
    photo = msg.get("photo")
    cid   = msg["chat"]["id"]
    uid   = msg["from"]["id"]
    uname = msg["from"].get("username", "")
    fname = msg["from"].get("first_name", "")
    _lang_code = msg["from"].get("language_code", "")
    # v112 централизованный user tracking → bot_users (resale-DB)
    try:
        from bot_user_tracker import track_user_async
        track_user_async(telegram_id=uid, username=uname, first_name=fname,
                         language=_lang_code, action="message")
    except Exception:
        pass
    # v51 ANTISPAM (module-level)
    if _antispam_mod:
        try:
            reason = _antispam_mod.is_spam(uid, text, is_admin=(uid == ADMIN_ID))
            if reason:
                print(f"[antispam] blocked uid={uid} reason={reason}", flush=True)
                return
        except Exception:
            pass
    lang  = user_lang.get(uid, "en")
    save_user(uid, uname, fname, lang)

    # Handle photo upload for add listing wizard
    if photo and uid in add_states:
        s = add_states[uid]
        if s.get("waiting_text") == "photos":
            file_id = photo[-1]["file_id"]
            s.setdefault("photos", []).append(file_id)
            add_states[uid] = s
            count = len(s["photos"])
            _send(cid, f"✅ Photo {count} received. Send more or tap Skip.",
                  _reply_with_skip_cancel(uid))
            return

    if not text:
        return

    # Language selection (bottom reply keyboard at /start)
    if text in LANG_BUTTONS:
        user_lang[uid] = LANG_BUTTONS[text]
        _reset(uid)
        save_user(uid, uname, fname, user_lang[uid])
        _send(cid, _t(uid, "main_menu"), kb_main_reply(uid))
        return

    # Bottom reply-keyboard buttons → dispatch to handlers
    rkey = is_main_menu_text(text)
    if rkey:
        dispatch_main_button(cid, uid, rkey)
        return

    # Wizard step buttons (emirate / property_type / bedrooms)
    if dispatch_wizard_button(cid, uid, text):
        return

    if text.startswith("/"):
        cmd = text.split()[0].lower().lstrip("/").split("@")[0]
        # /alert_del_42 → remove alert id 42
        if cmd.startswith("alert_del_"):
            try:
                aid = int(cmd[len("alert_del_"):])
                from db_schema import delete_alert
                delete_alert(uid, aid)
                _send(cid, _t(uid, "alert_deleted"), kb_main_reply(uid))
            except Exception:
                pass
            return
        # /unwatch_42 → remove watchlist id 42  (v55)
        if cmd.startswith("unwatch_"):
            try:
                wid = int(cmd[len("unwatch_"):])
                from db_schema import remove_watchlist
                ok = remove_watchlist(uid, wid)
                # NB: avoid shadowing outer `msg` dict — use distinct local name
                reply = _t(uid, "watch_removed") if ok else "Not found."
                _send(cid, reply, kb_main_reply(uid))
            except Exception as _e:
                _send(cid, "⚠ /unwatch_N — invalid id.")
            return
        if cmd == "watch":
            show_watchlist(cid, uid); return
        if cmd == "unwatch":
            # без аргумента — показать список с командами удаления
            show_watchlist(cid, uid); return
        if cmd == "favs":
            show_favorites(cid, uid); return
        if cmd == "alerts":
            show_alerts(cid, uid); return
        if cmd in ("language", "lang"):
            # v54 UX (Layla follow-up): команда /language заменяет «🌐 Язык» в меню.
            _send(cid, "🌐  Select your language / Выберите язык / اختر لغتك",
                  kb_lang_reply())
            return
        if cmd == "compare":
            show_compare(cid, uid); return
        if cmd == "compare_clear":
            gs(uid)["compare"] = []
            _send(cid, "🗑 Compare cart cleared.", kb_main_reply(uid))
            return
        if cmd == "start":
            user_lang.pop(uid, None); _reset(uid)
            # v55: parse deep-link payload + log cross-bot UTM jump.
            # Supports old format (from_hub, from_offplan_3190) and new
            # extended format (from_offplan_3190_utm_resale_card_share).
            if " " in text:
                payload = text.split(" ", 1)[1].strip()
                print(f"[resale] /start payload={payload} uid={uid}", flush=True)
                try:
                    from db_schema import log_cross_bot_jump
                    from_bot, src, camp, content = _parse_payload(payload)
                    log_cross_bot_jump(
                        from_bot=from_bot, to_bot="resale",
                        user_id=uid, payload=payload,
                        utm_source=src, utm_campaign=camp, utm_content=content,
                    )
                except Exception as _e:
                    print(f"[resale] /start cbj log err: {_e}", flush=True)
            send_welcome_with_logo(cid, uid)
        elif cmd == "menu":  show_main(cid, uid)
        elif cmd == "stats": show_stats(cid, uid)
        elif cmd == "parse":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            _send(cid, "⚙️ Parser started — incremental sync running in background...")
            from telethon_parser import run_parser_thread
            run_parser_thread(backfill=False)
        elif cmd == "backfillall":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            _send(cid,
                "🔄 *FULL BACKFILL запущен* для всех каналов с 01.01.2026.\n\n"
                "Существующие записи **не удаляются** — дедупликация через "
                "`listing_key` UNIQUE.\n\n"
                "Парсер пройдёт все 3 канала с начала года, пропустит уже "
                "сохранённые сообщения и добавит пропущенные.\n\n"
                "Прогресс: смотри Railway logs или вызывай /stats через 15–30 мин.")
            from telethon_parser import run_parser_thread
            run_parser_thread(backfill=True)
        elif cmd == "auditreview":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            # Show audit stats + sample
            try:
                conn = get_conn()
                with conn.cursor() as c:
                    c.execute("SELECT COUNT(*) AS n FROM listings WHERE is_active=TRUE AND is_audit=TRUE")
                    total = c.fetchone()["n"]
                    c.execute("""
                        SELECT split_part(audit_reason, '_', 1) || '_' || split_part(audit_reason, '_', 2) AS bucket,
                               COUNT(*) AS n
                        FROM listings
                        WHERE is_active=TRUE AND is_audit=TRUE
                        GROUP BY bucket ORDER BY n DESC LIMIT 10
                    """)
                    buckets = c.fetchall()
                    c.execute("""
                        SELECT id, audit_reason, LEFT(original_text, 150) AS snippet
                        FROM listings
                        WHERE is_active=TRUE AND is_audit=TRUE
                        ORDER BY audit_flagged_at DESC NULLS LAST LIMIT 5
                    """)
                    samples = c.fetchall()
                conn.close()
                lines = [f"📋 *Audit Review*\n\n*Total flagged:* {total}\n\n*Top reasons:*"]
                for b in buckets:
                    lines.append(f"  `{b['bucket']}`: {b['n']}")
                lines.append("\n*Recent samples:*")
                for s in samples:
                    lines.append(f"\n• id={s['id']} reason: `{s['audit_reason'][:80]}`")
                    lines.append(f"  text: _{s['snippet'][:120].replace('`','')}_")
                _send(cid, "\n".join(lines))
            except Exception as e:
                _send(cid, f"⚠️ Ошибка: {e}")
        elif cmd == "leads":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            try:
                conn = get_conn()
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT l.action, COUNT(*) AS cnt
                        FROM leads l
                        WHERE l.created_at > NOW() - INTERVAL '7 days'
                        GROUP BY l.action ORDER BY cnt DESC
                    """)
                    actions = list(cur.fetchall())
                    cur.execute("""
                        SELECT lst.building, lst.area, COUNT(*) AS conversions
                        FROM leads l JOIN listings lst ON lst.id = l.listing_id
                        WHERE l.created_at > NOW() - INTERVAL '30 days'
                          AND l.action IN ('book','contact','save')
                        GROUP BY lst.building, lst.area
                        ORDER BY conversions DESC LIMIT 15
                    """)
                    top = list(cur.fetchall())
                    cur.execute("SELECT COUNT(*) AS n FROM favorites")
                    favs_total = cur.fetchone()["n"]
                    cur.execute("SELECT COUNT(*) AS n FROM price_alerts WHERE is_active=TRUE")
                    alerts_n = cur.fetchone()["n"]
                conn.close()
            except Exception as e:
                _send(cid, f"DB error: {e}"); return

            txt = ["📈 *LEADS ANALYTICS (7 days)*", ""]
            for r in actions:
                txt.append(f"  {r['action']:8} {r['cnt']:>5}")
            txt += ["", "*TOP-15 buildings by leads (30d):*"]
            for r in top:
                txt.append(f"  {r['conversions']:>3}  {r['building'] or '—'} · {r['area'] or '—'}")
            txt += ["", f"❤️ Total favorites: {favs_total}",
                          f"🔔 Active alerts:   {alerts_n}"]
            _send(cid, "\n".join(txt))
            return
        elif cmd == "digest":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            try:
                from cron_worker import _digest_text
                _send(cid, _digest_text())
            except Exception as e:
                _send(cid, f"Error: {e}")
            return
        elif cmd == "freeze":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            try:
                conn = get_conn()
                with conn.cursor() as cur:
                    cur.execute("UPDATE listings SET is_frozen=TRUE WHERE is_active=TRUE AND is_frozen=FALSE")
                    n = cur.rowcount
                conn.commit()
                conn.close()
                _send(cid, f"🧊 Заморожено {n} записей. Парсер больше не будет их апдейтить.")
            except Exception as e:
                _send(cid, f"Error: {e}")
            return
        elif cmd == "unfreeze":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            args = text.split()
            try:
                conn = get_conn()
                with conn.cursor() as cur:
                    if len(args) > 1 and args[1] == "all":
                        cur.execute("UPDATE listings SET is_frozen=FALSE WHERE is_frozen=TRUE")
                        n = cur.rowcount
                        _send(cid, f"🔥 Разморожено всех ({n}). Парсер снова сможет апдейтить.")
                    elif len(args) > 1:
                        lid = int(args[1])
                        cur.execute("UPDATE listings SET is_frozen=FALSE WHERE id=%s", (lid,))
                        n = cur.rowcount
                        _send(cid, f"🔥 Разморожено id={lid} (rows={n}).")
                    else:
                        _send(cid, "Использование: /unfreeze <id> | /unfreeze all")
                conn.commit()
                conn.close()
            except Exception as e:
                _send(cid, f"Error: {e}")
            return
        elif cmd == "freezestat":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            try:
                conn = get_conn()
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM listings WHERE is_frozen=TRUE")
                    frozen = cur.fetchone()["count"]
                    cur.execute("SELECT COUNT(*) FROM listings WHERE is_active=TRUE AND is_frozen=FALSE")
                    not_frozen = cur.fetchone()["count"]
                conn.close()
                _send(cid, f"🧊 Заморожено: *{frozen}*\n🆕 Незамороженных активных: *{not_frozen}*\n\nНовые записи добавляются с `is_frozen=FALSE` — парсер их обновляет; ручную чистку отрабатывают `/freeze` после правок.")
            except Exception as e:
                _send(cid, f"Error: {e}")
            return
        elif cmd == "auditrun":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            _send(cid, "🔍 Запускаю flag_audit.py в фоне — займёт ~5 мин на 5k записей.")
            def _run_audit():
                import subprocess, os
                os.chdir(os.path.dirname(__file__) or ".")
                subprocess.Popen(["python", "flag_audit.py"])
            threading.Thread(target=_run_audit, daemon=True).start()
        elif cmd == "cleanup":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            try:
                conn = get_conn()
                with conn.cursor() as cur:
                    cur.execute("""
                        DELETE FROM listings
                        WHERE 
                          (listing_key IS NULL AND building IS NULL AND area IS NULL)
                          OR (price > 50000000 AND deal_type = 'sale')
                          OR (price IS NULL AND building IS NULL AND area IS NULL)
                    """)
                    deleted = cur.rowcount
                conn.commit()
                conn.close()
                _send(cid, f"✅ Удалено {deleted} некорректных объявлений")
            except Exception as e:
                _send(cid, f"⚠️ Ошибка: {e}")
        elif cmd == "catchup":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            from telethon_parser import run_catchup_thread, get_real_last_message_id, CHANNELS
            lines = ["🔄 *Catchup запущен* — парсим пропущенные сообщения\n"]
            for ch in CHANNELS:
                last_id = get_real_last_message_id(ch)
                lines.append(f"  @{ch}: от msg_id={last_id}")
            _send(cid, "\n".join(lines))
            run_catchup_thread()
        elif cmd == "airescan":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            _send(cid, "🔄 Запускаю AI классификацию аренда/продажа...")
            threading.Thread(target=ai_rescan_deal_types, args=(cid,), daemon=True).start()
        elif cmd == "fullrescan":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            _send(cid, "🔄 Запускаю ПОЛНЫЙ AI-пересмотр базы…\nЭто займёт 15–30 минут.")
            threading.Thread(target=full_ai_rescan, args=(cid,), daemon=True).start()
        elif cmd == "admin":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            show_admin_menu(cid, uid)
        elif cmd == "add":   start_add_listing(cid, uid)
        elif cmd == "help":
            _send(cid,
                "/start — Welcome\n"
                "/menu — Main menu\n"
                "/add — List your property\n"
                "/stats — Statistics (admin)\n"
                "/parse — Trigger incremental parse (admin)\n"
                "/catchup — Resume from last known message (admin)\n"
                "/backfillall — Full backfill all channels from 01.01.2026 (admin)\n"
                "/auditreview — Show audit stats + sample (admin)\n"
                "/auditrun — Re-run audit flagging on all records (admin)\n"
                "/airescan — AI deal_type rescan (admin)\n"
                "/fullrescan — Full AI re-parse all listings (admin)")
        return

    # Handle admin field edit input
    if uid == ADMIN_ID and uid in admin_states:
        astate = admin_states[uid]
        if astate.get("edit_field") and astate.get("edit_qid") is not None:
            field = astate["edit_field"]
            qid   = astate["edit_qid"]
            val   = text
            if field in ("price", "bedrooms"):
                try:
                    val = int(text.replace(".", "").replace(",", "").replace(" ", "").upper().rstrip("M"))
                    if field == "price" and ("m" in text.lower() or "м" in text.lower()):
                        val = val * 1_000_000
                except ValueError:
                    _send(cid, "❌ Неверный формат. Введите число.")
                    return
            astate.setdefault("edits", {})[field] = val
            astate.pop("edit_field", None)
            astate.pop("edit_qid",   None)
            labels = {"building": "Здание", "area": "Район",
                      "price": "Цена", "bedrooms": "Спальни"}
            _send(cid, f"✅ {labels.get(field, field)}: *{val}*\n\nНажмите «✅ Сохранить» для записи в базу.",
                  None)
            queue = astate.get("queue", [])
            idx   = astate.get("idx", 0)
            if queue:
                show_review_item(cid, uid, idx)
            return

    # /add wizard — bottom reply keyboard buttons
    if uid in add_states:
        if dispatch_add_button(cid, uid, text):
            return

    # Handle add listing text inputs
    if uid in add_states:
        s = add_states[uid]
        waiting = s.get("waiting_text")
        if waiting == "custom_area":
            s["data"]["area"] = text
            s["waiting_text"] = None
            s["step"] += 1
            add_states[uid] = s
            add_next_step(cid, uid)
            return
        if waiting in ("building", "size", "price", "contact", "floor", "unit", "description"):
            s["data"][waiting] = text
            s["waiting_text"] = None
            s["step"] += 1
            add_states[uid] = s
            add_next_step(cid, uid)
            return

    s = gs(uid)

    # Custom area search input (main search)
    if s.get("waiting") == "custom_area":
        s["waiting"] = None
        resp = _send(cid, _t(uid, "searching"))
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM listings WHERE area ILIKE %s AND is_active = TRUE ORDER BY is_hot_deal DESC, price ASC LIMIT 50",
                    (f"%{text}%",)
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            conn.close()
        except Exception as e:
            print(f"[area search] {e}")
            rows = []
        if not rows:
            mid = resp.get("result", {}).get("message_id")
            _edit(cid, mid, _t(uid, "area_custom_none", text=text))
            return
        s["results"] = rows
        s["total"]   = len(rows)
        s["page"]    = 0
        mid = resp.get("result", {}).get("message_id")
        send_results(cid, uid, mid)
        return

    # Building search input
    if s.get("waiting") == "building":
        # Keep waiting="building" so next input also searches by building
        s["filters"]["building"] = text
        print(f"[SEARCH] building query=\"{text}\"")
        resp = _send(cid, _t(uid, "searching"))
        do_search(uid)
        mid = resp.get("result", {}).get("message_id")
        total = s.get("total", 0)
        print(f"[SEARCH] query=\"{text}\" results={total}")
        send_results(cid, uid, mid)
        return

    # Natural language search
    if len(text) > 5:
        filters = parse_nl(text, lang)
        if filters:
            # Merge with existing filters so prior wizard category (e.g. SALE)
            # is preserved when NL did not explicitly mention deal_type / type.
            existing = dict(s.get("filters", {}))
            for k, v in filters.items():
                existing[k] = v
            # Restore default_deal if NL did not specify deal_type
            if "deal_type" not in existing and s.get("default_deal"):
                existing["deal_type"] = s["default_deal"]
            s["filters"] = existing
            resp = _send(cid, _t(uid, "searching"))
            do_search(uid)
            mid = resp.get("result", {}).get("message_id")
            send_results(cid, uid, mid)


# ── Polling ───────────────────────────────────────────────────────────────────
def run_bot():
    print("[bot] Starting polling...")
    offset = 0
    while True:
        try:
            r = requests.get(f"{API}/getUpdates",
                params={"offset": offset, "timeout": 30,
                        "allowed_updates": ["message", "callback_query"]},
                timeout=35)
            data = r.json()
            if not data.get("ok"): time.sleep(5); continue
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    if "callback_query" in upd: handle_cb(upd["callback_query"])
                    elif "message" in upd:       handle_msg(upd["message"])
                except Exception as e:
                    print(f"[bot] Update error: {e}")
                    # v53: log to bot_error_events для watchdog
                    if _err_logger:
                        try:
                            import traceback as _tb
                            uid = None
                            ctx = {}
                            if "callback_query" in upd:
                                uid = upd["callback_query"]["from"]["id"]
                                ctx["data"] = upd["callback_query"].get("data", "")[:200]
                                handler = "handle_cb"
                            elif "message" in upd:
                                uid = upd["message"]["from"]["id"]
                                ctx["text"] = (upd["message"].get("text") or "")[:200]
                                handler = "handle_msg"
                            else:
                                handler = "update_loop"
                            _err_logger.log_error("resale", handler, str(e),
                                                    error_class=type(e).__name__,
                                                    user_id=uid, context=ctx,
                                                    tb=_tb.format_exc()[-1500:])
                        except Exception:
                            pass
        except requests.RequestException as e:
            print(f"[bot] Net: {e}"); time.sleep(5)
        except Exception as e:
            print(f"[bot] Err: {e}"); time.sleep(5)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("[bot] Dubai Resale Intelligence Bot v5")
    init_db()
    print("[bot] DB ready.")
    if os.environ.get("SESSION_STRING"):
        from telethon_parser import start_scheduler
        start_scheduler()
        print("[bot] Parser started.")
    else:
        print("[bot] SESSION_STRING not set — Telethon parser disabled.")
    from market_updater import start_market_scheduler
    start_market_scheduler()
    print("[bot] Market updater started.")
    try:
        from cron_worker import start_all as _start_cron
        _start_cron()
    except Exception as e:
        print(f"[bot] cron_worker init failed: {e}")
    run_bot()


if __name__ == "__main__":
    main()

