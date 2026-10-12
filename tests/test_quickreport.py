"""Тесты quickreport: yaml-активы, стаб-скоринг, сборка QuickReportContext."""

from __future__ import annotations

import hashlib
import io
import sys
from datetime import UTC, date, datetime

import pytest

from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.analytics.models import IntersectionSummary, NearestObject
from terralogic_engine.analytics.pipeline import AnalysisPipeline
from terralogic_engine.domain.models import CollectionRequest
from terralogic_engine.quickreport import (
    ConfigurationOutcome,
    ScoredFactor,
    build_quick_context_from_report,
    compute_verdict,
    evaluate_configuration,
    load_scoring_rules,
    load_zone_guidance,
    render_quickreport,
    render_service_report,
)
from terralogic_engine.reporting.models import (
    DocumentReportContext,
    DocumentVriItemContext,
    DocumentVriZoneContext,
    ParcelPlanningZoneReportContext,
    ParcelReportContext,
    PzzDocumentContext,
    ReportContext,
    SearchAreaReportContext,
    SourceEvidenceContext,
    UrbanPlanningReportContext,
    ZouitReportContext,
)
from terralogic_engine.reporting.service import ReportingService
from terralogic_engine.store.local import LocalCaseStore

from .fakes import FakeDgisClient, FakeNspdClient, FakeOsmClient


def _fake_tile(z: int, x: int, y: int, timeout: float) -> bytes:
    """Однотонный тайл подложки вместо сетевого OSM."""
    from PIL import Image

    image = Image.new("RGB", (256, 256), (238, 240, 233))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


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
    assert rules.methodology_version == "6.1"
    assert rules.base_score == 100
    assert rules.group_cap == 40
    assert sum(rules.comm_weights.values()) == rules.comm_max == 10
    # у всех основных типов зон есть текст для таблицы факторов
    for key in ("water_protection", "coastal_strip", "airport_subzone"):
        assert key in guidance.zones
    assert "communications" in guidance.misc


def test_coverage_words_thresholds(rules):
    assert rules.coverage_word(100) == "практически весь участок"
    assert rules.coverage_word(80) == "практически весь участок"
    assert rules.coverage_word(50) == "часть участка"
    assert rules.coverage_word(5) == "край участка"


def test_classify_groups_and_bands(rules):
    matched = rules.classify("Охранная зона", "Водоохранная зона р. Банка")
    assert matched is not None
    group, factor = matched
    assert group.key == "water" and factor.id == "water_protection_zone"
    assert factor.deduction_for(100.0) == 25
    assert factor.deduction_for(75.0) == 15
    assert factor.deduction_for(40.0) == 15
    assert factor.deduction_for(10.0) == 5
    matched = rules.classify(None, "Шестая подзона приаэродромной территории")
    assert matched is not None
    group, factor = matched
    assert factor.id == "airport_subzone_no_height"
    assert factor.deduction_for(100.0) == 5
    matched = rules.classify(None, "Третья подзона приаэродромной территории")
    assert matched is not None and matched[1].id == "airport_subzone_height"
    assert matched[1].deduction_for(5.0) == 15
    matched = rules.classify(None, "Охранная зона ЛЭП 750 кВ")
    assert matched is not None and matched[1].deduction_for(1.0) == 20
    assert rules.classify("проезды", "ул. Ленина") is None


def test_compute_verdict_arithmetic(rules):
    # водоохранная 100% (25) + прибрежная (10) → группа 35; U = 10; G = 0
    scored = [
        ScoredFactor("water", "Водные", "Водоохранная зона", "ВЗ", "nspd", 100.0, 25.0),
        ScoredFactor("water", "Водные", "Прибрежная", "Полоса", "nspd", 40.0, 10.0),
    ]
    verdict = compute_verdict(
        rules, scored, u=10.0, a=0.0, c=0.0,
        stop_reasons=[], suspend_reasons=[], preliminary_flags=[],
    )
    assert verdict.score == 55
    assert verdict.status.key == "attention"  # вычет группы ≥ 15
    assert verdict.preliminary is True  # U > 0

    # потолок группы 40: две ЛЭП по 20 → 40, не 40+
    leps = [
        ScoredFactor("power_line", "Инфраструктура", "ЛЭП", "ЛЭП-1", "nspd", 5.0, 20.0),
        ScoredFactor("power_line", "Инфраструктура", "ЛЭП", "ЛЭП-2", "nspd", 5.0, 20.0),
        ScoredFactor("power_line", "Инфраструктура", "ЛЭП", "ЛЭП-3", "nspd", 5.0, 20.0),
    ]
    verdict = compute_verdict(
        rules, leps, u=0.0, a=0.0, c=0.0,
        stop_reasons=[], suspend_reasons=[], preliminary_flags=[],
    )
    assert verdict.groups[0].deduction == 40
    assert verdict.score == 60
    assert verdict.preliminary is False

    # stop и insufficient — без балла
    verdict = compute_verdict(
        rules, [], u=10.0, a=0.0, c=0.0,
        stop_reasons=["стоп"], suspend_reasons=[], preliminary_flags=[],
    )
    assert verdict.score is None and verdict.status.key == "stop"
    verdict = compute_verdict(
        rules, [], u=10.0, a=0.0, c=0.0,
        stop_reasons=[], suspend_reasons=["нет сведений"], preliminary_flags=[],
    )
    assert verdict.score is None and verdict.status.key == "insufficient"

    # ok: 95 ≥ 85, групп ≥ 15 нет
    verdict = compute_verdict(
        rules, [ScoredFactor("water", "Водные", "Водоохранная", "ВЗ", "nspd", 10.0, 5.0)],
        u=0.0, a=0.0, c=0.0, stop_reasons=[], suspend_reasons=[], preliminary_flags=[],
    )
    assert verdict.score == 95 and verdict.status.key == "ok"


# ---------------------------------------------------------------------------
# Контекст
# ---------------------------------------------------------------------------


def _zouit(
    name: str,
    zone_type: str | None,
    coverage: float,
    *,
    source: str = "nspd",
    registry_number: str | None = None,
) -> ZouitReportContext:
    return ZouitReportContext(
        feature_id=f"f-{name}",
        source=source,
        name=name,
        registry_number=registry_number,
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
        sources=[
            SourceEvidenceContext(
                source="nspd",
                snapshot_id="nspd-1",
                retrieved_at=now,
                adapter_version="test",
                content_sha256="0" * 64,
            ),
            SourceEvidenceContext(
                source="rgis",
                snapshot_id="rgis-1",
                retrieved_at=now,
                adapter_version="test",
                content_sha256="0" * 64,
            ),
        ],
        warnings=["исходное предупреждение"],
    )


_CONFIG_OK = ConfigurationOutcome(c=0, detail="тест: 12×12 размещается")


def test_build_quick_context_from_report(rules, guidance):
    quick = build_quick_context_from_report(
        _base_context(), rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )

    assert quick.methodology_version == "6.1"
    # водоохранная 100% (25) + прибрежная 40% (10) = 35; U = 10; G = 0 → 55
    assert quick.verdict.score == 55
    assert quick.verdict.status_key == "attention"
    assert quick.verdict.preliminary is True  # U > 0
    assert any("Водные ограничения" in reason for reason in quick.verdict.reasons)

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


def test_unmatched_zone_suspends_score(rules, guidance):
    base = _base_context()
    base.zouit.append(_zouit("Проезд общего пользования", "проезды", 15.0))
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )
    assert any("Проезд общего пользования" in w for w in quick.warnings)
    # фактор без правила → приостановка выдачи (п. 3.3)
    assert quick.verdict.score is None
    assert quick.verdict.status_key == "insufficient"
    assert any("Проезд общего пользования" in r for r in quick.verdict.reasons)


def test_no_zones_gives_max_score(rules, guidance):
    base = _base_context()
    base.zouit = []
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )
    # ограничений нет, но все коммуникации неизвестны: 100 − U(10) = 90
    assert quick.verdict.score == 90
    assert quick.verdict.status_key == "ok"
    assert quick.verdict.preliminary is True
    assert [f.rule_id for f in quick.factors] == ["communications_unknown"]


def test_factors_collapse_duplicate_zone_names(rules, guidance):
    """Краткая и полная версии одной зоны + дубли слоёв — одна строка таблицы."""
    base = _base_context()
    base.zouit = [
        _zouit("Водоохранная зона", "Водоохранная зона", 100.0),
        _zouit("Водоохранная зона реки Оки в округах Серпухов и Ступино", None, 95.0),
        _zouit("Водоохранная зона", None, 100.0),
        _zouit("Приаэродромная территория", None, 100.0),
        _zouit(
            "Шестая подзона приаэродромной территории аэродрома Москва (Шереметьево)",
            None,
            100.0,
        ),
    ]
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )

    zone_factors = [f for f in quick.factors if not f.informational]
    # одна строка на тип зоны, короткое каноническое имя, максимальный охват
    assert [f.zone_name for f in zone_factors] == [
        "Водоохранная зона",
        "Приаэродромная территория",
    ]
    assert zone_factors[0].coverage_percent == 100.0
    # причины вердикта без дублей и без «Группа: Группа»
    assert "Водные ограничения водного объекта: Водоохранная зона" in (
        quick.verdict.reasons
    )
    assert "Приаэродромная территория" in quick.verdict.reasons
    assert not any("Шереметьево" in reason for reason in quick.verdict.reasons)
    markdown = render_quickreport(quick, today=date(2026, 10, 7))
    assert "| 1 | Водоохранная зона | Практически весь участок |" in markdown
    assert "| 2 | Приаэродромная территория | Практически весь участок |" in markdown
    assert "| 3 |" not in markdown
    assert "Шереметьево" not in markdown


def test_scoring_merges_short_and_full_zone_names(rules, guidance):
    """Краткое наименование-префикс полного — одна зона, двойного вычета нет."""
    base = _base_context()
    base.zouit = [
        _zouit(
            "Водоохранная зона р. Пахра",
            "Водоохранная зона",
            100.0,
            registry_number="50:00-6.2054",
        ),
        _zouit("Водоохранная зона", None, 82.4, source="rgis"),
    ]
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )

    protocol = quick.protocol
    assert protocol is not None
    assert len(protocol.factors) == 1
    assert protocol.factors[0].zone_name == "Водоохранная зона р. Пахра"
    assert protocol.factors[0].coverage_percent == 100.0
    # один фактор 25, а не 25 + 25 с усечением потолком
    assert protocol.groups[0].raw_deduction == 25
    # 100 − 25 (водные) − 10 (U) − 0 (G)
    assert quick.verdict.score == 65
    assert any(
        "слияние краткого и полного наименования" in note
        for note in protocol.classify_notes
    )


def test_scoring_keeps_distinct_zones_with_equal_coverage(rules, guidance):
    """Равный охват — не основание для слияния: разные зоны не поглощаются.

    Слой-витрина, повторяющий тип первичной записи реестра («Охранная зона
    транспорта» = zone_type подзоны аэродрома), — краткое наименование той же
    зоны и отбрасывается; посторонняя зона с тем же охватом («Зоны и
    территории ОКН») остаётся фактором без правила и приостанавливает выдачу.
    """
    base = _base_context()
    base.zouit = [
        _zouit(
            "Шестая подзона приаэродромной территории аэродрома Москва (Шереметьево)",
            "Охранная зона транспорта",
            100.0,
            registry_number="50:00-6.2819",
        ),
        _zouit("Охранная зона транспорта", None, 90.3, source="rgis"),
        _zouit("Зоны и территории ОКН", None, 90.3, source="rgis"),
    ]
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )

    protocol = quick.protocol
    assert protocol is not None
    # первичная запись-подзона классифицирована, эхо-витрина не считается фактором
    assert len(protocol.factors) == 1
    assert protocol.factors[0].group_key == "airport"
    # эхо не попало в нераспознанные, посторонняя зона — попала
    assert protocol.unmatched_zones == ["Зоны и территории ОКН"]
    assert quick.verdict.score is None
    assert quick.verdict.status_key == "insufficient"
    assert any("Зоны и территории ОКН" in r for r in quick.verdict.reasons)
    assert any(
        "совпадает с типом первичной записи реестра" in note
        for note in protocol.classify_notes
    )


def test_scoring_ambiguous_short_name_stays_separate(rules, guidance):
    """Краткое наименование — префикс двух полных: слияние неоднозначно."""
    base = _base_context()
    base.zouit = [
        _zouit("Водоохранная зона р. Пахра", None, 100.0, registry_number="50:00-6.2054"),
        _zouit("Водоохранная зона р. Москва", None, 100.0, registry_number="50:00-6.3000"),
        _zouit("Водоохранная зона", None, 50.0, source="rgis"),
    ]
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )

    protocol = quick.protocol
    assert protocol is not None
    # три записи остаются: две полные + неоднозначная краткая
    assert len(protocol.factors) == 3
    # 25 + 25 + 15 = 65, усечено потолком группы 40
    assert protocol.groups[0].raw_deduction == 65
    assert protocol.groups[0].deduction == 40
    assert any("нескольким зонам" in note for note in protocol.classify_notes)


def test_unknown_configuration_suspends_score(rules, guidance):
    """Без геометрической проверки числовая оценка не выдаётся (п. 4.4)."""
    quick = build_quick_context_from_report(
        _base_context(), rules=rules, guidance=guidance, configuration=None
    )
    assert quick.verdict.score is None
    assert quick.verdict.status_key == "insufficient"
    assert any("конфигурация" in r for r in quick.verdict.reasons)


def test_protocol_and_service_report(rules, guidance):
    """Протокол скоринга: внутренние цифры расчёта + рендер сервисного отчёта."""
    quick = build_quick_context_from_report(
        _base_context(), rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )
    protocol = quick.protocol
    assert protocol is not None
    # водная группа 35 (сырая = после потолка), U = 10, G = 0 → 55
    assert protocol.score == 55 and protocol.raw_score == 55
    assert protocol.restrictions_total == 35
    assert protocol.u_total == 10
    assert [group.key for group in protocol.groups] == ["water"]
    assert protocol.groups[0].raw_deduction == 35
    assert protocol.groups[0].deduction == 35
    assert len(protocol.factors) == 2  # водоохранная + прибрежная после слияния
    assert protocol.area is not None and protocol.area.status == "no_regulations"
    assert protocol.geometry is not None and protocol.geometry.c == 0
    assert protocol.g == 0
    assert "правило 3" in protocol.status_rule
    assert {source.source for source in protocol.sources} == {"nspd", "rgis"}
    checks = {check.name: check.result for check in protocol.checks}
    assert "пересечение выявлено" in checks["Водоохранные зоны и прибрежные защитные полосы"]
    assert checks["Приаэродромная территория"] == "пересечений не выявлено"

    markdown = render_service_report(quick, today=date(2026, 10, 7))
    assert markdown.startswith("# Сервисный отчёт скоринга — 50:11:0020310:49\n")
    assert "Методика 6.1 · sha256 активов:" in markdown
    assert "**Оценка = max(0; 100 − 35 (ограничения) − 10 (U) − 0 (G)) = 55/100**" in markdown
    assert "правило 3" in markdown
    # протокольная таблица зон хранит сырые наименования представителей
    assert "Водоохранная зона р. Банка" in markdown
    assert "| Электроснабжение | неизвестно | 4 |" in markdown
    assert "**U = 10**" in markdown
    assert "Сформирован 07.10.2026" in markdown


def test_service_report_without_score_and_protocol(rules, guidance):
    """Нерассчитанная оценка и контекст без протокола — читаемая заглушка."""
    quick = build_quick_context_from_report(
        _base_context(), rules=rules, guidance=guidance, configuration=None
    )
    markdown = render_service_report(quick, today=date(2026, 10, 7))
    assert "**Оценка не рассчитана**" in markdown
    assert "конфигурация участка не проверена" in markdown
    assert "правило 2" in markdown

    quick.protocol = None
    markdown = render_service_report(quick, today=date(2026, 10, 7))
    assert "Протокол недоступен" in markdown


# ---------------------------------------------------------------------------
# Методика 6.1: A (минимальная площадь), стоп-фактор, геометрия
# ---------------------------------------------------------------------------


def test_area_at_minimum_deducts_five(rules, guidance):
    """S == Smin по регламенту → A = 5 (п. 4.3)."""
    base = _with_pzz(_base_context())  # Ж-2, ИЖС 2.1*: min 500 м², отступ 3 м
    base.parcel.declared_area_m2 = 500.0
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )
    # водная группа 35 + U 10 + G 5 → 50
    assert quick.verdict.score == 50
    assert any("минимальной границе" in r for r in quick.verdict.reasons)


def test_area_below_minimum_suspends(rules, guidance):
    """S < Smin → ручная проверка, оценка не выдаётся (п. 4.3)."""
    base = _with_pzz(_base_context())
    base.parcel.declared_area_m2 = 400.0
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )
    assert quick.verdict.score is None
    assert quick.verdict.status_key == "insufficient"
    assert any("ниже минимальной" in r for r in quick.verdict.reasons)


def test_stop_factor_without_housing_use(rules, guidance):
    """В таблице зоны нет жилищных ВРИ → стоп-фактор, без балла (раздел 5)."""
    base = _with_pzz(_base_context())
    base.documents.pzz.zones[0].items = [_vri_item("4.2", "Торговые центры")]
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )
    assert quick.verdict.score is None
    assert quick.verdict.status_key == "stop"
    assert any("отсутствует в регламенте" in r for r in quick.verdict.reasons)


def _rectangle(width: float, depth: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[(0, 0), (width, 0), (width, depth), (0, depth), (0, 0)]],
    }


def _square(side: float) -> dict:
    return _rectangle(side, side)


def _eval_config(geometry: dict, margin: float) -> int:
    outcome = evaluate_configuration(
        geometry,
        margin,
        source_crs="EPSG:32637",
        metric_crs="EPSG:32637",
        test_m=10,
        control_m=12,
        grid_step_m=1.0,
        fine_grid_step_m=0.5,
        tight_c=5,
        no_fit_c=10,
    )
    assert outcome is not None
    return outcome.c


def test_evaluate_configuration_variants():
    # 100×100 с отступом 3 м — контрольный квадрат размещается
    assert _eval_config(_square(100), 3) == 0
    # 16×16 с отступом 3 м → область 10×10: 12×12 не входит, 10×10 входит
    assert _eval_config(_square(16), 3) == 5
    # полоса 26×4 с отступом 3 м → область после отступов пуста
    assert _eval_config(_rectangle(26, 4), 3) == 10


# ---------------------------------------------------------------------------
# Рендер
# ---------------------------------------------------------------------------


def test_render_quickreport_structure(rules, guidance):
    quick = build_quick_context_from_report(
        _base_context(), rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )

    markdown = render_quickreport(quick, today=date(2026, 10, 7))

    assert markdown.startswith("# Экспресс-оценка земельного участка\n")
    assert (
        "Отчёт от 07.10.2026 · Данные от 06.10.2026 · Методика оценки — версия 6.1"
    ) in markdown
    assert "**Кадастровый номер 50:11:0020310:49**" in markdown
    # балл + статус + отметка «предварительная» (U > 0) + причины + пояснение
    assert (
        "## Оценка участка — 55/100 (Требует повышенного внимания, предварительная)"
        in markdown
    )
    assert "Возможность реализовать ваш замысел требует подробного анализа." in markdown
    assert "Основные причины:" in markdown
    assert "Водные ограничения водного объекта" in markdown
    assert "Чем выше балл, тем меньше выявленных ограничений" in markdown
    # таблица факторов: заголовок и обе зоны (короткие канонические имена),
    # informational-строки нет
    assert "| № | Зона | Охват участка | Что это значит для вас |" in markdown
    assert "| 1 | Водоохранная зона | Практически весь участок |" in markdown
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
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )

    markdown = render_quickreport(quick, today=date(2026, 10, 7))

    assert (
        "## Оценка участка — 90/100 (Можно продолжить рассмотрение, предварительная)"
        in markdown
    )
    assert "| 1 |" not in markdown
    assert "на участок не попадают" in markdown


def test_render_quickreport_defaults_today(rules, guidance):
    quick = build_quick_context_from_report(
        _base_context(), rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )
    markdown = render_quickreport(quick)
    assert f"Отчёт от {datetime.now(UTC):%d.%m.%Y}" in markdown


# ---------------------------------------------------------------------------
# Территориальная зона и ВРИ из ПЗЗ
# ---------------------------------------------------------------------------


def _vri_item(code: str, name: str, **limits) -> DocumentVriItemContext:
    return DocumentVriItemContext(code=code, name=name, **limits)


def _with_pzz(base: ReportContext) -> ReportContext:
    items = [
        _vri_item(
            "2.1*",
            "Для индивидуального жилищного строительства",
            area_min=500,
            area_max=500000,
            building_percentage="40%",
            margin=3,
        ),
        _vri_item("2.2*", "Для ведения личного подсобного хозяйства"),
        # дубль той же строки из пересекающейся таблицы документа
        _vri_item("2.1*", "Для индивидуального жилищного строительства"),
        # нежилищные коды в жилищный блок не попадают
        _vri_item("4.2", "Торговые центры"),
    ]
    base.documents = DocumentReportContext(
        pzz=PzzDocumentContext(
            number="1986/8",
            version_date="2026-08-06",
            files=["reglament.pdf"],
            zones=[
                DocumentVriZoneContext(zone_code="Ж-2", found=True, items=items),
                DocumentVriZoneContext(zone_code="СХ-1", found=False),
            ],
        )
    )
    return base


def test_zone_regulations_from_pzz(rules, guidance):
    base = _with_pzz(_base_context())
    quick = build_quick_context_from_report(base, rules=rules, guidance=guidance)

    regulations = quick.zone_regulations
    assert regulations is not None
    assert regulations.zone_code == "Ж-2"
    assert regulations.doc_number == "1986/8"
    assert regulations.doc_version_date == "2026-08-06"
    assert regulations.total_uses == 4
    # дубль 2.1* схлопнут, 4.2 отфильтрован
    assert [use.code for use in regulations.housing_uses] == ["2.1*", "2.2*"]

    markdown = render_quickreport(quick, today=date(2026, 10, 7))
    assert "## Территориальная зона и разрешённое использование" in markdown
    assert "Зона Ж-2 — по данным ПЗЗ № 1986/8, редакция от 2026-08-06." in markdown
    assert (
        "- **2.1* Для индивидуального жилищного строительства** — участок "
        "500–500 000 м², застройка до 40%, отступ от границ 3 м"
    ) in markdown
    assert markdown.count("2.1* Для индивидуального") == 1
    assert "4.2" not in markdown


def test_zone_use_line_one_sided_area_limits(rules, guidance):
    """Односторонний предел площади: «от/до X м²», без «–—»."""
    base = _with_pzz(_base_context())
    base.documents.pzz.zones[0].items = [
        _vri_item(
            "2.1", "Для индивидуального жилищного строительства", area_min=30000
        ),
        _vri_item(
            "2.2", "Для ведения личного подсобного хозяйства", area_max=4000
        ),
    ]
    quick = build_quick_context_from_report(base, rules=rules, guidance=guidance)

    markdown = render_quickreport(quick, today=date(2026, 10, 7))

    assert "участок от 30 000 м²" in markdown
    assert "участок до 4 000 м²" in markdown
    assert "–—" not in markdown


def test_zone_regulations_absent_without_documents(rules, guidance):
    quick = build_quick_context_from_report(
        _base_context(), rules=rules, guidance=guidance
    )
    assert quick.zone_regulations is None
    markdown = render_quickreport(quick, today=date(2026, 10, 7))
    assert "Сведения о территориальной зоне из документа ПЗЗ не получены" in markdown


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
    service = ReportingService(
        store=store, acquisition=acquisition, tile_fetcher=_fake_tile
    )

    result = service.prepare_quickreport("case-quickreport")

    assert result.case_id == "case-quickreport"
    assert result.collection_run_id == receipt.run_id
    assert result.analysis_id == analysis.id
    assert result.methodology_version == "6.1"
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


async def test_prepare_quickreport_builds_map_artifact(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    acquisition = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    )
    await acquisition.collect(
        CollectionRequest(
            case_id="case-quickreport",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
        )
    )
    AnalysisPipeline(store=store).analyze("case-quickreport")
    service = ReportingService(
        store=store, acquisition=acquisition, tile_fetcher=_fake_tile
    )

    result = service.prepare_quickreport("case-quickreport")

    assert result.map_relative_path is not None
    assert result.map_relative_path.startswith("maps/quickreport-")
    map_file = (
        tmp_path / "store" / "cases" / "case-quickreport" / result.map_relative_path
    )
    assert map_file.read_bytes().startswith(b"\x89PNG")
    assert (
        f"![Схема участка и зон (предварительная)](../{result.map_relative_path})"
        in (result.markdown)
    )
    assert "© OpenStreetMap contributors" in result.markdown


async def test_prepare_quickreport_builds_pdf_artifact(tmp_path) -> None:
    pytest.importorskip("weasyprint", reason="extra 'pdf' не установлен")
    store = LocalCaseStore(tmp_path / "store")
    acquisition = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    )
    await acquisition.collect(
        CollectionRequest(
            case_id="case-quickreport",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
        )
    )
    AnalysisPipeline(store=store).analyze("case-quickreport")
    service = ReportingService(
        store=store, acquisition=acquisition, tile_fetcher=_fake_tile
    )

    result = service.prepare_quickreport("case-quickreport")

    assert result.pdf_relative_path is not None
    assert result.pdf_relative_path.startswith("reports/quickreport-")
    pdf_file = (
        tmp_path / "store" / "cases" / "case-quickreport" / result.pdf_relative_path
    )
    assert pdf_file.read_bytes().startswith(b"%PDF")
    assert not any("pdf не построен" in w for w in result.warnings)


async def test_prepare_quickreport_without_pdf_extra_degrades(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalCaseStore(tmp_path / "store")
    acquisition = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    )
    await acquisition.collect(
        CollectionRequest(
            case_id="case-quickreport",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
        )
    )
    AnalysisPipeline(store=store).analyze("case-quickreport")
    service = ReportingService(
        store=store, acquisition=acquisition, tile_fetcher=_fake_tile
    )
    monkeypatch.setitem(sys.modules, "weasyprint", None)  # import → ImportError

    result = service.prepare_quickreport("case-quickreport")

    assert result.pdf_relative_path is None
    assert result.markdown.startswith("# Экспресс-оценка")
    assert any("pdf не построен" in w for w in result.warnings)


async def test_prepare_quickreport_offline_still_builds_map(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    acquisition = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    )
    await acquisition.collect(
        CollectionRequest(
            case_id="case-quickreport",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
        )
    )
    AnalysisPipeline(store=store).analyze("case-quickreport")
    service = ReportingService(
        store=store,
        acquisition=acquisition,
        tile_fetcher=lambda z, x, y, timeout: None,
    )

    result = service.prepare_quickreport("case-quickreport")

    assert result.map_relative_path is not None
    assert "Подложка карты недоступна" in result.markdown
    assert any("подложка" in warning for warning in result.warnings)
