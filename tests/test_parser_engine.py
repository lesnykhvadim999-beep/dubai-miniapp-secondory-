"""Pytest regression suite for parser_engine.py — covers B001..B026 fixes.

Goal: regression-safety net so future parser changes don't break landmark guards,
deal_type hierarchy, rent_period detection, property_type classification,
sub-cluster artifact filtering, and area inference.

Run before any parser_engine.py commit:
    pytest tests/test_parser_engine.py -v

If FAIL — fix before push.

Coverage (target: 30+ tests):
  • detect_deal_type_v133       (B005)
  • detect_rent_period
  • extract_property_type        (B003 / B004)
  • detect_area                  (B001 / B002 / B026)
  • detect_building              (B002)
  • iconic landmarks view-guard  (B002 ext)
  • sub-cluster artifacts        (villa audit)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser_engine import (
    detect_area,
    detect_building,
    detect_deal_type_v133,
    detect_rent_period,
    extract_property_type,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. detect_deal_type_v133  (B005 — sale-hard wins over rental-yield)
# ─────────────────────────────────────────────────────────────────────────────
class TestDealTypeV133:
    # 10 strong SALE cases (some with rental-yield trap)
    @pytest.mark.parametrize("text", [
        "1BR for sale in Marina, rental yield 8%",
        "Apartment for sale in Downtown Dubai. SP: 3.5M AED",
        "HOT DEAL | 1BR for sale | JVC | AED 950K | rental yield 9%",
        "Selling price 4M AED | 2BR | Downtown | strong rental potential",
        "Asking price AED 25M | Palm Jumeirah | villa | great rental ROI",
        "Penthouse for sale | DIFC | 12M | yearly rental return 6%",
        "Original price 4.2M | Resale | Burj Vista | rental yield mentioned",
        "Продажа квартиры в Дубае | 2BR | SP: 2,400,000 | аренда возможна",
        "On sale | townhouse | Dubai Hills | rental income strong",
        "1BR for sale | Marina | rental yield 8% expected",
    ])
    def test_sale_hard_wins_over_rental_yield(self, text):
        result, conf = detect_deal_type_v133(text)
        assert result == "sale", f"Expected sale, got {result} for {text!r}"
        assert conf >= 0.90, f"Confidence {conf} too low for {text!r}"

    # 10 strong RENT cases (various periods)
    @pytest.mark.parametrize("text", [
        "1BR for rent in JLT, AED 70,000/year, 4 cheques",
        "Apartment to let in JVC | Yearly rent AED 75,000",
        "Monthly rent AED 8,500 | Studio | Business Bay",
        "Villa for rent Palm Jumeirah | per annum AED 800,000",
        "Сдается квартира | 1BR | 90,000 AED в год",
        "Long-term rent | 2BR | Downtown | AED 180,000 yearly",
        "Short-term rent | Studio | JBR | AED 350/night",
        "Annual rent 250k AED | 3BR apartment | Marina",
        "Rent: 60,000 AED per year | Studio | Silicon Oasis",
        "Monthly rent 12,000 AED | 1BR | Business Bay | per month",
    ])
    def test_rent_hard_signals(self, text):
        result, conf = detect_deal_type_v133(text)
        assert result == "rent", f"Expected rent, got {result} for {text!r}"
        assert conf >= 0.90

    # 5 ambiguous — fall through to default
    @pytest.mark.parametrize("text", [
        "1BR Marina AED 1.6M",
        "Studio JLT brand new",
        "Apartment Downtown 2BR",
        "Villa Dubai Hills 4BR",
        "Penthouse DIFC luxury",
    ])
    def test_ambiguous_falls_through_to_default(self, text):
        result, conf = detect_deal_type_v133(text, default="sale")
        # Ambiguous → low confidence (0.50)
        assert conf < 0.90, f"Expected ambiguous (low conf), got conf={conf} for {text!r}"
        assert result == "sale", f"Expected default=sale, got {result!r}"

    def test_empty_text_returns_default(self):
        result, conf = detect_deal_type_v133("", default="sale")
        assert conf < 0.90
        assert result == "sale"

    def test_empty_text_custom_default(self):
        result, conf = detect_deal_type_v133("", default="")
        assert conf == 0.50
        assert result == ""


# ─────────────────────────────────────────────────────────────────────────────
# 2. detect_rent_period
# ─────────────────────────────────────────────────────────────────────────────
class TestRentPeriod:
    def test_yearly_explicit_slash(self):
        assert detect_rent_period("AED 100,000/year") == "yearly"

    def test_yearly_per_annum(self):
        assert detect_rent_period("AED 100,000 per annum") == "yearly"

    def test_yearly_annual(self):
        assert detect_rent_period("250k AED annual") == "yearly"

    def test_yearly_russian(self):
        assert detect_rent_period("90,000 AED в год") == "yearly"

    def test_monthly_per_month(self):
        assert detect_rent_period("AED 5,000 per month") == "monthly"

    def test_monthly_slash(self):
        assert detect_rent_period("AED 8,500/month") == "monthly"

    def test_monthly_russian(self):
        assert detect_rent_period("AED 8,500 в месяц") == "monthly"

    def test_daily_per_night(self):
        assert detect_rent_period("AED 500 per night") == "daily"

    def test_daily_nightly(self):
        assert detect_rent_period("AED 350 nightly") == "daily"

    def test_daily_short_term(self):
        assert detect_rent_period("Short-term rental AED 200/day") == "daily"

    def test_price_fallback_monthly(self):
        # No explicit period, price < 30k → monthly
        assert detect_rent_period("AED 4500", price=4500) == "monthly"

    def test_price_fallback_yearly(self):
        # No explicit period, price > 500k → yearly
        assert detect_rent_period("AED 600000", price=600_000) == "yearly"

    def test_unknown_when_no_signal(self):
        assert detect_rent_period("AED 100,000") == "unknown"

    def test_unknown_empty(self):
        assert detect_rent_period("") == "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# 3. extract_property_type  (B003 / B004)
# ─────────────────────────────────────────────────────────────────────────────
class TestPropertyType:
    def test_studio_bedrooms_zero(self):
        assert extract_property_type("Apartment for sale Marina", bedrooms=0) == "studio"

    def test_studio_keyword(self):
        assert extract_property_type("Studio for sale in JLT", bedrooms=None) == "studio"

    def test_studio_russian(self):
        assert extract_property_type("Студия в Дубае", bedrooms=None) == "studio"

    def test_apartment_default(self):
        assert extract_property_type("1BR for sale in Marina", bedrooms=1) == "apartment"

    def test_villa_explicit(self):
        assert extract_property_type(
            "Villa for sale Emirates Hills 7BR", bedrooms=7
        ) == "villa"

    def test_townhouse_explicit(self):
        assert extract_property_type(
            "Townhouse for sale Dubai Hills 3BR", bedrooms=3
        ) == "townhouse"

    def test_penthouse_explicit(self):
        assert extract_property_type("Penthouse DIFC 4BR", bedrooms=4) == "penthouse"

    def test_duplex_explicit(self):
        assert extract_property_type("Duplex DIFC 3BR", bedrooms=3) == "duplex"

    def test_apt_in_villa_community_is_apt(self):
        # B003 guard: «apartment in villa community» is apartment, not villa
        result = extract_property_type(
            "1BR apartment in villa community Dubai Hills", bedrooms=1
        )
        assert result == "apartment", f"Expected apartment, got {result}"

    def test_office_tower_apartment_guard_B004(self):
        # B004 guard: «1BR in office tower» is apartment, not office
        result = extract_property_type(
            "1BR in Business Bay office tower", bedrooms=1
        )
        assert result == "apartment", f"Expected apartment, got {result}"

    def test_office_tower_explicit_office(self):
        result = extract_property_type(
            "Office for sale Business Bay 1500 sqft", bedrooms=None
        )
        assert result == "office"

    def test_residential_plot(self):
        # B003: «residential plot» is plot, not villa
        result = extract_property_type(
            "Residential plot for sale in JVC", bedrooms=None
        )
        assert result == "plot"

    def test_villa_plot_is_plot(self):
        # «Villa plot» is plot, not villa
        result = extract_property_type("Villa plot for sale Al Furjan", bedrooms=None)
        assert result == "plot"

    def test_duplex_views_phrase_not_duplex(self):
        # «duplex views» is description (2-storey view), not duplex unit
        result = extract_property_type(
            "1BR apartment with duplex views Marina", bedrooms=1
        )
        assert result == "apartment", f"Expected apartment, got {result}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. detect_area  (B001 / B002 / B026)
# ─────────────────────────────────────────────────────────────────────────────
class TestArea:
    def test_jvc_abbreviation_expansion(self):
        # «JVC» → Jumeirah Village Circle
        area, conf, emirate, possible = detect_area("1BR in JVC, fully furnished")
        assert area == "Jumeirah Village Circle"
        assert emirate == "Dubai"

    def test_full_name_match(self):
        area, conf, emirate, _ = detect_area(
            "Townhouse in Jumeirah Village Circle community"
        )
        assert area == "Jumeirah Village Circle"
        assert conf >= 0.85

    def test_burj_khalifa_view_does_not_hallucinate_downtown(self):
        # B026/B002: «view of Burj Khalifa» alone should not produce Downtown high-conf
        # without any direct mention of Downtown.
        area, conf, emirate, _ = detect_area("1BR with view of Burj Khalifa")
        # Either None or low-confidence
        if area is not None:
            assert conf < 0.60, (
                f"Burj Khalifa view should not produce high-confidence area, "
                f"got area={area} conf={conf}"
            )

    def test_unknown_text_returns_none(self):
        area, conf, _, _ = detect_area("Random text without any area mention")
        assert area is None
        assert conf == 0.0

    def test_marina_full_name(self):
        area, conf, emirate, _ = detect_area("1BR apartment in Dubai Marina")
        assert area == "Dubai Marina"
        assert emirate == "Dubai"


# ─────────────────────────────────────────────────────────────────────────────
# 5. detect_building  (B002 — landmark view guard)
# ─────────────────────────────────────────────────────────────────────────────
class TestBuildingLandmarks:
    def test_burj_khalifa_view_only_nullified(self):
        # B002: «view of Burj Khalifa» — building is NOT Burj Khalifa
        bld, conf, *_ = detect_building("1BR with view of Burj Khalifa, Downtown")
        assert bld is None or bld.lower() != "burj khalifa", (
            f"Burj Khalifa view should nullify building, got {bld}"
        )

    def test_burj_khalifa_inside_keeps(self):
        # «inside Burj Khalifa» — это building
        bld, conf, *_ = detect_building("1BR inside Burj Khalifa, Downtown")
        assert bld == "Burj Khalifa", f"Expected Burj Khalifa, got {bld}"

    def test_palm_jumeirah_view_nullified(self):
        bld, *_ = detect_building("Marina apartment, view of Palm Jumeirah")
        assert bld is None or "palm jumeirah" not in bld.lower(), (
            f"Palm Jumeirah view should nullify building, got {bld}"
        )

    def test_atlantis_near_nullified(self):
        bld, *_ = detect_building("Apartment near Atlantis hotel, JBR")
        assert bld is None or "atlantis" not in bld.lower(), (
            f"Atlantis near should nullify building, got {bld}"
        )

    def test_atlantis_the_royal_residences_keeps(self):
        # Whitelisted legitimate residences
        bld, *_ = detect_building("Atlantis The Royal Residences, Palm Jumeirah")
        assert bld == "Atlantis The Royal Residences"

    def test_sheikh_zayed_road_street_nullified(self):
        # «on Sheikh Zayed Road» = street reference, not building
        bld, *_ = detect_building("Apartment on Sheikh Zayed Road")
        assert bld is None or "sheikh zayed road" not in (bld or "").lower(), (
            f"SZR street should nullify building, got {bld}"
        )

    def test_marina_skyline_overlooking_nullified(self):
        bld, *_ = detect_building("apartment overlooking Marina Skyline")
        assert bld is None or "marina skyline" not in (bld or "").lower()

    def test_dubai_mall_view_nullified(self):
        bld, *_ = detect_building("1BR with Dubai Mall view")
        assert bld is None or "dubai mall" not in (bld or "").lower()

    def test_dubai_eye_view_nullified(self):
        bld, *_ = detect_building("Marina apt with Dubai Eye view")
        assert bld is None or "dubai eye" not in (bld or "").lower()

    def test_burj_al_arab_facing_nullified(self):
        bld, *_ = detect_building("Beachfront apt facing Burj Al Arab")
        assert bld is None or "burj al arab" not in (bld or "").lower()


# ─────────────────────────────────────────────────────────────────────────────
# 6. Sub-cluster artifacts  (villa audit findings)
# ─────────────────────────────────────────────────────────────────────────────
class TestSubClusterArtifacts:
    def test_cluster_5_not_building(self):
        bld, *_ = detect_building("Townhouse in Cluster 5, Tilal Al Ghaf")
        assert bld is None or "cluster 5" not in (bld or "").lower(), (
            f"«Cluster 5» should not be building, got {bld}"
        )

    def test_phase_2_not_building(self):
        bld, *_ = detect_building("Villa in Phase 2, Arabian Ranches")
        assert bld is None or "phase 2" not in (bld or "").lower()

    def test_type_b_not_building(self):
        bld, *_ = detect_building("Villa Type B, Reem")
        assert bld is None or "type b" not in (bld or "").lower()

    def test_sidra_real_subcommunity_keeps(self):
        # «Sidra» — legit sub-community in Dubai Hills Estate
        bld, conf, *_ = detect_building("Villa in Sidra 3, Dubai Hills Estate")
        assert bld == "Sidra", f"Expected Sidra, got {bld}"
        assert conf >= 0.85

    def test_maple_real_subcommunity_keeps(self):
        bld, conf, *_ = detect_building("Villa in Maple, Dubai Hills")
        assert bld == "Maple"
        assert conf >= 0.85


# ─────────────────────────────────────────────────────────────────────────────
# 7. Marketing-phrase stoplist  (B003)
# ─────────────────────────────────────────────────────────────────────────────
class TestMarketingPhrasesStoplist:
    def test_last_day_not_building(self):
        bld, *_ = detect_building("LAST DAY apartment for sale, Marina")
        assert bld is None or "last day" not in (bld or "").lower(), (
            f"«LAST DAY» should not be building, got {bld}"
        )

    def test_act_fast_not_building(self):
        bld, *_ = detect_building("Act fast Marina apartment for sale")
        assert bld is None or "act fast" not in (bld or "").lower()
