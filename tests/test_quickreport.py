"""Тесты quickreport: yaml-активы, стаб-скоринг, сборка QuickReportContext."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

import pytest

from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.analytics.models import IntersectionSummary, NearestObject
from terralogic_engine.analytics.pipeline import AnalysisPipeline
from terralogic_engine.domain.models import CollectionRequest
from terralogic_engine.quickreport import (
    build_quick_context_from_report,
    compute_score,
    load_scoring_rules,
    load_zone_guidance,
    match_guidance,
    render_quickreport,
)
from terralogic_engine.reporting.models import (
    ParcelPlanningZoneReportContext,
    ParcelReportContext,
    ReportContext,
    SearchAreaReportContext,
    UrbanPlanningReportContext,
    ZouitReportContext,
)
from terralogic_engine.reporting.service import ReportingService
from terralogic_engine.store.local import LocalCaseStore

from .fakes import FakeDgisClient, FakeNspdClient, FakeOsmClient


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


# ---------------------------------------------------------------------------
# Рендер
# ---------------------------------------------------------------------------


def test_render_quickreport_structure(rules, guidance):
    quick = build_quick_context_from_report(
        _base_context(), rules=rules, guidance=guidance
    )

    markdown = render_quickreport(quick, today=date(2026, 10, 7))

    assert markdown.startswith("# Экспресс-оценка земельного участка\n")
    assert (
        "Отчёт от 07.10.2026 · Данные от 06.10.2026 · "
        "Методика оценки — версия 0.1-stub"
    ) in markdown
    assert "**Кадастровый номер 50:11:0020310:49**" in markdown
    # скор в заголовке вердикта + градация + summary
    assert "## Оценка участка — 86/100 (без существенных ограничений)" in markdown
    assert "Рекомендуется стандартная проверка перед покупкой." in markdown
    # таблица факторов: заголовок и обе зоны, informational-строки нет
    assert "| № | Зона | Охват участка | Что это значит для вас |" in markdown
    assert "| 1 | Водоохранная зона р. Банка | Практически весь участок |" in markdown
    assert "| 2 | Прибрежная защитная полоса | Часть участка |" in markdown
    assert "| 3 |" not in markdown
    assert "Коммуникации не проверены |" not in markdown
    # паспорт участка
    assert "## Паспорт участка" in markdown
    assert "- **Адрес:** Московская область, Красногорск" in markdown
    assert "- **Статус / площадь:** Учтённый · 1 000 м²" in markdown
    assert "- **Категория земель:** Земли населённых пунктов" in markdown
    assert "- **Разрешённое использование:** ИЖС" in markdown
    assert "- **Территориальная зона:** Ж-2, Зона застройки ИЖС" in markdown
    assert (
        "- **Кадастровая стоимость:** 5 000 000 руб. (не является рыночной ценой)"
        in markdown
    )
    # окружение + блок коммуникаций
    assert "## Окружение" in markdown
    assert "- Водные объекты: пруд Круглый ~42 м" in markdown
    assert "### Коммуникации" in markdown
    assert "электричеству" in markdown
    # заглушка карты и дисклеймер
    assert "Схема расположения зон приводится в подробном заключении." in markdown
    assert "## Ограничения отчёта" in markdown
    assert (
        "не заменяет юридическое, кадастровое или градостроительное заключение"
        in markdown
    )
    assert markdown.endswith("\n")
    # маркетинговых блоков образца нет
    assert "Заключение»" not in markdown
    assert "Подбор" not in markdown


def test_render_quickreport_without_zones(rules, guidance):
    base = _base_context()
    base.zouit = []
    quick = build_quick_context_from_report(base, rules=rules, guidance=guidance)

    markdown = render_quickreport(quick, today=date(2026, 10, 7))

    assert "## Оценка участка — 100/100 (без существенных ограничений)" in markdown
    assert "| 1 |" not in markdown
    assert "на участок не попадают" in markdown


def test_render_quickreport_defaults_today(rules, guidance):
    quick = build_quick_context_from_report(
        _base_context(), rules=rules, guidance=guidance
    )
    markdown = render_quickreport(quick)
    assert f"Отчёт от {datetime.now(UTC):%d.%m.%Y}" in markdown


# ---------------------------------------------------------------------------
# prepare_quickreport end-to-end
# ---------------------------------------------------------------------------


async def test_prepare_quickreport_persists_markdown(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    acquisition = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    )
    receipt = await acquisition.collect(
        CollectionRequest(
            case_id="case-quickreport",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
        )
    )
    analysis = AnalysisPipeline(store=store).analyze("case-quickreport")
    service = ReportingService(store=store, acquisition=acquisition)

    result = service.prepare_quickreport("case-quickreport")

    assert result.case_id == "case-quickreport"
    assert result.collection_run_id == receipt.run_id
    assert result.analysis_id == analysis.id
    assert result.methodology_version == "0.1-stub"
    assert result.markdown.startswith("# Экспресс-оценка земельного участка\n")
    assert "52:26:0040002:3823" in result.markdown
    persisted = store.get_latest_generated_report("case-quickreport", receipt.run_id)
    assert persisted is not None
    assert persisted.id == result.report_id
    assert persisted.template_id == "quick_report"
    assert persisted.template_version == result.methodology_version
    assert len(persisted.template_sha256) == 64
    assert persisted.markdown == result.markdown
    assert persisted.content_sha256 == result.content_sha256
    assert (
        result.content_sha256
        == hashlib.sha256(result.markdown.encode("utf-8")).hexdigest()
    )
    report_file = (
        tmp_path / "store" / "cases" / "case-quickreport" / result.relative_path
    )
    assert report_file.read_text("utf-8") == result.markdown
