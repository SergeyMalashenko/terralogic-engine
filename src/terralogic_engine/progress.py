"""Progress reporting contract for long-running case preparation.

Синхронный коллбэк ``(percent, stage)`` общий для асинхронного
acquisition pipeline и синхронного quickreport: оба вызывают его
напрямую, без await. MCP-слой мостит коллбэк в
``ctx.report_progress`` (см. ``terralogic_engine.mcp``), а клиенты
(telegram-бот) отображают только проценты — ``stage`` остаётся
внутренней меткой этапа и наружу не транслируется.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

ProgressCallback: TypeAlias = Callable[[float, str], None]


def report_progress(
    callback: ProgressCallback | None,
    percent: float,
    stage: str,
) -> None:
    """Emit one progress event when a listener is attached."""

    if callback is not None:
        callback(percent, stage)


def scaled_progress(
    callback: ProgressCallback | None,
    lo: float,
    hi: float,
) -> ProgressCallback | None:
    """Remap a sub-phase 0–100 scale onto the [lo, hi] global range."""

    if callback is None:
        return None

    def _scaled(percent: float, stage: str) -> None:
        callback(lo + (hi - lo) * percent / 100.0, stage)

    return _scaled
