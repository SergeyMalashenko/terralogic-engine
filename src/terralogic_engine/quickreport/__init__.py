"""QuickReport: экспресс-оценка участка — контекст, скоринг 6.1, yaml-активы."""

from .context import build_quick_context, build_quick_context_from_report
from .geometry import ConfigurationOutcome, evaluate_configuration
from .models import (
    QuickFactor,
    QuickPassport,
    QuickReportContext,
    QuickReportResult,
    QuickSurroundings,
    QuickVerdict,
    QuickZoneRegulations,
    QuickZoneUse,
)
from .render import render_quickreport
from .rules import (
    QuickRulesError,
    ScoredFactor,
    ScoringRules,
    VerdictResult,
    ZoneGuidance,
    compute_verdict,
    load_scoring_rules,
    load_zone_guidance,
    match_guidance,
    methodology_sha256,
)

__all__ = [
    "ConfigurationOutcome",
    "QuickFactor",
    "QuickPassport",
    "QuickReportContext",
    "QuickReportResult",
    "QuickRulesError",
    "QuickSurroundings",
    "QuickVerdict",
    "QuickZoneRegulations",
    "QuickZoneUse",
    "ScoredFactor",
    "ScoringRules",
    "VerdictResult",
    "ZoneGuidance",
    "build_quick_context",
    "build_quick_context_from_report",
    "compute_verdict",
    "evaluate_configuration",
    "load_scoring_rules",
    "load_zone_guidance",
    "match_guidance",
    "methodology_sha256",
    "render_quickreport",
]
