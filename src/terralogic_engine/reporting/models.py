"""Typed contracts for report generation and persisted Markdown artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from terralogic_engine.analytics.models import (
    IntersectionSummary,
    NearestObject,
    SpatialRelation,
)
from terralogic_engine.domain.models import ReceiptStatus, SourceName


class ParcelReportContext(BaseModel):
    """Compact federal parcel passport without its heavy GeoJSON contour."""

    cadastral_number: str
    address: str | None = None
    status: str | None = None
    declared_area_m2: float | None = None
    calculated_area_m2: float
    land_category: str | None = None
    permitted_use: str | None = None
    cadastral_value_rub: float | None = None
    geometry_type: str


class SearchAreaReportContext(BaseModel):
    """Search-area parameters shared by OSM and 2GIS."""

    parcel_minimum_radius_m: float
    margin_m: int
    search_radius_m: float


class LegalActReportContext(BaseModel):
    """Published legal-act attributes attached to one NSPD zone."""

    name: str | None = None
    number: str | None = None
    date: str | None = None
    issuer: str | None = None


class ZouitReportContext(BaseModel):
    """One ZOUIT with source attributes and deterministic intersection metrics."""

    feature_id: str
    source: SourceName
    name: str
    registry_number: str | None = None
    zone_type: str | None = None
    registration_date: str | None = None
    restrictions: str | None = None
    legal_act: LegalActReportContext | None = None
    relation: SpatialRelation
    intersection_area_m2: float
    parcel_coverage_percent: float
    zone_coverage_percent: float


class InfrastructureObjectContext(BaseModel):
    """A bounded example object retained for a transport category."""

    feature_id: str
    name: str
    distance_to_search_point_m: float | None = None


class TransportCategoryContext(BaseModel):
    """Collected 2GIS transport inventory; no parcel-distance claim is made."""

    group: str
    group_name: str
    category: str
    category_name: str
    object_count: int = Field(ge=0)
    examples: list[InfrastructureObjectContext] = Field(default_factory=list)
    distance_basis: Literal["search_point"] = "search_point"


class RoadClassContext(BaseModel):
    """Count and bounded names for one collected OSM highway class."""

    road_class: str
    road_class_name: str
    object_count: int = Field(ge=0)
    named_examples: list[str] = Field(default_factory=list)


class SourceEvidenceContext(BaseModel):
    """Provenance for one immutable source snapshot."""

    source: SourceName
    snapshot_id: str
    retrieved_at: datetime
    adapter_version: str
    content_sha256: str


class ReportSectionTemplate(BaseModel):
    """One ordered section required by a report template."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    order: int = Field(ge=1)
    heading: str
    purpose: str
    required: bool = True


class ReportTemplate(BaseModel):
    """Independent, immutable, and versioned report-format entity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str
    version: str
    name: str
    description: str
    language: Literal["ru"] = "ru"
    output_format: Literal["markdown"] = "markdown"
    sections: tuple[ReportSectionTemplate, ...]
    generation_rules: tuple[str, ...]
    markdown_skeleton: str
    content_sha256: str


class ReportContext(BaseModel):
    """Bounded factual context supplied to Hermes for narrative generation."""

    context_version: str = "1.1"
    case_id: str
    collection_run_id: str
    analysis_id: str
    analytics_version: str
    collection_status: ReceiptStatus
    collected_at: datetime
    analyzed_at: datetime
    parcel: ParcelReportContext
    search_area: SearchAreaReportContext
    zouit_summary: IntersectionSummary
    zouit: list[ZouitReportContext] = Field(default_factory=list)
    natural_intersections: list[IntersectionSummary] = Field(default_factory=list)
    natural_nearest: list[NearestObject] = Field(default_factory=list)
    social_nearest: list[NearestObject] = Field(default_factory=list)
    transport_inventory: list[TransportCategoryContext] = Field(default_factory=list)
    road_inventory: list[RoadClassContext] = Field(default_factory=list)
    sources: list[SourceEvidenceContext] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PrepareCaseResult(BaseModel):
    """Compact result of collection plus deterministic analytics."""

    case_id: str
    collection_run_id: str
    collection_status: ReceiptStatus
    analysis_id: str
    report_context_ready: bool = True
    reused_collection: bool = False
    feature_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class GeneratedReport(BaseModel):
    """One persisted Markdown report tied to source and analytics versions."""

    id: str
    case_id: str
    collection_run_id: str
    analysis_id: str
    title: str
    template_id: str
    template_version: str
    template_sha256: str
    generated_at: datetime
    model_name: str | None = None
    relative_path: str
    content_sha256: str
    markdown: str


class SavedReportResult(BaseModel):
    """Compact MCP response after a Markdown report is persisted."""

    report_id: str
    case_id: str
    collection_run_id: str
    analysis_id: str
    template_id: str
    template_version: str
    template_sha256: str
    relative_path: str
    content_sha256: str
    generated_at: datetime
