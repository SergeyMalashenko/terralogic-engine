"""Bounded NarrativeGenerator для блока «Что уже удалось установить» (§62–63 ТЗ).

Narrative — опциональный LLM-этап поверх детерминированного конвейера:
генератор вызывается только после ``build_quick_context`` → скоринг 6.2 →
``build_quick_model`` и получает bounded ``EstablishedFactsInput``, собранный
исключительно из ``QuickReportContext`` и ``QuickReportModel`` (маркетинг
вход не попадает). LLM формулирует уже установленные факты, но не создаёт
их: балл, статус, паспорт, факторы и маркетинг остаются детерминированными.

Структура:

- ``EstablishedFactsInput`` / ``EstablishedFactsNarrative`` — контракты
  входа/выхода с observability-метаданными (prompt version, sha256 входа,
  latency, attempts, usage);
- ``NarrativeExecutor`` / ``SubprocessNarrativeExecutor`` — infrastructure
  boundary (в проде ``scripts/hermes-writer.sh``, в тестах фейк);
- ``HermesNarrativeGenerator`` — prompt v1, строгий structured output
  (``=== NARRATIVE_RESULT ===`` + JSON ``{"text": "..."}``, extra fields
  запрещены), детерминированные guardrails, один retry только при битом
  structured output;
- ``DeterministicNarrativeGenerator`` — текущая established-сводка в том же
  Markdown-виде, fallback не меняет baseline;
- ``FallbackNarrativeGenerator`` — обёртка: None/exception/timeout/invalid
  LLM-генератора деградирует в детерминированный результат с warning (§77).

API синхронный: ``prepare_quickreport()`` синхронен, asyncio внутрь
MCP-пайплайна не вводится.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .model import QuickReportModel
from .models import QuickReportContext

if TYPE_CHECKING:
    from ..qa.models import QaIssue

NARRATIVE_RESULT_MARKER = "=== NARRATIVE_RESULT ==="
WRITER_CMD_ENV = "TERRALOGIC_WRITER_CMD"
WRITER_TIMEOUT_ENV = "TERRALOGIC_WRITER_TIMEOUT_S"
WRITER_MODEL_ENV = "TERRALOGIC_WRITER_MODEL"
WRITER_USAGE_FILE_ENV = "TERRALOGIC_WRITER_USAGE_FILE"
_DEFAULT_TIMEOUT_S = 300
DEFAULT_WRITER_MODEL = "qwen3.5:9b-q4_K_M"

WRITER_PROMPT_VERSION = "quickreport_established_facts_prompt_v2"

MAX_NARRATIVE_CHARS = 700

# Цель методики 6.2 (scoring_rules.yaml: target).
_METHODOLOGY_TARGET = "индивидуальный жилой дом"

# Категоричные юридические обещания, которых в экспресс-отчёте быть не может.
BANNED_NARRATIVE_PHRASES = (
    "гарантир",
    "можно строить",
    "разрешено строительство",
    "юридически чист",
    "полностью безопас",
)

_NUMBER_RE = re.compile(r"\d[\d ]*")

WRITER_PROMPT_TEMPLATE = """\
Ты — редактор экспресс-отчёта об участке TerraLogicX. Твоя единственная
задача — сформулировать раздел «Что уже удалось установить»: один связный
абзац из 2–3 предложений на русском языке, обращение на «вы».

ЖЁСТКИЕ РАМКИ:
- Используй только приведённые ниже факты (JSON). Не добавляй новых фактов,
  зон, чисел, процентов и расстояний; не используй собственные знания модели.
- Не повторяй кадастровый номер и полный адрес: они уже есть в заголовке и
  паспорте участка.
- Не называй балл и название статуса: они уже показаны в блоке «Оценка».
- Различай источники: документ ПЗЗ подтверждает зону и регламент, а
  пространственный анализ только выявляет пересечения и охват. Не пиши, что
  пространственный анализ «подтвердил регламент».
- Не делай правовых и градостроительных заключений: запрещены категоричные
  обещания («гарантировано», «можно строить», «разрешено строительство»,
  «юридически чист» и т.п.).
- Пробелы данных (evidence_gaps) формулируй как «не установлены / требуется
  проверить»; не превращай неустановленное в подтверждённое.
- Не называй и не изменяй цены и условия услуг; никакого маркетинга и
  призывов к действию.
- Текст — один абзац без переводов строк, списков и Markdown-разметки.
{issues_block}
УСТАНОВЛЕННЫЕ ФАКТЫ (JSON):
{facts_json}

ФИНАЛ — последней строкой строго:
=== NARRATIVE_RESULT === {{"text": "..."}}
"""

_ISSUES_INTRO = (
    "ЗАМЕЧАНИЯ КРИТИКА (учитывай при переформулировке, исправляй только"
    " раздел «Что уже удалось установить»):"
)


# ---------------------------------------------------------------------------
# Контракты входа/выхода
# ---------------------------------------------------------------------------


class EstablishedParcelFacts(BaseModel):
    """Кадастровые сведения участка, разрешённые к показу генератору."""

    cadastral_number: str
    address: str | None = None
    area_m2: float | None = None
    land_category: str | None = None
    permitted_use: str | None = None  # исходный текст ВРИ источника
    vri_code: str | None = None  # канонический ВРИ (П/0412)
    vri_name: str | None = None
    territorial_zone: str | None = None
    cadastral_value_rub: float | None = None


class EstablishedRestriction(BaseModel):
    """Один фактор внимания: идентификатор, зона и охват."""

    rule_id: str
    zone_name: str
    coverage_percent: float
    coverage_word: str


class EstablishedZoneRegulation(BaseModel):
    """Регламент территориальной зоны из ПЗЗ (при наличии)."""

    zone_code: str
    doc_number: str | None = None
    doc_version_date: str | None = None


class EstablishedFactsInput(BaseModel):
    """Bounded вход генератора: только факты из context и model (§62).

    Маркетинг, цены и CTA сюда не попадают по построению.
    """

    target_use: str
    parcel: EstablishedParcelFacts
    zone_regulation: EstablishedZoneRegulation | None = None
    restrictions: list[EstablishedRestriction] = Field(default_factory=list)
    environment: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    score: int | None = None
    status_title: str
    preliminary: bool = False
    deterministic_facts: list[str] = Field(default_factory=list)


class EstablishedFactsNarrative(BaseModel):
    """Результат генерации: текст раздела + observability-метаданные."""

    text: str
    source: Literal["llm", "deterministic_fallback"]
    model_name: str | None = None
    prompt_version: str | None = None
    input_sha256: str
    latency_ms: float | None = None
    attempts: int = 0
    warnings: list[str] = Field(default_factory=list)
    usage: dict[str, object] | None = None  # model/token fields, без CoT


# ---------------------------------------------------------------------------
# Infrastructure boundary: исполнитель
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NarrativeExecResult:
    """Сырой результат запуска исполнителя narrative-генератора."""

    stdout: str
    returncode: int
    stderr: str = ""
    duration_ms: float = 0.0
    usage: dict[str, object] | None = None


class NarrativeExecutor(Protocol):
    """Исполнитель роли writer'а (Hermes в проде, фейк в тестах)."""

    def run(self, prompt: str, workdir: Path) -> NarrativeExecResult:
        """Один one-shot запуск по промпту."""


_USAGE_KEYS = (
    "model",
    "prompt_tokens",
    "completion_tokens",
    "input_tokens",
    "output_tokens",
    "total_tokens",
)


def _read_usage_file(path: Path) -> dict[str, object] | None:
    """Базовые model/token fields из usage-файла Hermes; без chain-of-thought."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    usage = {key: payload[key] for key in _USAGE_KEYS if key in payload}
    return usage or None


class SubprocessNarrativeExecutor:
    """Запуск внешнего CLI-агента: prompt — последним аргументом команды."""

    def __init__(
        self,
        command: tuple[str, ...],
        *,
        timeout: int = _DEFAULT_TIMEOUT_S,
        model_name: str = DEFAULT_WRITER_MODEL,
        usage_file: bool = False,
    ) -> None:
        if not command:
            raise ValueError("команда writer'а не задана")
        self.command = command
        self.timeout = timeout
        self.model_name = model_name
        self.usage_file = usage_file

    @classmethod
    def from_env(cls, env_var: str = WRITER_CMD_ENV) -> SubprocessNarrativeExecutor | None:
        """Исполнитель из env TERRALOGIC_WRITER_CMD; пусто → None (отключён).

        Продовая конфигурация — wrapper `scripts/hermes-writer.sh` (Hermes +
        локальная Qwen, отдельный профиль ~/.hermes-writer без tools):
        timeout — TERRALOGIC_WRITER_TIMEOUT_S (default 300), модель —
        TERRALOGIC_WRITER_MODEL (default qwen3.5:9b-q4_K_M), usage-файл
        включается непустым TERRALOGIC_WRITER_USAGE_FILE.
        """
        raw = os.environ.get(env_var, "").strip()
        if not raw:
            return None
        timeout_raw = os.environ.get(WRITER_TIMEOUT_ENV, "").strip()
        try:
            timeout = int(timeout_raw) if timeout_raw else _DEFAULT_TIMEOUT_S
        except ValueError:
            timeout = _DEFAULT_TIMEOUT_S
        model_name = os.environ.get(WRITER_MODEL_ENV, "").strip() or DEFAULT_WRITER_MODEL
        return cls(
            tuple(shlex.split(raw)),
            timeout=timeout,
            model_name=model_name,
            usage_file=bool(os.environ.get(WRITER_USAGE_FILE_ENV, "").strip()),
        )

    def run(self, prompt: str, workdir: Path) -> NarrativeExecResult:
        env = None
        usage_path: Path | None = None
        if self.usage_file:
            usage_path = workdir / "hermes-usage.json"
            env = {**os.environ, WRITER_USAGE_FILE_ENV: str(usage_path)}
        started = time.monotonic()
        proc = subprocess.run(
            [*self.command, prompt],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
            env=env,
        )
        duration_ms = (time.monotonic() - started) * 1000
        usage = _read_usage_file(usage_path) if usage_path is not None else None
        return NarrativeExecResult(
            stdout=proc.stdout,
            returncode=proc.returncode,
            stderr=proc.stderr,
            duration_ms=duration_ms,
            usage=usage,
        )


# ---------------------------------------------------------------------------
# Сборка bounded input и prompt
# ---------------------------------------------------------------------------


def build_established_facts_input(
    context: QuickReportContext, model: QuickReportModel
) -> EstablishedFactsInput:
    """Bounded input только из QuickReportContext и QuickReportModel.

    Маркетинг (presentation layer) в input не передаётся.
    """
    parcel = context.parcel
    vri = parcel.vri
    regulations = context.zone_regulations
    return EstablishedFactsInput(
        target_use=_METHODOLOGY_TARGET,
        parcel=EstablishedParcelFacts(
            cadastral_number=parcel.cadastral_number,
            address=parcel.address,
            area_m2=parcel.area_m2,
            land_category=parcel.land_category,
            permitted_use=parcel.permitted_use,
            vri_code=vri.code if vri is not None else None,
            vri_name=vri.name if vri is not None else None,
            territorial_zone=parcel.territorial_zone,
            cadastral_value_rub=parcel.cadastral_value_rub,
        ),
        zone_regulation=(
            EstablishedZoneRegulation(
                zone_code=regulations.zone_code,
                doc_number=regulations.doc_number,
                doc_version_date=regulations.doc_version_date,
            )
            if regulations is not None
            else None
        ),
        restrictions=[
            EstablishedRestriction(
                rule_id=factor.rule_id,
                zone_name=factor.zone_name,
                coverage_percent=factor.coverage_percent,
                coverage_word=factor.coverage_word,
            )
            for factor in model.attention_factors
            if not factor.informational
        ],
        environment=list(model.environment.lines),
        evidence_gaps=[item.text for item in model.missing_information],
        score=model.score,
        status_title=model.status.status_title,
        preliminary=model.status.preliminary,
        deterministic_facts=list(model.established_facts),
    )


def input_sha256(facts_input: EstablishedFactsInput) -> str:
    """Стабильный отпечаток сериализованного входа (observability)."""
    return hashlib.sha256(facts_input.model_dump_json().encode("utf-8")).hexdigest()


def build_narrative_prompt(
    facts_input: EstablishedFactsInput, issues: list[QaIssue] | None = None
) -> str:
    """Промпт writer'а (v1); issues — замечания критика для targeted revision."""
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
    facts_json = json.dumps(
        facts_input.model_dump(mode="json"), ensure_ascii=False, indent=2
    )
    return WRITER_PROMPT_TEMPLATE.format(
        issues_block=issues_block,
        facts_json=facts_json,
    )


# ---------------------------------------------------------------------------
# Structured output: parse и детерминированные guardrails
# ---------------------------------------------------------------------------


class _NarrativeOutput(BaseModel):
    """Строгий structured output writer'а: только text, extra fields запрещены."""

    model_config = ConfigDict(extra="forbid")

    text: str


def parse_narrative_output(stdout: str) -> _NarrativeOutput | None:
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
        return _NarrativeOutput.model_validate(payload)
    except ValidationError:
        return None


def text_numbers(text: str) -> set[str]:
    """Числа текста: «1 000» и «1000» нормализуются к одному виду."""
    return {
        match.group(0).replace(" ", "").rstrip("., ") for match in _NUMBER_RE.finditer(text)
    }


def input_allowed_numbers(facts_input: EstablishedFactsInput) -> set[str]:
    """Все числа сериализованного EstablishedFactsInput — допустимое множество."""
    return text_numbers(facts_input.model_dump_json())


def check_narrative_text(
    text: str, facts_input: EstablishedFactsInput
) -> str | None:
    """None — текст валиден; иначе причина отклонения (§62–63 рамки)."""
    if not text.strip():
        return "пустой текст"
    if len(text) > MAX_NARRATIVE_CHARS:
        return f"длиннее {MAX_NARRATIVE_CHARS} символов"
    if "\n" in text:
        return "переводы строк недопустимы (один абзац)"
    folded = text.casefold()
    cadastral_number = facts_input.parcel.cadastral_number.casefold()
    if cadastral_number in folded:
        return "повторяет кадастровый номер из заголовка и паспорта"
    address = (facts_input.parcel.address or "").casefold()
    if address and address in folded:
        return "повторяет полный адрес из заголовка и паспорта"
    status_title = facts_input.status_title.casefold()
    if status_title and status_title in folded:
        return "повторяет статус из блока оценки"
    if (
        "пространственный анализ" in folded
        and "регламент" in folded
        and any(marker in folded for marker in ("подтвердил", "подтверждает"))
    ):
        return "смешивает пространственный анализ с подтверждением регламента"
    for phrase in BANNED_NARRATIVE_PHRASES:
        if phrase in folded:
            return f"запрещённая категоричная фраза «{phrase}»"
    foreign = text_numbers(text) - input_allowed_numbers(facts_input)
    if foreign:
        return f"чужие числа: {sorted(foreign)}"
    return None


# ---------------------------------------------------------------------------
# Генераторы
# ---------------------------------------------------------------------------


class NarrativeGenerator(Protocol):
    """Генератор текста раздела «Что уже удалось установить»."""

    def generate_established_facts(
        self, facts_input: EstablishedFactsInput, *, issues: list[QaIssue] | None = None
    ) -> EstablishedFactsNarrative | None:
        """Один bounded прогон; None — результат непригоден/недоступен."""


class DeterministicNarrativeGenerator:
    """Fallback без LLM: текущая established-сводка в том же Markdown-виде.

    Текст — тот же bullet list, что рендерит deterministic-путь, поэтому
    fallback не меняет baseline отчёта ни на байт.
    """

    def generate_established_facts(
        self, facts_input: EstablishedFactsInput, *, issues: list[QaIssue] | None = None
    ) -> EstablishedFactsNarrative:
        return EstablishedFactsNarrative(
            text="\n".join(f"- {fact}" for fact in facts_input.deterministic_facts),
            source="deterministic_fallback",
            input_sha256=input_sha256(facts_input),
        )


class HermesNarrativeGenerator:
    """LLM-генератор поверх NarrativeExecutor (Hermes + Qwen в проде).

    Один retry — только при битом structured output; отклонение рамками
    (чужие числа, категоричные фразы, длина, переводы строк) ретраев не
    даёт. Любой сбой → None + ``last_error``; решение о fallback принимает
    FallbackNarrativeGenerator.
    """

    def __init__(
        self, executor: NarrativeExecutor | None, *, model_name: str | None = None
    ) -> None:
        self._executor = executor
        self.model_name = model_name or getattr(executor, "model_name", None)
        self.last_error: str | None = None

    def generate_established_facts(
        self, facts_input: EstablishedFactsInput, *, issues: list[QaIssue] | None = None
    ) -> EstablishedFactsNarrative | None:
        self.last_error = None
        if self._executor is None:
            self.last_error = f"writer отключён: {WRITER_CMD_ENV} не задан"
            return None
        digest = input_sha256(facts_input)
        prompt = build_narrative_prompt(facts_input, issues)
        warnings: list[str] = []
        latency_ms = 0.0
        last_error = "нет результата"
        for attempt in range(2):
            try:
                with tempfile.TemporaryDirectory(prefix="terralogic-writer-") as tmp:
                    exec_result = self._executor.run(prompt, Path(tmp))
            except Exception as exc:  # noqa: BLE001 — writer не ломает отчёт
                self.last_error = f"writer недоступен: {exc}"
                return None
            latency_ms += exec_result.duration_ms
            if exec_result.returncode != 0:
                detail = f"writer завершился с кодом {exec_result.returncode}"
                if exec_result.stderr.strip():
                    detail += f": {exec_result.stderr.strip()[-300:]}"
                self.last_error = detail
                return None
            output = parse_narrative_output(exec_result.stdout)
            if output is not None:
                reason = check_narrative_text(output.text, facts_input)
                if reason is not None:
                    self.last_error = f"результат отклонён рамками: {reason}"
                    return None
                return EstablishedFactsNarrative(
                    text=" ".join(output.text.split()),
                    source="llm",
                    model_name=self.model_name,
                    prompt_version=WRITER_PROMPT_VERSION,
                    input_sha256=digest,
                    latency_ms=latency_ms,
                    attempts=attempt + 1,
                    warnings=warnings,
                    usage=exec_result.usage,
                )
            last_error = "NARRATIVE_RESULT не найден или невалиден"
            warnings.append(f"попытка {attempt + 1}: {last_error} — retry")
            prompt = (
                f"{prompt}\n\nПРЕДЫДУЩИЙ ОТВЕТ ОТКЛОНЁН: {last_error}.\n"
                "Верни исправленный результат: валидный JSON после маркера"
                f" {NARRATIVE_RESULT_MARKER}."
            )
        self.last_error = f"writer не вернул валидный результат: {last_error}"
        return None


class FallbackNarrativeGenerator:
    """LLM-генератор с fail-safe деградацией в детерминированный текст (§77).

    None/exception/timeout/invalid output первичного генератора →
    детерминированный результат с warning; отчёт никогда не прерывается.
    """

    def __init__(
        self,
        primary: NarrativeGenerator | None,
        fallback: DeterministicNarrativeGenerator | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback or DeterministicNarrativeGenerator()

    def generate_established_facts(
        self, facts_input: EstablishedFactsInput, *, issues: list[QaIssue] | None = None
    ) -> EstablishedFactsNarrative:
        if self._primary is None:
            return self._fallback.generate_established_facts(facts_input)
        try:
            result = self._primary.generate_established_facts(facts_input, issues=issues)
        except Exception as exc:  # noqa: BLE001 — narrative не ломает отчёт
            result = None
            reason = f"исключение генератора: {exc}"
        else:
            reason = getattr(self._primary, "last_error", None) or "генератор вернул None"
        if result is not None:
            return result
        fallback = self._fallback.generate_established_facts(facts_input)
        fallback.warnings.append(
            f"LLM narrative недоступен ({reason}); применён детерминированный текст"
        )
        return fallback
