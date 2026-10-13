"""Контракты Report QA (§61, §64, §73 ТЗ TerraLogicX).

Единая модель проблемы для детерминированного валидатора и LLM-критика:
валидатор проверяет равенство полей кодом, критик — семантику; оба
возвращают `QaIssue`. Ни один из них не меняет отчёт: targeted revision
и LLM Writer — отдельная итерация.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

QaSeverity = Literal["info", "warning", "error", "critical"]


class QaIssue(BaseModel):
    """Одна найденная проблема отчёта (валидатор или критик)."""

    rule_id: str
    section: str
    severity: QaSeverity
    message: str
    claim: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    suggested_action: str = ""


class ValidatorResult(BaseModel):
    """Итог детерминированной валидации: pass — ни одной error/critical."""

    status: Literal["pass", "fail"]
    issues: list[QaIssue] = Field(default_factory=list)


class CriticResult(BaseModel):
    """Структурированный ответ LLM-критика (§73 ТЗ)."""

    status: Literal["pass", "needs_revision"]
    issues: list[QaIssue] = Field(default_factory=list)
