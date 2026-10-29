"""Тесты продуктовой модели QuickReport (§34, §37–41, §83–85 ТЗ)."""

from __future__ import annotations

from datetime import date

import pytest

from terralogic_engine.quickreport import (
    build_quick_context_from_report,
    build_quick_model,
    load_marketing,
    render_quick_model,
    render_quickreport,
)
from terralogic_engine.quickreport.rules import marketing_sha256
from terralogic_engine.reporting.models import VriReportContext

from .test_quickreport import _CONFIG_OK, _base_context


@pytest.fixture()
def quick(rules, guidance):
    return build_quick_context_from_report(
        _base_context(), rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )


@pytest.fixture()
def rules():
    from terralogic_engine.quickreport import load_scoring_rules

    return load_scoring_rules()


@pytest.fixture()
def guidance():
    from terralogic_engine.quickreport import load_zone_guidance

    return load_zone_guidance()


# ---------------------------------------------------------------------------
# Провенанс: KNOWN / DERIVED / TO_VERIFY и origin-разметка (§21–22, §28–31, §41)
# ---------------------------------------------------------------------------


def test_factor_origins_are_marked(quick):
    zone_factor = next(f for f in quick.factors if not f.informational)
    assert zone_factor.restriction_origin == "SOURCE"
    assert zone_factor.coverage_origin == "DERIVED"
    assert zone_factor.meaning_origin == "STATIC"


def test_passport_vri_origin(rules, guidance):
    base = _base_context()
    base.parcel.permitted_use = "ИЖС"
    base.parcel.permitted_use_vri = None
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )
    # только текст источника — без обогащения классификатором
    assert quick.parcel.vri_origin == "SOURCE"

    base.parcel.permitted_use_vri = VriReportContext(
        code="2.1",
        name="Для индивидуального жилищного строительства",
        description="",
        act_number="П/0412",
        revision_date="2026-06-02",
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=_CONFIG_OK
    )
    assert quick.parcel.vri_origin == "LEGAL"
    # source fact сохранён после legal-обогащения (§11, §83)
    assert quick.parcel.permitted_use == "ИЖС"
    assert quick.parcel.vri.code == "2.1"


def test_surroundings_distance_kind(quick):
    assert quick.surroundings.distance_kind == "straight_line"


# ---------------------------------------------------------------------------
# Missing Information как модель (§34)
# ---------------------------------------------------------------------------


def test_missing_items_collected_deterministically(quick):
    keys = [item.key for item in quick.missing]
    # все три коммуникации неизвестны (U > 0), подъезд — всегда пробел
    assert "comm_electricity" in keys
    assert "comm_water" in keys
    assert "comm_sewage" in keys
    assert "access_unchecked" in keys
    # геометрия проверена (_CONFIG_OK), зонирование известно — этих пробелов нет
    assert "geometry_unknown" not in keys
    assert "no_zoning" not in keys
    assert all(item.state == "TO_VERIFY" for item in quick.missing)


def test_missing_items_reflect_evidence_gaps(rules, guidance):
    base = _base_context()
    base.urban_planning.parcel_zones = []
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, configuration=None
    )
    keys = [item.key for item in quick.missing]
    assert "geometry_unknown" in keys  # C неизвестен без проверки
    assert "no_zoning" in keys  # сведений о зонировании нет


# ---------------------------------------------------------------------------
# QuickReportModel (§40) и семантические состояния (§85)
# ---------------------------------------------------------------------------


def test_build_quick_model_structure(quick):
    model = build_quick_model(quick)
    assert model.identity.cadastral_number == "50:11:0020310:49"
    assert model.identity.methodology_version == "6.3"
    assert model.score == quick.verdict.score == 55
    assert model.main_conclusion == quick.verdict.status_summary
    assert model.attention_factors == quick.factors
    assert model.missing_information == quick.missing
    assert model.limitations  # статический disclaimer
    assert model.marketing is None  # без конфигурации блока нет
    # established_facts — короткая сводка, а не второй паспорт или копия таблицы зон
    assert len(model.established_facts) <= 4
    assert model.established_facts == [
        "По кадастровым сведениям: площадь 1 000 м²; разрешённое использование — ИЖС.",
        "Территориальная зона участка установлена.",
        "Пространственный анализ выполнен: факторов, требующих внимания, — 2.",
        "Выполнен предварительный геометрический тест конфигурации участка.",
    ]
    markdown = render_quickreport(quick, today=date(2026, 10, 7))
    established = markdown.split("## Что уже удалось установить", 1)[1].split(
        "## Факторы, требующие внимания", 1
    )[0]
    for duplicated in (
        "Адрес (местоположение)",
        "Категория земель:",
        "Кадастровая стоимость:",
        "Водоохранная зона:",
        "(100%)",
    ):
        assert duplicated not in established
    assert "| Разрешённое использование | ИЖС |" in markdown
    assert "| 1 | Водоохранная зона | Практически весь участок |" in markdown
    # TO_VERIFY-формулировки не превращаются в категорические (§85)
    uncertainty_marks = ("не установлены", "не проверен", "не получен", "требуется", "Нет сведений")
    for item in model.missing_information:
        assert any(mark in item.text for mark in uncertainty_marks), item.text
        assert "отсутствует" not in item.text
        assert "коммуникаций нет" not in item.text.casefold()


def test_to_verify_not_rendered_as_categorical(quick):
    markdown = render_quickreport(quick, today=date(2026, 10, 7))
    assert "## Что ещё нужно проверить" in markdown
    assert "Сведения об электроснабжении участка не установлены" in markdown
    assert "Фактический подъезд к участку не проверен" in markdown
    # запрет клиентской формулировки «коммуникаций нет» (п. 4.2 методики)
    assert "коммуникаций нет" not in markdown.casefold()


# ---------------------------------------------------------------------------
# MarketingConfig (§37–39, §83)
# ---------------------------------------------------------------------------


def test_marketing_config_loads():
    config = load_marketing()
    assert config.sha256 == marketing_sha256()
    detailed = next(s for s in config.services if s.key == "detailed_report")
    assert detailed.price_rub == 30000
    assert detailed.credit_rub == 3000
    selection = next(s for s in config.services if s.key == "parcel_selection")
    assert selection.price_from_rub == 45000
    assert config.cta


def test_marketing_block_rendered_from_config(quick):
    config = load_marketing()
    model = build_quick_model(quick, marketing=config)
    assert model.marketing is not None
    markdown = render_quick_model(
        model, today=date(2026, 10, 7), zone_regulations=quick.zone_regulations
    )
    # цены в отчёте — из конфигурации, не из кода (§83)
    assert "## Следующий шаг" in markdown
    assert "30 000 ₽" in markdown
    assert "27 000 ₽" in markdown  # с зачётом экспресс-отчёта
    assert "от 45 000 ₽" in markdown
    assert config.cta.split("\n")[0].strip()[:20] in markdown
    # маркетинг идёт после дисклеймера
    assert markdown.index("## Ограничения отчёта") < markdown.index("## Следующий шаг")


def test_marketing_absent_without_config(quick):
    markdown = render_quickreport(quick, today=date(2026, 10, 7))
    assert "## Следующий шаг" not in markdown
    assert "30 000 ₽" not in markdown


def test_marketing_not_in_report_context(quick):
    """ReportContext/QuickReportContext не несут коммерческих полей (§37)."""
    assert not hasattr(quick, "marketing")
    assert "marketing" not in type(quick).model_fields


# ---------------------------------------------------------------------------
# Golden: структура продукта и байт-стабильность (§84)
# ---------------------------------------------------------------------------


def test_full_product_structure_golden(quick):
    config = load_marketing()
    first = render_quickreport(quick, today=date(2026, 10, 7), marketing=config)
    second = render_quickreport(quick, today=date(2026, 10, 7), marketing=config)
    # детерминированные секции байт-стабильны при фиксированной дате
    assert first == second
    for section in (
        "# Экспресс-оценка земельного участка",
        "## Оценка участка — 55/100",
        "## Что уже удалось установить",
        "## Факторы, требующие внимания",
        "## Паспорт участка",
        "## Территориальная зона и разрешённое использование",
        "## Окружение",
        "## Схема расположения зон",
        "## Ограничения отчёта",
        "## Что ещё нужно проверить",
        "## Следующий шаг",
        "## Права и использование отчёта",
    ):
        assert section in first, section
    # порядок секций — продуктовый (§23): карта с ограничениями отчёта —
    # рядом с паспортом и окружением, «Что ещё нужно проверить» — после карты
    assert first.index("## Что уже удалось установить") < first.index(
        "## Факторы, требующие внимания"
    )
    assert first.index("## Окружение") < first.index("## Схема расположения зон")
    assert first.index("## Схема расположения зон") < first.index(
        "## Ограничения отчёта"
    )
    assert first.index("## Ограничения отчёта") < first.index(
        "## Что ещё нужно проверить"
    )
