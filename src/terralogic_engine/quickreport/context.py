"""Сборка контекста QuickReport из отчётного контекста кейса.

Источник данных — `reporting.build_report_context` (immutable run кейса);
здесь только производные: факторы с текстами guidance, стаб-скоринг,
человекочитаемое окружение. Разделено на чистую часть
(`build_quick_context_from_report`, тестируется без store) и store-обёртку.
"""

from __future__ import annotations

from terralogic_engine.reporting.context import build_report_context
from terralogic_engine.reporting.models import ReportContext, ZouitReportContext
from terralogic_engine.store.base import CaseStore

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
    ScoringRule,
    ScoringRules,
    ZoneGuidance,
    compute_score,
    load_scoring_rules,
    load_zone_guidance,
    match_guidance,
)

_MAX_SURROUNDING_LINES = 6
_HOUSING_CODE_PREFIXES = ("1", "2")


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


def _rule_for_zone(
    rules: ScoringRules, guidance: ZoneGuidance, zone: ZouitReportContext
) -> tuple[ScoringRule, str] | None:
    """Первое правило, чей guidance матчит зону (по zone_type/name)."""
    entry = match_guidance(guidance, zone_type=zone.zone_type, name=zone.name)
    if entry is None:
        return None
    for rule in rules.rules:
        if rule.guidance_key == entry.key:
            return rule, entry.key
    return None


def _factors(
    base: ReportContext,
    rules: ScoringRules,
    guidance: ZoneGuidance,
    warnings: list[str],
) -> tuple[list[QuickFactor], list[tuple[ScoringRule, float]]]:
    factors: list[QuickFactor] = []
    deductions: list[tuple[ScoringRule, float]] = []
    for zone in base.zouit:
        matched = _rule_for_zone(rules, guidance, zone)
        if matched is None:
            warnings.append(f"зона без правила скоринга: {zone.name}")
            continue
        rule, guidance_key = matched
        entry = guidance.zones[guidance_key]
        coverage = (
            rule.fixed_coverage
            if rule.fixed_coverage is not None
            else zone.parcel_coverage_percent
        )
        factors.append(
            QuickFactor(
                rule_id=rule.id,
                title=entry.title,
                zone_name=zone.name,
                coverage_percent=coverage,
                coverage_word=rules.coverage_word(coverage),
                guidance=entry.guidance,
                action=entry.action,
                informational=rule.informational,
            )
        )
        deductions.append((rule, coverage))
    # Коммуникации: данных в контуре нет — всегда информационный блок.
    comm_note = guidance.misc.get("communications")
    if comm_note:
        for rule in rules.rules:
            if rule.informational and rule.guidance_key == "communications":
                factors.append(
                    QuickFactor(
                        rule_id=rule.id,
                        title=rule.title,
                        zone_name="—",
                        coverage_percent=0.0,
                        coverage_word="—",
                        guidance=comm_note,
                        informational=True,
                    )
                )
                break
    factors.sort(
        key=lambda f: (f.informational, -_weight_of(rules, f.rule_id), -f.coverage_percent)
    )
    return factors, deductions


def _weight_of(rules: ScoringRules, rule_id: str) -> float:
    for rule in rules.rules:
        if rule.id == rule_id:
            return rule.weight
    return 0.0


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
) -> QuickReportContext:
    """Чистая сборка QuickReportContext из ReportContext (без store)."""
    rules = rules or load_scoring_rules()
    guidance = guidance or load_zone_guidance()
    warnings = list(base.warnings)
    factors, deductions = _factors(base, rules, guidance, warnings)
    score = compute_score(rules, deductions)
    grade = rules.grade_for(score)
    return QuickReportContext(
        case_id=base.case_id,
        collection_run_id=base.collection_run_id,
        collected_at=base.collected_at,
        methodology_version=rules.methodology_version,
        parcel=_passport(base),
        verdict=QuickVerdict(
            score=score,
            grade_key=grade.key,
            grade_title=grade.title,
            grade_summary=grade.summary,
        ),
        factors=factors,
        zone_regulations=_zone_regulations(base),
        surroundings=_surroundings(base, guidance),
        warnings=warnings,
    )


def build_quick_context(
    store: CaseStore,
    case_id: str,
    *,
    collection_run_id: str | None = None,
    rules: ScoringRules | None = None,
    guidance: ZoneGuidance | None = None,
) -> QuickReportContext:
    """Store-обёртка: контекст последнего (или явного) run кейса."""
    base = build_report_context(store, case_id, collection_run_id=collection_run_id)
    return build_quick_context_from_report(base, rules=rules, guidance=guidance)
