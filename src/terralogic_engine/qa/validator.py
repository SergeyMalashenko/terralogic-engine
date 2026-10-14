"""Детерминированный валидатор QuickReport (§64 ТЗ).

Проверяет кодом то, что не нужно отдавать LLM: балл и статус соответствуют
модели, площадь и ВРИ — контексту, обязательные секции на месте, цены — из
MarketingConfig, дисклеймер не изменён. Работает по уже построенным
``QuickReportModel`` + Markdown, без сети и без LLM. Неизвестное состояние
помечается ``warning``, а не ``error``: валидатор не должен блокировать
отчёт по собственным пробелам.
"""

from __future__ import annotations

import re

from terralogic_engine.quickreport.model import REPORT_LIMITATIONS, QuickReportModel

from .models import QaIssue, ValidatorResult

_SCORED_HEAD_RE = re.compile(r"^## Оценка участка — (\d+)/100\b", re.MULTILINE)
_UNSCORED_HEAD_RE = re.compile(r"^## Оценка не рассчитана\b", re.MULTILINE)
# Цена в маркетинговом блоке: «30 000 ₽» / «от 45 000 ₽»
_PRICE_RE = re.compile(r"(?:от\s+)?(\d[\d ]*)\s*₽")

_SECTION_SCORED = "## Оценка участка"
_SECTION_UNSCORED = "## Оценка не рассчитана"


def _issue(
    rule_id: str, section: str, severity: str, message: str, **extra: object
) -> QaIssue:
    return QaIssue(
        rule_id=rule_id,
        section=section,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        **extra,  # type: ignore[arg-type]
    )


def _check_score(model: QuickReportModel, markdown: str) -> list[QaIssue]:
    issues: list[QaIssue] = []
    scored = _SCORED_HEAD_RE.search(markdown)
    unscored = _UNSCORED_HEAD_RE.search(markdown)
    if model.score is None:
        if scored is not None or unscored is None:
            issues.append(
                _issue(
                    "score_matches_model",
                    "Оценка",
                    "error",
                    "модель без балла, а в заголовке балл присутствует"
                    " (или нет отметки «не рассчитана»)",
                    suggested_action="пересобрать вердикт из QuickReportModel",
                )
            )
        return issues
    if scored is None:
        issues.append(
            _issue(
                "score_matches_model",
                "Оценка",
                "error",
                f"модель даёт {model.score}/100, а строка балла не найдена",
            )
        )
        return issues
    if int(scored.group(1)) != model.score:
        issues.append(
            _issue(
                "score_matches_model",
                "Оценка",
                "critical",
                f"в тексте {scored.group(1)}/100, в модели {model.score}/100",
                claim=scored.group(0),
                suggested_action="балл должен происходить только из методики 6.1",
            )
        )
    return issues


def _check_status(model: QuickReportModel, markdown: str) -> list[QaIssue]:
    verdict = model.status
    head_match = re.search(
        r"^## Оценка[^\n]*\(([^)]*)\)", markdown, flags=re.MULTILINE
    )
    if head_match is None:
        return [
            _issue(
                "status_matches_score",
                "Оценка",
                "warning",
                "строка статуса в заголовке не найдена — нечего сверять",
            )
        ]
    marks = head_match.group(1)
    issues: list[QaIssue] = []
    if verdict.status_title not in marks:
        issues.append(
            _issue(
                "status_matches_score",
                "Оценка",
                "error",
                f"статус модели «{verdict.status_title}» отсутствует в заголовке",
                claim=head_match.group(0),
            )
        )
    if verdict.preliminary and "предварительная" not in marks:
        issues.append(
            _issue(
                "status_matches_score",
                "Оценка",
                "error",
                "модель предварительная, а отметки «предварительная» нет",
            )
        )
    return issues


def _check_area(model: QuickReportModel, markdown: str) -> list[QaIssue]:
    area = model.parcel_passport.area_m2
    if area is None:
        return []
    formatted = f"{area:,.0f}".replace(",", " ")
    if f"{formatted} м²" in markdown:
        return []
    return [
        _issue(
            "area_matches_context",
            "Паспорт участка",
            "error",
            f"площадь модели {formatted} м² не найдена в тексте паспорта",
        )
    ]


def _check_vri(model: QuickReportModel, markdown: str) -> list[QaIssue]:
    parcel = model.parcel_passport
    if parcel.vri_origin != "LEGAL" or parcel.vri is None:
        return []
    issues: list[QaIssue] = []
    if f"ВРИ {parcel.vri.code}" not in markdown:
        issues.append(
            _issue(
                "vri_matches_legal",
                "Паспорт участка",
                "error",
                f"код ВРИ {parcel.vri.code} из классификатора не найден в тексте",
            )
        )
    if parcel.vri.name.casefold() not in markdown.casefold():
        issues.append(
            _issue(
                "vri_matches_legal",
                "Паспорт участка",
                "error",
                "наименование ВРИ из классификатора не совпадает с текстом",
            )
        )
    return issues


def _check_zone_names(model: QuickReportModel, markdown: str) -> list[QaIssue]:
    issues: list[QaIssue] = []
    for factor in model.attention_factors:
        if factor.informational:
            continue
        if f"| {_cell_text(factor.zone_name)} |" not in markdown:
            issues.append(
                _issue(
                    "zone_names_match",
                    "Факторы, требующие внимания",
                    "error",
                    f"зона «{factor.zone_name}» из модели не найдена в таблице",
                )
            )
    return issues


def _cell_text(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _mandatory_sections(model: QuickReportModel) -> list[str]:
    sections = [
        "# Экспресс-оценка земельного участка",
        "## Что уже удалось установить",
        "## Факторы, требующие внимания",
        "## Паспорт участка",
        "## Территориальная зона и разрешённое использование",
        "## Окружение",
        "## Что ещё нужно проверить",
        "## Схема расположения зон",
        "## Ограничения отчёта",
    ]
    if model.marketing is not None:
        sections.append("## Следующий шаг")
    return sections


def _check_sections(model: QuickReportModel, markdown: str) -> list[QaIssue]:
    issues: list[QaIssue] = []
    if _SECTION_SCORED not in markdown and _SECTION_UNSCORED not in markdown:
        issues.append(
            _issue(
                "mandatory_sections",
                "Оценка",
                "critical",
                "отсутствует секция оценки участка",
            )
        )
    for section in _mandatory_sections(model):
        if section not in markdown:
            issues.append(
                _issue(
                    "mandatory_sections",
                    section.lstrip("# "),
                    "critical",
                    f"обязательная секция отсутствует: {section!r}",
                    suggested_action="секции QuickReport фиксированы продуктовым контрактом",
                )
            )
    return issues


def _check_prices(model: QuickReportModel, markdown: str) -> list[QaIssue]:
    marketing = model.marketing
    if marketing is None:
        if "## Следующий шаг" in markdown:
            return [
                _issue(
                    "prices_match_config",
                    "Следующий шаг",
                    "error",
                    "коммерческий блок присутствует без MarketingConfig",
                )
            ]
        return []
    block = markdown.split("## Следующий шаг", 1)[-1]
    expected: set[str] = set()
    for service in marketing.services:
        if service.price_rub is not None:
            expected.add(f"{service.price_rub:,}".replace(",", " "))
            if service.credit_rub:
                credited = service.price_rub - service.credit_rub
                expected.add(f"{credited:,}".replace(",", " "))
        if service.price_from_rub is not None:
            expected.add(f"{service.price_from_rub:,}".replace(",", " "))
    issues: list[QaIssue] = []
    for match in _PRICE_RE.finditer(block):
        found = match.group(1).replace(" ", "")
        if found not in {value.replace(" ", "") for value in expected}:
            issues.append(
                _issue(
                    "prices_match_config",
                    "Следующий шаг",
                    "critical",
                    f"цена {match.group(0)!r} не соответствует MarketingConfig",
                    claim=match.group(0),
                    suggested_action="цены задаются только в marketing.yaml",
                )
            )
    for value in expected:
        if value not in block:
            issues.append(
                _issue(
                    "prices_match_config",
                    "Следующий шаг",
                    "error",
                    f"цена из конфигурации {value} ₽ отсутствует в блоке",
                )
            )
    return issues


def _check_limitations(markdown: str) -> list[QaIssue]:
    if REPORT_LIMITATIONS in markdown:
        return []
    return [
        _issue(
            "limitations_static",
            "Ограничения отчёта",
            "critical",
            "текст ограничений изменён — юридически значимый шаблон статичен",
            suggested_action="вернуть REPORT_LIMITATIONS без изменений",
        )
    ]


def _check_factors_evidence(
    model: QuickReportModel, context: object | None
) -> list[QaIssue]:
    """Каждый фактор трассируется к протоколу скоринга (§64: evidence).

    ``context`` — QuickReportContext (protocol); None → проверка пропускается.
    """
    protocol = getattr(context, "protocol", None)
    if protocol is None:
        return []
    informational_ids = {"communications_unknown"}
    issues: list[QaIssue] = []
    for factor in model.attention_factors:
        if factor.informational and factor.rule_id in informational_ids:
            continue
        traceable = any(
            factor.zone_name == item.display_name or factor.title == item.factor_title
            for item in protocol.factors
        )
        if not traceable and factor.rule_id not in informational_ids:
            issues.append(
                _issue(
                    "factors_have_evidence",
                    "Факторы, требующие внимания",
                    "warning",
                    f"фактор {factor.rule_id} («{factor.zone_name}») не трассируется"
                    " к протоколу скоринга",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Методика 6.2 (§10): инварианты Q/BLOCKING/STOP в протоколе
# ---------------------------------------------------------------------------


def _check_q30_preliminary(
    model: QuickReportModel, context: object | None
) -> list[QaIssue]:
    """Q>0 → оценка всегда предварительная и «повышенное внимание» (§8).

    Применимо только к отчётам с выданным баллом: при BLOCKING_UNKNOWN/STOP
    (приоритет §2) числовая оценка не выдаётся, и компоненты Q в протоколе
    остаются просто записями о неустановленных режимах.
    """
    protocol = getattr(context, "protocol", None)
    if protocol is None or protocol.q_total <= 0 or model.score is None:
        return []
    issues: list[QaIssue] = []
    if not model.status.preliminary:
        issues.append(
            _issue(
                "q30_requires_preliminary",
                "Оценка",
                "error",
                f"в протоколе Q = {protocol.q_total:g}, а отчёт не отмечен "
                "«предварительная»",
                suggested_action="Q30 всегда даёт предварительную оценку (§12)",
            )
        )
    if model.status.status_key != "attention":
        issues.append(
            _issue(
                "q30_requires_preliminary",
                "Оценка",
                "error",
                f"в протоколе Q = {protocol.q_total:g}, а статус "
                f"«{model.status.status_title}» вместо «повышенное внимание»",
            )
        )
    return issues


def _check_factor_integrity(context: object | None) -> list[QaIssue]:
    """Целостность факторов протокола (6.2 §10).

    Вычет без основания (нет effect_code), дубль одной зоны (две записи с
    одним реестровым номером/наименованием), одновременное начисление d и q
    по одной зоне — ошибки публикации.
    """
    protocol = getattr(context, "protocol", None)
    if protocol is None:
        return []
    issues: list[QaIssue] = []
    for factor in protocol.factors:
        if not factor.effect_code or not factor.catalog_item:
            issues.append(
                _issue(
                    "deduction_has_basis",
                    "Классификация зон",
                    "error",
                    f"вычет без основания: «{factor.zone_name}» "
                    f"({_g_safe(factor.deduction)}) без effect_code/класса каталога",
                )
            )
    by_zone: dict[str, list[object]] = {}
    for factor in protocol.factors:
        key = (
            factor.registry_number
            or " ".join(factor.zone_name.casefold().split())
        )
        by_zone.setdefault(key, []).append(factor)
    for key, items in by_zone.items():
        if len(items) < 2:
            continue
        kinds = {getattr(item, "deduction_kind", "d") for item in items}
        if kinds == {"d", "q"}:
            issues.append(
                _issue(
                    "no_dq_overlap",
                    "Классификация зон",
                    "error",
                    f"по зоне «{key}» начислены и d, и q — по одной зоне "
                    "одновременно не начисляются (§3)",
                )
            )
        else:
            issues.append(
                _issue(
                    "no_duplicate_factor",
                    "Классификация зон",
                    "error",
                    f"дубль фактора: зона «{key}» учтена {len(items)} раза",
                )
            )
    return issues


def _g_safe(value: float) -> str:
    return f"{value:g}"


def validate_quickreport(
    model: QuickReportModel,
    markdown: str,
    *,
    context: object | None = None,
) -> ValidatorResult:
    """Все детерминированные проверки отчёта; fail — есть error/critical."""
    issues: list[QaIssue] = []
    issues.extend(_check_score(model, markdown))
    issues.extend(_check_status(model, markdown))
    issues.extend(_check_area(model, markdown))
    issues.extend(_check_vri(model, markdown))
    issues.extend(_check_zone_names(model, markdown))
    issues.extend(_check_sections(model, markdown))
    issues.extend(_check_prices(model, markdown))
    issues.extend(_check_limitations(markdown))
    issues.extend(_check_factors_evidence(model, context))
    issues.extend(_check_q30_preliminary(model, context))
    issues.extend(_check_factor_integrity(context))
    status = (
        "fail"
        if any(issue.severity in ("error", "critical") for issue in issues)
        else "pass"
    )
    return ValidatorResult(status=status, issues=issues)
