"""Обратная совместимость: бывший трёхслотовый writer заменён bounded
NarrativeGenerator из ``quickreport.narrative`` (§62–63 ТЗ, промпт v1).

Старые поля main_conclusion / established_summary / factor_meanings сведены
к единственному narrative-слоту раздела «Что уже удалось установить»;
трёхслотовой логики здесь больше нет — ниже только алиасы новых контрактов
для существующих вызывающих (ReportingService принимает старый executor
как compatibility shim).
"""

from __future__ import annotations

from .narrative import (
    NARRATIVE_RESULT_MARKER,
    WRITER_CMD_ENV,
    NarrativeExecResult,
    NarrativeExecutor,
    SubprocessNarrativeExecutor,
)

WriterExecResult = NarrativeExecResult
WriterExecutor = NarrativeExecutor
SubprocessWriterExecutor = SubprocessNarrativeExecutor

__all__ = [
    "NARRATIVE_RESULT_MARKER",
    "WRITER_CMD_ENV",
    "SubprocessWriterExecutor",
    "WriterExecResult",
    "WriterExecutor",
]
