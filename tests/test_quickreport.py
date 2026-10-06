"""Тесты quickreport: yaml-активы, стаб-скоринг, сборка QuickReportContext."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from terralogic_engine.analytics.models import IntersectionSummary, NearestObject
from terralogic_engine.quickreport import (
    build_quick_context_from_report,
    compute_score,
    load_scoring_rules,
    load_zone_guidance,
    match_guidance,
)
from terralogic_engine.reporting.models import (
    ParcelPlanningZoneReportContext,
    ParcelReportContext,
    ReportContext,
    SearchAreaReportContext,
    UrbanPlanningReportContext,
    ZouitReportContext,
)


@pytest.fixture()
def rules():
    return load_scoring_rules()


@pytest.fixture()
def guidance():
    return load_zone_guidance()


# ---------------------------------------------------------------------------
# yaml-активы
# ---------------------------------------------------------------------------


def test_package_assets_load_and_keys_are_consistent(rules, guidance):
    assert rules.methodology_version == "0.1-stub"
    assert rules.base_score == 100
    for rule in rules.rules:
        if rule.guidance_key == "communications":
            assert rule.guidance_key in guidance.misc
        else:
            assert rule.guidance_key in guidance.zones, rule.guidance_key


def test_coverage_words_thresholds(rules):
    assert rules.coverage_word(100) == "практически весь участок"
    assert rules.coverage_word(80) == "практически весь участок"
    assert rules.coverage_word(50) == "часть участка"
    assert rules.coverage_word(5) == "край участка"


def test_grade_for_thresholds(rules):
    assert rules.grade_for(100).key == "clear"
    assert rules.grade_for(75).key == "clear"
    assert rules.grade_for(60).key == "attention"
    assert rules.grade_for(10).key == "severe"


def test_match_guidance_by_zone_type_and_name(guidance):
    entry = match_guidance(guidance, zone_type="Водоохранная зона", name="р. Банка")
    assert entry is not None and entry.key == "water_protection"
    entry = match_guidance(guidance, zone_type=None, name="Охранная зона ЛЭП 750 кВ")
    assert entry is not None and entry.key == "power_line"
    assert match_guidance(guidance, zone_type="проезды", name="ул. Ленина") is None


def test_compute_score_arithmetic(rules):
    by_id = {rule.id: rule for rule in rules.rules}
    score = compute_score(
        rules,
        [
            (by_id["water_protection"], 100.0),  # −10
            (by_id["coastal_strip"], 40.0),  # −4
            (by_id["communications_unknown"], 0.0),  # informational — не вычитает
        ],
    )
    assert score == 86
    assert compute_score(rules, [(by_id["water_protection"], 100.0)] * 20) == 0


# ---------------------------------------------------------------------------
# Контекст
# ---------------------------------------------------------------------------


def _zouit(name: str, zone_type: str | None, coverage: float) -> ZouitReportContext:
    return ZouitReportContext(
        feature_id=f"f-{name}",
        source="nspd",
        name=name,
        zone_type=zone_type,
        relation="intersects",
        intersection_area_m2=coverage * 10,
        parcel_coverage_percent=coverage,
        zone_coverage_percent=10.0,
    )


def _base_context() -> ReportContext:
    now = datetime(2026, 10, 6, tzinfo=UTC)
    return ReportContext(
        case_id="case-quick",
        collection_run_id="run-1",
        analysis_id="analysis-1",
        analytics_version="1.0",
        collection_status="complete",
        collected_at=now,
        analyzed_at=now,
        parcel=ParcelReportContext(
            cadastral_number="50:11:0020310:49",
            address="Московская область, Красногорск",
            status="Учтённый",
            declared_area_m2=1000.0,
            calculated_area_m2=1005.0,
            land_category="Земли населённых пунктов",
            permitted_use="ИЖС",
            cadastral_value_rub=5_000_000.0,
            geometry_type="MultiPolygon",
        ),
        search_area=SearchAreaReportContext(
            parcel_minimum_radius_m=20.0,
            margin_m=100,
            search_radius_m=500.0,
        ),
        zouit_summary=IntersectionSummary(
            key="zouit", name="ЗОУИТ", candidate_count=2, intersecting_count=2
        ),
        zouit=[
            _zouit("Водоохранная зона р. Банка", "Водоохранная зона", 100.0),
            _zouit("Прибрежная защитная полоса", "Прибрежная полоса", 40.0),
        ],
        natural_nearest=[
            NearestObject(
                group="natural",
                group_name="Природные объекты",
                category="water",
                category_name="Водные объекты",
                source="osm",
                status="found",
                candidate_count=1,
                object_name="пруд Круглый",
                distance_m=42.0,
            )
        ],
        urban_planning=UrbanPlanningReportContext(
            collected=True,
            parcel_zones=[
                ParcelPlanningZoneReportContext(
                    feature_id="pz-1", zone="Ж-2", name="Зона застройки ИЖС"
                )
            ],
        ),
        warnings=["исходное предупреждение"],
    )


def test_build_quick_context_from_report(rules, guidance):
    quick = build_quick_context_from_report(
        _base_context(), rules=rules, guidance=guidance
    )

    assert quick.methodology_version == "0.1-stub"
    # −10 (ВЗП 100%) −4 (полоса 40%) = 86 → clear
    assert quick.verdict.score == 86
    assert quick.verdict.grade_key == "clear"

    zone_factors = [f for f in quick.factors if not f.informational]
    assert [f.rule_id for f in zone_factors] == ["water_protection", "coastal_strip"]
    assert zone_factors[0].coverage_word == "практически весь участок"
    assert zone_factors[1].coverage_word == "часть участка"
    assert "водоём" in zone_factors[0].guidance
    # информационный блок коммуникаций — последним
    assert quick.factors[-1].rule_id == "communications_unknown"
    assert quick.factors[-1].informational is True

    assert quick.parcel.cadastral_number == "50:11:0020310:49"
    assert quick.parcel.territorial_zone == "Ж-2, Зона застройки ИЖС"
    assert quick.parcel.area_m2 == 1000.0

    assert quick.surroundings.lines == ["Водные объекты: пруд Круглый ~42 м"]
    assert quick.surroundings.communications_note is not None
    assert "исходное предупреждение" in quick.warnings


def test_unmatched_zone_goes_to_warnings(rules, guidance):
    base = _base_context()
    base.zouit.append(_zouit("Проезд общего пользования", "проезды", 15.0))
    quick = build_quick_context_from_report(base, rules=rules, guidance=guidance)
    assert any("Проезд общего пользования" in w for w in quick.warnings)
    # нераспознанная зона балл не меняет
    assert quick.verdict.score == 86


def test_no_zones_gives_max_score(rules, guidance):
    base = _base_context()
    base.zouit = []
    quick = build_quick_context_from_report(base, rules=rules, guidance=guidance)
    assert quick.verdict.score == 100
    assert quick.verdict.grade_key == "clear"
    assert [f.rule_id for f in quick.factors] == ["communications_unknown"]
