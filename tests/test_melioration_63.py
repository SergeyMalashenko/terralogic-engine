"""Тесты методики 6.3: специальный маршрут мелиорации §6.1.

INFO_MELIORATION — только общий информационный слой (нет данных
reclamation РГИС): вне суммы вычетов, предварительная оценка.
Подтверждённая система (тип Орошение/Осушение) при неизвестном режиме —
Q30 в группе melioration_influence. is_valuable=да — отдельный Q30 за
перечень особо ценных угодий (§6.1.5), потолок группы 40.
"""

from __future__ import annotations

import pytest

from terralogic_engine.quickreport import (
    build_quick_context_from_report,
    load_scoring_rules,
    load_zone_guidance,
    load_zouit_catalog,
)
from terralogic_engine.reporting.models import ReclamationReportContext

from .test_quickreport import _CONFIG_OK, _base_context, _zouit


@pytest.fixture()
def rules():
    return load_scoring_rules()


@pytest.fixture()
def guidance():
    return load_zone_guidance()


@pytest.fixture()
def catalog():
    return load_zouit_catalog()


def _melioration_base(reclamation: list[ReclamationReportContext] | None):
    base = _base_context()
    return base.model_copy(
        update={
            "zouit": [_zouit("Мелиорируемые земли", None, 100.0, source="rgis")],
            "reclamation": reclamation or [],
        }
    )


def _build(base, rules, guidance, catalog, **kwargs):
    kwargs.setdefault("configuration", _CONFIG_OK)
    return build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, **kwargs
    )


def test_63_reclamation_system_unknown_regime_q30(rules, guidance, catalog):
    """Подтверждена система (Осушение 100%), режим неизвестен → Q30,
    предварительная оценка, повышенное внимание (§14, строка 4)."""
    base = _melioration_base(
        [ReclamationReportContext(reclamation_type="Осушение", parcel_coverage_percent=100.0)]
    )
    quick = _build(base, rules, guidance, catalog)
    assert quick.verdict.score == 60  # 100 − Q30 − U(10)
    assert quick.verdict.preliminary is True
    assert quick.verdict.status_key == "attention"
    factor = quick.protocol.factors[0]
    assert factor.effect_code == "Q30"
    assert factor.deduction_kind == "q"
    assert factor.group_key == "melioration_influence"
    assert "Осушение" in factor.factor_title
    assert factor.contributes_to_score is True
    assert "не установлен" in factor.decision_reason
    # перечень не проверен: is_valuable не получен — отдельного Q30 нет
    assert len(quick.protocol.factors) == 1
    assert all(
        factor.zone_name != "Перечень особо ценных с/х угодий"
        for factor in quick.protocol.factors
    )


def test_63_valuable_list_yes_second_q30_group_cap(rules, guidance, catalog):
    """is_valuable=да → Q30 системы + Q30 перечня в одной группе;
    сумма 60 усечена потолком 40 (§14: две нагрузки, потолок группы)."""
    base = _melioration_base(
        [
            ReclamationReportContext(
                reclamation_type="Орошение",
                parcel_coverage_percent=100.0,
                is_valuable="да",
            )
        ]
    )
    quick = _build(base, rules, guidance, catalog)
    assert quick.verdict.score == 50  # 100 − min(40; 30+30) − U(10)
    assert quick.verdict.preliminary is True
    assert quick.verdict.status_key == "attention"
    factors = quick.protocol.factors
    assert len(factors) == 2
    valuable = next(
        factor for factor in factors if factor.zone_name.startswith("Перечень")
    )
    assert valuable.effect_code == "Q30"
    assert "противоречие" in valuable.decision_reason
    group = next(
        group
        for group in quick.protocol.groups
        if group.key == "melioration_influence"
    )
    assert group.raw_deduction == 60
    assert group.deduction == 40
    # строка перечня видна в таблице факторов отчёта
    assert any(factor.rule_id == "valuable_list" for factor in quick.factors)
    # перечень проверен → пробела valuable_list_unchecked нет
    assert all(item.key != "valuable_list_unchecked" for item in quick.missing)


def test_63_valuable_list_not_member(rules, guidance, catalog):
    """is_valuable=нет → только Q30 системы; перечень без вычета, с протокольной
    пометкой (§6.1.5: отрицательный результат имеет источник и дату)."""
    base = _melioration_base(
        [
            ReclamationReportContext(
                reclamation_type="Орошение",
                parcel_coverage_percent=100.0,
                is_valuable="нет",
            )
        ]
    )
    quick = _build(base, rules, guidance, catalog)
    assert quick.verdict.score == 60  # 100 − Q30 − U(10)
    assert len(quick.protocol.factors) == 1
    assert any("не входит" in note for note in quick.warnings)


def test_63_info_melioration_is_not_e0(rules, guidance, catalog):
    """INFO_MELIORATION — не E0 и не «записей не выявлено»: вне балла,
    но запись видима и оценка предварительная (§6.1.3, §6.1.6)."""
    quick = _build(_melioration_base(None), rules, guidance, catalog)
    factor = quick.protocol.factors[0]
    assert factor.effect_code == "INFO_MELIORATION"
    assert factor.deduction_kind == "info"
    assert factor.contributes_to_score is False
    assert factor.deduction == 0.0
    assert quick.protocol.q_total == 0.0
    assert quick.verdict.score == 90  # 100 − U(10)
    assert quick.verdict.preliminary is True


def test_63_info_melioration_unknown_c_blocks_score(rules, guidance, catalog):
    """Общий слой + неизвестный C → без балла по C (§14, строка 2):
    INFO_MELIORATION не отменяет иных существенных пробелов."""
    quick = _build(
        _melioration_base(None), rules, guidance, catalog, configuration=None
    )
    assert quick.verdict.score is None
    assert quick.verdict.status_key == "insufficient"


def test_63_layer_plus_reclamation_single_carrier(rules, guidance, catalog):
    """Зона parcel_zouit (code 56) + записи reclamation — один фактор-носитель
    мелиорации, без двойного счёта (§7)."""
    base = _melioration_base(
        [
            ReclamationReportContext(
                reclamation_type="Осушение",
                parcel_coverage_percent=85.0,
            ),
            ReclamationReportContext(
                reclamation_type="Орошение",
                parcel_coverage_percent=6.9,
            ),
        ]
    )
    quick = _build(base, rules, guidance, catalog)
    melioration = [
        factor
        for factor in quick.protocol.factors
        if factor.catalog_item == "melioration_lands"
    ]
    assert len(melioration) == 1
    assert "Осушение" in melioration[0].factor_title
    assert "Орошение" in melioration[0].factor_title
    assert quick.verdict.score == 60  # один Q30, не два
