"""Bounded narrative writer QuickReport (§62–63, §74 ТЗ).

Writer — опциональный LLM-этап поверх детерминированной модели: он получает
уже готовые факты, балл и статус и может только переформулировать три
текстовых поля (main_conclusion, established_summary, factor_meanings).
Он не ищет и не вычисляет факты, не меняет балл, статус, цены и дисклеймер.

Выход проходит детерминированные рамки (`validate_writer_output`): чужие
числа, категоричные юридические обещания, неизвестные rule_id и переполнение
длины отбрасываются по полю; битое поле заменяется детерминированным
текстом. Любой сбой исполнителя — None + warning (fail-safe, §77).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

from ..qa.models import QaIssue
from .model import QuickNarrative, QuickReportModel

NARRATIVE_RESULT_MARKER = "=== NARRATIVE_RESULT ==="
WRITER_CMD_ENV = "TERRALOGIC_WRITER_CMD"
_DEFAULT_TIMEOUT_S = 300

_MAX_CONCLUSION_CHARS = 600
_MAX_SUMMARY_CHARS = 1200
_MAX_MEANING_CHARS = 300

# Категоричные юридические обещания, которых в экспресс-отчёте быть не может.
_BANNED_PHRASES = (
    "гарантир",
    "можно строить",
    "разрешено строительство",
    "юридически чист",
    "полностью безопас",
)

_NUMBER_RE = re.compile(r"\d[\d ]*")

WRITER_PROMPT_TEMPLATE = """\
Ты — редактор экспресс-отчёта об участке TerraLogicX. Получаешь готовые
факты и делаешь их понятнее. Ты НЕ аналитик: ничего не вычисляй и не ищи.

ЖЁСТКИЕ РАМКИ:
- Используй только приведённые факты. Не добавляй новых фактов, чисел,
  процентов, расстояний, нормативных последствий и обещаний.
- Не меняй и не называй балл, статус, цены, условия услуг.
- Не пиши «коммуникаций нет», если они лишь не установлены; пробелы данных
  формулируй как «не установлены / требуется проверить».
- Запрещены категоричные юридические обещания («гарантировано»,
  «можно строить», «юридически чист» и т.п.).
- Пиши по-русски, кратко и нейтрально, обращение на «вы».

ЗАДАЧА — три поля (все необязательны, пиши только то, что улучшает текст):
- main_conclusion: один-два предложения вывода (что участок можно/нельзя
  продолжать рассматривать и почему) по статусу и причинам.
- established_summary: абзац — человекочитаемая интерпретация списка
  установленных фактов (не перечисление, а связный текст).
- factor_meanings: для указанных rule_id — короткое «что это значит для вас»
  (одно предложение), только для перечисленных rule_id.
{issues_block}
ФАКТЫ ОТЧЁТА:
{facts_block}

ФИНАЛ — последней строкой строго:
=== NARRATIVE_RESULT === {{"main_conclusion": "...", "established_summary": "...", "factor_meanings": {{"rule_id": "..."}}}}
"""

_ISSUES_INTRO = (
    "ЗАМЕЧАНИЯ КРИТИКА (учитывай при переформулировке, исправляй только"
    " затронутые секции):"
)


@dataclass(frozen=True)
class WriterExecResult:
    """Сырой результат запуска исполнителя writer'а."""

    stdout: str
    returncode: int


class WriterExecutor(Protocol):
    """Исполнитель роли writer'а (Hermes в проде, фейк в тестах)."""

    def run(self, prompt: str, workdir: Path) -> WriterExecResult:
        """Один one-shot запуск по промпту."""


class SubprocessWriterExecutor:
    """Запуск внешнего CLI-агента: prompt — последним аргументом команды."""

    def __init__(
        self, command: tuple[str, ...], *, timeout: int = _DEFAULT_TIMEOUT_S
    ) -> None:
        if not command:
            raise ValueError("команда writer'а не задана")
        self.command = command
        self.timeout = timeout

    @classmethod
    def from_env(cls, env_var: str = WRITER_CMD_ENV) -> SubprocessWriterExecutor | None:
        """Исполнитель из env TERRALOGIC_WRITER_CMD; пусто → None (отключён)."""
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            return None
        return cls(tuple(shlex.split(raw)))

    def run(self, prompt: str, workdir: Path) -> WriterExecResult:
        proc = subprocess.run(
            [*self.command, prompt],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        return WriterExecResult(stdout=proc.stdout, returncode=proc.returncode)


class _WriterOutput(BaseModel):
    """Сырой JSON writer'а до применения рамок."""

    main_conclusion: str | None = None
    established_summary: str | None = None
    factor_meanings: dict[str, str] = {}


def _facts_block(model: QuickReportModel) -> str:
    parcel = model.parcel_passport
    lines = [
        f"Кадастровый номер: {parcel.cadastral_number}",
        f"Адрес: {parcel.address or 'нет данных'}",
        f"Площадь: {parcel.area_m2 if parcel.area_m2 is not None else 'нет данных'} м²",
        f"Категория земель: {parcel.land_category or 'нет данных'}",
        f"Разрешённое использование: {parcel.permitted_use or 'нет данных'}",
        (
            f"Оценка: {model.score if model.score is not None else 'не рассчитана'}"
            f" / статус: {model.status.status_title}"
            f"{' (предварительная)' if model.status.preliminary else ''}"
        ),
    ]
    if model.status.reasons:
        lines.append("Причины статуса:")
        lines.extend(f"- {reason}" for reason in model.status.reasons)
    if model.established_facts:
        lines.append("Установленные факты:")
        lines.extend(f"- {fact}" for fact in model.established_facts)
    if model.attention_factors:
        lines.append("Факторы внимания (rule_id | зона | охват | смысл):")
        lines.extend(
            f"- {factor.rule_id} | {factor.zone_name} | {factor.coverage_word}"
            f" | {factor.guidance}"
            for factor in model.attention_factors
            if not factor.informational
        )
    if model.missing_information:
        lines.append("Пробелы данных (формулируются как «не установлены»):")
        lines.extend(f"- {item.text}" for item in model.missing_information)
    return "\n".join(lines)


def build_writer_prompt(
    model: QuickReportModel, issues: list[QaIssue] | None = None
) -> str:
    """Промпт writer'а; issues — замечания критика для targeted revision (§74)."""
    if issues:
        issues_block = "\n".join(
            [_ISSUES_INTRO]
            + [
                f"- [{issue.severity}] {issue.section}: {issue.message}"
                + (f" Действие: {issue.suggested_action}" if issue.suggested_action else "")
                for issue in issues
            ]
        )
    else:
        issues_block = ""
    return WRITER_PROMPT_TEMPLATE.format(
        issues_block=issues_block,
        facts_block=_facts_block(model),
    )


def parse_writer_output(stdout: str) -> _WriterOutput | None:
    """Разбор последней строки с маркером; невалидно → None."""
    payload = None
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith(NARRATIVE_RESULT_MARKER):
            try:
                payload = json.loads(stripped[len(NARRATIVE_RESULT_MARKER) :].strip())
            except json.JSONDecodeError:
                return None
            break
    if payload is None:
        return None
    try:
        return _WriterOutput.model_validate(payload)
    except ValidationError:
        return None


def _context_numbers(model: QuickReportModel) -> set[str]:
    """Числа, законно присутствующие в контексте (балл, %, площадь, стоимость)."""
    numbers: set[str] = set()
    if model.score is not None:
        numbers.add(str(model.score))
    numbers.add("100")
    for factor in model.attention_factors:
        coverage = factor.coverage_percent
        numbers.add(str(int(coverage)) if coverage == int(coverage) else str(coverage))
    area = model.parcel_passport.area_m2
    if area is not None:
        numbers.add(str(int(area)))
    value = model.parcel_passport.cadastral_value_rub
    if value is not None:
        numbers.add(str(int(value)))
    return numbers


def _field_numbers(text: str) -> set[str]:
    """Числа текста: «1 000» и «1000» нормализуются к одному виду."""
    return {
        match.group(0).replace(" ", "").rstrip("., ") for match in _NUMBER_RE.finditer(text)
    }


def _check_text(text: str, *, max_chars: int, allowed_numbers: set[str]) -> str | None:
    """None — поле валидно; иначе причина отброса."""
    if len(text) > max_chars:
        return f"длиннее {max_chars} символов"
    folded = text.casefold()
    for phrase in _BANNED_PHRASES:
        if phrase in folded:
            return f"запрещённая категоричная фраза «{phrase}»"
    foreign = _field_numbers(text) - allowed_numbers
    if foreign:
        return f"чужие числа: {sorted(foreign)}"
    return None


def validate_writer_output(
    output: _WriterOutput, model: QuickReportModel
) -> tuple[QuickNarrative, list[str]]:
    """Детерминированные рамки: битое поле отбрасывается, остальные живут.

    Возвращает (narrative, rejections): narrative может быть «пустым»
    (все поля отброшены) — вызывающий решает по rejections.
    """
    allowed = _context_numbers(model)
    rejections: list[str] = []
    narrative = QuickNarrative()

    if output.main_conclusion:
        reason = _check_text(
            output.main_conclusion, max_chars=_MAX_CONCLUSION_CHARS,
            allowed_numbers=allowed,
        )
        if reason is None:
            narrative.main_conclusion = output.main_conclusion.strip()
        else:
            rejections.append(f"main_conclusion отброшен: {reason}")
    if output.established_summary:
        reason = _check_text(
            output.established_summary, max_chars=_MAX_SUMMARY_CHARS,
            allowed_numbers=allowed,
        )
        if reason is None:
            narrative.established_summary = output.established_summary.strip()
        else:
            rejections.append(f"established_summary отброшен: {reason}")

    known_rule_ids = {
        factor.rule_id for factor in model.attention_factors if not factor.informational
    }
    for rule_id, meaning in output.factor_meanings.items():
        if rule_id not in known_rule_ids:
            rejections.append(f"factor_meanings[{rule_id!r}] отброшен: нет такого фактора")
            continue
        reason = _check_text(
            meaning, max_chars=_MAX_MEANING_CHARS, allowed_numbers=allowed
        )
        if reason is None:
            narrative.factor_meanings[rule_id] = meaning.strip()
        else:
            rejections.append(f"factor_meanings[{rule_id!r}] отброшен: {reason}")
    return narrative, rejections


def _is_empty(narrative: QuickNarrative) -> bool:
    return (
        narrative.main_conclusion is None
        and narrative.established_summary is None
        and not narrative.factor_meanings
    )


def run_writer(
    executor: WriterExecutor | None,
    model: QuickReportModel,
    *,
    issues: list[QaIssue] | None = None,
    warnings: list[str] | None = None,
) -> tuple[QuickNarrative | None, list[str]]:
    """Прогон writer'а: (narrative, rejections); сбой → (None, [warning]).

    Один retry при битом NARRATIVE_RESULT. Все поля, не прошедшие рамки,
    отброшены; полностью пустой результат → None.
    """
    warnings = warnings if warnings is not None else []
    if executor is None:
        warnings.append("writer отключён: TERRALOGIC_WRITER_CMD не задан")
        return None, []
    prompt = build_writer_prompt(model, issues)
    last_error = "нет результата"
    for attempt in range(2):
        try:
            with tempfile.TemporaryDirectory(prefix="terralogic-writer-") as tmp:
                exec_result = executor.run(prompt, Path(tmp))
        except Exception as exc:  # noqa: BLE001 — writer не ломает отчёт
            warnings.append(f"writer недоступен: {exc}")
            return None, []
        if exec_result.returncode != 0:
            warnings.append(f"writer завершился с кодом {exec_result.returncode}")
            return None, []
        output = parse_writer_output(exec_result.stdout)
        if output is not None:
            narrative, rejections = validate_writer_output(output, model)
            warnings.extend(rejections)
            if _is_empty(narrative):
                warnings.append("writer: все поля отброшены рамками")
                return None, rejections
            return narrative, rejections
        last_error = "NARRATIVE_RESULT не найден или невалиден"
        prompt = (
            f"{prompt}\n\nПРЕДЫДУЩИЙ ОТВЕТ ОТКЛОНЁН: {last_error}.\n"
            "Верни исправленный результат: валидный JSON после маркера"
            f" {NARRATIVE_RESULT_MARKER}."
        )
        if attempt == 1:
            break
    warnings.append(f"writer не вернул валидный результат: {last_error}")
    return None, []
