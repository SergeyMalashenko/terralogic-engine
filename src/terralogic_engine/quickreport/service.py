"""Сервисный отчёт скоринга — протокол работы модели для разработчиков.

Рядом с клиентским QuickReport строится технический Markdown: входные
данные, классификация и слияние зон, вычеты групп, U/A/C/G, арифметика
оценки, правило статуса, достаточность данных и внутренние
предупреждения. Источник — ``QuickReportContext.protocol`` (собирается
в ``context.build_quick_context_from_report``). Детерминированный рендер,
без LLM.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from .models import QuickProtocol, QuickReportContext

_COMM_QUESTION_NAMES = {
    "electricity": "Электроснабжение",
    "water": "Водоснабжение",
    "sewage": "Отведение бытовых стоков",
}

_AREA_STATUS_NAMES = {
    "computed": "вычислен",
    "min_not_set": "минимум регламентом не установлен",
    "no_regulations": "регламент ПЗЗ не получен",
    "applicability_unknown": "применимость регламента к ВРИ не установлена",
    "below_minimum": "площадь ниже минимальной — ручная проверка",
}


def _format_date(value: date) -> str:
    return f"{value:%d.%m.%Y}"


def _g(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}"


def _pct(value: float) -> str:
    return f"{round(value, 1):g}%"


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _summary_lines(context: QuickReportContext, protocol: QuickProtocol) -> list[str]:
    verdict = context.verdict
    lines = ["## 1. Итог расчёта", ""]
    if verdict.score is None:
        lines.append(
            f"**Оценка не рассчитана** — статус «{verdict.status_title}» "
            f"({protocol.status_key}; {protocol.status_rule})."
        )
        if protocol.stop_reasons:
            lines.append("")
            lines.append("Стоп-факторы:")
            lines.extend(f"- {reason}" for reason in protocol.stop_reasons)
        if protocol.suspend_reasons:
            lines.append("")
            lines.append("Основания приостановки (п. 3.3 / раздел 4):")
            lines.extend(f"- {reason}" for reason in protocol.suspend_reasons)
    else:
        parts = [
            str(protocol.base_score),
            f"− {_g(protocol.restrictions_total)} (ограничения)",
            f"− {_g(protocol.u_total)} (U)",
            f"− {_g(protocol.g or 0.0)} (G)",
        ]
        arithmetic = " ".join(parts)
        clamp_note = (
            ""
            if protocol.raw_score == verdict.score
            else f" (сырая сумма {protocol.raw_score} усечена до {verdict.score})"
        )
        lines.append(f"**Оценка = max(0; {arithmetic}) = {verdict.score}/100**{clamp_note}")
        lines.append("")
        lines.append(
            f"Статус: `{verdict.status_key}` — «{verdict.status_title}» "
            f"({protocol.status_rule})."
        )
    preliminary = "да" if verdict.preliminary else "нет"
    lines.append("")
    suffix = ""
    if verdict.preliminary:
        flags: list[str] = []
        if protocol.u_total > 0:
            flags.append("U > 0")
        flags.extend(protocol.preliminary_flags)
        if flags:
            suffix = f" — {', '.join(flags)}"
    lines.append(f"Предварительная: {preliminary}{suffix}.")
    return lines


def _inputs_lines(context: QuickReportContext, protocol: QuickProtocol) -> list[str]:
    parcel = context.parcel
    vri = parcel.vri
    vri_display = (
        f"{vri.code} — {vri.name} (П/0412, ред. {vri.revision_date})"
        if vri is not None
        else None
    )
    lines = ["## 2. Входные данные", ""]
    rows = [
        ("Кадастровый номер", parcel.cadastral_number),
        ("Площадь, м²", _g(parcel.area_m2)),
        ("Категория земель", parcel.land_category),
        ("Разрешённое использование", parcel.permitted_use),
        ("ВРИ (классификатор П/0412)", vri_display),
        ("Префикс цели (классификатор ВРИ)", protocol.target_use_prefix),
        ("Территориальная зона", parcel.territorial_zone),
        ("Цель методики", "индивидуальный жилой дом"),
    ]
    lines.extend(f"- **{name}:** {value or 'нет данных'}" for name, value in rows)
    lines.extend(
        [
            "",
            "### Источники (неизменяемые снапшоты)",
            "",
            "| Источник | Снапшот | Получен | Адаптер | sha256 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for source in protocol.sources:
        lines.append(
            f"| {_cell(source.source)} | {_cell(source.snapshot_id)} | "
            f"{source.retrieved_at:%Y-%m-%d %H:%M} | {_cell(source.adapter_version)} | "
            f"`{source.content_sha256[:12]}` |"
        )
    if not protocol.sources:
        lines.append("| — | — | — | — | — |")
    return lines


def _classification_lines(protocol: QuickProtocol) -> list[str]:
    lines = [
        "## 4. Классификация и слияние зон (каталог ст. 105 ЗК РФ)",
        "",
        (
            "Слияние по реестровому номеру, иначе по нормализованному названию; "
            "краткое наименование-префикс полного объединяется с ним; слой-витрина, "
            "повторяющий тип первичной записи реестра, отдельным фактором не "
            "считается; охват — максимум между источниками. Обработка класса: "
            "спецправило (d), универсальная шкала E0–E4 при подтверждённом режиме "
            "(d), иначе Q30 (q — неопределённость режима)."
        ),
        "",
        "| Зона (представитель слияния) | Источник | П. ст. 105 | Группа | Фактор | Эффект | Тип | Охват | Вычет | Акт |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for factor in protocol.factors:
        act = (
            f"{factor.act_number} ({factor.act_date})"
            if factor.act_number
            else "—"
        )
        lines.append(
            f"| {_cell(factor.zone_name)} | {_cell(factor.source)} | "
            f"{_cell(factor.catalog_item)} | {_cell(factor.group_title)} | "
            f"{_cell(factor.factor_title)} | {_cell(factor.effect_code)} | "
            f"{factor.deduction_kind} | {_pct(factor.coverage_percent)} | "
            f"{_g(factor.deduction)} | {_cell(act)} |"
        )
    if not protocol.factors:
        lines.append("| — | — | — | — | — | — | — | — | — | — |")
    lines.append("")
    if protocol.unmatched_zones:
        lines.append("Зоны без надёжной классификации по каталогу (BLOCKING_UNKNOWN):")
        lines.extend(f"- {name}" for name in protocol.unmatched_zones)
    else:
        lines.append("Зон вне каталога нет.")
    lines.append("")
    if protocol.classify_notes:
        lines.append("Заметки слияния:")
        lines.extend(f"- {note}" for note in protocol.classify_notes)
    else:
        lines.append("Заметок слияния нет.")
    lines.append("")
    conservative = "да" if protocol.conservative else "нет"
    lines.append(f"Консервативный сценарий (расхождение версий границ): {conservative}.")
    return lines


def _catalog_coverage_lines(protocol: QuickProtocol) -> list[str]:
    lines = [
        "## 5. Полнота покрытия каталога ст. 105 ЗК РФ",
        "",
        (
            "По каждому классу: пересечение вычислено («найдена зона») либо "
            "записей этого класса в источниках не выявлено. Отсутствие записи "
            "не является отрицательным результатом проверки полноты поиска."
        ),
        "",
        "| П. ст. 105 | Класс | Состояние |",
        "| --- | --- | --- |",
    ]
    names = {"found": "найдена зона", "none_found": "записей не выявлено"}
    for item in protocol.catalog_coverage:
        lines.append(
            f"| {_cell(item.item)} | {_cell(item.class_name)} | "
            f"{names.get(item.state, item.state)} |"
        )
    return lines


def _groups_lines(protocol: QuickProtocol) -> list[str]:
    lines = [
        "## 6. Вычеты групп ограничений",
        "",
        "| Группа | Сырая сумма | в т.ч. Q | После потолка | Зоны (короткие имена) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for group in protocol.groups:
        cap_note = (
            f"{_g(group.deduction)}"
            if group.raw_deduction == group.deduction
            else f"{_g(group.deduction)} (усечено потолком)"
        )
        lines.append(
            f"| {_cell(group.title)} | {_g(group.raw_deduction)} | "
            f"{_g(group.q_part)} | {cap_note} | "
            f"{_cell(' и '.join(dict.fromkeys(group.zone_names)))} |"
        )
    if not protocol.groups:
        lines.append("| — | 0 | 0 | 0 | — |")
    lines.extend(["", f"**Σ ограничений = {_g(protocol.restrictions_total)}**."])
    if protocol.q_total > 0:
        lines.append(
            f"**в т.ч. Q (неопределённость режима) = {_g(protocol.q_total)}** — "
            "оценка предварительная, статус «повышенное внимание» обязателен (§8/§12)."
        )
    return lines


def _communications_lines(protocol: QuickProtocol) -> list[str]:
    lines = [
        "## 7. U — неопределённость коммуникаций (п. 4.2)",
        "",
        "| Вопрос | Состояние | Вычет |",
        "| --- | --- | --- |",
    ]
    for comm in protocol.communications:
        question = _COMM_QUESTION_NAMES.get(comm.question, comm.question)
        lines.append(f"| {question} | {_cell(comm.state)} | {_g(comm.weight)} |")
    lines.extend(["", f"**U = {_g(protocol.u_total)}**."])
    return lines


def _area_lines(protocol: QuickProtocol) -> list[str]:
    area = protocol.area
    lines = ["## 8. A — минимальная площадь (п. 4.3)", ""]
    if area is None:
        lines.append("Проверка не выполнялась.")
        return lines
    status = _AREA_STATUS_NAMES.get(area.status, area.status)
    lines.append(f"- Статус проверки: {status}")
    lines.append(f"- S участка: {_g(area.parcel_area_m2)} м²")
    if area.target_code or area.target_name:
        target = " ".join(part for part in (area.target_code, area.target_name) if part)
        lines.append(f"- Строка регламента: {_cell(target)}")
    lines.append(f"- Smin по регламенту: {_g(area.s_min_m2)} м²")
    lines.append(f"- Результат: A = {_g(area.deduction)}")
    if area.note:
        lines.append(f"- Примечание: {area.note}")
    return lines


def _geometry_lines(protocol: QuickProtocol) -> list[str]:
    lines = ["## 9. C — конфигурация участка (п. 4.4)", ""]
    if protocol.geometry is None:
        lines.append("Геометрическая проверка не выполнена — C неизвестен.")
    else:
        lines.append(f"- C = {protocol.geometry.c}")
        lines.append(f"- Протокол теста: {protocol.geometry.detail}")
    return lines


def _arithmetic_lines(protocol: QuickProtocol) -> list[str]:
    lines = ["## 10. G и итоговая арифметика", ""]
    a_value = protocol.area.deduction if protocol.area else None
    c_value = protocol.geometry.c if protocol.geometry else None
    lines.append(f"- A = {_g(a_value)}, C = {_g(c_value)} → G = max(A; C) = {_g(protocol.g)}")
    lines.append(f"- Σ ограничений = {_g(protocol.restrictions_total)}, U = {_g(protocol.u_total)}")
    if protocol.score is None:
        lines.append("- Числовая оценка не выдаётся (см. раздел 1).")
    else:
        parts = [
            str(protocol.base_score),
            f"− {_g(protocol.restrictions_total)}",
            f"− {_g(protocol.u_total)}",
            f"− {_g(protocol.g or 0.0)}",
        ]
        lines.append(f"- Оценка = max(0; {' '.join(parts)}) = {protocol.score}")
    return lines


def _sufficiency_lines(context: QuickReportContext, protocol: QuickProtocol) -> list[str]:
    lines = ["## 11. Достаточность данных и статус", ""]
    lines.append("Обязательные проверки — раздел 3. Блокирующие пробелы:")
    blockers = protocol.suspend_reasons
    if blockers:
        lines.extend(f"- {reason}" for reason in blockers)
    else:
        lines.append("- нет")
    lines.append("")
    lines.append(f"Статус `{protocol.status_key}`: {protocol.status_rule}.")
    return lines


def render_service_report(
    context: QuickReportContext,
    *,
    today: date | None = None,
) -> str:
    """Markdown протокола скоринга для разработчиков.

    Без protocol (старые контексты) отдаёт короткую заглушку — сервисный
    отчёт никогда не ломает основной конвейер.
    """
    report_date = today or datetime.now(UTC).date()
    protocol = context.protocol
    header = [
        f"# Сервисный отчёт скоринга — {context.parcel.cadastral_number}",
        "",
        "Протокол работы скоринговой модели: из чего сложились цифры "
        "экспресс-отчёта. Для разработчиков, не для клиента.",
        "",
        (
            f"Кейс {context.case_id} · Run {context.collection_run_id} · "
            f"Данные от {_format_date(context.collected_at.date())} · "
            f"Сформирован {_format_date(report_date)}"
        ),
    ]
    if protocol is None:
        return "\n".join(
            [
                *header,
                "",
                "Протокол недоступен: контекст собран версией без протокола скоринга.",
                "",
            ]
        )
    lines = [
        *header,
        f"Методика {context.methodology_version} · "
        f"sha256 активов: `{protocol.methodology_sha256[:12]}`",
        "",
        f"Формула: {protocol.formula}.",
        "",
        *_summary_lines(context, protocol),
        "",
        *_inputs_lines(context, protocol),
        "",
        "## 3. Обязательные проверки ключевых зон (п. 4.1)",
        "",
        "| Проверка | Результат |",
        "| --- | --- |",
        *(
            f"| {_cell(check.name)} | {_cell(check.result)} |"
            for check in protocol.checks
        ),
        "",
        *_classification_lines(protocol),
        *_catalog_coverage_lines(protocol),
        "",
        *_groups_lines(protocol),
        "",
        *_communications_lines(protocol),
        "",
        *_area_lines(protocol),
        "",
        *_geometry_lines(protocol),
        "",
        *_arithmetic_lines(protocol),
        "",
        *_sufficiency_lines(context, protocol),
        "",
        "## 12. Предупреждения (внутренний протокол)",
        "",
    ]
    if context.warnings:
        lines.extend(f"- {warning}" for warning in context.warnings)
    else:
        lines.append("Предупреждений нет.")
    lines.append("")
    return "\n".join(lines)
