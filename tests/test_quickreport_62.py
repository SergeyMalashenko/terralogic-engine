"""Тесты методики 6.2: каталог ст. 105, универсальная шкала E, Q30, QA-правила."""

from __future__ import annotations

import pytest

from terralogic_engine.qa.validator import validate_quickreport
from terralogic_engine.quickreport import (
    ScoredFactor,
    build_quick_context_from_report,
    compute_verdict,
    load_scoring_rules,
    load_zone_guidance,
    load_zouit_catalog,
)
from terralogic_engine.quickreport.model import build_quick_model
from terralogic_engine.quickreport.render import render_quick_model
from terralogic_engine.quickreport.rules import RegimeEffect, ZouitCatalog

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


def _verdict(rules, scored, **kwargs):
    kwargs.setdefault("u", 10.0)
    kwargs.setdefault("a", 0.0)
    kwargs.setdefault("c", 0.0)
    kwargs.setdefault("stop_reasons", [])
    kwargs.setdefault("suspend_reasons", [])
    kwargs.setdefault("preliminary_flags", [])
    return compute_verdict(rules, scored, **kwargs)


def _q(item: str, group: str, title: str, zone: str) -> ScoredFactor:
    return ScoredFactor(
        group, group, title, zone, "nspd", 86.0, 30.0,
        catalog_item=item, effect_code="Q30", deduction_kind="q",
        regime_state="unknown",
    )


def _d(item: str, group: str, zone: str, deduction: float, code: str) -> ScoredFactor:
    return ScoredFactor(
        group, group, zone, zone, "nspd", 100.0, deduction,
        catalog_item=item, effect_code=code, deduction_kind="d", regime_state="known",
    )


# ---------------------------------------------------------------------------
# §9: условные примеры кейса 50:09:0000000:198995
# ---------------------------------------------------------------------------


def test_62_case_198995_both_regimes_unknown(rules):
    """Qокн=30 + Qавиа=30 + U=10, G=0 → 30/100, предварительная, повышенное внимание."""
    verdict = _verdict(
        rules,
        [
            _q("1", "cultural_heritage", "Зоны охраны ОКН", "Зона регулирования Р-1 (3)"),
            _q("8", "airport", "Приаэродромная территория", "Приаэродромная территория"),
        ],
    )
    assert verdict.score == 30
    assert verdict.preliminary is True
    assert verdict.status.key == "attention"
    assert verdict.q_total == 60


def test_62_case_198995_okn_unknown_airport_confirmed(rules):
    """Qокн=30 + dавиа=15 + U=10 → 45/100, предварительная, повышенное внимание."""
    verdict = _verdict(
        rules,
        [
            _q("1", "cultural_heritage", "Зоны охраны ОКН", "Зона регулирования Р-1 (3)"),
            _d("8", "airport", "Подзона с высотными ограничениями", 15.0,
               "special:airport_subzone_height"),
        ],
    )
    assert verdict.score == 45
    assert verdict.preliminary is True
    assert verdict.status.key == "attention"


def test_62_case_198995_okn_e2(rules):
    """ОКН отвечает E2=15, dавиа=15, U=10 → 60/100."""
    verdict = _verdict(
        rules,
        [
            _d("1", "cultural_heritage", "Зона регулирования Р-1 (3)", 15.0, "E2"),
            _d("8", "airport", "Подзона с высотными ограничениями", 15.0,
               "special:airport_subzone_height"),
        ],
    )
    assert verdict.score == 60
    assert verdict.preliminary is True  # U=10 > 0
    assert verdict.status.key == "attention"


def test_62_case_198995_okn_e3(rules):
    """ОКН отвечает E3=25, dавиа=15, U=10 → 50/100."""
    verdict = _verdict(
        rules,
        [
            _d("1", "cultural_heritage", "Зона регулирования Р-1 (3)", 25.0, "E3"),
            _d("8", "airport", "Подзона с высотными ограничениями", 15.0,
               "special:airport_subzone_height"),
        ],
    )
    assert verdict.score == 50
    assert verdict.status.key == "attention"


def test_62_q_forces_preliminary_and_attention(rules):
    """Q>0: предварительная + повышенное внимание даже без U и малых вычетов (§8)."""
    verdict = _verdict(
        rules,
        [_q("11", "nature_protection", "Охранные зоны ООПТ", "ООПТ Лосиный остров")],
        u=0.0,
    )
    assert verdict.score == 70
    assert verdict.preliminary is True
    assert verdict.status.key == "attention"


def test_62_group_cap_keeps_q_visible(rules):
    """Потолок группы 40: Q видно в q_part и при усечении (§8/§12)."""
    verdict = _verdict(
        rules,
        [
            _q("1", "cultural_heritage", "Зоны охраны ОКН", "Зона А"),
            _d("2", "cultural_heritage", "Зона Б", 25.0, "E3"),
        ],
    )
    group = next(g for g in verdict.groups if g.key == "cultural_heritage")
    assert group.raw_deduction == 55
    assert group.deduction == 40
    assert group.q_part == 30
    assert verdict.score == 50  # 100 − 40 − 10


# ---------------------------------------------------------------------------
# Классификация по каталогу через контекст
# ---------------------------------------------------------------------------


def _base_with(zones, **overrides):
    base = _base_context()
    return base.model_copy(update={"zouit": zones, **overrides})


def test_62_okn_vitrina_merges_into_named_zone(rules, guidance, catalog):
    """Витрина «Зоны и территории ОКН» + именная Р-1(3) → один Q30 (§7/§9)."""
    base = _base_with(
        [
            _zouit("Зоны и территории объектов культурного наследия",
                   "Зоны охраны объектов культурного наследия", 86.4, source="rgis"),
            _zouit(
                "Зона регулирования застройки и хозяйственной деятельности Р-1 (3) "
                "объекта культурного наследия «Усадьба Татищева»",
                "Зона охраны объекта культурного наследия", 86.4,
                registry_number="50:09-6.9999",
            ),
        ]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    protocol = quick.protocol
    heritage = [f for f in protocol.factors if f.group_key == "cultural_heritage"]
    assert len(heritage) == 1
    factor = heritage[0]
    assert factor.effect_code == "Q30"
    assert factor.deduction_kind == "q"
    assert factor.deduction == 30
    assert factor.catalog_item == "1"
    assert factor.registry_number == "50:09-6.9999"
    assert quick.verdict.score == 60  # 100 − 30 − 10(U)
    assert quick.verdict.preliminary is True
    assert quick.verdict.status_key == "attention"
    assert protocol.q_total == 30


def test_62_okn_regime_document_reaches_protocol(rules, guidance, catalog):
    """Акт зоны из документального контура попадает в протокол Q30-фактора."""
    from terralogic_engine.reporting.models import (
        DocumentReportContext,
        ZouitRegimeDocumentContext,
    )

    base = _base_with(
        [
            _zouit(
                "Зона регулирования застройки и хозяйственной деятельности Р-1 (3) "
                "объекта культурного наследия «Усадьба Татищева»",
                "Зона охраны объекта культурного наследия", 86.4,
                registry_number="50:09-6.9999",
            )
        ],
        documents=DocumentReportContext(
            zouit_regimes=[
                ZouitRegimeDocumentContext(
                    registry_number="50:09-6.9999",
                    name="Зона регулирования застройки Р-1 (3)",
                    document_number="1182-ПП",
                    registration_date="2025-10-14",
                )
            ]
        ),
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    factor = quick.protocol.factors[0]
    assert factor.act_number == "1182-ПП"
    assert factor.act_date == "2025-10-14"
    # режим текстом есть, но E не присваивается автоматически — остаётся Q30
    assert factor.effect_code == "Q30"


def test_62_airport_general_layer_is_q30(rules, guidance, catalog):
    """Нерасшифрованная приаэродромная территория → Q30 вместо высотного 15 (§3)."""
    base = _base_with(
        [_zouit("Приаэродромная территория", "Приаэродромная территория", 92.0,
                source="rgis")]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    factors = [f for f in quick.protocol.factors if f.group_key == "airport"]
    assert len(factors) == 1
    assert factors[0].effect_code == "Q30"
    assert factors[0].deduction == 30
    assert quick.verdict.score == 60
    assert quick.verdict.preliminary is True


def test_62_airport_sixth_subzone_keeps_special_rule(rules, guidance, catalog):
    """Шестая подзона — спецправило 6.1 (1/3/5 по охвату), не Q30."""
    base = _base_with(
        [_zouit("Шестая подзона приаэродромной территории а/д Москва (Шереметьево)",
                "Приаэродромная территория", 100.0)]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    factor = quick.protocol.factors[0]
    assert factor.effect_code == "special:airport_subzone_no_height"
    assert factor.deduction == 5
    assert factor.deduction_kind == "d"


def test_62_airport_subzone_needs_sector_for_15(rules, guidance, catalog):
    """Подзона без подтверждённого сектора → Q30; с сектором — спецправило 15 (§3)."""
    base = _base_with(
        [_zouit("Пятая подзона приаэродромной территории а/д Москва (Шереметьево)",
                "Приаэродромная территория", 100.0)]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    assert quick.protocol.factors[0].effect_code == "Q30"

    base_with_sector = _base_with(
        [
            _zouit("Третья подзона приаэродромной территории а/д Москва (Домодедово)",
                   "Приаэродромная территория", 100.0),
            _zouit("Сектор 3.1 третьей подзоны приаэродромной территории а/д Москва "
                   "(Домодедово)", "Приаэродромная территория", 100.0),
        ]
    )
    quick = build_quick_context_from_report(
        base_with_sector, rules=rules, guidance=guidance, catalog=catalog,
        configuration=_CONFIG_OK,
    )
    factors = quick.protocol.factors
    assert len(factors) == 1  # сектор поглощён подзоной, отдельным фактором не считается
    assert factors[0].effect_code == "special:airport_subzone_height"
    assert factors[0].deduction == 15
    assert any("сектор поглощён" in note for note in quick.protocol.classify_notes)


def test_62_airport_general_layer_dropped_when_subzone_present(rules, guidance, catalog):
    """При наличии подзоны общий контур отбрасывается (первичный реестр важнее)."""
    base = _base_with(
        [
            _zouit("Приаэродромная территория", "Приаэродромная территория", 92.0,
                   source="rgis"),
            _zouit("Шестая подзона приаэродромной территории а/д Москва (Шереметьево)",
                   "Приаэродромная территория", 100.0),
        ]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    assert len(quick.protocol.factors) == 1
    assert quick.protocol.factors[0].effect_code == "special:airport_subzone_no_height"
    assert any("замещён подзоной" in note for note in quick.protocol.classify_notes)


def test_62_oopt_universal_q30(rules, guidance, catalog):
    """ООПТ — группа природоохранных ограничений, универсальная шкала → Q30."""
    base = _base_with(
        [_zouit("границы охранной зоны ООПТ Национальный парк «Лосиный остров»",
                "Охранная зона особо охраняемого природного объекта", 92.0)]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    factor = quick.protocol.factors[0]
    assert factor.group_key == "nature_protection"
    assert factor.catalog_item == "11"
    assert factor.effect_code == "Q30"
    assert quick.verdict.score == 60


def test_62_utility_infra_classified_q30(rules, guidance, catalog):
    """«Охранная зона инженерных коммуникаций» — внекаталожный класс → Q30."""
    base = _base_with(
        [_zouit("Охранная зона Ф.6", "Охранная зона инженерных коммуникаций", 29.4)]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    factor = quick.protocol.factors[0]
    assert factor.group_key == "utility_infra"
    assert factor.catalog_item == "utility_infra"
    assert factor.effect_code == "Q30"
    assert quick.verdict.score == 60


def test_62_unclassifiable_zone_blocks_score(rules, guidance, catalog):
    """Зона вне каталога без надёжной классификации → BLOCKING_UNKNOWN (§6)."""
    base = _base_with(
        [_zouit("Полоса кватарных ограничений", "Полоса кватарных ограничений", 50.0)]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    assert quick.verdict.score is None
    assert quick.verdict.status_key == "insufficient"
    assert quick.protocol.unmatched_zones == ["Полоса кватарных ограничений"]


def test_62_melioration_lands_excluded_not_zouit(rules, guidance, catalog):
    """«Мелиорируемые земли» (категория карточки РГИС) — не ЗОУИТ ст. 105:
    исключается из скоринга и таблицы факторов с протокольной пометкой."""
    base = _base_with([_zouit("Мелиорируемые земли", None, 100.0, source="rgis")])
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    assert quick.verdict.score == 90  # 100 − U(10), вычетов нет
    assert quick.protocol.unmatched_zones == []
    assert all(
        factor.zone_name != "Мелиорируемые земли" for factor in quick.factors
    )
    assert any("не является ЗОУИТ" in note for note in quick.warnings)


def test_62_sanitary_water_supply_hits_special_rule(rules, guidance, catalog):
    """«Зона санитарной охраны» — ЗСО водоснабжения (п. 16, спецправило 6.1),
    а не округа лечебных ресурсов (п. 15): частное раньше общего."""
    base = _base_with(
        [_zouit("Зона санитарной охраны", "Зона санитарной охраны", 100.0)]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    factor = quick.protocol.factors[0]
    assert factor.catalog_item == "16"
    assert factor.deduction == 30
    assert factor.deduction_kind == "d"
    assert quick.verdict.score == 60  # 100 − 30 − U(10)


def test_62_sanitary_resort_district_stays_universal(rules, guidance, catalog):
    """«Округ санитарной охраны» курорта — п. 15, универсальная шкала → Q30."""
    base = _base_with(
        [_zouit("Округ санитарной охраны курорта «Сестрорецк»",
                "Округа санитарной и горно-санитарной охраны", 40.0)]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    factor = quick.protocol.factors[0]
    assert factor.catalog_item == "15"
    assert factor.effect_code == "Q30"


def test_62_regime_effect_assigns_e_level(rules, guidance):
    """Подтверждённый режим из курируемой таблицы → E-уровень (d), не Q30."""
    catalog = load_zouit_catalog()
    custom = ZouitCatalog(
        entries=catalog.entries,
        groups=catalog.groups,
        regime_effects=(
            RegimeEffect(
                effect="E2",
                evidence="акт № 1182-ПП, требования к регламентам застройки",
                registry_number="50:09-6.9999",
            ),
        ),
    )
    base = _base_with(
        [_zouit("Зона регулирования застройки Р-1 (3) ОКН «Усадьба Татищева»",
                "Зона охраны объекта культурного наследия", 86.4,
                registry_number="50:09-6.9999")]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=custom, configuration=_CONFIG_OK
    )
    factor = quick.protocol.factors[0]
    assert factor.effect_code == "E2"
    assert factor.deduction == 15
    assert factor.deduction_kind == "d"
    assert factor.regime_state == "known"
    assert quick.verdict.score == 75  # 100 − 15 − 10
    assert quick.protocol.q_total == 0


def test_62_catalog_coverage_in_protocol(rules, guidance, catalog):
    """Протокол несёт покрытие каталога: найденные классы и пустые."""
    base = _base_with(
        [_zouit("Водоохранная зона р. Банка", "Водоохранная зона", 100.0)]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    coverage = {c.item: c.state for c in quick.protocol.catalog_coverage}
    assert coverage["13"] == "found"
    assert coverage["8"] == "none_found"
    assert len(quick.protocol.catalog_coverage) == len(catalog.entries)


# ---------------------------------------------------------------------------
# QA-валидатор (6.2 §10)
# ---------------------------------------------------------------------------


def _qa_model_and_markdown(quick):
    model = build_quick_model(quick, marketing=None)
    markdown = render_quick_model(model)
    return model, markdown


def test_62_validator_passes_consistent_q30_report(rules, guidance, catalog):
    base = _base_with(
        [_zouit("Зоны и территории объектов культурного наследия",
                "Зоны охраны объектов культурного наследия", 86.4, source="rgis")]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    model, markdown = _qa_model_and_markdown(quick)
    result = validate_quickreport(model, markdown, context=quick)
    assert result.status == "pass", [i.message for i in result.issues]


def test_62_validator_q30_without_score_is_fine(rules, guidance, catalog):
    """Q в протоколе при невыданном балле (BLOCKING_UNKNOWN) — не ошибка (§2 > §8)."""
    from terralogic_engine.reporting.models import UrbanPlanningReportContext

    base = _base_with(
        [_zouit("Приаэродромная территория", "Приаэродромная территория", 92.0,
                source="rgis")],
    )
    base = base.model_copy(
        update={"urban_planning": UrbanPlanningReportContext(collected=False)}
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=None
    )
    assert quick.verdict.score is None
    assert quick.protocol.q_total == 30
    model, markdown = _qa_model_and_markdown(quick)
    result = validate_quickreport(model, markdown, context=quick)
    assert not any(i.rule_id == "q30_requires_preliminary" for i in result.issues)


def test_62_validator_catches_q30_without_preliminary(rules, guidance, catalog):
    base = _base_with(
        [_zouit("Зоны и территории объектов культурного наследия",
                "Зоны охраны объектов культурного наследия", 86.4, source="rgis")]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    model, markdown = _qa_model_and_markdown(quick)
    broken = model.model_copy(
        update={"status": model.status.model_copy(update={"preliminary": False})}
    )
    result = validate_quickreport(broken, markdown, context=quick)
    assert any(i.rule_id == "q30_requires_preliminary" for i in result.issues)


def test_62_validator_catches_duplicate_zone(rules, guidance, catalog):
    base = _base_with(
        [_zouit("Зоны и территории объектов культурного наследия",
                "Зоны охраны объектов культурного наследия", 86.4, source="rgis")]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    protocol = quick.protocol
    duplicate = protocol.factors[0]
    broken_protocol = protocol.model_copy(
        update={"factors": [*protocol.factors, duplicate]}
    )
    broken_quick = quick.model_copy(update={"protocol": broken_protocol})
    model, markdown = _qa_model_and_markdown(quick)
    result = validate_quickreport(model, markdown, context=broken_quick)
    assert any(i.rule_id == "no_duplicate_factor" for i in result.issues)


def test_62_validator_catches_deduction_without_basis(rules, guidance, catalog):
    base = _base_with(
        [_zouit("Зоны и территории объектов культурного наследия",
                "Зоны охраны объектов культурного наследия", 86.4, source="rgis")]
    )
    quick = build_quick_context_from_report(
        base, rules=rules, guidance=guidance, catalog=catalog, configuration=_CONFIG_OK
    )
    protocol = quick.protocol
    broken_factor = protocol.factors[0].model_copy(
        update={"effect_code": "", "catalog_item": ""}
    )
    broken_protocol = protocol.model_copy(update={"factors": [broken_factor]})
    broken_quick = quick.model_copy(update={"protocol": broken_protocol})
    model, markdown = _qa_model_and_markdown(quick)
    result = validate_quickreport(model, markdown, context=broken_quick)
    assert any(i.rule_id == "deduction_has_basis" for i in result.issues)
