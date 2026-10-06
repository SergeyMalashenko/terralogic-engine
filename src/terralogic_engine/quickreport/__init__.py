"""QuickReport: экспресс-оценка участка — контекст, стаб-скоринг, yaml-активы."""

from .context import build_quick_context, build_quick_context_from_report
from .models import (
    QuickFactor,
    QuickPassport,
    QuickReportContext,
    QuickSurroundings,
    QuickVerdict,
)
from .rules import (
    QuickRulesError,
    ScoringRules,
    ZoneGuidance,
    compute_score,
    load_scoring_rules,
    load_zone_guidance,
    match_guidance,
)

__all__ = [
    "QuickFactor",
    "QuickPassport",
    "QuickReportContext",
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
]
