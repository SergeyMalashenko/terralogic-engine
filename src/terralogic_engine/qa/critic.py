"""LLM-критик QuickReport (§65–75 ТЗ): Hermes + Gemini, read-only, fail-safe.

Критик — отдельная роль, не discovery-Hermes (§65). В v1 у него нет
инструментов вообще: весь контекст передаётся в промпте, поэтому он
read-only по построению (§69–70). Критик не переписывает отчёт (§68):
возвращает структурированный CriticResult с issues.

Любой сбой (нет команды, timeout, битый JSON) — None + warning, отчёт
не ломается (§77).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from terralogic_engine.quickreport.model import QuickReportModel

from .models import CriticResult, ValidatorResult

CRITIC_RESULT_MARKER = "=== CRITIC_RESULT ==="
CRITIC_CMD_ENV = "TERRALOGIC_CRITIC_CMD"
_DEFAULT_TIMEOUT_S = 300
_MAX_DRAFT_CHARS = 60_000

CRITIC_PROMPT_TEMPLATE = """\
Ты — независимый рецензент экспресс-отчёта об участке TerraLogicX. Проверяешь СЕМАНТИКУ, не формат.

ЖЁСТКАЯ ПОЛИТИКА ДОКАЗАТЕЛЬНОСТИ:
- Используй только приведённые ниже контекст отчёта, текст черновика и результаты детерминированного валидатора.
- Не используй собственные знания модели как фактическое или юридическое доказательство.
- Утверждение, которое нельзя ни подтвердить, ни опровергнуть из имеющихся данных, помечай как unverifiable / insufficient_evidence.
- Не классифицируй неподтверждённые утверждения как ложные без доказательства.
- Ты НЕ переписываешь отчёт: находишь проблему, локализуешь, объясняешь, предлагаешь действие.
- Маркетинговый блок («Следующий шаг») не проверяй как аналитическое утверждение — его параметры уже сверены кодом.

ЧТО ПРОВЕРЯТЬ: неподтверждённое утверждение; преувеличение; скрытая неопределённость; противоречие между разделами; неверная интерпретация ограничения; причинное утверждение без основания; юридическое превышение («можно строить», «гарантировано»); факт сильнее доказательства; путаница участок/окружение; факт вместо предположения.

КОНТЕКСТ ОТЧЁТА (модель):
{context_block}

РЕЗУЛЬТАТ ДЕТЕРМИНИРОВАННОГО ВАЛИДАТОРА:
{validator_block}

ЧЕРНОВИК ОТЧЁТА (Markdown):
{draft_block}

ФИНАЛ — последней строкой строго:
=== CRITIC_RESULT === {{"status": "pass|needs_revision", "issues": [{{"rule_id": "unsupported_claim|overstatement|missing_uncertainty|contradiction|incorrect_interpretation|unsupported_causality|legal_overreach|fact_stronger_than_evidence|parcel_surroundings_confusion|fact_vs_assumption", "section": "раздел отчёта", "severity": "info|warning|error|critical", "message": "в чём проблема", "claim": "спорная фраза из черновика или null", "evidence_ids": [], "suggested_action": "что сделать"}}]}}
Пустой issues при status="pass" — [].
"""


@dataclass(frozen=True)
class CriticExecResult:
    """Сырой результат запуска исполнителя критика."""

    stdout: str
    returncode: int


class CriticExecutor(Protocol):
    """Исполнитель роли критика (Hermes+Gemini в проде, фейк в тестах)."""

    def run(self, prompt: str, workdir: Path) -> CriticExecResult:
        """Один one-shot запуск по промпту."""


class SubprocessCriticExecutor:
    """Запуск внешнего CLI-агента: prompt — последним аргументом команды."""

    def __init__(
        self, command: tuple[str, ...], *, timeout: int = _DEFAULT_TIMEOUT_S
    ) -> None:
        if not command:
            raise ValueError("команда критика не задана")
        self.command = command
        self.timeout = timeout

    @classmethod
    def from_env(cls, env_var: str = CRITIC_CMD_ENV) -> SubprocessCriticExecutor | None:
        """Исполнитель из env TERRALOGIC_CRITIC_CMD; пусто → None (отключён)."""
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            return None
        return cls(tuple(shlex.split(raw)))

    def run(self, prompt: str, workdir: Path) -> CriticExecResult:
        proc = subprocess.run(
            [*self.command, prompt],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        return CriticExecResult(stdout=proc.stdout, returncode=proc.returncode)


def build_critic_prompt(
    model: QuickReportModel, markdown: str, validator: ValidatorResult
) -> str:
    """Промпт критика: политика §71 + контекст + итоги валидатора + черновик."""
    parcel = model.parcel_passport
    context_lines = [
        f"Кадастровый номер: {parcel.cadastral_number}",
        f"Адрес: {parcel.address or 'нет данных'}",
        f"Площадь: {parcel.area_m2 if parcel.area_m2 is not None else 'нет данных'} м²",
        f"Категория земель: {parcel.land_category or 'нет данных'}",
        f"Разрешённое использование (источник): {parcel.permitted_use or 'нет данных'}",
    ]
    if parcel.vri is not None:
        context_lines.append(
            f"ВРИ по классификатору П/0412: {parcel.vri.code} — {parcel.vri.name}"
        )
    context_lines.append(
        f"Оценка: {model.score if model.score is not None else 'не рассчитана'}"
        f" / статус: {model.status.status_title}"
        f"{' (предварительная)' if model.status.preliminary else ''}"
    )
    if model.established_facts:
        context_lines.append("Установленные факты:")
        context_lines.extend(f"- {fact}" for fact in model.established_facts)
    if model.attention_factors:
        context_lines.append("Факторы внимания:")
        context_lines.extend(
            f"- {factor.zone_name}: {factor.coverage_word}"
            f" ({factor.coverage_percent:.0f}%) — {factor.guidance}"
            for factor in model.attention_factors
        )
    if model.missing_information:
        context_lines.append("Пробелы данных (TO_VERIFY):")
        context_lines.extend(f"- {item.text}" for item in model.missing_information)

    if validator.issues:
        validator_lines = [
            f"- [{issue.severity}] {issue.rule_id} ({issue.section}): {issue.message}"
            for issue in validator.issues
        ]
        validator_block = f"Статус: {validator.status}\n" + "\n".join(validator_lines)
    else:
        validator_block = f"Статус: {validator.status}, проблем не найдено."

    return CRITIC_PROMPT_TEMPLATE.format(
        context_block="\n".join(context_lines),
        validator_block=validator_block,
        draft_block=markdown[:_MAX_DRAFT_CHARS],
    )


def parse_critic_result(stdout: str) -> CriticResult | None:
    """Разбор последней строки с маркером; невалидно → None (§83: schema)."""
    payload: Any = None
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith(CRITIC_RESULT_MARKER):
            try:
                payload = json.loads(stripped[len(CRITIC_RESULT_MARKER) :].strip())
            except json.JSONDecodeError:
                return None
            break
    if payload is None:
        return None
    try:
        return CriticResult.model_validate(payload)
    except ValidationError:
        return None


def run_critic(
    executor: CriticExecutor | None,
    model: QuickReportModel,
    markdown: str,
    validator: ValidatorResult,
    *,
    warnings: list[str] | None = None,
) -> CriticResult | None:
    """Прогон критика с fail-safe: любой сбой → None и warning (§77).

    Один retry при битом RESULT (текст ошибки добавляется к промпту),
    как в query_documents второго контура.
    """
    warnings = warnings if warnings is not None else []
    if executor is None:
        warnings.append("критик отключён: TERRALOGIC_CRITIC_CMD не задан")
        return None
    prompt = build_critic_prompt(model, markdown, validator)
    last_error = "нет результата"
    for attempt in range(2):
        try:
            with tempfile.TemporaryDirectory(prefix="terralogic-critic-") as tmp:
                exec_result = executor.run(prompt, Path(tmp))
        except Exception as exc:  # noqa: BLE001 — критик не ломает отчёт
            warnings.append(f"критик недоступен: {exc}")
            return None
        if exec_result.returncode != 0:
            warnings.append(
                f"критик завершился с кодом {exec_result.returncode}"
            )
            return None
        result = parse_critic_result(exec_result.stdout)
        if result is not None:
            return result
        last_error = "CRITIC_RESULT не найден или невалиден"
        prompt = (
            f"{prompt}\n\nПРЕДЫДУЩИЙ ОТВЕТ ОТКЛОНЁН: {last_error}.\n"
            "Верни исправленный результат: валидный JSON после маркера"
            f" {CRITIC_RESULT_MARKER}."
        )
        if attempt == 1:
            break
    warnings.append(f"критик не вернул валидный результат: {last_error}")
    return None
