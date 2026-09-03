"""Typed, persisted output of the TerraLogic spatial-analysis stage."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SpatialRelation = Literal[
    "disjoint",
    "intersects",
    "object_inside_parcel",
    "parcel_inside_object",
]
NearestStatus = Literal["found", "not_found_within_aoi"]


class IntersectionItem(BaseModel):
    """Intersection of one stored source object with the land parcel."""

    feature_id: str
    source: Literal["nspd", "osm", "rgis"]
    feature_class: str
    name: str
    geometry_type: str
    relation: SpatialRelation
    intersection_area_m2: float | None = None
    parcel_coverage_percent: float | None = None
    object_coverage_percent: float | None = None
    intersection_length_m: float | None = None


class IntersectionSummary(BaseModel):
    """Non-double-counted intersection summary for one semantic class."""

    key: str
    name: str
    candidate_count: int = Field(ge=0)
    intersecting_count: int = Field(ge=0)
    union_intersection_area_m2: float | None = None
    parcel_coverage_percent: float | None = None
    union_intersection_length_m: float | None = None


class NearestObject(BaseModel):
    """Nearest stored object for one requested category inside the AOI."""

    group: str
    group_name: str
    category: str
    category_name: str
    source: Literal["osm", "dgis"]
    status: NearestStatus
    candidate_count: int = Field(ge=0)
    feature_id: str | None = None
    object_name: str | None = None
    distance_m: float | None = Field(default=None, ge=0)


class AnalysisResult(BaseModel):
    """Complete analytics result tied to one immutable collection receipt."""

    id: str
    case_id: str
    collection_run_id: str
    aoi_id: str
    analytics_version: str
    calculated_at: datetime
    parcel_feature_id: str
    parcel_area_m2: float = Field(gt=0)
    metric_crs: str
    search_radius_m: float = Field(ge=0)
    zouit_summary: IntersectionSummary
    zouit_intersections: list[IntersectionItem] = Field(default_factory=list)
    natural_summaries: list[IntersectionSummary] = Field(default_factory=list)
    natural_intersections: list[IntersectionItem] = Field(default_factory=list)
    social_nearest: list[NearestObject] = Field(default_factory=list)
    natural_nearest: list[NearestObject] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
