"""Детерминированный рендер QuickReportContext в Markdown.

Структура документа повторяет образец QuickReport.docx без маркетинговых
блоков: заголовок с датами и версией методики, вердикт, таблица факторов,
паспорт участка, окружение с блоком коммуникаций, заглушка карты и
статический дисклеймер ограничений отчёта. Новых зависимостей нет —
чистая сборка строк; LLM в генерации не участвует.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from .models import QuickFactor, QuickReportContext

SCORE_SCALE = 100

_DISCLAIMER = (
    "Отчёт подготовлен по данным публичных источников. Полнота и актуальность "
    "сведений зависят от организаций, которые ведут и обновляют эти источники, "
    "и находятся вне контроля TerraLogicX. Отчёт содержит результаты "
    "предварительного анализа и не заменяет юридическое, кадастровое или "
    "градостроительное заключение."
)

_MAP_PLACEHOLDER = "Схема расположения зон приводится в подробном заключении."

_NO_FACTORS_NOTE = (
    "По собранным данным зоны с особыми условиями использования территории "
    "на участок не попадают."
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
        "| № | Зона | Охват участка | Что это значит для вас |",
        "| --- | --- | --- | --- |",
    ]
    for number, factor in enumerate(zone_factors, start=1):
        lines.append(
            f"| {number} | {_cell(factor.zone_name)} | "
            f"{_cell(_capitalize(factor.coverage_word))} | "
            f"{_cell(factor.guidance)} |"
        )
    return lines


def _passport_lines(context: QuickReportContext) -> list[str]:
    parcel = context.parcel
    status_area = " · ".join(
        part for part in (parcel.status, _format_area(parcel.area_m2)) if part
    )
    rows = (
        ("Адрес", parcel.address),
        ("Статус / площадь", status_area or None),
        ("Категория земель", parcel.land_category),
        ("Разрешённое использование", parcel.permitted_use),
        ("Территориальная зона", parcel.territorial_zone),
        ("Кадастровая стоимость", _format_cadastral_value(parcel.cadastral_value_rub)),
    )
    return [f"- **{label}:** {value or 'нет данных'}" for label, value in rows]


def _surroundings_lines(context: QuickReportContext) -> list[str]:
    lines = ["Справочно: расстояния по прямой, не по маршруту.", ""]
    if context.surroundings.lines:
        lines.extend(f"- {line}" for line in context.surroundings.lines)
    else:
        lines.append("Данных об окружении в собранных источниках нет.")
    if context.surroundings.communications_note:
        lines.extend(
            [
                "",
                "### Коммуникации",
                "",
                context.surroundings.communications_note,
            ]
        )
    return lines


def render_quickreport(
    context: QuickReportContext, *, today: date | None = None
) -> str:
    """Собирает Markdown QuickReport; дата отчёта — today (по умолчанию сегодня)."""
    report_date = today or datetime.now(UTC).date()
    parcel = context.parcel
    verdict = context.verdict

    lines = [
        "# Экспресс-оценка земельного участка",
        "",
        "Потенциал и ограничения для строительства дома",
        "",
        (
            f"Отчёт от {_format_date(report_date)} · "
            f"Данные от {_format_date(context.collected_at.date())} · "
            f"Методика оценки — версия {context.methodology_version}"
        ),
        "",
        f"**Кадастровый номер {parcel.cadastral_number}**"
        + (f" | {parcel.address}" if parcel.address else ""),
        "",
        (
            f"## Оценка участка — {verdict.score}/{SCORE_SCALE} "
            f"({verdict.grade_title})"
        ),
        "",
        verdict.grade_summary,
        "",
        "## Факторы, требующие внимания",
        "",
        *_factors_table(context.factors),
        "",
        "## Паспорт участка",
        "",
        *_passport_lines(context),
        "",
        "## Окружение",
        "",
        *_surroundings_lines(context),
        "",
        "## Схема расположения зон",
        "",
        _MAP_PLACEHOLDER,
        "",
        "## Ограничения отчёта",
        "",
        _DISCLAIMER,
        "",
    ]
    return "\n".join(lines)
