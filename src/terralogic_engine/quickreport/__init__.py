"""QuickReport: экспресс-оценка участка — контекст, скоринг 6.1, yaml-активы."""

from .context import build_quick_context, build_quick_context_from_report
from .geometry import ConfigurationOutcome, evaluate_configuration
from .model import QuickReportModel, build_quick_model
from .models import (
    QuickFactor,
    QuickMissingItem,
    QuickPassport,
    QuickProtocol,
    QuickReportContext,
    QuickReportResult,
    QuickSurroundings,
    QuickVerdict,
    QuickZoneRegulations,
    QuickZoneUse,
)
from .render import render_quick_model, render_quickreport
from .rules import (
    MarketingConfig,
    QuickRulesError,
    ScoredFactor,
    ScoringRules,
    VerdictResult,
    ZoneGuidance,
    compute_verdict,
    load_marketing,
    load_scoring_rules,
    load_zone_guidance,
    match_guidance,
    methodology_sha256,
)
from .service import render_service_report

__all__ = [
    "ConfigurationOutcome",
    "MarketingConfig",
    "QuickFactor",
    "QuickMissingItem",
    "QuickPassport",
    "QuickProtocol",
    "QuickReportContext",
    "QuickReportModel",
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
    "build_quick_model",
    "compute_verdict",
    "evaluate_configuration",
    "load_marketing",
    "load_scoring_rules",
    "load_zone_guidance",
    "match_guidance",
    "methodology_sha256",
    "render_quick_model",
    "render_quickreport",
    "render_service_report",
]
