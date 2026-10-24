"""Сборка контекста QuickReport из отчётного контекста кейса.

Источник данных — `reporting.build_report_context` (immutable run кейса);
здесь только производные: факторы с текстами guidance, скоринг методики 6.2
(каталог классов ст. 105 ЗК РФ, универсальная шкала E0–E4, Q30 при
неизвестном режиме, U коммуникаций, A площади, C конфигурации),
человекочитаемое окружение. Разделено на чистую часть
(`build_quick_context_from_report`, тестируется без store; геометрическая
проверка передаётся готовым `ConfigurationOutcome`) и store-обёртку
(`build_quick_context` — добывает контур участка и считает C).
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from terralogic_engine.reporting.context import build_report_context
from terralogic_engine.reporting.models import (
    ReportContext,
    ZouitRegimeDocumentContext,
    ZouitReportContext,
)
from terralogic_engine.store.base import CaseStore

from .geometry import ConfigurationOutcome, evaluate_configuration
from .models import (
    QuickFactor,
    QuickMissingItem,
    QuickPassport,
    QuickProtocol,
    QuickProtocolArea,
    QuickProtocolCheck,
    QuickProtocolClassState,
    QuickProtocolComm,
    QuickProtocolFactor,
    QuickProtocolGeometry,
    QuickProtocolGroup,
    QuickProtocolSource,
    QuickReportContext,
    QuickSurroundings,
    QuickVerdict,
    QuickZoneRegulations,
    QuickZoneUse,
)
from .rules import (
    GroupDeduction,
    ScoredFactor,
    ScoringRules,
    VerdictResult,
    ZoneGuidance,
    ZouitCatalog,
    compute_verdict,
    load_missing_texts,
    load_scoring_rules,
    load_zone_guidance,
    load_zouit_catalog,
    match_guidance,
    methodology_sha256,
)

_MAX_SURROUNDING_LINES = 6
_HOUSING_CODE_PREFIXES = ("1", "2")
_NUMBER_RE = re.compile(r"\d[\d ]*(?:[.,]\d+)?")


def _is_housing_use(code: str | None) -> bool:
    """Коды классификатора ВРИ групп 1.* (садоводство/огородничество) и 2.* (жильё)."""
    if not code:
        return False
    head = code.lstrip().split(".", 1)[0].rstrip("*")
    return head in _HOUSING_CODE_PREFIXES


def _zone_regulations(base: ReportContext) -> QuickZoneRegulations | None:
    """ВРИ территориальной зоны участка из документального контура (ПЗЗ)."""
    documents = base.documents
    if documents is None or documents.pzz is None:
        return None
    found = [zone for zone in documents.pzz.zones if zone.found]
    if not found:
        return None
    passport_zone = base.urban_planning.parcel_zones[0].zone if (
        base.urban_planning.parcel_zones
    ) else None
    zone = next(
        (item for item in found if item.zone_code == passport_zone),
        found[0],
    )
    housing = [
        QuickZoneUse(
            code=item.code,
            name=item.name,
            area_min=item.area_min,
            area_max=item.area_max,
            building_percentage=item.building_percentage,
            margin=item.margin,
        )
        for item in zone.items
        if _is_housing_use(item.code)
    ]
    # одна и та же строка ВРИ встречается в пересекающихся таблицах документа
    seen: set[tuple[str | None, str | None]] = set()
    unique_housing: list[QuickZoneUse] = []
    for use in housing:
        key = (use.code, use.name)
        if key in seen:
            continue
        seen.add(key)
        unique_housing.append(use)
    return QuickZoneRegulations(
        zone_code=zone.zone_code,
        doc_number=documents.pzz.number,
        doc_version_date=documents.pzz.version_date,
        total_uses=len(zone.items),
        housing_uses=unique_housing,
    )


# ---------------------------------------------------------------------------
# Факторы для таблицы отчёта (guidance-тексты; на балл влияет скоринг ниже)
# ---------------------------------------------------------------------------


def _factors(
    base: ReportContext,
    rules: ScoringRules,
    guidance: ZoneGuidance,
    warnings: list[str],
    *,
    unknown_regime: set[str],
    catalog: ZouitCatalog,
) -> list[QuickFactor]:
    """Строки таблицы факторов — по одной на тип зоны.

    Записи одной зоны под кратким и полным наименованием, дубли слоёв и
    источников схлопываются в одну строку с коротким каноническим
    названием (title из zone_guidance); охват — максимальный среди
    объединённых записей (консервативный показ). Для зон с неизвестным
    режимом (Q30) текст — вариант «неопределённость» (6.2 §8/§12), а не
    описание подтверждённого режима. Зона без записи guidance получает
    строку из класса каталога (универсальная шкала).
    """
    collapsed: dict[str, QuickFactor] = {}
    for zone in base.zouit:
        entry = match_guidance(guidance, zone_type=zone.zone_type, name=zone.name)
        catalog_entry = catalog.classify(zone.zone_type, zone.name)
        if catalog_entry is not None and catalog_entry.handling == "not_zouit":
            continue
        if entry is None:
            if catalog_entry is None:
                warnings.append(f"зона без текста guidance: {zone.name}")
                continue
            key = f"catalog:{catalog_entry.item}"
            title = catalog_entry.class_name
        else:
            key = entry.key
            title = entry.title
        is_unknown_regime = key in unknown_regime or (
            catalog_entry is not None
            and f"catalog:{catalog_entry.item}" in unknown_regime
        )
        if is_unknown_regime:
            guidance_text = (
                entry.guidance_unknown_regime
                if entry is not None and entry.guidance_unknown_regime
                else rules.texts.get("q30_factor_guidance", "")
            )
            action = (
                entry.action if entry is not None else None
            ) or rules.texts.get("q30_factor_action")
        else:
            guidance_text = entry.guidance if entry is not None else ""
            action = entry.action if entry is not None else None
        coverage = zone.parcel_coverage_percent
        existing = collapsed.get(key)
        if existing is None or coverage > existing.coverage_percent:
            collapsed[key] = QuickFactor(
                rule_id=key,
                title=title,
                zone_name=title,
                coverage_percent=coverage,
                coverage_word=rules.coverage_word(coverage),
                guidance=guidance_text,
                action=action,
                informational=False,
            )
    factors = list(collapsed.values())
    comm_note = guidance.misc.get("communications")
    if comm_note:
        factors.append(
            QuickFactor(
                rule_id="communications_unknown",
                title="Коммуникации не проверены",
                zone_name="—",
                coverage_percent=0.0,
                coverage_word="—",
                guidance=comm_note,
                informational=True,
            )
        )
    factors.sort(key=lambda f: (f.informational, -f.coverage_percent))
    return factors


# ---------------------------------------------------------------------------
# Скоринг 6.2: классификация зон в группы, U, A, достаточность
# ---------------------------------------------------------------------------


def _zone_merge_key(zone: ZouitReportContext) -> str:
    """Ключ слияния: реестровый номер, иначе нормализованное название (п. 3.1)."""
    registry = (zone.registry_number or "").strip()
    if registry:
        return registry
    return " ".join(zone.name.casefold().split())


def _zone_tokens(name: str) -> tuple[str, ...]:
    """Нормализованные токены наименования зоны (casefold, одиночные пробелы)."""
    return tuple(" ".join(name.casefold().split()).split())


def _merge_short_full_names(
    parts: dict[str, dict[str, float]],
    representative: dict[str, ZouitReportContext],
    registry_of: dict[str, str | None],
    notes: list[str],
) -> None:
    """Слияние краткого и полного наименований одной зоны (п. 3.1).

    Краткое наименование — токен-префикс полного («Водоохранная зона» ⊂
    «Водоохранная зона р. Пахра»): записи описывают одну зону под кратким
    (витрина) и полным (реестр) описанием и объединяются, охват — максимум,
    представитель — полное наименование. Разные зоны одного типа («…р. Пахра»
    и «…р. Москва») префиксом не связаны и не объединяются. Конфликт
    реестровых номеров (оба есть и различаются) запрещает слияние; краткое
    наименование-префикс нескольких полных остаётся отдельной записью.
    """
    for short_key in sorted(
        parts, key=lambda key: len(_zone_tokens(representative[key].name))
    ):
        if short_key not in parts:
            continue
        short_tokens = _zone_tokens(representative[short_key].name)
        if len(short_tokens) < 2:
            continue
        candidates: list[str] = []
        for key, zone in representative.items():
            if key == short_key or key not in parts:
                continue
            tokens = _zone_tokens(zone.name)
            is_prefix = (
                len(tokens) > len(short_tokens)
                and tokens[: len(short_tokens)] == short_tokens
            )
            if not is_prefix:
                continue
            short_reg = registry_of.get(short_key)
            long_reg = registry_of.get(key)
            if short_reg and long_reg and short_reg != long_reg:
                continue
            candidates.append(key)
        if len(candidates) != 1:
            if len(candidates) > 1:
                notes.append(
                    f"краткое наименование {representative[short_key].name!r} "
                    "соответствует нескольким зонам — оставлено отдельной записью"
                )
            continue
        long_key = candidates[0]
        for source, coverage in parts.pop(short_key).items():
            slots = parts[long_key]
            slots[source] = max(slots.get(source, 0.0), coverage)
        notes.append(
            f"слияние краткого и полного наименования: "
            f"{representative[short_key].name!r} объединено с {representative[long_key].name!r}"
        )
        registry_of[long_key] = registry_of.get(long_key) or registry_of.get(short_key)


def _drop_category_echoes(
    parts: dict[str, dict[str, float]],
    representative: dict[str, ZouitReportContext],
    registry_of: dict[str, str | None],
    notes: list[str],
) -> None:
    """Слой-витрина, повторяющий тип первичной записи реестра, отбрасывается.

    Витрина (запись без реестрового номера) отдаёт пересечение под кратким
    наименованием категории («Охранная зона транспорта», «Зоны охраны
    объектов культурного наследия»), которое совпадает с типом zone_type
    первичной записи реестра ЗОУИТ. Это краткое описание того же объекта,
    а не самостоятельная зона: первичная запись важнее витрины (п. 3.1).
    Совпадение только по охвату без совпадения наименования основанием
    не является — разные зоны могут иметь равный охват.
    """
    registered_types = {
        " ".join(zone.zone_type.casefold().split())
        for key, zone in representative.items()
        if key in parts and registry_of.get(key) and (zone.zone_type or "").strip()
    }
    for key in list(parts):
        if registry_of.get(key):
            continue
        zone = representative[key]
        name_norm = " ".join(zone.name.casefold().split())
        if name_norm and name_norm in registered_types:
            coverage = max(parts.pop(key).values())
            notes.append(
                f"слой {zone.name!r} ({coverage:g}%) совпадает с типом первичной "
                "записи реестра — краткое наименование той же зоны, "
                "отдельным фактором не считается"
            )


def _regime_document(
    base: ReportContext, zone: ZouitReportContext
) -> ZouitRegimeDocumentContext | None:
    """Акт, устанавливающий зону: по реестровому номеру, иначе по имени."""
    documents = base.documents
    if documents is None:
        return None
    registry = (zone.registry_number or "").strip()
    if registry:
        for regime in documents.zouit_regimes:
            if (regime.registry_number or "").strip() == registry:
                return regime
    folded = " ".join(zone.name.casefold().split())
    for regime in documents.zouit_regimes:
        if regime.name and " ".join(regime.name.casefold().split()) == folded:
            return regime
    return None


_AIRPORT_ORDINALS = (
    ("перв", 1),
    ("втор", 2),
    ("трет", 3),
    ("четверт", 4),
    ("четвёрт", 4),
    ("пят", 5),
    ("шест", 6),
    ("седьм", 7),
    ("восьм", 8),
)


def _airport_ordinal(name: str) -> int | None:
    """Номер приаэродромной подзоны из наименования записи (None — не подзона)."""
    folded = name.casefold()
    marker = folded.find("подзон")
    if marker < 0:
        return None
    head = folded[:marker]
    for stem, number in _AIRPORT_ORDINALS:
        if stem in head:
            return number
    return None


def _special_factor(
    rules: ScoringRules,
    guidance: ZoneGuidance,
    *,
    catalog_item: str,
    zone: ZouitReportContext,
    coverage: float,
    factor_id: str,
    act: ZouitRegimeDocumentContext | None,
    notes: list[str],
) -> ScoredFactor | None:
    """Фактор сохранённого спецправила 6.1 (d — подтверждённое влияние)."""
    pair = rules.factor_by_id(factor_id)
    if pair is None:
        notes.append(f"каталог ссылается на отсутствующее спецправило {factor_id!r}")
        return None
    group, rule = pair
    entry = match_guidance(guidance, zone_type=zone.zone_type, name=zone.name)
    return ScoredFactor(
        group_key=group.key,
        group_title=group.title,
        factor_title=rule.title,
        zone_name=zone.name,
        source=str(getattr(zone.source, "value", zone.source)),
        coverage=coverage,
        deduction=rule.deduction_for(coverage),
        display_name=entry.title if entry is not None else rule.title,
        catalog_item=catalog_item,
        effect_code=f"special:{rule.id}",
        deduction_kind="d",
        regime_state="known",
        act_number=act.document_number if act else None,
        act_date=(act.registration_date if act else None),
        registry_number=zone.registry_number,
    )


def _q30_factor(
    rules: ScoringRules,
    guidance: ZoneGuidance,
    catalog: ZouitCatalog,
    *,
    catalog_item: str,
    zone: ZouitReportContext,
    coverage: float,
    act: ZouitRegimeDocumentContext | None,
) -> ScoredFactor:
    """Q30: пересечение подтверждено, существенный режим зоны неизвестен (§12)."""
    entry = match_guidance(guidance, zone_type=zone.zone_type, name=zone.name)
    group_key = _catalog_group_key(catalog, catalog_item)
    return ScoredFactor(
        group_key=group_key,
        group_title=catalog.groups.get(group_key, group_key),
        factor_title=_catalog_class_name(catalog, catalog_item),
        zone_name=zone.name,
        source=str(getattr(zone.source, "value", zone.source)),
        coverage=coverage,
        deduction=rules.q_regime_unknown,
        display_name=entry.title if entry is not None else zone.name,
        catalog_item=catalog_item,
        effect_code="Q30",
        deduction_kind="q",
        regime_state="unknown",
        act_number=act.document_number if act else None,
        act_date=(act.registration_date if act else None),
        registry_number=zone.registry_number,
    )


def _catalog_group_key(catalog: ZouitCatalog, item: str) -> str:
    for entry in catalog.entries:
        if entry.item == item:
            return entry.group_key
    return ""


def _catalog_class_name(catalog: ZouitCatalog, item: str) -> str:
    for entry in catalog.entries:
        if entry.item == item:
            return entry.class_name
    return ""


def _classify_airport(
    airport_zones: list[tuple[ZouitReportContext, float]],
    rules: ScoringRules,
    guidance: ZoneGuidance,
    catalog: ZouitCatalog,
    base: ReportContext,
    notes: list[str],
) -> list[ScoredFactor]:
    """Приаэродромная территория (6.2 §3, §5 п. 8).

    «Шестая подзона» — спецправило 6.1 (бэнды 1/3/5); иная подзона с
    подтверждённым сектором — спецправило 15; подзона без сектора — Q30
    (режим в части сектора не установлен); общий контур без подзон — Q30.
    Записи секторов не дают отдельных факторов: сектор подтверждает режим
    своей подзоны. Общий контур отбрасывается при наличии записей подзон
    (первичный реестр важнее витрины).
    """
    factors: list[ScoredFactor] = []
    subzones = [
        (zone, coverage)
        for zone, coverage in airport_zones
        if _airport_ordinal(zone.name) is not None
    ]
    general = [
        (zone, coverage)
        for zone, coverage in airport_zones
        if _airport_ordinal(zone.name) is None
    ]
    pure_subzone_ordinals = {
        _airport_ordinal(zone.name)
        for zone, _ in subzones
        if "сектор" not in zone.name.casefold()
    }
    sector_ordinals = {
        _airport_ordinal(zone.name)
        for zone, _ in subzones
        if "сектор" in zone.name.casefold()
    }
    for zone, coverage in subzones:
        ordinal = _airport_ordinal(zone.name)
        is_sector = "сектор" in zone.name.casefold()
        if is_sector and ordinal in pure_subzone_ordinals:
            notes.append(
                f"сектор поглощён записью своей подзоны и отдельным фактором "
                f"не считается: {zone.name!r}"
            )
            continue
        act = _regime_document(base, zone)
        if ordinal == 6:
            factor = _special_factor(
                rules,
                guidance,
                catalog_item="8",
                zone=zone,
                coverage=coverage,
                factor_id="airport_subzone_no_height",
                act=act,
                notes=notes,
            )
        elif is_sector or ordinal in sector_ordinals:
            factor = _special_factor(
                rules,
                guidance,
                catalog_item="8",
                zone=zone,
                coverage=coverage,
                factor_id="airport_subzone_height",
                act=act,
                notes=notes,
            )
        else:
            factor = _q30_factor(
                rules, guidance, catalog, catalog_item="8", zone=zone,
                coverage=coverage, act=act,
            )
        if factor is not None:
            factors.append(factor)
    if general:
        if subzones:
            names = ", ".join(zone.name for zone, _ in general)
            notes.append(
                f"общий контур приаэродромной территории замещён подзоной: {names}"
            )
        else:
            for zone, coverage in general:
                factors.append(
                    _q30_factor(
                        rules, guidance, catalog, catalog_item="8", zone=zone,
                        coverage=coverage, act=_regime_document(base, zone),
                    )
                )
    return factors


# Префиксы наименований классовых слоёв-витрин (без конкретного объекта):
# «Зоны и территории ОКН», «Охранные зоны ООПТ» и т.п. Записи уровня объекта
# («Водоохранная зона р. Пахра») витринами не являются.
_CLASS_LAYER_PREFIXES = ("зоны и территории", "охранные зоны ")


def _is_class_layer_name(name: str) -> bool:
    """Наименование — классовый слой-витрина (общая форма без объекта)."""
    folded = " ".join(name.casefold().split())
    return folded.startswith(_CLASS_LAYER_PREFIXES)


def _merge_class_echoes(
    factors: list[ScoredFactor], notes: list[str]
) -> list[ScoredFactor]:
    """Классовая витрина не даёт второго вычета к реестровой записи (6.2 §7).

    Внутри одного класса каталога: запись-витрина (нет реестрового номера,
    общее наименование слоя — «Зоны и территории ОКН», «Охранные зоны ООПТ»)
    вливается в единственную реестровую запись класса (охват — максимум);
    если реестровых записей несколько, витрина отбрасывается с отметкой
    (приписать одному объекту нельзя); если реестровых нет, витрины класса
    сливаются в одну. Записи уровня объекта без номера (краткое наименование
    конкретной зоны) — не витрины и сохраняются отдельно (консервативно).
    Несколько реестровых записей (разные объекты) — самостоятельные факторы.
    """
    by_item: dict[str, list[ScoredFactor]] = {}
    for factor in factors:
        by_item.setdefault(factor.catalog_item, []).append(factor)
    result: list[ScoredFactor] = []
    for item, group_factors in by_item.items():
        echo_ids = {
            id(f)
            for f in group_factors
            if not f.registry_number and _is_class_layer_name(f.zone_name)
        }
        named = [f for f in group_factors if f.registry_number]
        if not echo_ids:
            result.extend(group_factors)
            continue
        echoes = [f for f in group_factors if id(f) in echo_ids]
        keepers = [f for f in group_factors if id(f) not in echo_ids]
        if len(named) == 1:
            keeper = named[0]
            coverage = max([keeper.coverage, *(f.coverage for f in echoes)])
            notes.append(
                f"слияние записей класса {item}: слой-витрина — тот же класс, "
                "вторым вычетом не считается"
            )
            result.extend(
                replace(keeper, coverage=coverage) if f is keeper else f
                for f in keepers
            )
            continue
        if not named:
            keeper = echoes[0]
            coverage = max(f.coverage for f in echoes)
            notes.append(
                f"слияние записей-витрин класса {item}: одна зона класса, "
                "один фактор"
            )
            result.append(replace(keeper, coverage=coverage))
            result.extend(keepers)
            continue
        names = ", ".join(f.zone_name for f in echoes)
        notes.append(
            f"витрина класса {item} без реестрового номера при нескольких "
            f"именных зонах отброшена (нельзя приписать одному объекту): {names}"
        )
        result.extend(keepers)
    return result


def _classify_zones(
    base: ReportContext,
    rules: ScoringRules,
    guidance: ZoneGuidance,
    catalog: ZouitCatalog,
) -> tuple[list[ScoredFactor], list[str], bool, list[str]]:
    """Группы ограничений по каталогу ст. 105 ЗК РФ (6.2 §5).

    Слияние: по реестровому номеру, иначе по нормализованному названию;
    затем — краткое наименование-префикс полного и слои-витрины, повторяющие
    тип первичной записи реестра (_merge_short_full_names,
    _drop_category_echoes). Внутри источника и между источниками — максимум
    охвата (дубли слоёв и версии границ неотличимы без геометрии; расхождение
    версий — отметка «предварительная»). Классификация: каталог задаёт класс
    и обработку — спецправило 6.1 (d), универсальная шкала E при
    подтверждённом режиме (курируемая таблица regime_effects), иначе Q30.
    Зона вне каталога — без надёжной классификации → BLOCKING_UNKNOWN.
    """
    parts: dict[str, dict[str, float]] = {}
    representative: dict[str, ZouitReportContext] = {}
    registry_of: dict[str, str | None] = {}
    for zone in base.zouit:
        key = _zone_merge_key(zone)
        source = str(getattr(zone.source, "value", zone.source))
        slots = parts.setdefault(key, {})
        slots[source] = max(slots.get(source, 0.0), zone.parcel_coverage_percent)
        representative.setdefault(key, zone)
        registry = (zone.registry_number or "").strip()
        registry_of.setdefault(key, registry or None)

    notes: list[str] = []
    _merge_short_full_names(parts, representative, registry_of, notes)
    _drop_category_echoes(parts, representative, registry_of, notes)

    conservative = False
    classified: list[ScoredFactor] = []
    still_unmatched: list[str] = []
    airport_zones: list[tuple[ZouitReportContext, float]] = []
    for key, by_source in parts.items():
        coverage = min(100.0, max(by_source.values()))
        spread = max(by_source.values()) - min(by_source.values())
        if len(by_source) > 1 and spread > 1.0:
            conservative = True
        zone = representative[key]
        entry = catalog.classify(zone.zone_type, zone.name)
        if entry is None:
            still_unmatched.append(zone.name)
            continue
        if entry.handling == "not_zouit":
            notes.append(
                f"не является ЗОУИТ по ст. 105 ЗК РФ — исключена из скоринга: "
                f"{zone.name!r}"
            )
            continue
        if entry.handling == "airport":
            airport_zones.append((zone, coverage))
            continue
        if entry.obsolete:
            notes.append(
                f"класс ст. 105 п. {entry.item} утратил силу в проверенной "
                f"редакции — проверить актуальность основания: {zone.name!r}"
            )
        if entry.handling.startswith("special:"):
            factor = _special_factor(
                rules,
                guidance,
                catalog_item=entry.item,
                zone=zone,
                coverage=coverage,
                factor_id=entry.handling.removeprefix("special:"),
                act=_regime_document(base, zone),
                notes=notes,
            )
            if factor is not None:
                classified.append(factor)
            continue
        # universal: подтверждённый режим (курируемая таблица) → E, иначе Q30
        effect = catalog.regime_effect_for(
            registry_number=registry_of.get(key), name=zone.name
        )
        act = _regime_document(base, zone)
        if effect is not None:
            deduction = rules.universal_scale.get(effect.effect)
            if deduction is None:
                notes.append(f"неизвестный код влияния {effect.effect!r} — Q30")
                effect = None
        if effect is not None:
            guidance_entry = match_guidance(
                guidance, zone_type=zone.zone_type, name=zone.name
            )
            classified.append(
                ScoredFactor(
                    group_key=entry.group_key,
                    group_title=catalog.groups.get(entry.group_key, entry.group_key),
                    factor_title=entry.class_name,
                    zone_name=zone.name,
                    source=str(getattr(zone.source, "value", zone.source)),
                    coverage=coverage,
                    deduction=deduction,
                    display_name=(
                        guidance_entry.title if guidance_entry is not None else zone.name
                    ),
                    catalog_item=entry.item,
                    effect_code=effect.effect,
                    deduction_kind="d",
                    regime_state="known",
                    act_number=act.document_number if act else None,
                    act_date=(act.registration_date if act else None),
                    registry_number=zone.registry_number,
                )
            )
            notes.append(
                f"режим зоны подтверждён ({effect.effect}): {zone.name!r} — "
                f"{effect.evidence}"
            )
        else:
            classified.append(
                _q30_factor(
                    rules, guidance, catalog, catalog_item=entry.item, zone=zone,
                    coverage=coverage, act=act,
                )
            )

    classified.extend(
        _classify_airport(airport_zones, rules, guidance, catalog, base, notes)
    )
    classified = _merge_class_echoes(classified, notes)
    return classified, still_unmatched, conservative, notes


def _communications_u(rules: ScoringRules) -> float:
    """U: все три вопроса коммуникаций неизвестны (источников подключения нет)."""
    return min(rules.comm_max, sum(rules.comm_weights.values()))


def _target_use_prefix(permitted_use: str | None) -> str | None:
    """Префикс кода ВРИ заявленной цели по тексту разрешённого использования."""
    text = (permitted_use or "").casefold()
    if "садовод" in text:
        return "1"
    if "личного подсобного" in text:
        return "2.2"
    if "блокированн" in text:
        return "2.3"
    if "ижс" in text or "жил" in text:
        return "2.1"
    return None


def _first_number(value: object) -> float | None:
    """Первое число из ячейки регламента («3 (0**)» → 3.0)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMBER_RE.search(str(value))
    if match is None:
        return None
    return float(match.group(0).replace(" ", "").replace(",", "."))


def _target_use_item(
    regulations: QuickZoneRegulations, prefix: str | None
) -> QuickZoneUse | None:
    """Строка регламента для ВРИ участка: «2.1» — только точное совпадение
    (2.1.1 — другой ВРИ), групповой префикс «1» — любой код 1.*."""
    if prefix is None:
        return None
    for use in regulations.housing_uses:
        code = (use.code or "").lstrip().rstrip("*")
        if "." in prefix:
            if code == prefix:
                return use
        elif code == prefix or code.startswith(f"{prefix}."):
            return use
    return None


def _area_deduction(
    base: ReportContext,
    rules: ScoringRules,
    regulations: QuickZoneRegulations | None,
    target_item: QuickZoneUse | None,
) -> tuple[float | None, list[str], list[str], QuickProtocolArea]:
    """A по п. 4.3; (A, notes, suspend_reasons, протокольная запись)."""
    notes: list[str] = []
    suspend: list[str] = []
    area = base.parcel.declared_area_m2 or base.parcel.calculated_area_m2
    info = QuickProtocolArea(
        status="computed",
        deduction=None,
        parcel_area_m2=area,
        target_code=(target_item.code if target_item else None),
        target_name=(target_item.name if target_item else None),
    )
    if regulations is None:
        note = rules.texts.get("pzz_missing", "")
        return None, [note], suspend, info.model_copy(
            update={"status": "no_regulations", "note": note}
        )
    if target_item is None:
        note = "применимость регламента к ВРИ участка не установлена — ручная проверка"
        return None, notes, [note], info.model_copy(
            update={"status": "applicability_unknown", "note": note}
        )
    s_min = _first_number(target_item.area_min)
    info = info.model_copy(update={"s_min_m2": s_min})
    if s_min is None:
        note = rules.texts.get("area_min_not_set", "")
        notes.append(note)
        return 0.0, notes, suspend, info.model_copy(
            update={"status": "min_not_set", "deduction": 0.0, "note": note}
        )
    if area == s_min:
        deduction = rules.area_equal_deduction
        return deduction, notes, suspend, info.model_copy(
            update={"deduction": deduction, "note": "S = Smin"}
        )
    if area < s_min:
        note = rules.texts.get("area_below_minimum", "")
        return None, notes, [note], info.model_copy(
            update={"status": "below_minimum", "note": note}
        )
    return 0.0, notes, suspend, info.model_copy(
        update={"deduction": 0.0, "note": "S > Smin"}
    )


def _sufficiency_blockers(
    base: ReportContext, rules: ScoringRules, *, zoning_known: bool
) -> list[str]:
    """Третий уровень достаточности (раздел 4): нет ключевых сведений.

    Зонирование считается известным и по слою РГИС (parcel_zones), и по
    таблице ВРИ из документа ПЗЗ (документальный контур).
    """
    blockers: list[str] = []
    if not base.parcel.land_category:
        blockers.append(rules.texts.get("no_land_category", ""))
    if not base.parcel.permitted_use:
        blockers.append(rules.texts.get("no_permitted_use", ""))
    if not zoning_known:
        blockers.append(rules.texts.get("no_zoning", ""))
    sources = {str(getattr(item.source, "value", item.source)) for item in base.sources}
    if "nspd" not in sources:
        blockers.append(rules.texts.get("no_zone_checks", ""))
    return [blocker for blocker in blockers if blocker]


def _preliminary_flags(
    base: ReportContext, conservative: bool
) -> list[str]:
    """Некритичные пробелы и консервативные допущения (отметка «предварительная»)."""
    flags = [
        warning
        for warning in base.warnings
        if "partial" in warning.casefold() or "неполн" in warning.casefold()
    ]
    if base.documents is not None and base.documents.partial:
        flags.append("документальный контур неполон")
    if conservative:
        flags.append("консервативный сценарий: расхождение границ зоны между источниками")
    return flags


# ---------------------------------------------------------------------------
# Внутренний протокол расчёта (сервисный отчёт для разработчиков)
# ---------------------------------------------------------------------------

_MANDATORY_ZONE_CHECKS = (
    ("water", "Водоохранные зоны и прибрежные защитные полосы"),
    ("airport", "Приаэродромная территория"),
    ("power_line", "Охранные зоны объектов электросетевого хозяйства"),
    ("sanitary", "Санитарные зоны (ЗСО, СЗЗ)"),
    ("forest", "Лесные ограничения (ОЗУ, защитные леса)"),
)


def _protocol_checks(
    base: ReportContext,
    scored: list[ScoredFactor],
    regulations: QuickZoneRegulations | None,
) -> list[QuickProtocolCheck]:
    """Обязательные проверки ключевых зон (п. 4.1) и их результат для протокола."""
    checks: list[QuickProtocolCheck] = []
    if regulations is not None:
        doc = "ПЗЗ"
        if regulations.doc_number:
            doc += f" № {regulations.doc_number}"
        if regulations.doc_version_date:
            doc += f", редакция от {regulations.doc_version_date}"
        checks.append(
            QuickProtocolCheck(
                name="Территориальное зонирование (ПЗЗ)",
                result=f"получен: {doc}; зона {regulations.zone_code}",
            )
        )
    elif base.urban_planning.parcel_zones:
        checks.append(
            QuickProtocolCheck(
                name="Территориальное зонирование (ПЗЗ)",
                result="зона из слоя РГИС; документ ПЗЗ не получен",
            )
        )
    else:
        checks.append(
            QuickProtocolCheck(
                name="Территориальное зонирование (ПЗЗ)", result="не получено"
            )
        )
    sources = {str(getattr(item.source, "value", item.source)) for item in base.sources}
    nspd_ok = "nspd" in sources
    checks.append(
        QuickProtocolCheck(
            name="Реестр ЗОУИТ (пересечения с участком)",
            result=(
                f"выполнена; записей с пересечением: {len(base.zouit)}"
                if nspd_ok
                else "не выполнена (источник недоступен)"
            ),
        )
    )
    for group_key, name in _MANDATORY_ZONE_CHECKS:
        found = [item for item in scored if item.group_key == group_key]
        if found:
            names = ", ".join(
                dict.fromkeys(item.display_name or item.factor_title for item in found)
            )
            result = f"пересечение выявлено: {names}"
        elif nspd_ok:
            result = "пересечений не выявлено"
        else:
            result = "проверка не выполнена (источник недоступен)"
        checks.append(QuickProtocolCheck(name=name, result=result))
    return checks


def _status_rule(
    rules: ScoringRules,
    *,
    score: int | None,
    stop_reasons: list[str],
    suspend_reasons: list[str],
    groups: tuple[GroupDeduction, ...],
) -> str:
    """Какое правило раздела 5 методики определило статус (по приоритету)."""
    if stop_reasons:
        return "правило 1 — подтверждён стоп-фактор"
    if suspend_reasons:
        return "правило 2 — недостаточно данных для отбора"
    if any(group.q_part > 0 for group in groups):
        return "правило 3 — неизвестный режим подтверждённой зоны (Q)"
    if score is not None and score < rules.attention_score_below:
        return f"правило 3 — оценка ниже {rules.attention_score_below}"
    if any(group.deduction >= rules.attention_group_deduction for group in groups):
        return f"правило 3 — вычет группы ≥ {rules.attention_group_deduction:g}"
    return "правило 4 — можно продолжить рассмотрение"


def _protocol(
    base: ReportContext,
    rules: ScoringRules,
    *,
    catalog: ZouitCatalog,
    scored: list[ScoredFactor],
    unmatched: list[str],
    conservative: bool,
    classify_notes: list[str],
    stop_reasons: list[str],
    suspend_reasons: list[str],
    preliminary_flags: list[str],
    area_info: QuickProtocolArea,
    configuration: ConfigurationOutcome | None,
    regulations: QuickZoneRegulations | None,
    target_prefix: str | None,
    verdict_result: VerdictResult,
) -> QuickProtocol:
    """Сводит внутренние структуры расчёта в протокол сервисного отчёта."""
    restrictions_total = sum(group.deduction for group in verdict_result.groups)
    g = verdict_result.g
    raw_score = None
    if verdict_result.score is not None:
        raw_score = round(
            rules.base_score - restrictions_total - verdict_result.u - (g or 0.0)
        )
    found_items = {factor.catalog_item for factor in scored if factor.catalog_item}
    return QuickProtocol(
        methodology_sha256=methodology_sha256(),
        formula=(
            "Оценка = max(0; base − Σ вычетов групп (потолок "
            f"{rules.group_cap:g}) − U − G), G = max(A; C)"
        ),
        base_score=rules.base_score,
        factors=[
            QuickProtocolFactor(
                group_key=item.group_key,
                group_title=item.group_title,
                factor_title=item.factor_title,
                zone_name=item.zone_name,
                display_name=item.display_name or item.factor_title,
                source=item.source,
                coverage_percent=item.coverage,
                deduction=item.deduction,
                catalog_item=item.catalog_item,
                effect_code=item.effect_code,
                deduction_kind=item.deduction_kind,
                regime_state=item.regime_state,
                act_number=item.act_number,
                act_date=item.act_date,
                registry_number=item.registry_number,
            )
            for item in scored
        ],
        unmatched_zones=list(unmatched),
        classify_notes=list(classify_notes),
        conservative=conservative,
        groups=[
            QuickProtocolGroup(
                key=group.key,
                title=group.title,
                raw_deduction=group.raw_deduction,
                deduction=group.deduction,
                zone_names=list(group.zone_names),
                q_part=group.q_part,
            )
            for group in verdict_result.groups
        ],
        restrictions_total=restrictions_total,
        q_total=verdict_result.q_total,
        catalog_coverage=[
            QuickProtocolClassState(
                item=entry.item,
                class_name=entry.class_name,
                state="found" if entry.item in found_items else "none_found",
            )
            for entry in catalog.entries
        ],
        communications=[
            QuickProtocolComm(question=key, state="неизвестно", weight=weight)
            for key, weight in rules.comm_weights.items()
        ],
        u_total=verdict_result.u,
        area=area_info,
        geometry=(
            QuickProtocolGeometry(c=configuration.c, detail=configuration.detail)
            if configuration is not None
            else None
        ),
        g=g,
        raw_score=raw_score,
        score=verdict_result.score,
        status_key=verdict_result.status.key,
        status_rule=_status_rule(
            rules,
            score=verdict_result.score,
            stop_reasons=stop_reasons,
            suspend_reasons=suspend_reasons,
            groups=verdict_result.groups,
        ),
        preliminary=verdict_result.preliminary,
        preliminary_flags=list(preliminary_flags),
        stop_reasons=list(stop_reasons),
        suspend_reasons=list(suspend_reasons),
        checks=_protocol_checks(base, scored, regulations),
        sources=[
            QuickProtocolSource(
                source=str(getattr(item.source, "value", item.source)),
                snapshot_id=item.snapshot_id,
                retrieved_at=item.retrieved_at,
                adapter_version=item.adapter_version,
                content_sha256=item.content_sha256,
            )
            for item in base.sources
        ],
        target_use_prefix=target_prefix,
    )


# ---------------------------------------------------------------------------
# Сборка контекста
# ---------------------------------------------------------------------------


def _passport(base: ReportContext) -> QuickPassport:
    parcel = base.parcel
    territorial_zone = None
    if base.urban_planning.parcel_zones:
        first = base.urban_planning.parcel_zones[0]
        parts = [p for p in (first.zone, first.name) if p]
        territorial_zone = ", ".join(parts) or None
    return QuickPassport(
        cadastral_number=parcel.cadastral_number,
        address=parcel.address,
        status=parcel.status,
        area_m2=parcel.declared_area_m2 or parcel.calculated_area_m2,
        land_category=parcel.land_category,
        permitted_use=parcel.permitted_use,
        vri=parcel.permitted_use_vri,
        vri_origin=(
            "LEGAL"
            if parcel.permitted_use_vri is not None
            else ("SOURCE" if parcel.permitted_use else None)
        ),
        territorial_zone=territorial_zone,
        cadastral_value_rub=parcel.cadastral_value_rub,
    )


def _surroundings(base: ReportContext, guidance: ZoneGuidance) -> QuickSurroundings:
    lines: list[str] = []
    for item in [
        *base.natural_nearest,
        *base.transport_nearest,
        *base.social_nearest,
    ]:
        if item.distance_m is None:
            continue
        # object_name бывает числовым OSM-id (объект без имени) — тогда только категория
        name = item.object_name
        label = (
            f"{item.category_name}: {name}"
            if name and not name.isdigit()
            else item.category_name
        )
        lines.append(f"{label} ~{item.distance_m:.0f} м")
        if len(lines) >= _MAX_SURROUNDING_LINES:
            break
    return QuickSurroundings(
        lines=lines,
        communications_note=guidance.misc.get("communications"),
    )


_COMM_MISSING_KEYS = {
    "electricity": "comm_electricity",
    "water": "comm_water",
    "sewage": "comm_sewage",
}


def _missing_items(
    *,
    rules: ScoringRules,
    texts: dict[str, str],
    protocol_comms: list[QuickProtocolComm] | None,
    geometry_checked: bool,
    zoning_known: bool,
    area_status: str | None,
) -> list[QuickMissingItem]:
    """Детерминированная сборка пробелов evidence (§34 ТЗ).

    Источник — отсутствие данных/проверок, а не LLM: неизвестные вопросы
    коммуникаций (п. 4.2), невыполненный геометрический тест, отсутствие
    сведений о зонировании и регламента ПЗЗ, непроверенный подъезд.
    """
    items: list[QuickMissingItem] = []

    def _add(key: str) -> None:
        text = texts.get(key)
        if text:
            items.append(QuickMissingItem(key=key, text=text))

    comm_states = (
        {comm.question: comm.state for comm in protocol_comms}
        if protocol_comms is not None
        else {question: "неизвестно" for question in rules.comm_weights}
    )
    for question in rules.comm_weights:
        if comm_states.get(question) == "неизвестно":
            _add(_COMM_MISSING_KEYS.get(question, f"comm_{question}"))
    if not geometry_checked:
        _add("geometry_unknown")
    if not zoning_known:
        _add("no_zoning")
    if area_status == "no_regulations":
        _add("pzz_missing")
    _add("access_unchecked")  # методика 6.2: подъезд — пробел без вычета
    return items


def _factor_guidance_keys(
    guidance: ZoneGuidance, catalog: ZouitCatalog, factor: ScoredFactor
) -> set[str]:
    """Ключи строк таблицы факторов, соответствующие фактору скоринга."""
    keys: set[str] = set()
    entry = match_guidance(guidance, zone_type=None, name=factor.zone_name)
    if entry is not None:
        keys.add(entry.key)
    if factor.catalog_item:
        keys.add(f"catalog:{factor.catalog_item}")
    return keys


def build_quick_context_from_report(
    base: ReportContext,
    *,
    rules: ScoringRules | None = None,
    guidance: ZoneGuidance | None = None,
    catalog: ZouitCatalog | None = None,
    configuration: ConfigurationOutcome | None = None,
) -> QuickReportContext:
    """Чистая сборка QuickReportContext из ReportContext (без store).

    ``configuration`` — результат геометрической проверки (считается на уровне
    store-обёртки); None — C неизвестен и числовая оценка не выдаётся
    (методика 6.2, п. 4.4).
    """
    rules = rules or load_scoring_rules()
    guidance = guidance or load_zone_guidance()
    catalog = catalog or load_zouit_catalog()
    warnings = list(base.warnings)
    regulations = _zone_regulations(base)

    scored, unmatched, conservative, classify_notes = _classify_zones(
        base, rules, guidance, catalog
    )
    warnings.extend(classify_notes)
    unknown_regime: set[str] = set()
    for factor in scored:
        if factor.deduction_kind == "q":
            unknown_regime.update(_factor_guidance_keys(guidance, catalog, factor))
    factors = _factors(
        base, rules, guidance, warnings, unknown_regime=unknown_regime, catalog=catalog
    )
    stop_reasons: list[str] = []
    if regulations is not None and not regulations.housing_uses:
        stop_reasons.append(rules.texts.get("stop_no_target_use", ""))
    zoning_known = bool(base.urban_planning.parcel_zones) or regulations is not None
    suspend_reasons = _sufficiency_blockers(base, rules, zoning_known=zoning_known)
    suspend_reasons.extend(
        f"{rules.texts.get('unruled_zone', '')}: {name}" for name in unmatched
    )

    prefix = _target_use_prefix(base.parcel.permitted_use)
    target_item = (
        _target_use_item(regulations, prefix) if regulations is not None else None
    )
    a, area_notes, area_suspend, area_info = _area_deduction(
        base, rules, regulations, target_item
    )
    warnings.extend(note for note in area_notes if note)
    suspend_reasons.extend(area_suspend)

    c: float | None = None
    if configuration is not None:
        c = float(configuration.c)
        warnings.append(f"конфигурация: {configuration.detail}")
    elif not stop_reasons:
        suspend_reasons.append(rules.texts.get("geometry_unknown", ""))

    u = _communications_u(rules)
    preliminary_flags = _preliminary_flags(base, conservative)
    verdict_result = compute_verdict(
        rules,
        scored,
        u=u,
        a=a,
        c=c,
        stop_reasons=stop_reasons,
        suspend_reasons=suspend_reasons,
        preliminary_flags=preliminary_flags,
    )
    warnings.extend(verdict_result.notes)
    protocol = _protocol(
        base,
        rules,
        catalog=catalog,
        scored=scored,
        unmatched=unmatched,
        conservative=conservative,
        classify_notes=classify_notes,
        stop_reasons=stop_reasons,
        suspend_reasons=suspend_reasons,
        preliminary_flags=preliminary_flags,
        area_info=area_info,
        configuration=configuration,
        regulations=regulations,
        target_prefix=prefix,
        verdict_result=verdict_result,
    )
    missing = _missing_items(
        rules=rules,
        texts=load_missing_texts(),
        protocol_comms=protocol.communications,
        geometry_checked=configuration is not None,
        zoning_known=zoning_known,
        area_status=area_info.status if area_info is not None else None,
    )
    return QuickReportContext(
        case_id=base.case_id,
        collection_run_id=base.collection_run_id,
        collected_at=base.collected_at,
        methodology_version=rules.methodology_version,
        parcel=_passport(base),
        verdict=QuickVerdict(
            score=verdict_result.score,
            status_key=verdict_result.status.key,
            status_title=verdict_result.status.title,
            status_summary=verdict_result.status.summary,
            preliminary=verdict_result.preliminary,
            reasons=list(verdict_result.reasons),
            explanation=verdict_result.explanation,
            geometry_note=verdict_result.geometry_note,
        ),
        factors=factors,
        zone_regulations=regulations,
        surroundings=_surroundings(base, guidance),
        missing=missing,
        warnings=warnings,
        protocol=protocol,
    )


def _configuration_from_store(
    store: CaseStore,
    base: ReportContext,
    rules: ScoringRules,
    regulations: QuickZoneRegulations | None,
    warnings: list[str],
) -> ConfigurationOutcome | None:
    """Геометрическая проверка п. 4.4: контур участка + применимые отступы."""
    if regulations is None:
        return None
    prefix = _target_use_prefix(base.parcel.permitted_use)
    target_item = _target_use_item(regulations, prefix)
    margin = _first_number(target_item.margin) if target_item is not None else None
    if margin is None:
        warnings.append("конфигурация: нет применимых отступов из регламента ПЗЗ")
        return None
    receipts = store.list_collection_receipts(base.case_id)
    receipt = next(
        (item for item in receipts if item.run_id == base.collection_run_id), None
    )
    aoi_id = receipt.aoi_id if receipt is not None else None
    if aoi_id is None:
        return None
    try:
        aoi = store.get_area_of_interest(base.case_id, aoi_id)
    except KeyError:
        return None
    geometry: dict[str, Any] | None = aoi.parcel_geometry
    if not geometry:
        return None
    return evaluate_configuration(
        geometry,
        margin,
        source_crs=aoi.source_crs,
        metric_crs=aoi.metric_crs,
        test_m=rules.config_test_m,
        control_m=rules.config_control_m,
        grid_step_m=rules.config_grid_step_m,
        fine_grid_step_m=rules.config_fine_grid_step_m,
        tight_c=int(rules.config_tight_deduction),
        no_fit_c=int(rules.config_nofit_deduction),
    )


def build_quick_context(
    store: CaseStore,
    case_id: str,
    *,
    collection_run_id: str | None = None,
    rules: ScoringRules | None = None,
    guidance: ZoneGuidance | None = None,
    catalog: ZouitCatalog | None = None,
) -> QuickReportContext:
    """Store-обёртка: контекст последнего (или явного) run кейса + проверка C."""
    rules = rules or load_scoring_rules()
    base = build_report_context(store, case_id, collection_run_id=collection_run_id)
    warnings: list[str] = []
    configuration = _configuration_from_store(
        store, base, rules, _zone_regulations(base), warnings
    )
    context = build_quick_context_from_report(
        base,
        rules=rules,
        guidance=guidance,
        catalog=catalog,
        configuration=configuration,
    )
    context.warnings.extend(warnings)
    return context
