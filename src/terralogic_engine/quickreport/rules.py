"""Правила скоринга QuickReport — методика 6.3.

Оценка = max(0; base − Σ вычетов групп ограничений (потолок group_cap на
группу) − U − G), G = max(A; C). Специальный маршрут мелиорации §6.1:
INFO_MELIORATION — информационная запись вне суммы вычетов. Статусы первичного отбора — по приоритету
из yaml (первое выполненное условие): stop → insufficient → attention → ok.
Веса, бэнды охвата, пороги и клиентские тексты — в `assets/scoring_rules.yaml`;
тексты факторов для таблицы отчёта — в `assets/zone_guidance.yaml`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ASSETS = Path(__file__).resolve().parent / "assets"


def methodology_sha256() -> str:
    """Отпечаток методики: sha256 активов скоринга, каталога и текстов guidance."""
    digest = hashlib.sha256()
    for name in ("scoring_rules.yaml", "zouit_catalog_63.yaml", "zone_guidance.yaml"):
        digest.update((_ASSETS / name).read_bytes())
    return digest.hexdigest()


def marketing_sha256() -> str:
    """Отпечаток коммерческого блока (marketing.yaml); в методику не входит."""
    return hashlib.sha256((_ASSETS / "marketing.yaml").read_bytes()).hexdigest()


class QuickRulesError(ValueError):
    """Битый или несогласованный yaml-актив quickreport."""


# ---------------------------------------------------------------------------
# Тексты факторов (zone_guidance.yaml) — без изменений от версии 0.1
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ZoneGuidanceEntry:
    key: str
    match: tuple[str, ...]
    title: str
    guidance: str
    action: str | None = None
    guidance_unknown_regime: str | None = None


@dataclass(frozen=True)
class ZoneGuidance:
    zones: dict[str, ZoneGuidanceEntry]
    misc: dict[str, str] = field(default_factory=dict)  # ключ → текст guidance


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise QuickRulesError(f"не удалось разобрать {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise QuickRulesError(f"{path}: корень должен быть mapping")
    return raw


def load_zone_guidance(path: Path | None = None) -> ZoneGuidance:
    """Загружает zone_guidance.yaml; без path — пакетный актив."""
    raw = _load_yaml(path or _ASSETS / "zone_guidance.yaml")
    try:
        zones = {
            str(key): ZoneGuidanceEntry(
                key=str(key),
                match=tuple(str(m).casefold() for m in entry.get("match", [])),
                title=str(entry["title"]),
                guidance=str(entry["guidance"]).strip(),
                action=(
                    str(entry["action"]).strip() if entry.get("action") else None
                ),
                guidance_unknown_regime=(
                    str(entry["guidance_unknown_regime"]).strip()
                    if entry.get("guidance_unknown_regime")
                    else None
                ),
            )
            for key, entry in (raw.get("zones") or {}).items()
        }
        misc = {
            str(key): str(entry.get("guidance") or "").strip()
            for key, entry in (raw.get("misc") or {}).items()
            if isinstance(entry, dict)
        }
        return ZoneGuidance(zones=zones, misc=misc)
    except (KeyError, TypeError, ValueError) as exc:
        raise QuickRulesError(f"zone_guidance.yaml: битая структура: {exc}") from exc


def match_guidance(
    guidance: ZoneGuidance, *, zone_type: str | None, name: str
) -> ZoneGuidanceEntry | None:
    """Первая запись guidance, чьи match-подстроки встретились в типе/названии зоны."""
    haystack = f"{zone_type or ''} {name}".casefold()
    for entry in guidance.zones.values():
        if any(token in haystack for token in entry.match):
            return entry
    return None


# ---------------------------------------------------------------------------
# Методика 6.2: группы, бэнды охвата, статусы, вердикт
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Band:
    """Бэнд вычета по охвату: `above` — строго больше, `at_least` — от включительно."""

    deduction: float
    above: float | None = None
    at_least: float | None = None

    def matches(self, coverage: float) -> bool:
        if self.above is not None:
            return coverage > self.above
        if self.at_least is not None:
            return coverage >= self.at_least
        return True


@dataclass(frozen=True)
class FactorRule:
    """Фактор группы: совпадение по подстрокам + вычет (фикс или по бэндам)."""

    id: str
    title: str
    match: tuple[str, ...] = ()
    match_all: tuple[str, ...] = ()
    bands: tuple[Band, ...] = ()
    deduction: float | None = None

    def matches(self, haystack: str) -> bool:
        if self.match_all:
            return all(token in haystack for token in self.match_all)
        return any(token in haystack for token in self.match)

    def deduction_for(self, coverage: float) -> float:
        for band in self.bands:
            if band.matches(coverage):
                return band.deduction
        if self.deduction is not None:
            return self.deduction
        return 0.0


@dataclass(frozen=True)
class GroupRule:
    key: str
    title: str
    factors: tuple[FactorRule, ...]


@dataclass(frozen=True)
class StatusDef:
    key: str
    title: str
    summary: str


@dataclass(frozen=True)
class ScoringRules:
    """Методика 6.2 из scoring_rules.yaml."""

    methodology_version: str
    base_score: int
    score_min: int
    score_max: int
    group_cap: float
    statuses: tuple[StatusDef, ...]
    attention_score_below: int
    attention_group_deduction: float
    coverage_words: tuple[tuple[int, str], ...]
    comm_weights: dict[str, float]
    comm_max: float
    area_equal_deduction: float
    config_test_m: float
    config_control_m: float
    config_tight_deduction: float
    config_nofit_deduction: float
    config_grid_step_m: float
    config_fine_grid_step_m: float
    groups: tuple[GroupRule, ...]
    texts: dict[str, str]
    universal_scale: dict[str, float]
    q_regime_unknown: float

    def coverage_word(self, percent: float) -> str:
        for min_pct, word in self.coverage_words:
            if percent >= min_pct:
                return word
        return self.coverage_words[-1][1] if self.coverage_words else ""

    def status(self, key: str) -> StatusDef:
        for status in self.statuses:
            if status.key == key:
                return status
        raise QuickRulesError(f"scoring_rules.yaml: неизвестный статус {key!r}")

    def classify(
        self, zone_type: str | None, name: str
    ) -> tuple[GroupRule, FactorRule] | None:
        """Группа и фактор зоны по типу/названию; None — нет правила (группа 6)."""
        haystack = f"{zone_type or ''} {name}".casefold()
        for group in self.groups:
            for factor in group.factors:
                if factor.matches(haystack):
                    return group, factor
        return None

    def factor_by_id(self, factor_id: str) -> tuple[GroupRule, FactorRule] | None:
        """Группа и фактор спецправила по его id (для каталога 6.2)."""
        for group in self.groups:
            for factor in group.factors:
                if factor.id == factor_id:
                    return group, factor
        return None


def _load_bands(raw_bands: Any) -> tuple[Band, ...]:
    bands: list[Band] = []
    for item in raw_bands or []:
        bands.append(
            Band(
                deduction=float(item["deduction"]),
                above=(
                    float(item["above"]) if item.get("above") is not None else None
                ),
                at_least=(
                    float(item["at_least"])
                    if item.get("at_least") is not None
                    else None
                ),
            )
        )
    return tuple(bands)


def load_scoring_rules(path: Path | None = None) -> ScoringRules:
    """Загружает scoring_rules.yaml; без path — пакетный актив."""
    raw = _load_yaml(path or _ASSETS / "scoring_rules.yaml")
    try:
        thresholds = raw["thresholds"]
        communications = raw["communications"]
        area = raw["area"]
        configuration = raw["configuration"]
        groups: list[GroupRule] = []
        for raw_group in raw["groups"]:
            factors: list[FactorRule] = []
            for item in raw_group["factors"]:
                factors.append(
                    FactorRule(
                        id=str(item["id"]),
                        title=str(item["title"]),
                        match=tuple(
                            str(m).casefold() for m in (item.get("match") or [])
                        ),
                        match_all=tuple(
                            str(m).casefold() for m in (item.get("match_all") or [])
                        ),
                        bands=_load_bands(item.get("bands")),
                        deduction=(
                            float(item["deduction"])
                            if item.get("deduction") is not None
                            else None
                        ),
                    )
                )
            groups.append(
                GroupRule(
                    key=str(raw_group["key"]),
                    title=str(raw_group["title"]),
                    factors=tuple(factors),
                )
            )
        return ScoringRules(
            methodology_version=str(raw["methodology_version"]),
            base_score=int(raw["base_score"]),
            score_min=int(raw["score_min"]),
            score_max=int(raw["score_max"]),
            group_cap=float(raw["group_cap"]),
            statuses=tuple(
                StatusDef(
                    key=str(item["key"]),
                    title=str(item["title"]),
                    summary=str(item["summary"]),
                )
                for item in raw["statuses"]
            ),
            attention_score_below=int(thresholds["attention_score_below"]),
            attention_group_deduction=float(thresholds["attention_group_deduction"]),
            coverage_words=tuple(
                (int(item["min_pct"]), str(item["word"]))
                for item in raw["coverage_words"]
            ),
            comm_weights={
                str(key): float(value)
                for key, value in communications.items()
                if key != "max"
            },
            comm_max=float(communications["max"]),
            area_equal_deduction=float(area["equal_deduction"]),
            config_test_m=float(configuration["test_square_m"]),
            config_control_m=float(configuration["control_square_m"]),
            config_tight_deduction=float(configuration["tight_deduction"]),
            config_nofit_deduction=float(configuration["no_fit_deduction"]),
            config_grid_step_m=float(configuration["grid_step_m"]),
            config_fine_grid_step_m=float(configuration["fine_grid_step_m"]),
            groups=tuple(groups),
            texts={
                str(key): str(value).strip()
                for key, value in (raw.get("texts") or {}).items()
            },
            universal_scale={
                str(key).upper(): float(value)
                for key, value in (raw.get("universal_scale") or {}).items()
            },
            q_regime_unknown=float(raw.get("q_regime_unknown", 30)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise QuickRulesError(f"scoring_rules.yaml: битая структура: {exc}") from exc


# ---------------------------------------------------------------------------
# Каталог классов ЗОУИТ ст. 105 ЗК РФ (zouit_catalog_63.yaml) — методика 6.3
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogEntry:
    """Класс каталога: пункт ст. 105 (или внекаталожный фактор) → группа, обработка."""

    item: str  # номер пункта ст. 105 либо ключ внекаталожного фактора
    class_name: str
    group_key: str
    match: tuple[str, ...]
    handling: str  # "special:<factor_id>" | "airport" | "universal"
    obsolete: bool = False


@dataclass(frozen=True)
class RegimeEffect:
    """Курируемое подтверждённое влияние режима зоны: E-уровень и основание."""

    effect: str  # E0..E4
    evidence: str
    registry_number: str | None = None
    match: str | None = None  # фрагмент имени зоны (casefold)


@dataclass(frozen=True)
class ZouitCatalog:
    entries: tuple[CatalogEntry, ...]
    regime_effects: tuple[RegimeEffect, ...]
    groups: dict[str, str]  # group_key → название группы для протокола

    def classify(self, zone_type: str | None, name: str) -> CatalogEntry | None:
        """Класс каталога по типу/названию зоны; первое совпадение выигрывает."""
        haystack = f"{zone_type or ''} {name}".casefold()
        for entry in self.entries:
            if any(token in haystack for token in entry.match):
                return entry
        return None

    def regime_effect_for(
        self, *, registry_number: str | None, name: str
    ) -> RegimeEffect | None:
        """Курируемый подтверждённый режим: точный registry_number, затем имя."""
        registry = (registry_number or "").strip()
        if registry:
            for effect in self.regime_effects:
                if effect.registry_number and effect.registry_number == registry:
                    return effect
        folded = " ".join(name.casefold().split())
        for effect in self.regime_effects:
            if effect.match and effect.match in folded:
                return effect
        return None


def load_zouit_catalog(path: Path | None = None) -> ZouitCatalog:
    """Загружает zouit_catalog_63.yaml; без path — пакетный актив."""
    raw = _load_yaml(path or _ASSETS / "zouit_catalog_63.yaml")
    try:
        entries: list[CatalogEntry] = []
        for section in ("catalog", "extra"):
            for item in raw.get(section) or []:
                entries.append(
                    CatalogEntry(
                        item=str(item.get("item") or item["key"]),
                        class_name=str(item["class"]),
                        group_key=str(item["group"]),
                        match=tuple(
                            str(m).casefold() for m in (item.get("match") or [])
                        ),
                        handling=str(item["handling"]),
                        obsolete=bool(item.get("obsolete", False)),
                    )
                )
        effects = tuple(
            RegimeEffect(
                effect=str(item["effect"]).upper(),
                evidence=str(item.get("evidence") or "").strip(),
                registry_number=(
                    str(item["registry_number"]).strip()
                    if item.get("registry_number")
                    else None
                ),
                match=(
                    " ".join(str(item["match"]).casefold().split())
                    if item.get("match")
                    else None
                ),
            )
            for item in (raw.get("regime_effects") or [])
        )
        return ZouitCatalog(
            entries=tuple(entries),
            regime_effects=effects,
            groups={
                str(key): str(value).strip()
                for key, value in (raw.get("groups") or {}).items()
            },
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise QuickRulesError(f"zouit_catalog_63.yaml: битая структура: {exc}") from exc


# ---------------------------------------------------------------------------
# Вердикт
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoredFactor:
    """Классифицированный фактор ограничения с вычетом (после слияния дублей)."""

    group_key: str
    group_title: str
    factor_title: str
    zone_name: str
    source: str
    coverage: float
    deduction: float
    display_name: str = ""  # короткое имя для отчёта; пустое → factor_title
    # --- методика 6.2 ---
    catalog_item: str = ""  # пункт ст. 105 ЗК РФ либо ключ внекаталожного фактора
    effect_code: str = ""  # E0..E4 | Q30 | special:<rule_id>
    deduction_kind: str = "d"  # d — подтверждённое влияние; q — неопределённость режима
    regime_state: str = "known"  # known | unknown
    act_number: str | None = None  # акт, устанавливающий зону (если найден)
    act_date: str | None = None
    registry_number: str | None = None  # реестровый номер зоны (zone_id протокола)
    # --- методика 6.3 ---
    # False — информационная запись (INFO_MELIORATION): в сумму вычетов не
    # входит, в протоколе и отчёте остаётся видимой (6.3 §6.1.6).
    contributes_to_score: bool = True
    decision_reason: str = ""  # почему выбран этот маршрут (протокол §10)


@dataclass(frozen=True)
class GroupDeduction:
    key: str
    title: str
    deduction: float  # после потолка группы
    zone_names: tuple[str, ...]  # короткие отображаемые имена зон группы
    raw_deduction: float = 0.0  # сумма до потолка (протокол скоринга)
    q_part: float = 0.0  # часть суммы из компонентов q (неопределённость режима)


@dataclass(frozen=True)
class VerdictResult:
    """Итог скоринга 6.2: балл (None — не рассчитан), статус, причины."""

    score: int | None
    status: StatusDef
    preliminary: bool
    reasons: tuple[str, ...]
    explanation: str
    geometry_note: str | None
    u: float
    a: float | None
    c: float | None
    g: float | None
    groups: tuple[GroupDeduction, ...]
    notes: tuple[str, ...]  # внутренний протокол → warnings отчёта
    q_total: float = 0.0  # сумма компонентов q (неопределённость режима, 6.2 §12)


def compute_verdict(
    rules: ScoringRules,
    scored_factors: list[ScoredFactor],
    *,
    u: float,
    a: float | None,
    c: float | None,
    stop_reasons: list[str],
    suspend_reasons: list[str],
    preliminary_flags: list[str],
) -> VerdictResult:
    """Балл и статус по методике 6.2.

    stop_reasons — подтверждённые стоп-факторы (статус stop без балла);
    suspend_reasons — нет ключевых данных / приостановка по п. 3.3 (статус
    insufficient без балла); preliminary_flags — некритичные пробелы и
    консервативные допущения (отметка «предварительная»).
    """
    notes: list[str] = []
    totals: dict[str, GroupDeduction] = {}
    for factor in scored_factors:
        display = factor.display_name or factor.factor_title
        if not factor.contributes_to_score:
            # 6.3: информационные записи (INFO_MELIORATION) видимы в протоколе,
            # но не входят в суммы вычетов (deduction хранится нулевым).
            notes.append(
                f"информационная запись вне скоринга: {display!r}"
                + (f" — {factor.decision_reason}" if factor.decision_reason else "")
            )
            continue
        q_part = factor.deduction if factor.deduction_kind == "q" else 0.0
        current = totals.get(factor.group_key)
        if current is None:
            totals[factor.group_key] = GroupDeduction(
                key=factor.group_key,
                title=factor.group_title,
                deduction=factor.deduction,
                zone_names=(display,),
                q_part=q_part,
            )
        else:
            merged = GroupDeduction(
                key=current.key,
                title=current.title,
                deduction=current.deduction + factor.deduction,
                zone_names=(*current.zone_names, display),
                raw_deduction=current.raw_deduction,
                q_part=current.q_part + q_part,
            )
            totals[factor.group_key] = merged
    groups = tuple(
        GroupDeduction(
            key=group.key,
            title=group.title,
            deduction=min(group.deduction, rules.group_cap),
            zone_names=group.zone_names,
            raw_deduction=group.deduction,
            q_part=group.q_part,
        )
        for group in totals.values()
    )
    for group in groups:
        if group.deduction != totals[group.key].deduction:
            notes.append(
                f"группа {group.key}: вычет усечён потолком {rules.group_cap:g}"
            )
    restrictions_total = sum(group.deduction for group in groups)
    q_total = sum(group.q_part for group in groups)
    q_zones = [
        factor.zone_name for factor in scored_factors if factor.deduction_kind == "q"
    ]
    for zone_name in q_zones:
        notes.append(f"Q30: режим зоны не установлен — {zone_name!r}")

    g: float | None = None
    if a is not None or c is not None:
        g = max(a or 0.0, c or 0.0)

    explanation = rules.texts.get("score_explanation", "")
    if stop_reasons:
        return VerdictResult(
            score=None,
            status=rules.status("stop"),
            preliminary=False,
            reasons=tuple(stop_reasons),
            explanation=explanation,
            geometry_note=None,
            u=u,
            a=a,
            c=c,
            g=g,
            groups=groups,
            notes=tuple(notes),
            q_total=q_total,
        )
    if suspend_reasons:
        return VerdictResult(
            score=None,
            status=rules.status("insufficient"),
            preliminary=False,
            reasons=tuple(suspend_reasons),
            explanation=explanation,
            geometry_note=None,
            u=u,
            a=a,
            c=c,
            g=g,
            groups=groups,
            notes=tuple(notes),
            q_total=q_total,
        )

    score = round(rules.base_score - restrictions_total - u - (g or 0.0))
    score = max(rules.score_min, min(rules.score_max, score))

    reasons: list[str] = []
    for group in sorted(groups, key=lambda item: -item.deduction):
        if group.deduction <= 0:
            continue
        names = [
            name
            for name in dict.fromkeys(group.zone_names)
            if name != group.title
        ]
        reason = f"{group.title}: {' и '.join(names)}" if names else group.title
        if group.q_part > 0:
            reason += " — режим зоны не установлен (снижение за неопределённость)"
        reasons.append(reason)
    if a and a > 0:
        reasons.append(rules.texts.get("area_at_minimum", ""))
    if c == rules.config_nofit_deduction:
        reasons.append(rules.texts.get("geometry_no_fit", ""))
    elif c == rules.config_tight_deduction:
        reasons.append(rules.texts.get("geometry_tight", ""))
    if u > 0:
        reasons.append(rules.texts.get("communications_unknown", ""))

    has_q = q_total > 0
    significant_group = any(
        group.deduction >= rules.attention_group_deduction for group in groups
    )
    # 6.2 §8: при Q>0 оценка всегда предварительная и «повышенное внимание»,
    # даже если арифметика после группового потолка оставляет высокий балл.
    status = (
        rules.status("attention")
        if score < rules.attention_score_below or significant_group or has_q
        else rules.status("ok")
    )
    preliminary = u > 0 or bool(preliminary_flags) or has_q
    if preliminary_flags:
        notes.extend(f"предварительная: {flag}" for flag in preliminary_flags)
    geometry_note = None
    if c == 0:
        geometry_note = rules.texts.get("geometry_ok") or None
    elif c == rules.config_tight_deduction:
        geometry_note = rules.texts.get("geometry_tight") or None
    elif c == rules.config_nofit_deduction:
        geometry_note = rules.texts.get("geometry_no_fit") or None
    return VerdictResult(
        score=score,
        status=status,
        preliminary=preliminary,
        reasons=tuple(reason for reason in reasons if reason)[:3],
        explanation=explanation,
        geometry_note=geometry_note,
        u=u,
        a=a,
        c=c,
        g=g,
        groups=groups,
        notes=tuple(notes),
        q_total=q_total,
    )


# ---------------------------------------------------------------------------
# Тексты блока «Что ещё нужно проверить» (missing_texts.yaml)
# ---------------------------------------------------------------------------


def load_missing_texts(path: Path | None = None) -> dict[str, str]:
    """Загружает missing_texts.yaml; без path — пакетный актив."""
    raw = _load_yaml(path or _ASSETS / "missing_texts.yaml")
    return {str(key): str(value).strip() for key, value in raw.items()}


# ---------------------------------------------------------------------------
# Шаблоны раздела «Что уже удалось установить» (narrative_templates.yaml)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NarrativeTemplate:
    """Один утверждённый шаблон: буква варианта, status_key вердикта, текст."""

    variant: str
    status_key: str
    text: str


def load_narrative_templates(path: Path | None = None) -> dict[str, NarrativeTemplate]:
    """Загружает narrative_templates.yaml; ключ результата — status_key.

    Выбор варианта детерминированный (status_key вердикта методики 6.2);
    тексты — утверждённый продуктовый слой, в methodology_sha256 не входят.
    """
    raw = _load_yaml(path or _ASSETS / "narrative_templates.yaml")
    variants = raw.get("variants")
    if not isinstance(variants, dict) or not variants:
        raise QuickRulesError("narrative_templates.yaml: пустой блок variants")
    result: dict[str, NarrativeTemplate] = {}
    for variant, entry in variants.items():
        if not isinstance(entry, dict):
            raise QuickRulesError(
                f"narrative_templates.yaml: вариант {variant!r} должен быть mapping"
            )
        try:
            template = NarrativeTemplate(
                variant=str(variant),
                status_key=str(entry["status"]),
                text=str(entry["text"]).strip(),
            )
        except KeyError as exc:
            raise QuickRulesError(
                f"narrative_templates.yaml: вариант {variant!r}: нет поля {exc}"
            ) from exc
        if not template.text:
            raise QuickRulesError(
                f"narrative_templates.yaml: вариант {variant!r}: пустой text"
            )
        if template.status_key in result:
            raise QuickRulesError(
                f"narrative_templates.yaml: дубль status {template.status_key!r}"
            )
        result[template.status_key] = template
    return result


# ---------------------------------------------------------------------------
# Коммерческий блок (marketing.yaml) — §37–39 ТЗ
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketingService:
    """Одна коммерческая услуга next-step блока."""

    key: str
    name: str
    description: str
    price_rub: int | None = None
    price_from_rub: int | None = None
    credit_rub: int | None = None


@dataclass(frozen=True)
class MarketingConfig:
    """Статическая коммерческая конфигурация QuickReport.

    Цены и CTA — только отсюда; LLM их не генерирует (§39 ТЗ).
    """

    report_title: str
    services: tuple[MarketingService, ...]
    cta: str
    sha256: str


def load_marketing(path: Path | None = None) -> MarketingConfig:
    """Загружает marketing.yaml; без path — пакетный актив."""
    asset = path or _ASSETS / "marketing.yaml"
    raw = _load_yaml(asset)
    try:
        services = tuple(
            MarketingService(
                key=str(item["key"]),
                name=str(item["name"]),
                description=str(item.get("description") or "").strip(),
                price_rub=(
                    int(item["price_rub"]) if item.get("price_rub") is not None else None
                ),
                price_from_rub=(
                    int(item["price_from_rub"])
                    if item.get("price_from_rub") is not None
                    else None
                ),
                credit_rub=(
                    int(item["credit_rub"])
                    if item.get("credit_rub") is not None
                    else None
                ),
            )
            for item in raw["services"]
        )
        return MarketingConfig(
            report_title=str(raw["report_title"]),
            services=services,
            cta=str(raw["cta"]).strip(),
            sha256=hashlib.sha256(asset.read_bytes()).hexdigest(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise QuickRulesError(f"marketing.yaml: битая структура: {exc}") from exc
