"""Report QA — детерминированный валидатор и ограниченный LLM-критик.

Конвейер (§61 ТЗ): QuickReportModel → Markdown → ValidatorResult →
CriticResult. Ни валидатор, ни критик не меняют отчёт и не вычисляют
скоринг; критик опционален и fail-safe (§77).
"""

from .critic import (
    CRITIC_RESULT_MARKER,
    CriticExecResult,
    CriticExecutor,
    SubprocessCriticExecutor,
    build_critic_prompt,
    parse_critic_result,
    run_critic,
)
from .models import CriticResult, QaIssue, QaSeverity, ValidatorResult
from .validator import validate_quickreport

__all__ = [
    "CRITIC_RESULT_MARKER",
    "CriticExecResult",
    "CriticExecutor",
    "CriticResult",
    "QaIssue",
    "QaSeverity",
    "SubprocessCriticExecutor",
    "ValidatorResult",
    "build_critic_prompt",
    "parse_critic_result",
    "run_critic",
    "validate_quickreport",
]
