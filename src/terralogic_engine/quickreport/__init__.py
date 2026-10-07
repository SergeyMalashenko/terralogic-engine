"""QuickReport: экспресс-оценка участка — контекст, стаб-скоринг, yaml-активы."""

from .context import build_quick_context, build_quick_context_from_report
from .models import (
    QuickFactor,
    QuickPassport,
    QuickReportContext,
    QuickReportResult,
    QuickSurroundings,
    QuickVerdict,
)
from .render import render_quickreport
from .rules import (
    QuickRulesError,
    ScoringRules,
    ZoneGuidance,
    compute_score,
    load_scoring_rules,
    load_zone_guidance,
    match_guidance,
    methodology_sha256,
)

__all__ = [
    "QuickFactor",
    "QuickPassport",
    "QuickReportContext",
    "QuickReportResult",
    "QuickRulesError",
    "QuickSurroundings",
    "QuickVerdict",
    "ScoringRules",
    "ZoneGuidance",
    "build_quick_context",
    "build_quick_context_from_report",
    "compute_score",
    "load_scoring_rules",
    "load_zone_guidance",
    "match_guidance",
    "methodology_sha256",
    "render_quickreport",
]
