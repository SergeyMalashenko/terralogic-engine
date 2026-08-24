"""Stable domain contracts shared by acquisition, storage, and future analytics."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CollectionStatus = Literal["running", "complete", "partial", "failed"]
ReceiptStatus = Literal["complete", "partial", "failed"]
RefreshPolicy = Literal["never", "if_stale", "always"]
SourceName = Literal["nspd", "osm"]

CADASTRAL_NUMBER_PATTERN = re.compile(r"^\d+:\d+:\d+:\d+$")
CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(UTC)


class CollectionRequest(BaseModel):
    """One idempotent source-collection request."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    cadastral_number: str
    profile: str = "standard_land_report"
    profile_version: str = "1.0"
    refresh_policy: RefreshPolicy = "if_stale"
    allow_partial: bool = True

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        normalized = value.strip()
        if not CASE_ID_PATTERN.fullmatch(normalized):
            raise ValueError(
                "case_id must contain only letters, digits, '.', '_', or '-'"
            )
        return normalized

    @field_validator("cadastral_number", mode="before")
    @classmethod
    def normalize_cadastral_number(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise TypeError("cadastral_number must be a string")
        normalized = re.sub(r"\s+", "", value)
        if not CADASTRAL_NUMBER_PATTERN.fullmatch(normalized):
            raise ValueError(
                "cadastral_number must contain four numeric parts separated by ':'"
            )
        return normalized


class CaseInfo(BaseModel):
    """Persistent identity and current state of one analysis case."""

    case_id: str
    cadastral_number: str
    report_profile: str
    status: CollectionStatus = "running"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SourceSnapshot(BaseModel):
    """Immutable raw payload stored outside the dialogue context."""

    id: str
    case_id: str
    run_id: str
    source: SourceName
    retrieved_at: datetime
    adapter_version: str
    content_sha256: str
    relative_path: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AreaOfInterest(BaseModel):
    """Validated parcel contour passed to the contour-based OSM service."""

    id: str
    case_id: str
    parcel_geometry: dict[str, Any]
    query_geometry: dict[str, Any]
    bbox: tuple[float, float, float, float]
    representative_point: tuple[float, float]
    source_snapshot_id: str
    geometry_hash: str
    source_crs: str = "EPSG:4326"
    metric_crs: str
    validation_warnings: list[str] = Field(default_factory=list)


class GeoFeature(BaseModel):
    """Canonical source feature stored with provenance and optional geometry."""

    id: str
    case_id: str
    snapshot_id: str
    source: SourceName
    source_type: str | None = None
    source_id: str | None = None
    feature_class: str
    geometry: dict[str, Any] | None = None
    crs: str = "EPSG:4326"
    properties: dict[str, Any] = Field(default_factory=dict)


class CollectionReceipt(BaseModel):
    """Compact result returned to Hermes instead of large source payloads."""

    case_id: str
    run_id: str
    status: ReceiptStatus
    nspd_snapshot_id: str | None = None
    osm_snapshot_id: str | None = None
    aoi_id: str | None = None
    feature_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime
    reused: bool = False
