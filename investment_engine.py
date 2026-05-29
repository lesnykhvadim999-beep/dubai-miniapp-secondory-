"""investment_engine.py — экономическое заключение для RESALE объявления.

Использует:
  - DLD benchmarks (PRICE_BENCHMARKS из parser_engine) — sale_median/PSF по зданию
  - MARKET (per-area: avg PSF, rent_1br, growth_pct) из parser_engine
  - Listing data: building, area, deal_type, price, size_sqft, bedrooms

На выходе:
  {
    "roi": float,            # годовая доходность от аренды (sale → estimated, rent → expected)
    "growth_pct": float,
    "yearly_rent_aed": int,
    "payback_years": float,
    "value_in_5y_aed": int,
    "total_return_5y_pct": float,
    "vs_bank_pp": float,
    "risk_score": int (1-10),
    "risk_factors": [str],
    "recommendation": "STRONG_BUY"|"BUY"|"HOLD"|"AVOID",
    "data_source": "dld"|"market"|"estimated",
    "psf_vs_market_pct": float,  # цена за sqft vs средняя по району (- = ниже рынка)
  }
"""
from typing import Optional

BANK_DEPOSIT_RATE = 4.0
DEFAULT_ROI = 7.0
DEFAULT_GROWTH = 6.0

PREMIUM_AREAS = {
    "downtown dubai", "dubai marina", "palm jumeirah", "jbr",
    "emaar beachfront", "bluewaters", "city walk", "dubai hills estate",
    "saadiyat island", "yas island", "al maryah",
}
HIGH_YIELD_AREAS = {
    "jvc", "jumeirah village circle", "jvt", "business bay", "arjan",
    "dubai sports city", "dubai silicon oasis", "international city",
    "discovery gardens",
}


def _area_tier(area: Optional[str]) -> str:
    """Returns 'premium', 'mid' or 'budget'."""
    if not area: return "mid"
    a = area.lower()
    if any(k in a for k in PREMIUM_AREAS): return "premium"
    if any(k in a for k in HIGH_YIELD_AREAS): return "mid"
    return "mid"


def compute_investment_score(listing: dict,
                              dld_benchmark: Optional[dict] = None,
                              market: Optional[dict] = None) -> dict:
    """Compute investment metrics.
    listing — dict with: price, size_sqft, bedrooms, deal_type, building, area,
                          property_type
    dld_benchmark — output of _lookup_benchmark(building) from parser_engine
    market — dict per-area: {sqft (psf), rent_1br, growth} from MARKET
    """
    price = listing.get("price") or 0
    size_sqft = listing.get("size_sqft") or 0
    bedrooms = listing.get("bedrooms")
    deal_type = listing.get("deal_type") or "sale"
    area = listing.get("area")
    property_type = listing.get("property_type") or "apartment"

    # === ROI / yield ===
    data_source = "estimated"
    psf_vs_market_pct = None
    market_psf = (market or {}).get("sqft")
    listing_psf = price / size_sqft if (price and size_sqft and size_sqft > 100) else 0
    if market_psf and listing_psf:
        psf_vs_market_pct = round((listing_psf - market_psf) / market_psf * 100, 1)
        data_source = "market"

    # ROI estimation — track default usage + cap flag
    roi_used_default = False
    growth_used_default = False
    roi_capped = False

    if deal_type == "rent" and price > 10000:
        # This IS rent — yield depends on typical sale price for this building/size
        market_sale_psf = (dld_benchmark or {}).get("sale_psf_median") or market_psf or 1500
        if not ((dld_benchmark or {}).get("sale_psf_median") or market_psf):
            roi_used_default = True
        est_sale_price = market_sale_psf * size_sqft if size_sqft else price * 12
        raw_roi = (price / est_sale_price * 100) if est_sale_price else DEFAULT_ROI
        if not est_sale_price:
            roi_used_default = True
        roi = round(raw_roi, 1)
        # P1: wider range 0..25 (was 2..15) to expose real cases
        capped_roi = max(0.0, min(25.0, roi))
        if capped_roi != roi:
            roi_capped = True
        roi = capped_roi
        yearly_rent_aed = int(price)
        data_source = "dld" if dld_benchmark and dld_benchmark.get("sale_psf_median") else data_source
    elif deal_type == "sale":
        # Estimate expected rent based on area
        market_rent_1br = (market or {}).get("rent_1br") or 80000
        if not (market or {}).get("rent_1br"):
            roi_used_default = True
        # Scale by bedrooms
        br_mult = {0: 0.7, 1: 1.0, 2: 1.6, 3: 2.3, 4: 3.0, 5: 4.0}.get(bedrooms or 1, 2.0)
        if bedrooms is None or bedrooms == 0:
            br_mult = 0.7
        yearly_rent_aed = int(market_rent_1br * br_mult)
        raw_roi = (yearly_rent_aed / price * 100) if price else DEFAULT_ROI
        if not price:
            roi_used_default = True
        roi = round(raw_roi, 1)
        # P1: wider range 0..25 (was 2..15)
        capped_roi = max(0.0, min(25.0, roi))
        if capped_roi != roi:
            roi_capped = True
        roi = capped_roi
        if market: data_source = "market"
    else:
        roi = DEFAULT_ROI
        roi_used_default = True
        yearly_rent_aed = int(price * DEFAULT_ROI / 100) if price else 0

    # === Capital growth ===
    growth_raw = (market or {}).get("growth")
    if growth_raw is None:
        growth = DEFAULT_GROWTH
        growth_used_default = True
    else:
        growth = float(growth_raw)
    growth = max(1.0, min(20.0, float(growth)))

    # === Cash flow ===
    payback_years = round(100 / roi, 1) if roi > 0 else 99.0
    if deal_type == "sale" and price:
        value_in_5y_aed = int(price * ((1 + growth / 100) ** 5))
        capital_gain_5y_pct = round(((1 + growth / 100) ** 5 - 1) * 100, 1)
        total_return_5y_pct = round(capital_gain_5y_pct + (roi * 5), 1)
    else:
        # For rent, capital gain N/A (renter не получает прирост)
        value_in_5y_aed = 0
        capital_gain_5y_pct = 0
        total_return_5y_pct = round(roi * 5, 1)

    bank_5y_return = ((1 + BANK_DEPOSIT_RATE / 100) ** 5 - 1) * 100
    vs_bank = round(total_return_5y_pct - bank_5y_return, 1)

    # === Risk scoring ===
    risk = 5
    risk_factors = []

    tier = _area_tier(area)
    if tier == "premium":
        risk -= 2
    elif tier == "budget":
        risk += 1

    if not area:
        risk += 1
        risk_factors.append("no_area_specified")
    if not listing.get("building") and property_type not in ("plot", "whole_building"):
        risk += 1
        risk_factors.append("no_building_specified")
    if psf_vs_market_pct is not None:
        if psf_vs_market_pct < -25:
            # Significantly below market — either great deal OR misrepresented
            risk_factors.append("price_well_below_market_verify")
        elif psf_vs_market_pct > 40:
            risk += 1
            risk_factors.append("price_above_market")
    if not market_psf:
        risk += 1
        risk_factors.append("no_market_benchmark")

    if not price:
        risk += 2
        risk_factors.append("no_price")
    if not size_sqft:
        risk += 1
        risk_factors.append("no_size")

    if data_source == "dld":
        risk -= 1

    # P1: defaults flag — mark estimates so user knows numbers are not market-derived
    if roi_used_default or growth_used_default:
        risk += 1
        risk_factors.append("defaults_used")
    if roi_capped:
        risk_factors.append("roi_capped")

    risk = max(1, min(10, risk))

    # === Recommendation ===
    # P1: guard against STRONG_BUY when key inputs are defaults
    has_defaults = roi_used_default or growth_used_default
    if deal_type == "sale":
        if total_return_5y_pct >= 70 and risk <= 4 and not has_defaults:
            recommendation = "STRONG_BUY"
        elif total_return_5y_pct >= 50 and risk <= 6:
            recommendation = "BUY"
        elif total_return_5y_pct >= 30 and risk <= 7:
            recommendation = "HOLD"
        else:
            recommendation = "AVOID"
    else:  # rent
        if roi >= 8 and risk <= 5 and not has_defaults:
            recommendation = "STRONG_BUY"
        elif roi >= 6:
            recommendation = "BUY"
        elif roi >= 4:
            recommendation = "HOLD"
        else:
            recommendation = "AVOID"

    return {
        "roi": roi,
        "growth_pct": round(growth, 1),
        "yearly_rent_aed": yearly_rent_aed,
        "payback_years": payback_years,
        "value_in_5y_aed": value_in_5y_aed,
        "capital_gain_5y_pct": capital_gain_5y_pct,
        "total_return_5y_pct": total_return_5y_pct,
        "bank_5y_return_pct": round(bank_5y_return, 1),
        "vs_bank_pp": vs_bank,
        "risk_score": risk,
        "risk_factors": risk_factors,
        "recommendation": recommendation,
        "data_source": data_source,
        "psf_vs_market_pct": psf_vs_market_pct,
        "listing_psf": int(listing_psf) if listing_psf else None,
        "market_psf": int(market_psf) if market_psf else None,
        "roi_capped": roi_capped,
        "roi_used_default": roi_used_default,
        "growth_used_default": growth_used_default,
    }


# ── Conclusion text in 3 langs ─────────────────────────────────────────────
REC_LABEL = {
    "STRONG_BUY": {"en": "🟢 STRONG BUY", "ru": "🟢 ОТЛИЧНАЯ СДЕЛКА",
                    "ar": "🟢 شراء قوي"},
    "BUY":         {"en": "🟢 BUY",        "ru": "🟢 РЕКОМЕНДУЕМ",
                    "ar": "🟢 شراء"},
    "HOLD":        {"en": "🟡 HOLD",       "ru": "🟡 НЕЙТРАЛЬНО",
                    "ar": "🟡 محايد"},
    "AVOID":       {"en": "🔴 AVOID",      "ru": "🔴 НЕ РЕКОМЕНДУЕМ",
                    "ar": "🔴 تجنب"},
}
RISK_LABEL = {
    "en": ["Very Low","Low","Below avg","Below avg","Moderate","Moderate",
            "Above avg","Above avg","High","Very High"],
    "ru": ["Очень низкий","Низкий","Ниже сред.","Ниже сред.","Умеренный",
           "Умеренный","Выше сред.","Выше сред.","Высокий","Очень высокий"],
    "ar": ["منخفض جداً","منخفض","أقل من المتوسط","أقل من المتوسط","متوسط",
           "متوسط","أعلى من المتوسط","أعلى من المتوسط","مرتفع","مرتفع جداً"],
}
FACTOR_LABEL = {
    "no_area_specified":          {"en":"Area not specified","ru":"Район не указан","ar":"لم يتم تحديد المنطقة"},
    "no_building_specified":      {"en":"Building not specified","ru":"Здание не указано","ar":"لم يتم تحديد المبنى"},
    "no_market_benchmark":        {"en":"No market data for verification","ru":"Нет рыночных данных для сверки","ar":"لا توجد بيانات سوقية"},
    "no_price":                   {"en":"Price not disclosed","ru":"Цена не указана","ar":"السعر غير معلن"},
    "no_size":                    {"en":"Size not specified","ru":"Размер не указан","ar":"لم يتم تحديد المساحة"},
    "price_well_below_market_verify":{"en":"Price well below market — verify authenticity","ru":"Цена сильно ниже рынка — проверьте подлинность","ar":"السعر أقل بكثير من السوق — تحقق"},
    "price_above_market":         {"en":"Price above area median","ru":"Цена выше медианы района","ar":"السعر أعلى من المتوسط"},
    "defaults_used":              {"en":"Estimate uses defaults (no market data)","ru":"Оценочно (нет рыночных данных)","ar":"تقدير افتراضي (لا توجد بيانات سوقية)"},
    "roi_capped":                 {"en":"ROI capped to 0–25% range","ru":"ROI ограничен диапазоном 0–25%","ar":"ROI محدود بنطاق 0–25%"},
}


def _fmt_aed(n):
    if not n: return "—"
    if n >= 1_000_000: return f"{n/1_000_000:.2f}M AED"
    if n >= 1000:      return f"{n/1000:.0f}K AED"
    return f"{n:,} AED"


def build_conclusion_text(listing, score, lang="en"):
    if lang not in ("en","ru","ar"): lang = "en"
    L = lambda d: d.get(lang, d.get("en",""))

    T = {
        "title":   {"en":"💼 INVESTMENT ANALYSIS","ru":"💼 ИНВЕСТ-АНАЛИЗ","ar":"💼 تحليل الاستثمار"},
        "verdict": {"en":"Verdict","ru":"Вердикт","ar":"الحكم"},
        "roi":     {"en":"Rental Yield","ru":"Доход аренды","ar":"عائد الإيجار"},
        "rent":    {"en":"Yearly rent","ru":"Аренда / год","ar":"الإيجار السنوي"},
        "growth":  {"en":"Capital growth","ru":"Прирост капитала","ar":"نمو رأس المال"},
        "payback": {"en":"Payback period","ru":"Окупаемость","ar":"الاسترداد"},
        "5y_value":{"en":"Value in 5y","ru":"Стоимость / 5 лет","ar":"القيمة لـ5س"},
        "5y_total":{"en":"Total return 5y","ru":"Доход за 5 лет","ar":"العائد لـ5س"},
        "vs_bank": {"en":"vs Bank Deposit","ru":"Сверх депозита","ar":"مقابل الإيداع"},
        "psf_vs":  {"en":"PSF vs market","ru":"Цена/sqft vs рынок","ar":"السعر/قدم vs السوق"},
        "risk":    {"en":"Risk Level","ru":"Риск","ar":"المخاطر"},
        "factors": {"en":"Risk factors","ru":"Факторы риска","ar":"عوامل المخاطر"},
        "year":    {"en":"year","ru":"год","ar":"سنة"},
        "years":   {"en":"years","ru":"лет","ar":"سنوات"},
        "src_dld": {"en":"🟢 _Based on real DLD transactions_",
                    "ru":"🟢 _На основе реальных сделок DLD_",
                    "ar":"🟢 _استناداً إلى صفقات DLD_"},
        "src_mkt": {"en":"🟡 _Based on area market average_",
                    "ru":"🟡 _По средним показателям района_",
                    "ar":"🟡 _استناداً إلى متوسط المنطقة_"},
        "src_est": {"en":"🟠 _Estimated — limited data_",
                    "ru":"🟠 _Оценочно — мало данных_",
                    "ar":"🟠 _تقديري — بيانات محدودة_"},
    }

    sep = "━━━━━━━━━━━━━━━━━━━━━"
    lines = [sep, f"*{L(T['title'])}*", sep, ""]
    rec = REC_LABEL.get(score["recommendation"], REC_LABEL["HOLD"]).get(lang, "")
    lines.append(f"  *{L(T['verdict'])}:*  {rec}")
    lines.append("")
    lines.append(f"  💹  {L(T['roi'])}: *{score['roi']}%* / {L(T['year'])}")
    if score.get("yearly_rent_aed"):
        lines.append(f"  💵  {L(T['rent'])}: *{_fmt_aed(score['yearly_rent_aed'])}*")
    if listing.get("deal_type") == "sale":
        lines.append(f"  📈  {L(T['growth'])}: *+{score['growth_pct']}%* / {L(T['year'])}")
        lines.append(f"  ⏳  {L(T['payback'])}: *{score['payback_years']} {L(T['years'])}*")
        if score.get("value_in_5y_aed"):
            lines.append(f"  💎  {L(T['5y_value'])}: *{_fmt_aed(score['value_in_5y_aed'])}*")
    lines.append(f"  🚀  {L(T['5y_total'])}: *+{score['total_return_5y_pct']}%*")
    sign = "+" if score["vs_bank_pp"] >= 0 else ""
    lines.append(f"  🏦  {L(T['vs_bank'])}: *{sign}{score['vs_bank_pp']} pp*")

    if score.get("psf_vs_market_pct") is not None:
        psf_sign = "+" if score["psf_vs_market_pct"] >= 0 else ""
        ico = "📉" if score["psf_vs_market_pct"] < 0 else "📊"
        lines.append(f"  {ico}  {L(T['psf_vs'])}: *{psf_sign}{score['psf_vs_market_pct']}%*")

    rlabel = RISK_LABEL.get(lang, RISK_LABEL["en"])[score["risk_score"]-1]
    lines.append("")
    lines.append(f"  ⚠️  {L(T['risk'])}: *{rlabel}* ({score['risk_score']}/10)")

    if score["risk_factors"]:
        lines.append("")
        lines.append(f"  *{L(T['factors'])}:*")
        for f in score["risk_factors"]:
            label = FACTOR_LABEL.get(f, {}).get(lang) or f
            lines.append(f"    ▸ {label}")

    lines.append("")
    src_map = {"dld": T["src_dld"], "market": T["src_mkt"], "estimated": T["src_est"]}
    lines.append(f"  {L(src_map.get(score['data_source'], T['src_est']))}")
    return "\n".join(lines)
