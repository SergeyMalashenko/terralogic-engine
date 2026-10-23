"""Детерминированный рендер QuickReportModel в Markdown.

Продуктовая структура отчёта (§23 ТЗ, стандарт оформления v22): заголовок
с датами и версией методики, вердикт, «Что уже удалось установить»,
таблица факторов, паспорт участка (таблица), территориальная зона,
окружение с коммуникациями, схема расположения зон (PNG из ``mapimg``
либо заглушка) с ограничениями отчёта под картой, «Что ещё нужно
проверить», статический дисклеймер и коммерческий «Следующий шаг»
(только из MarketingConfig). Новых зависимостей нет — чистая сборка
строк. Единственный опциональный LLM-слот — ``model.narrative
.established_summary`` (текст раздела «Что уже удалось установить»,
готовится NarrativeGenerator'ом до рендера); сам рендер LLM не вызывает,
без narrative Markdown байт-стабилен.

Публичный ``render_quickreport(context, ...)`` сохранён как фасад:
``build_quick_model`` → рендер модели (§78–79 ТЗ).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from .model import QuickMarketing, QuickReportModel, build_quick_model
from .models import (
    QuickFactor,
    QuickMissingItem,
    QuickPassport,
    QuickReportContext,
    QuickVerdict,
    QuickZoneRegulations,
    QuickZoneUse,
)
from .rules import MarketingConfig

SCORE_SCALE = 100

_MAP_PLACEHOLDER = "Схема расположения зон приводится в подробном заключении."

_NO_FACTORS_NOTE = (
    "По собранным данным зоны с особыми условиями использования территории "
    "на участок не попадают."
)

_NO_MISSING_NOTE = "Пробелов в собранных данных не выявлено."

_NO_FACTS_NOTE = "Подтверждённых сведений в собранных источниках нет."

# Статический юридический блок в конце отчёта (без разрыва страницы).
_RIGHTS_USAGE_PARAGRAPHS = (
    (
        "Отчёт предназначен для использования заказчиком. Допускается его передача "
        "специалистам, привлечённым заказчиком для оценки участка, подготовки "
        "и реализации проекта."
    ),
    (
        "Копирование и воспроизведение охраняемых авторским правом материалов "
        "отчёта, их публикация, перепродажа и использование в составе коммерческих "
        "продуктов третьих лиц без письменного согласия правообладателя запрещены, "
        "кроме случаев, предусмотренных законодательством."
    ),
)


def _format_date(value: date) -> str:
    return f"{value:%d.%m.%Y}"


def _format_number(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def _format_area(area_m2: float | None) -> str | None:
    if area_m2 is None:
        return None
    return f"{_format_number(area_m2)} м²"


def _format_cadastral_value(value_rub: float | None) -> str | None:
    if value_rub is None:
        return None
    return f"{_format_number(value_rub)} руб. (не является рыночной ценой)"


def _cell(text: str) -> str:
    """Безопасная ячейка Markdown-таблицы: без переводов строк и pipe."""
    return " ".join(text.split()).replace("|", "\\|")


def _capitalize(word: str) -> str:
    return word[:1].upper() + word[1:] if word else word


def _factors_table(factors: list[QuickFactor]) -> list[str]:
    zone_factors = [factor for factor in factors if not factor.informational]
    if not zone_factors:
        return [_NO_FACTORS_NOTE]
    lines = [
        "| № | Ограничение | Охват участка | Практическое значение |",
        "| --- | --- | --- | --- |",
    ]
    for number, factor in enumerate(zone_factors, start=1):
        lines.append(
            f"| {number} | {_cell(factor.zone_name)} | "
            f"{_cell(_capitalize(factor.coverage_word))} | "
            f"{_cell(factor.guidance)} |"
        )
    return lines


def _permitted_use_display(parcel: QuickPassport) -> str | None:
    """Исходный текст ВРИ источника + каноническая ссылка на классификатор."""
    text = parcel.permitted_use
    vri = parcel.vri
    if vri is None:
        return text
    suffix = f"ВРИ {vri.code}"
    if text is None or vri.name.casefold() != text.casefold():
        suffix += f" — {vri.name}"
    suffix += f" (П/0412, ред. {vri.revision_date})"
    return f"{text} · {suffix}" if text else suffix


def _passport_lines(parcel: QuickPassport) -> list[str]:
    status_area = " · ".join(
        part for part in (parcel.status, _format_area(parcel.area_m2)) if part
    )
    rows = (
        ("Адрес", parcel.address),
        ("Статус / площадь", status_area or None),
        ("Категория земель", parcel.land_category),
        ("Разрешённое использование", _permitted_use_display(parcel)),
        ("Территориальная зона", parcel.territorial_zone),
        ("Кадастровая стоимость", _format_cadastral_value(parcel.cadastral_value_rub)),
    )
    lines = ["| Параметр | Значение |", "| --- | --- |"]
    lines.extend(
        f"| {label} | {_cell(value) if value else 'нет данных'} |"
        for label, value in rows
    )
    return lines


def _surroundings_lines(model: QuickReportModel) -> list[str]:
    surroundings = model.environment
    if surroundings.distance_kind == "straight_line":
        lines = ["Справочно: расстояния по прямой, не по маршруту.", ""]
    else:
        lines = [f"Справочно: расстояния — {surroundings.distance_kind}.", ""]
    if surroundings.lines:
        lines.extend(f"- {line}" for line in surroundings.lines)
    else:
        lines.append("Данных об окружении в собранных источниках нет.")
    if surroundings.communications_note:
        lines.extend(
            [
                "",
                "### Коммуникации",
                "",
                surroundings.communications_note,
            ]
        )
    return lines


def _format_limit_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _format_number(float(value))
    return " ".join(str(value).split()) or None


def _zone_use_line(use: QuickZoneUse) -> str:
    head = " ".join(part for part in (use.code, use.name) if part)
    limits: list[str] = []
    area_min = _format_limit_value(use.area_min)
    area_max = _format_limit_value(use.area_max)
    if area_min and area_max:
        limits.append(f"участок {area_min}–{area_max} м²")
    elif area_min:
        limits.append(f"участок от {area_min} м²")
    elif area_max:
        limits.append(f"участок до {area_max} м²")
    if use.building_percentage:
        limits.append(f"застройка до {_cell(str(use.building_percentage))}")
    margin = _format_limit_value(use.margin)
    if margin:
        limits.append(f"отступ от границ {margin} м")
    suffix = f" — {', '.join(limits)}" if limits else ""
    return f"- **{_cell(head)}**{suffix}"


def _zone_regulations_lines(regulations: QuickZoneRegulations | None) -> list[str]:
    if regulations is None:
        return [
            (
                "Сведения о территориальной зоне из документа ПЗЗ не получены: "
                "документ не найден или не обработан. Проверка по первичному "
                "документу — в подробном заключении."
            )
        ]
    doc = "ПЗЗ"
    if regulations.doc_number:
        doc += f" № {regulations.doc_number}"
    if regulations.doc_version_date:
        doc += f", редакция от {regulations.doc_version_date}"
    lines = [
        f"Зона {regulations.zone_code} — по данным {doc}.",
        (
            f"Всего видов разрешённого использования в таблице зоны: "
            f"{regulations.total_uses}."
        ),
    ]
    if regulations.housing_uses:
        lines.extend(
            [
                "",
                "Виды использования для строительства дома и ведения хозяйства:",
                "",
            ]
        )
        lines.extend(_zone_use_line(use) for use in regulations.housing_uses)
    lines.extend(
        [
            "",
            (
                "Полная таблица видов разрешённого использования, включая "
                "условно разрешённые и вспомогательные, приводится "
                "в подробном заключении."
            ),
        ]
    )
    return lines


def _verdict_lines(verdict: QuickVerdict) -> list[str]:
    """Блок оценки по методике 6.2: балл/нерасчёт, статус, причины, пояснение."""
    if verdict.score is None:
        head = f"## Оценка не рассчитана — {verdict.status_title}"
    else:
        marks = [verdict.status_title]
        if verdict.preliminary:
            marks.append("предварительная")
        head = f"## Оценка участка — {verdict.score}/{SCORE_SCALE} ({', '.join(marks)})"
    lines = [head, "", verdict.status_summary]
    if verdict.reasons:
        lines.extend(["", "Основные причины:"])
        lines.extend(f"- {reason}" for reason in verdict.reasons)
    if verdict.geometry_note:
        lines.extend(["", verdict.geometry_note])
    if verdict.explanation:
        lines.extend(["", verdict.explanation])
    return lines


def _established_lines(
    facts: list[str], summary: str | None = None
) -> list[str]:
    if summary:
        # §27 ТЗ: narrative заменяет deterministic-список целиком; fallback-
        # narrative — тот же список текстом, поэтому без LLM Markdown
        # не меняется.
        return [summary]
    if not facts:
        return [_NO_FACTS_NOTE]
    return [f"- {fact}" for fact in facts]


def _missing_lines(items: list[QuickMissingItem]) -> list[str]:
    if not items:
        return [_NO_MISSING_NOTE]
    return [f"- {item.text}" for item in items]


def _marketing_lines(marketing: QuickMarketing) -> list[str]:
    lines: list[str] = []
    for service in marketing.services:
        if service.price_rub is not None:
            price = f"{_format_number(service.price_rub)} ₽"
            if service.credit_rub:
                price += (
                    f" (с учётом экспресс-отчёта — "
                    f"{_format_number(service.price_rub - service.credit_rub)} ₽)"
                )
        elif service.price_from_rub is not None:
            price = f"от {_format_number(service.price_from_rub)} ₽"
        else:
            price = "цена по запросу"
        line = f"- **{_cell(service.name)}** — {price}."
        if service.description:
            line += f" {_cell(service.description)}"
        lines.append(line)
    if marketing.cta:
        lines.extend(["", marketing.cta])
    return lines


def render_quick_model(
    model: QuickReportModel,
    *,
    today: date | None = None,
    map_lines: list[str] | None = None,
    zone_regulations: QuickZoneRegulations | None = None,
) -> str:
    """Собирает Markdown из продуктовой модели; дата — today (по умолч. сегодня).

    ``map_lines`` — готовые строки раздела «Схема расположения зон»
    (ссылка на PNG + подпись); без них выводится статическая заглушка.
    """
    report_date = today or datetime.now(UTC).date()
    identity = model.identity
    narrative = model.narrative

    lines = [
        "# Экспресс-оценка земельного участка",
        "",
        "Потенциал и ограничения для строительства дома",
        "",
        (
            f"Отчёт от {_format_date(report_date)} · "
            f"Данные от {_format_date(identity.collected_at.date())} · "
            f"Методика оценки — версия {identity.methodology_version}"
        ),
        "",
        f"**Кадастровый номер {identity.cadastral_number}**"
        + (f" | {identity.address}" if identity.address else ""),
        "",
        *_verdict_lines(model.status),
        "",
        "## Что уже удалось установить",
        "",
        *_established_lines(
            model.established_facts,
            summary=(narrative.established_summary if narrative else None),
        ),
        "",
        "## Факторы, требующие внимания",
        "",
        *_factors_table(model.attention_factors),
        "",
        "## Паспорт участка",
        "",
        *_passport_lines(model.parcel_passport),
        "",
        "## Территориальная зона и разрешённое использование",
        "",
        *_zone_regulations_lines(zone_regulations),
        "",
        "## Окружение",
        "",
        *_surroundings_lines(model),
        "",
        "## Схема расположения зон",
        "",
        *(map_lines if map_lines else [_MAP_PLACEHOLDER]),
        "",
        "## Ограничения отчёта",
        "",
        model.limitations,
        "",
        "## Что ещё нужно проверить",
        "",
        *_missing_lines(model.missing_information),
        "",
    ]
    if model.marketing is not None:
        lines.extend(
            [
                "## Следующий шаг",
                "",
                *_marketing_lines(model.marketing),
                "",
            ]
        )
    lines.extend(
        [
            "## Права и использование отчёта",
            "",
            _RIGHTS_USAGE_PARAGRAPHS[0],
            "",
            _RIGHTS_USAGE_PARAGRAPHS[1],
            "",
        ]
    )
    return "\n".join(lines)


def render_quickreport(
    context: QuickReportContext,
    *,
    today: date | None = None,
    map_lines: list[str] | None = None,
    marketing: MarketingConfig | None = None,
) -> str:
    """Фасад обратной совместимости: контекст → модель → Markdown."""
    model = build_quick_model(context, marketing=marketing)
    return render_quick_model(
        model,
        today=today,
        map_lines=map_lines,
        zone_regulations=context.zone_regulations,
    )
