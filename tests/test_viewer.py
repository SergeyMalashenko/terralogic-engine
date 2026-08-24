from __future__ import annotations

from typing import Any

from terralogic_acquisition.domain.models import GeoFeature, SourceName
from terralogic_acquisition.viewer.cli import build_parser
from terralogic_acquisition.viewer.data import (
    build_feature_collection,
    feature_label,
    feature_table_rows,
    group_features,
)

from .fakes import PARCEL_GEOMETRY


def _feature(
    feature_id: str,
    *,
    source: SourceName = "nspd",
    feature_class: str = "nspd.parcel",
    geometry: dict[str, Any] | None = None,
    properties: dict[str, Any] | None = None,
) -> GeoFeature:
    return GeoFeature(
        id=feature_id,
        case_id="case-viewer",
        snapshot_id=f"snapshot-{source}",
        source=source,
        source_id=feature_id,
        feature_class=feature_class,
        geometry=geometry,
        properties=properties or {},
    )


def test_viewer_builds_geojson_and_omits_attribute_only_features() -> None:
    parcel = _feature(
        "parcel-1",
        geometry=PARCEL_GEOMETRY,
        properties={"cadastral_number": "52:26:0040002:3823"},
    )
    attribute_only = _feature("attribute-only")

    collection = build_feature_collection([parcel, attribute_only])

    assert collection["type"] == "FeatureCollection"
    assert len(collection["features"]) == 1
    assert collection["features"][0]["properties"]["label"] == (
        "52:26:0040002:3823"
    )
    assert feature_label(attribute_only) == "nspd.parcel · attribute-only"


def test_viewer_groups_and_summarizes_features() -> None:
    nspd = _feature("nspd-1")
    osm = _feature(
        "osm-1",
        source="osm",
        feature_class="osm.building",
        properties={"name": "Склад"},
    )

    groups = group_features([osm, nspd])
    rows = feature_table_rows([osm, nspd])

    assert list(groups) == [("nspd", "nspd.parcel"), ("osm", "osm.building")]
    assert rows[0]["label"] == "Склад"
    assert rows[0]["has_geometry"] is False


def test_viewer_cli_defaults_to_localhost() -> None:
    args = build_parser().parse_args([])

    assert args.store == "./case-store"
    assert args.host == "127.0.0.1"
    assert args.port == 8501
