from __future__ import annotations

from typing import Any

from shapely.geometry import mapping, shape

from terralogic_acquisition.domain.models import GeoFeature, SourceName
from terralogic_acquisition.viewer.cli import build_parser
from terralogic_acquisition.viewer.data import (
    build_feature_collection,
    feature_label,
    feature_table_rows,
    group_features,
    group_road_features,
    interior_ring_count,
    natural_contour_summary,
    osm_road_class,
    road_class_summary,
    source_summary_rows,
)

from .fakes import FOREST_GEOMETRY, PARCEL_GEOMETRY, ROAD_GEOMETRY


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
    dgis = _feature(
        "dgis-1",
        source="dgis",
        feature_class="education",
        properties={
            "name": "Школа",
            "distance_to_search_point_m": 420.0,
            "category_name": "Образование",
        },
    )

    groups = group_features([osm, nspd, dgis])
    rows = feature_table_rows([osm, nspd, dgis])
    summary = source_summary_rows([osm, nspd, dgis])

    assert list(groups) == [
        ("dgis", "education"),
        ("nspd", "nspd.parcel"),
        ("osm", "osm.building"),
    ]
    assert rows[0]["label"] == "Склад"
    assert rows[0]["has_geometry"] is False
    assert rows[2]["distance_m"] == 420.0
    assert summary[0] == {
        "source": "dgis",
        "class": "education",
        "objects": 1,
        "with_geometry": 0,
    }


def test_viewer_summarizes_natural_contours_and_polygon_holes() -> None:
    forest = _feature(
        "forest-1",
        source="osm",
        feature_class="forest",
        geometry=FOREST_GEOMETRY,
        properties={"name": "Лес с поляной"},
    )
    lake = _feature(
        "lake-1",
        source="osm",
        feature_class="lake",
        geometry=PARCEL_GEOMETRY,
    )
    river_without_geometry = _feature(
        "river-1",
        source="osm",
        feature_class="river",
    )

    collection = build_feature_collection([forest])
    summary = natural_contour_summary(
        [forest, lake, river_without_geometry]
    )

    assert interior_ring_count(FOREST_GEOMETRY) == 1
    assert interior_ring_count(dict(mapping(shape(FOREST_GEOMETRY)))) == 1
    assert collection["features"][0]["properties"]["interior_rings"] == 1
    assert summary == [
        {
            "class": "forest",
            "label": "Леса",
            "objects": 1,
            "contours": 1,
            "interior_rings": 1,
        },
        {
            "class": "lake",
            "label": "Озёра",
            "objects": 1,
            "contours": 1,
            "interior_rings": 0,
        },
        {
            "class": "river",
            "label": "Реки",
            "objects": 1,
            "contours": 0,
            "interior_rings": 0,
        },
    ]


def test_viewer_groups_and_labels_roads_by_highway_class() -> None:
    motorway = _feature(
        "road-motorway",
        source="osm",
        feature_class="road",
        geometry=ROAD_GEOMETRY,
        properties={"name": "М-7", "tags": {"highway": "motorway"}},
    )
    residential = _feature(
        "road-residential",
        source="osm",
        feature_class="road",
        geometry=ROAD_GEOMETRY,
        properties={"tags": {"highway": "residential"}},
    )
    unknown = _feature(
        "road-unknown",
        source="osm",
        feature_class="road",
        geometry=ROAD_GEOMETRY,
        properties={"tags": {"highway": "future_road_class"}},
    )
    grouped = group_road_features([unknown, residential, motorway])
    summary = road_class_summary([unknown, residential, motorway])
    geojson = build_feature_collection([motorway])

    assert osm_road_class(motorway) == "motorway"
    assert list(grouped) == ["motorway", "residential", "unknown"]
    assert geojson["features"][0]["properties"]["road_class"] == "motorway"
    assert geojson["features"][0]["properties"]["road_class_label"] == (
        "Автомагистраль"
    )
    assert summary == [
        {
            "road_class": "motorway",
            "label": "Автомагистраль",
            "objects": 1,
            "with_geometry": 1,
        },
        {
            "road_class": "residential",
            "label": "Жилая улица",
            "objects": 1,
            "with_geometry": 1,
        },
        {
            "road_class": "unknown",
            "label": "Неизвестный класс",
            "objects": 1,
            "with_geometry": 1,
        },
    ]


def test_viewer_cli_defaults_to_localhost() -> None:
    args = build_parser().parse_args([])

    assert args.store == "./case-store"
    assert args.host == "127.0.0.1"
    assert args.port == 8501
