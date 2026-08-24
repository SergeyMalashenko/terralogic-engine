"""Versioned profiles that bound source collection."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CollectionProfile(BaseModel):
    """Deterministic inputs used for one collection run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str
    nspd_blocks: tuple[str, ...]
    osm_blocks: tuple[str, ...]
    nspd_limit_per_layer: int = Field(default=100, ge=1, le=100)
    osm_limit_per_block: int = Field(default=100, ge=1, le=500)
    osm_margin_m: int = Field(default=1000, ge=0, le=10_000)
    stale_after_seconds: int = Field(default=86_400, ge=0)


STANDARD_LAND_REPORT = CollectionProfile(
    name="standard_land_report",
    version="1.0",
    nspd_blocks=(
        "zouit",
        "urban_planning",
        "environmental_and_special",
        "egrn_objects",
        "cadastral_division",
        "cadastral_value",
    ),
    osm_blocks=(
        "buildings",
        "transport",
        "landuse",
        "infrastructure",
        "poi",
    ),
)

_PROFILES = {
    (STANDARD_LAND_REPORT.name, STANDARD_LAND_REPORT.version): STANDARD_LAND_REPORT,
}


def get_collection_profile(name: str, version: str) -> CollectionProfile:
    """Return a known immutable profile or fail before source calls begin."""

    try:
        return _PROFILES[(name, version)]
    except KeyError as exc:
        raise ValueError(
            f"Unknown collection profile {name!r} version {version!r}"
        ) from exc
