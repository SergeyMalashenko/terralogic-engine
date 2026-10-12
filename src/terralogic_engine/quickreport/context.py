"""Сборка контекста QuickReport из отчётного контекста кейса.

Источник данных — `reporting.build_report_context` (immutable run кейса);
здесь только производные: факторы с текстами guidance, скоринг методики 6.1
(группы ограничений, U коммуникаций, A площади, C конфигурации),
человекочитаемое окружение. Разделено на чистую часть
(`build_quick_context_from_report`, тестируется без store; геометрическая
проверка передаётся готовым `ConfigurationOutcome`) и store-обёртку
(`build_quick_context` — добывает контур участка и считает C).
"""

from __future__ import annotations

import re
from typing import Any

from terralogic_engine.reporting.context import build_report_context
from terralogic_engine.reporting.models import ReportContext, ZouitReportContext
from terralogic_engine.store.base import CaseStore

from .geometry import ConfigurationOutcome, evaluate_configuration
from .models import (
    QuickFactor,
    QuickPassport,
    QuickProtocol,
    QuickProtocolArea,
    QuickProtocolCheck,
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
    compute_verdict,
    load_scoring_rules,
    load_zone_guidance,
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
) -> list[QuickFactor]:
    """Строки таблицы факторов — по одной на тип зоны.

    Записи одной зоны под кратким и полным наименованием, дубли слоёв и
    источников схлопываются в одну строку с коротким каноническим
    названием (title из zone_guidance); охват — максимальный среди
    объединённых записей (консервативный показ).
    """
    collapsed: dict[str, QuickFactor] = {}
    for zone in base.zouit:
        entry = match_guidance(guidance, zone_type=zone.zone_type, name=zone.name)
        if entry is None:
            warnings.append(f"зона без текста guidance: {zone.name}")
            continue
        coverage = zone.parcel_coverage_percent
        existing = collapsed.get(entry.key)
        if existing is None or coverage > existing.coverage_percent:
            collapsed[entry.key] = QuickFactor(
                rule_id=entry.key,
                title=entry.title,
                zone_name=entry.title,
                coverage_percent=coverage,
                coverage_word=rules.coverage_word(coverage),
                guidance=entry.guidance,
                action=entry.action,
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
# Скоринг 6.1: классификация зон в группы, U, A, достаточность
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


def _classify_zones(
    base: ReportContext,
    rules: ScoringRules,
    guidance: ZoneGuidance,
) -> tuple[list[ScoredFactor], list[str], bool, list[str]]:
    """Группы ограничений по п. 3.1; (факторы, нераспознанные, консервативно, notes).

    Слияние: по реестровому номеру, иначе по нормализованному названию;
    затем — краткое наименование-префикс полного и слои-витрины, повторяющие
    тип первичной записи реестра (_merge_short_full_names,
    _drop_category_echoes). Внутри источника и между источниками — максимум
    охвата (дубли слоёв и версии границ неотличимы без геометрии; расхождение
    версий — отметка «предварительная»). В группе airport при наличии
    записи-подзоны общий контур территории отбрасывается (первичный реестр
    важнее витрин). Нераспознанная зона — фактор без правила (группа 6);
    совпадение охвата с классифицированной зоной основанием для слияния
    не является. display_name — короткое имя зоны для отчёта (title guidance).
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
    for key, by_source in parts.items():
        coverage = min(100.0, max(by_source.values()))
        spread = max(by_source.values()) - min(by_source.values())
        if len(by_source) > 1 and spread > 1.0:
            conservative = True
        zone = representative[key]
        matched = rules.classify(zone.zone_type, zone.name)
        if matched is None:
            still_unmatched.append(zone.name)
            continue
        group, factor = matched
        entry = match_guidance(guidance, zone_type=zone.zone_type, name=zone.name)
        classified.append(
            ScoredFactor(
                group_key=group.key,
                group_title=group.title,
                factor_title=factor.title,
                zone_name=zone.name,
                source=str(getattr(zone.source, "value", zone.source)),
                coverage=coverage,
                deduction=factor.deduction_for(coverage),
                display_name=entry.title if entry is not None else factor.title,
            )
        )

    # приаэродромная: запись-подзона (реестр ЗОУИТ) вытесняет общий контур
    airport = [item for item in classified if item.group_key == "airport"]
    if any("подзон" in item.zone_name.casefold() for item in airport):
        dropped = [item for item in airport if "подзон" not in item.zone_name.casefold()]
        if dropped:
            names = ", ".join(item.zone_name for item in dropped)
            notes.append(f"общий контур приаэродромной территории замещён подзоной: {names}")
            classified = [
                item
                for item in classified
                if item.group_key != "airport" or "подзон" in item.zone_name.casefold()
            ]
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
    if score is not None and score < rules.attention_score_below:
        return f"правило 3 — оценка ниже {rules.attention_score_below}"
    if any(group.deduction >= rules.attention_group_deduction for group in groups):
        return f"правило 3 — вычет группы ≥ {rules.attention_group_deduction:g}"
    return "правило 4 — можно продолжить рассмотрение"


def _protocol(
    base: ReportContext,
    rules: ScoringRules,
    *,
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
            )
            for group in verdict_result.groups
        ],
        restrictions_total=restrictions_total,
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
        territorial_zone=territorial_zone,
        cadastral_value_rub=parcel.cadastral_value_rub,
    )


def _surroundings(base: ReportContext, guidance: ZoneGuidance) -> QuickSurroundings:
    lines: list[str] = []
    for item in [*base.natural_nearest, *base.social_nearest]:
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


def build_quick_context_from_report(
    base: ReportContext,
    *,
    rules: ScoringRules | None = None,
    guidance: ZoneGuidance | None = None,
    configuration: ConfigurationOutcome | None = None,
) -> QuickReportContext:
    """Чистая сборка QuickReportContext из ReportContext (без store).

    ``configuration`` — результат геометрической проверки (считается на уровне
    store-обёртки); None — C неизвестен и числовая оценка не выдаётся
    (методика 6.1, п. 4.4).
    """
    rules = rules or load_scoring_rules()
    guidance = guidance or load_zone_guidance()
    warnings = list(base.warnings)
    factors = _factors(base, rules, guidance, warnings)
    regulations = _zone_regulations(base)

    scored, unmatched, conservative, classify_notes = _classify_zones(
        base, rules, guidance
    )
    warnings.extend(classify_notes)
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
        configuration=configuration,
    )
    context.warnings.extend(warnings)
    return context
