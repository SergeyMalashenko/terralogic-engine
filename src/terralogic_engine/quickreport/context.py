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
    QuickReportContext,
    QuickSurroundings,
    QuickVerdict,
    QuickZoneRegulations,
    QuickZoneUse,
)
from .rules import (
    ScoredFactor,
    ScoringRules,
    ZoneGuidance,
    compute_verdict,
    load_scoring_rules,
    load_zone_guidance,
    match_guidance,
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
    factors: list[QuickFactor] = []
    for zone in base.zouit:
        entry = match_guidance(guidance, zone_type=zone.zone_type, name=zone.name)
        if entry is None:
            warnings.append(f"зона без текста guidance: {zone.name}")
            continue
        coverage = zone.parcel_coverage_percent
        factors.append(
            QuickFactor(
                rule_id=entry.key,
                title=entry.title,
                zone_name=zone.name,
                coverage_percent=coverage,
                coverage_word=rules.coverage_word(coverage),
                guidance=entry.guidance,
                action=entry.action,
                informational=False,
            )
        )
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


def _classify_zones(
    base: ReportContext,
    rules: ScoringRules,
) -> tuple[list[ScoredFactor], list[str], bool, list[str]]:
    """Группы ограничений по п. 3.1; (факторы, нераспознанные, консервативно, notes).

    Слияние: по реестровому номеру, иначе по нормализованному названию;
    внутри источника и между источниками — максимум охвата (дубли слоёв и
    версии границ неотличимы без геометрии; расхождение версий — отметка
    «предварительная»). В группе airport при наличии записи-подзоны общий
    контур территории отбрасывается (первичный реестр важнее витрин).
    Нераспознанная зона приписывается классифицированному близнецу из того
    же источника с равным охватом (дубль слоя), иначе — фактор без правила.
    """
    parts: dict[str, dict[str, float]] = {}
    representative: dict[str, ZouitReportContext] = {}
    for zone in base.zouit:
        key = _zone_merge_key(zone)
        source = str(getattr(zone.source, "value", zone.source))
        slots = parts.setdefault(key, {})
        slots[source] = max(slots.get(source, 0.0), zone.parcel_coverage_percent)
        representative.setdefault(key, zone)

    notes: list[str] = []
    conservative = False
    classified: list[ScoredFactor] = []
    unmatched: list[tuple[ZouitReportContext, str, float]] = []
    for key, by_source in parts.items():
        coverage = min(100.0, max(by_source.values()))
        spread = max(by_source.values()) - min(by_source.values())
        if len(by_source) > 1 and spread > 1.0:
            conservative = True
        zone = representative[key]
        source = str(getattr(zone.source, "value", zone.source))
        matched = rules.classify(zone.zone_type, zone.name)
        if matched is None:
            unmatched.append((zone, source, coverage))
            continue
        group, factor = matched
        classified.append(
            ScoredFactor(
                group_key=group.key,
                group_title=group.title,
                factor_title=factor.title,
                zone_name=zone.name,
                source=source,
                coverage=coverage,
                deduction=factor.deduction_for(coverage),
            )
        )

    # нераспознанные зоны: близнец с равным охватом из того же источника
    still_unmatched: list[str] = []
    for zone, source, coverage in unmatched:
        twin = next(
            (
                item
                for item in classified
                if item.source == source and abs(item.coverage - coverage) <= 0.5
            ),
            None,
        )
        if twin is None:
            still_unmatched.append(zone.name)
        else:
            notes.append(
                f"зона {zone.name!r} принята как дубль слоя {twin.zone_name!r}"
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
) -> tuple[float | None, list[str], list[str]]:
    """A по п. 4.3; (A, notes, suspend_reasons)."""
    notes: list[str] = []
    suspend: list[str] = []
    if regulations is None:
        return None, [rules.texts.get("pzz_missing", "")], suspend
    if target_item is None:
        return None, notes, [
            "применимость регламента к ВРИ участка не установлена — ручная проверка"
        ]
    s_min = _first_number(target_item.area_min)
    if s_min is None:
        notes.append(rules.texts.get("area_min_not_set", ""))
        return 0.0, notes, suspend
    area = base.parcel.declared_area_m2 or base.parcel.calculated_area_m2
    if area == s_min:
        return rules.area_equal_deduction, notes, suspend
    if area < s_min:
        return None, notes, [rules.texts.get("area_below_minimum", "")]
    return 0.0, notes, suspend


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

    scored, unmatched, conservative, classify_notes = _classify_zones(base, rules)
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
    a, area_notes, area_suspend = _area_deduction(base, rules, regulations, target_item)
    warnings.extend(note for note in area_notes if note)
    suspend_reasons.extend(area_suspend)

    c: float | None = None
    if configuration is not None:
        c = float(configuration.c)
        warnings.append(f"конфигурация: {configuration.detail}")
    elif not stop_reasons:
        suspend_reasons.append(rules.texts.get("geometry_unknown", ""))

    u = _communications_u(rules)
    verdict_result = compute_verdict(
        rules,
        scored,
        u=u,
        a=a,
        c=c,
        stop_reasons=stop_reasons,
        suspend_reasons=suspend_reasons,
        preliminary_flags=_preliminary_flags(base, conservative),
    )
    warnings.extend(verdict_result.notes)
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
