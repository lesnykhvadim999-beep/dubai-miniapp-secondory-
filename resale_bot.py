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
    Send welcome message with logo on top.
    Priority: photo + caption in one message.
    Fallback: text-only welcome if logo unavailable.
    """
    welcome_text = _t(uid, "welcome")
    kb = kb_lang()
    fid = get_logo_file_id()

    if fid:
        # Try photo + caption (Telegram caption limit = 1024 chars)
        caption = welcome_text[:1024]
        try:
            resp = _api("sendPhoto",
                        chat_id=cid,
                        photo=fid,
                        caption=caption,
                        parse_mode="Markdown",
                        reply_markup=kb)
            if resp.get("ok"):
                print(f"[logo] Welcome sent with logo to {cid}")
                return
            else:
                print(f"[logo] sendPhoto failed: {resp.get('description')} — fallback to text")
        except Exception as e:
            print(f"[logo] sendPhoto error: {e} — fallback to text")
    else:
        print(f"[logo] No logo file_id — sending text-only welcome")

    # Fallback: text only
    _send(cid, welcome_text, kb)

# ── Translations ──────────────────────────────────────────────────────────────
T = {
"en": {
    "welcome": (
        "𝗗𝘂𝗯𝗮𝗶 𝗥𝗲𝗮𝗹 𝗘𝘀𝘁𝗮𝘁𝗲 𝗜𝗻𝘁𝗲𝗹𝗹𝗶𝗴𝗲𝗻𝗰𝗲\n"
        "────────────────────\n"
        "Your private real estate advisor for the UAE market.\n"
        "3,700+ verified listings\n"
        "Investment analysis · ROI · Rental yield\n"
        "Hot deals · Below-market opportunities\n"
        "Dubai · Abu Dhabi · RAK · Sharjah\n\n"
        "Ваш персональный советник по недвижимости ОАЭ.\n"
        "3 700+ проверенных объектов\n"
        "Инвест. анализ · ROI · Доходность аренды\n"
        "Горячие сделки · Цены ниже рынка\n"
        "Дубай · Абу-Даби · РАК · Шарджа\n\n"
        "────────────────────\n"
        "Select your language / Выберите язык"
    ),
    "lang_set": "English selected",
    "main_menu": "────────────────────\n  UAE PROPERTY SEARCH\n────────────────────",
    "btn_ai":       "✦  AI Property Advisor",
    "btn_filter":   "Search by Filters",
    "btn_hot":      "Hot Deals  ·  Below Market",
    "btn_new":      "New Listings",
    "btn_budget":   "Search by Budget",
    "btn_area":     "Search by Area",
    "btn_building": "Search by Building",
    "btn_lang":     "Language",
    "btn_add":      "List My Property",
    # Bottom reply-keyboard (persistent main menu)
    "rbtn_search":   "🔍 Search",
    "rbtn_hot":      "🔥 Hot Deals",
    "rbtn_area":     "🏘 By Area",
    "rbtn_building": "🏢 By Building",
    "rbtn_budget":   "💰 By Budget",
    "rbtn_new":      "🆕 New",
    "rbtn_ai":       "✦ AI Assistant",
    "rbtn_add":      "➕ List Property",
    "rbtn_lang":     "🌐 Language",
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
    "ai_start": "────────────────────\n  AI PROPERTY ADVISOR\n────────────────────\n\nI'll find the perfect property\nbased on your goals.\n\nLet's begin:",
    "ai_goal_q":    "What is your goal?",
    "ai_invest":    "Investment",
    "ai_live":      "To Live In",
    "ai_holiday":   "Holiday Home",
    "ai_unsure":    "Not Sure Yet",
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
    "add_size_q": "Size in sqft\n(type in chat)",
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
        "3 700+ проверенных объектов\n"
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
    "btn_hot":      "Горячие предложения  ·  Ниже рынка",
    "btn_new":      "Новые объявления",
    "btn_budget":   "Поиск по бюджету",
    "btn_area":     "Поиск по району",
    "btn_building": "Поиск по зданию",
    "btn_lang":     "Язык",
    "btn_add":      "Разместить объект",
    # Bottom reply-keyboard (persistent main menu)
    "rbtn_search":   "🔍 Подбор",
    "rbtn_hot":      "🔥 Горячие",
    "rbtn_area":     "🏘 По району",
    "rbtn_building": "🏢 По зданию",
    "rbtn_budget":   "💰 По бюджету",
    "rbtn_new":      "🆕 Новые",
    "rbtn_ai":       "✦ AI Помощник",
    "rbtn_add":      "➕ Разместить",
    "rbtn_lang":     "🌐 Язык",
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
    "ai_start": "────────────────────\n  AI ПОДБОР ОБЪЕКТА\n────────────────────\n\nНайду идеальный объект\nпод ваши цели.\n\nНачнём:",
    "ai_goal_q":    "Цель покупки?",
    "ai_invest":    "Инвестиция",
    "ai_live":      "Для жизни",
    "ai_holiday":   "Для отдыха",
    "ai_unsure":    "Не уверен",
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
    "add_size_q": "Площадь в кв. футах\n(напишите в чате)\nПример: 642",
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
        "٣٧٠٠+ عقار موثق\n"
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
    "btn_hot":      "أفضل الصفقات  ·  أقل من السوق",
    "btn_new":      "الإعلانات الجديدة",
    "btn_budget":   "البحث بالميزانية",
    "btn_area":     "البحث بالمنطقة",
    "btn_building": "البحث بالمبنى",
    "btn_lang":     "اللغة",
    "btn_add":      "إضافة عقار",
    # Bottom reply-keyboard (persistent main menu)
    "rbtn_search":   "🔍 بحث",
    "rbtn_hot":      "🔥 صفقات",
    "rbtn_area":     "🏘 المنطقة",
    "rbtn_building": "🏢 المبنى",
    "rbtn_budget":   "💰 الميزانية",
    "rbtn_new":      "🆕 جديد",
    "rbtn_ai":       "✦ مساعد AI",
    "rbtn_add":      "➕ إضافة عقار",
    "rbtn_lang":     "🌐 اللغة",
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
    "ai_start": "────────────────────\n  مستشار AI العقاري\n────────────────────\n\nسأجد العقار المثالي\nلأهدافك.\n\nلنبدأ:",
    "ai_goal_q":  "ما هدفك؟",
    "ai_invest":  "استثمار",
    "ai_live":    "للسكن",
    "ai_holiday": "منزل إجازة",
    "ai_unsure":  "لست متأكداً",
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
    "add_size_q": "المساحة بالقدم المربع",
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

def _answer(cbid):
    _api("answerCallbackQuery", callback_query_id=cbid)

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
    """Persistent bottom menu — always visible.
    Logical grouping: search-first (Подбор / Hot), then by-filter (Area/Building/Budget),
    then alternative entry points (AI, Add), then settings."""
    return _reply_kb([
        [_t(uid, "rbtn_search"),   _t(uid, "rbtn_hot")],
        [_t(uid, "rbtn_area"),     _t(uid, "rbtn_building")],
        [_t(uid, "rbtn_budget"),   _t(uid, "rbtn_new")],
        [_t(uid, "rbtn_ai"),       _t(uid, "rbtn_add")],
        [_t(uid, "rbtn_lang")],
    ])


def is_main_menu_text(text: str):
    """Returns the rbtn_* key if `text` matches any reply-keyboard label
    in any language. Used to dispatch text presses to handlers."""
    if not text: return None
    for lang_code, strings in T.items():
        for k, v in strings.items():
            if k.startswith("rbtn_") and v == text:
                return k
    return None

# ── Keyboards ─────────────────────────────────────────────────────────────────
def kb_lang():
    return _kb(
        [_btn("🇬🇧  English",  "lang|en")],
        [_btn("🇷🇺  Русский",  "lang|ru")],
        [_btn("🇦🇪  العربية", "lang|ar")],
    )

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
        [_btn(_t(uid, "btn_back"),   "menu|main")],
    )

def kb_deal(uid):
    return _kb(
        [_btn(_t(uid, "d_sale"),   "deal|sale"),  _btn(_t(uid, "d_rent"), "deal|rent")],
        [_btn(_t(uid, "d_any"),    "deal|any")],
        [_btn(_t(uid, "btn_back"), "menu|main")],
    )

def kb_proptype(uid):
    return _kb(
        [_btn(_t(uid, "pt_apt"),   "pt|apartment"), _btn(_t(uid, "pt_villa"), "pt|villa")],
        [_btn(_t(uid, "pt_town"),  "pt|townhouse"), _btn(_t(uid, "pt_pent"), "pt|penthouse")],
        [_btn(_t(uid, "pt_any"),   "pt|any")],
        [_btn(_t(uid, "btn_back"), "em|back")],
    )

def kb_budget(uid, is_rent=False):
    if is_rent:
        return _kb(
            [_btn(_t(uid, "rb_u100"),   "bud|r_u100"), _btn(_t(uid, "rb_100200"), "bud|r_100200")],
            [_btn(_t(uid, "rb_200p"),   "bud|r_200p")],
            [_btn(_t(uid, "b_any"),     "bud|any")],
            [_btn(_t(uid, "btn_back"),  "pt|back")],
        )
    return _kb(
        [_btn(_t(uid, "b_u1"),     "bud|u1"),    _btn(_t(uid, "b_12"),  "bud|1-2")],
        [_btn(_t(uid, "b_25"),     "bud|2-5"),   _btn(_t(uid, "b_5p"),  "bud|5p")],
        [_btn(_t(uid, "b_any"),    "bud|any")],
        [_btn(_t(uid, "btn_back"), "pt|back")],
    )

def kb_bedrooms(uid):
    return _kb(
        [_btn(_t(uid, "br_studio"), "br|0"), _btn(_t(uid, "br_1"), "br|1"), _btn(_t(uid, "br_2"), "br|2")],
        [_btn(_t(uid, "br_3"),      "br|3"), _btn(_t(uid, "br_4p"), "br|4p"), _btn(_t(uid, "br_any"), "br|any")],
        [_btn(_t(uid, "btn_back"),  "bud|back")],
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
    rows.append([_btn(_t(uid, "btn_back"), "menu|main")])
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
def get_market_summary(area: str, strategy: str = None) -> str:
    """Get market summary text from market_data table."""
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
    """Get best areas dynamically from market_data based on strategy."""
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
def format_card(listing, uid, rank=None):
    emirate   = listing.get("emirate") or ""
    area      = listing.get("area") or ""
    building  = listing.get("building") or ""
    br        = listing.get("bedrooms")
    size      = listing.get("size_sqft")
    view      = listing.get("view")
    status    = listing.get("status")
    furn      = listing.get("furnishing")
    deal_type = listing.get("deal_type", "sale")
    price     = listing.get("price")
    ppf       = listing.get("price_per_sqft")
    pct       = listing.get("price_vs_market_percent")
    disc      = listing.get("discount_percent")
    roi       = listing.get("roi_estimate")
    score     = listing.get("investment_score")

    lines = []

    # Локация
    if building:
        lines.append(f"🏢 *{building}*")
    loc_parts = [p for p in [area, emirate] if p and p != "UAE"]
    if loc_parts:
        lines.append("📍 " + "  ·  ".join(loc_parts))
    elif not building:
        lines.append("🌍 UAE")

    lines.append("")

    # Цена
    if price:
        p_str = f"💰 *{_fmt(price)}*"
        if deal_type == "rent":
            p_str += " / год"
        lines.append(p_str)
        if deal_type == "sale":
            if ppf:
                lines.append(f"📐 {int(ppf * 10.764):,} AED/m²".replace(",", " "))
            elif size and size > 0:
                sqm = size * 0.0929
                if sqm > 0:
                    lines.append(f"📐 {int(price / sqm):,} AED/m²".replace(",", " "))
    else:
        lines.append("💰 Цена по запросу")

    lines.append("")

    # Характеристики: спальни + площадь
    br_str   = _fmt_br(br) if br is not None else ""
    size_str = _fmt_size(size) if size else ""
    if br_str and size_str:
        lines.append(f"🛍 {br_str}  ·  {size_str}")
    elif br_str:
        lines.append(f"🛍 {br_str}")
    elif size_str:
        lines.append(f"📐 {size_str}")

    # Вид, меблировка, статус — в одну строку
    extras = []
    if view:   extras.append(view)
    if furn:   extras.append(furn.title())
    if status: extras.append(status.title())
    if extras:
        lines.append("  ·  ".join(extras))

    if deal_type == "rent":
        lines.append("🏠 For Rent")

    # Аналитика
    analytics = []
    if pct and pct < -3:
        lbl = "% ниже рынка аренды" if deal_type == "rent" else "% below market"
        analytics.append(f"📉 {abs(round(pct, 1))}{lbl}")
    if disc and disc >= 5:
        analytics.append(f"🏷 {disc}% below original price")
    if roi and deal_type == "sale":
        analytics.append(f"📈 ROI {roi}% / year")
    if score:
        analytics.append(f"⭐ Score {score}/10")
    if analytics:
        lines.append("")
        lines.extend(analytics)

    return "\n".join(lines)

    # ── Price — always visible ──────────────────────────────────────────────
    price          = listing.get("price")
    price_per_sqft = listing.get("price_per_sqft")
    if price:
        price_label = f"💰 *{_fmt(price)}*"
        if deal_type == "rent":
            price_label += " / год"
        lines.append(price_label)
        if price_per_sqft:
            sqm_price = int(price_per_sqft * 10.764)
            lines.append(f"📐 {sqm_price:,} AED/m²".replace(",", " "))
        elif size and size > 0 and deal_type != "rent":
            sqm = size * 0.0929
            if sqm > 0:
                lines.append(f"📐 {int(price / sqm):,} AED/m²".replace(",", " "))
    else:
        lines.append("💰 Цена по запросу")

    dq   = listing.get("deal_quality", "normal")
    pct  = listing.get("price_vs_market_percent")
    disc = listing.get("discount_percent")
    roi  = listing.get("roi_estimate")
    score= listing.get("investment_score")

    if br is not None:
        lines.append(f"🛏 {_fmt_br(br)}")
    if size:
        lines.append(f"📏 {_fmt_size(size)}")
    if floor:
        lines.append(f"🏗 Floor {floor}")
    if view:
        lines.append(f"🌅 {view}")
    if furn:
        lines.append(f"🛋 {furn.title()}")
    if status:
        lines.append(f"🔑 {status.title()}")
    if deal_type == "rent":
        lines.append("🏷 For Rent")

    if pct and pct < 0:
        pct_label = "% ниже рынка аренды" if deal_type == "rent" else "% below market"
        lines.append(f"📉 {abs(round(pct, 1))}{pct_label}")
    if disc and disc >= 3:
        lines.append(f"🏷 {disc}% below original price")
    if roi and deal_type == "sale":
        lines.append(f"📈 ROI {roi}% / year")
    if score:
        lines.append(f"⭐ Score {score}/10")

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
        if pct and pct < 0:     lines.append(f"  {abs(round(pct,1))}% below market average")
        if disc and disc >= 3:  lines.append(f"  {disc}% below original price")

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
    "bedrooms", "size", "price", "status", "furnishing",
    "view", "contact", "photos"
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


def start_add_listing(cid, uid, mid=None):
    add_states[uid] = {"step": 0, "data": {}, "photos": []}
    text = _t(uid, "add_start")
    kb = _kb(
        [_btn(_t(uid, "d_sale"), "add|deal|sale"), _btn(_t(uid, "d_rent"), "add|deal|rent")],
        [_btn(_t(uid, "add_cancel"), "add|cancel")],
    )
    if mid: _edit(cid, mid, text + "\n\n" + _t(uid, "add_deal_q"), kb)
    else:   _send(cid, text + "\n\n" + _t(uid, "add_deal_q"), kb)


def add_next_step(cid, uid):
    s = add_states.get(uid, {})
    step = s.get("step", 0)
    data = s.get("data", {})

    if step >= len(ADD_STEPS):
        submit_listing(cid, uid)
        return

    current = ADD_STEPS[step]

    if current == "emirate":
        kb = _kb(
            [_btn("🇦🇪  Dubai", "add|emirate|Dubai"),          _btn("🕌  Abu Dhabi", "add|emirate|Abu Dhabi")],
            [_btn("🏝  Ras Al Khaimah", "add|emirate|Ras Al Khaimah"), _btn("🏙  Sharjah", "add|emirate|Sharjah")],
            [_btn(_t(uid, "add_cancel"), "add|cancel")],
        )
        _send(cid, _t(uid, "add_emirate_q"), kb)

    elif current == "area":
        emirate = data.get("emirate", "Dubai")
        areas = {
            "Dubai": ADD_AREAS_DUBAI,
            "Abu Dhabi": ADD_AREAS_AD,
            "Ras Al Khaimah": ADD_AREAS_RAK,
            "Sharjah": ADD_AREAS_SHJ,
        }.get(emirate, ADD_AREAS_DUBAI)

        rows = [[_btn(a, f"add|area|{a}")] for a in areas]
        rows.append([_btn(_t(uid, "add_area_custom_btn"), "add|area|_custom_")])
        rows.append([_btn(_t(uid, "add_cancel"), "add|cancel")])
        _send(cid, _t(uid, "add_area_q"), {"inline_keyboard": rows})

    elif current == "building":
        add_states[uid]["waiting_text"] = "building"
        _send(cid, _t(uid, "add_building_q"),
              _kb([_btn(_t(uid, "add_cancel"), "add|cancel")]))

    elif current == "type":
        kb = _kb(
            [_btn(_t(uid, "pt_apt"),  "add|type|apartment"), _btn(_t(uid, "pt_villa"), "add|type|villa")],
            [_btn(_t(uid, "pt_town"), "add|type|townhouse"),  _btn(_t(uid, "pt_pent"), "add|type|penthouse")],
            [_btn(_t(uid, "add_cancel"), "add|cancel")],
        )
        _send(cid, _t(uid, "add_type_q"), kb)

    elif current == "bedrooms":
        kb = _kb(
            [_btn(_t(uid, "br_studio"), "add|br|0"), _btn(_t(uid, "br_1"), "add|br|1"), _btn(_t(uid, "br_2"), "add|br|2")],
            [_btn(_t(uid, "br_3"),      "add|br|3"), _btn(_t(uid, "br_4p"), "add|br|4")],
            [_btn(_t(uid, "add_cancel"), "add|cancel")],
        )
        _send(cid, _t(uid, "add_br_q"), kb)

    elif current == "size":
        add_states[uid]["waiting_text"] = "size"
        _send(cid, _t(uid, "add_size_q"),
              _kb([_btn(_t(uid, "add_cancel"), "add|cancel")]))

    elif current == "price":
        add_states[uid]["waiting_text"] = "price"
        _send(cid, _t(uid, "add_price_q"),
              _kb([_btn(_t(uid, "add_cancel"), "add|cancel")]))

    elif current == "status":
        kb = _kb(
            [_btn(_t(uid, "add_status_vacant"), "add|status|vacant"),
             _btn(_t(uid, "add_status_rented"), "add|status|rented")],
            [_btn(_t(uid, "add_cancel"), "add|cancel")],
        )
        _send(cid, _t(uid, "add_status_q"), kb)

    elif current == "furnishing":
        kb = _kb(
            [_btn(_t(uid, "add_furn_yes"),  "add|furn|furnished"),
             _btn(_t(uid, "add_furn_no"),   "add|furn|unfurnished")],
            [_btn(_t(uid, "add_furn_semi"), "add|furn|semi-furnished")],
            [_btn(_t(uid, "add_cancel"),    "add|cancel")],
        )
        _send(cid, _t(uid, "add_furn_q"), kb)

    elif current == "view":
        rows = [[_btn(v, f"add|view|{v}")] for v in ADD_VIEWS]
        rows.append([_btn(_t(uid, "add_cancel"), "add|cancel")])
        _send(cid, _t(uid, "add_view_q"), {"inline_keyboard": rows})

    elif current == "contact":
        add_states[uid]["waiting_text"] = "contact"
        _send(cid, _t(uid, "add_contact_q"),
              _kb([_btn(_t(uid, "add_cancel"), "add|cancel")]))

    elif current == "photos":
        add_states[uid]["waiting_text"] = "photos"
        _send(cid, _t(uid, "add_photo_q"), _kb(
            [_btn(_t(uid, "add_skip"),   "add|skip_photos")],
            [_btn(_t(uid, "add_cancel"), "add|cancel")],
        ))


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

    # Notify user
    _send(cid, _t(uid, "add_done"), _kb([_btn(_t(uid, "btn_menu"), "menu|main")]))
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
        kb = _kb([_btn(_t(uid, "btn_menu"), "menu|main")])
        if mid: _edit(cid, mid, _t(uid, "no_results"), kb)
        else:   _send(cid, _t(uid, "no_results"), kb)
        return

    start = page * PER_PAGE
    end   = min(start + PER_PAGE, len(results))
    items = results[start:end]

    deal_type = gs(uid).get("filters", {}).get("deal_type", "sale")
    type_label = "FOR RENT" if deal_type == "rent" else "FOR SALE"
    header = f"{_sep()}\n  {total} PROPERTIES  ·  {type_label}\n{_sep()}"
    if mid: _edit(cid, mid, header)
    else:   _send(cid, header)

    for i, lst in enumerate(items, start=start+1):
        text = format_card(lst, uid, rank=i)
        lid  = lst.get("id") or lst["id"]
        kb = _kb(
            [_btn(_t(uid, "btn_analysis"), f"detail|{lid}"), _btn(_t(uid, "btn_book"),    f"book|{lid}")],
            [_btn(_t(uid, "btn_similar"),  f"similar|{lid}"), _btn(_t(uid, "btn_send"),   f"send|{lid}")],
        )
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
    nav = []
    if remaining > 0:
        s["page"] += 1
        nav.append([_btn(_t(uid, "btn_more", n=remaining), "results|more")])
    nav.append([_btn("← " + _t(uid, "deal_q"), "filter|deal_type_reset")])
    nav.append([_btn(_t(uid, "btn_back"), "results|back")])
    nav.append([_btn(_t(uid, "btn_menu"), "menu|main")])
    _send(cid, _sep(), {"inline_keyboard": nav})


# ── Natural language + Claude ─────────────────────────────────────────────────
def claude_parse(text, lang="en"):
    if not ANTHROPIC_KEY: return {}
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 300,
                  "messages": [{"role": "user", "content":
                    f'Parse UAE real estate search query. Return ONLY JSON: '
                    f'{{emirate, area, building, deal_type(sale/rent), property_type, '
                    f'bedrooms(int,0=studio), max_price(AED int), min_price(AED int), '
                    f'view, status, furnishing, hot_only(bool), sort(best_deals/newest/price_asc)}}. '
                    f'Use null for missing. Query: "{text}"'
                  }]},
            timeout=10,
        )
        if resp.status_code != 200: return {}
        raw = resp.json()["content"][0]["text"].strip()
        m = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
        if not m: return {}
        return {k: v for k, v in json.loads(m.group()).items() if v is not None and v is not False}
    except Exception as e:
        print(f"[claude] {e}"); return {}


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

    meaningful = {k for k in filters if k not in ["sort", "hot_only"]}
    if len(meaningful) < 2 and len(text.split()) > 3:
        cf = claude_parse(text, lang)
        if cf: filters.update(cf)

    return filters


# ── AI Advisor ────────────────────────────────────────────────────────────────
def show_ai_start(cid, uid, mid=None):
    s = gs(uid); s["ai_step"] = 1; s["ai_data"] = {}
    text = _t(uid, "ai_start") + "\n\n" + _t(uid, "ai_goal_q")
    kb = _kb(
        [_btn(_t(uid, "ai_invest"),  "ai|goal|invest"),  _btn(_t(uid, "ai_live"),   "ai|goal|live")],
        [_btn(_t(uid, "ai_holiday"), "ai|goal|holiday"), _btn(_t(uid, "ai_unsure"), "ai|goal|unsure")],
        [_btn(_t(uid, "btn_menu"),   "menu|main")],
    )
    if mid: _edit(cid, mid, text, kb)
    else:   _send(cid, text, kb)


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
        else:
            _edit(cid, mid, _t(uid, "budget_q"), kb_budget(uid))
            return
        _edit(cid, mid, text, kb)

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
                "sea":      ["Dubai Marina", "Jumeirah Beach Residence", "Palm Jumeirah"],
                "downtown": ["Downtown Dubai", "Business Bay", "DIFC"],
                "family":   ["Dubai Hills Estate", "Jumeirah Village Circle", "Meydan"],
                "premium":  ["Palm Jumeirah", "Downtown Dubai", "Bluewaters Island"],
                "business": ["Business Bay", "DIFC", "Downtown Dubai"],
            }
            areas = lifestyle_map.get(lifestyle, ["Downtown Dubai", "Dubai Marina"])
        else:
            areas = ["Downtown Dubai", "Dubai Marina", "Jumeirah Village Circle"]

        # Generate market summary for top area
        summary_text = ""
        if areas:
            mkt = get_market_summary(areas[0], strategy)
            if mkt:
                summary_text = mkt

        filters = dict(s.get("filters", {}))
        best = []
        for area in areas[:3]:
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

    save_lead(uid, "", lid, "view")
    text     = format_detail(listing, uid)
    lead_url = f"{LEAD_BOT_URL}?start=resale_{lid}"
    kb = _kb(
        [_url_btn(_t(uid, "btn_book"), lead_url)],
        [_btn(_t(uid, "btn_similar"), f"similar|{lid}"), _btn(_t(uid, "btn_back"), "results|back")],
        [_btn(_t(uid, "btn_menu"), "menu|main")],
    )

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

    text = (
        f"{_sep()}\n  ADMIN STATISTICS  ·  Dubai Resale Bot\n{_sep()}\n\n"

        f"  Total listings:        {s['total']}\n"
        f"  For Sale:              {s['sale_total']}  ({s['sale_clean']} clean)\n"
        f"  For Rent:              {s['rent_total']}  ({s['rent_clean']} clean)\n"
        f"  Hot deals:             {s['hot_deals']}\n"
        f"  Below market:          {s['below_market']}\n"
        f"  Needs review:          {s['needs_review']}\n"
        f"  Review queue:          {s['review_queue']}\n"
        f"  Pending moderation:    {s['pending']}\n"
        f"  Buildings tracked:     {s['buildings_count']}\n"
        f"  Areas covered:         {s['areas_count']}\n"
        f"  Parsed channels:       {s['groups_count']}"
        f"{corrupt_warn}\n\n"

        f"{_sep()}\n  ANALYTICS\n{_sep()}\n"
        f"  Avg sale price:        {_fmt_m(s['avg_sale_price'])}\n"
        f"  Avg rent/year:         {_fmt_m(s['avg_rent_price'])}\n"
        f"  Avg price/sqft:        {int(s['avg_price_sqft']) if s['avg_price_sqft'] else '—'} AED\n"
        f"  Avg ROI:               {s['avg_roi']}%\n\n"

        f"{_sep()}\n  ACTIVITY\n{_sep()}\n"
        f"  Today (Dubai time):    {s['today_listings']}\n"
        f"  Yesterday:             {s['yesterday_listings']}\n"
        f"  This week:             {s['week_listings']}\n"
        f"  This month:            {s['month_listings']}\n\n"

        f"{_sep()}\n  TODAY SYNC\n{_sep()}\n"
        f"  New parsed:            {s['today_new']}\n"
        f"  Duplicates:            {s['today_dupes']}\n"
        f"  Hot deals found:       {s['today_hot']}\n"
        f"  Errors:                {s['today_errors']}\n"
        f"  Sync runs:             {s['syncs_today']}\n"
        f"  Last sync:             {_fmt_dt(s['last_sync'])}\n\n"

        f"{_sep()}\n  CHANNELS\n{_sep()}\n"
        f"{by_channel}\n\n"

        f"{_sep()}\n  BY EMIRATE\n{_sep()}\n"
        f"{by_em}\n\n"

        f"{_sep()}\n  BY DEAL QUALITY\n{_sep()}\n"
        f"{by_q}\n\n"

        f"{_sep()}\n  USERS\n{_sep()}\n"
        f"  Total users:           {s['users_total']}\n"
        f"  Active today:          {active_today}\n"
        f"  Searches today:        {searches_today}\n"
        f"  Views today:           {views_today}\n"
        f"  Leads today:           {s['leads_today']}\n"
        f"  Leads this week:       {s['leads_week']}\n"
        f"{_sep()}"
    )
    _send(cid, f"`{text}`")


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
    If an old inline-menu message is given, delete it to keep the chat clean."""
    if mid:
        try: _api("deleteMessage", chat_id=cid, message_id=mid)
        except: pass
    _send(cid, _t(uid, "main_menu"), kb_main_reply(uid))


def dispatch_main_button(cid, uid, rkey):
    """Dispatches a press of a bottom reply-keyboard button to the same handler
    as the corresponding inline button."""
    if rkey == "rbtn_search":
        _reset(uid); _send(cid, _t(uid, "emirate_q"), kb_emirate(uid))
    elif rkey == "rbtn_hot":
        _reset(uid); gs(uid)["filters"] = {"hot_only": True, "sort": "best_deals"}
        _send(cid, _t(uid, "searching")); do_search(uid); send_results(cid, uid)
    elif rkey == "rbtn_new":
        _reset(uid); gs(uid)["filters"] = {"sort": "newest"}
        _send(cid, _t(uid, "searching")); do_search(uid); send_results(cid, uid)
    elif rkey == "rbtn_budget":
        _reset(uid); _send(cid, _t(uid, "budget_q"), kb_budget(uid))
    elif rkey == "rbtn_area":
        _reset(uid); _send(cid, _t(uid, "area_q"), kb_areas(uid))
    elif rkey == "rbtn_building":
        _reset(uid); gs(uid)["waiting"] = "building"
        _send(cid, _t(uid, "bld_q"),
              _kb([_btn(_t(uid, "btn_back"), "menu|main")]))
    elif rkey == "rbtn_ai":
        show_ai_start(cid, uid)
    elif rkey == "rbtn_add":
        start_add_listing(cid, uid)
    elif rkey == "rbtn_lang":
        _send(cid, "Select language:", kb_lang())


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
        # После выбора языка — показываем выбор типа сделки
        text = _t(uid, "deal_type_q")
        kb = _kb(
            [_btn("🏠  " + _t(uid, "d_sale"),  "default_deal|sale")],
            [_btn("🔑  " + _t(uid, "d_rent"),  "default_deal|rent")],
            [_btn(_t(uid, "d_any_deal"),              "default_deal|any")],
        )
        _send(cid, text, kb)

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
            _reset(uid); gs(uid)["filters"] = {"hot_only": True, "sort": "best_deals"}
            _edit(cid, mid, _t(uid, "searching")); do_search(uid); send_results(cid, uid, mid)
        elif sub == "new":
            _reset(uid); gs(uid)["filters"] = {"sort": "newest"}
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
        is_rent = gs(uid)["filters"].get("deal_type") == "rent"
        if is_rent:
            rent_map = {
                "r_u100": (None, 100_000), "r_100200": (100_000, 200_000), "r_200p": (200_000, None)
            }
            if val in rent_map:
                mn, mx = rent_map[val]
                if mn: gs(uid)["filters"]["min_price"] = mn
                if mx: gs(uid)["filters"]["max_price"] = mx
        else:
            bmap = {"u1":(None,1_000_000),"1-2":(1_000_000,2_000_000),"2-5":(2_000_000,5_000_000),"5p":(5_000_000,None)}
            if val in bmap:
                mn, mx = bmap[val]
                if mn: gs(uid)["filters"]["min_price"] = mn
                if mx: gs(uid)["filters"]["max_price"] = mx
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
                  _kb(
                      [_btn(_t(uid, "add_skip"),   "add|skip_photos")],
                      [_btn(_t(uid, "add_cancel"), "add|cancel")],
                  ))
            return

    if not text:
        return

    # Bottom reply-keyboard buttons → dispatch to handlers
    rkey = is_main_menu_text(text)
    if rkey:
        dispatch_main_button(cid, uid, rkey)
        return

    if text.startswith("/"):
        cmd = text.split()[0].lower().lstrip("/").split("@")[0]
        if cmd == "start":
            user_lang.pop(uid, None); _reset(uid)
            send_welcome_with_logo(cid, uid)
        elif cmd == "menu":  show_main(cid, uid)
        elif cmd == "stats": show_stats(cid, uid)
        elif cmd == "parse":
            if uid != ADMIN_ID:
                _send(cid, "Access denied."); return
            _send(cid, "⚙️ Parser started — incremental sync running in background...")
            from telethon_parser import run_parser_thread
            run_parser_thread(backfill=False)
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
        if waiting in ("building", "size", "price", "contact"):
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
            s["filters"] = filters
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
    run_bot()


if __name__ == "__main__":
    main()

