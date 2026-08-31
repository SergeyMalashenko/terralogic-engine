from __future__ import annotations

from typing import Any

from shapely.geometry import mapping, shape

from terralogic_acquisition.analytics.models import (
    AnalysisResult,
    IntersectionItem,
    IntersectionSummary,
    NearestObject,
)
from terralogic_acquisition.domain.models import GeoFeature, SourceName, utc_now
from terralogic_acquisition.viewer.cli import build_parser
from terralogic_acquisition.viewer.data import (
    build_feature_collection,
    dgis_map_label,
    feature_label,
    feature_table_rows,
    group_features,
    group_road_features,
    interior_ring_count,
    natural_contour_summary,
    natural_intersection_detail_rows,
    natural_intersection_rows,
    natural_nearest_rows,
    osm_road_class,
    osm_road_map_label,
    osm_road_reference,
    osm_waterbody_type,
    road_class_summary,
    source_summary_rows,
    social_nearest_rows,
    zouit_analysis_rows,
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
    assert rows[2]["map_label"] == "Школа"
    assert summary[0] == {
        "source": "dgis",
        "class": "education",
        "objects": 1,
        "with_geometry": 0,
    }


def test_viewer_builds_bounded_dgis_text_labels() -> None:
    named = _feature(
        "dgis-named",
        source="dgis",
        feature_class="education",
        geometry={"type": "Point", "coordinates": [43.6, 56.06]},
        properties={
            "name": "Средняя общеобразовательная школа № 1",
            "category_name": "Образование",
        },
    )
    address_only = _feature(
        "dgis-address",
        source="dgis",
        feature_class="shops",
        properties={"address_name": "улица Ленина, 10"},
    )

    collection = build_feature_collection([named])

    assert dgis_map_label(named) == "Средняя общеобразовательная школа № 1"
    assert dgis_map_label(named, max_length=12) == "Средняя общ…"
    assert dgis_map_label(address_only) is None
    assert collection["features"][0]["properties"]["map_label"] == (
        "Средняя общеобразовательная школа № 1"
    )


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
        properties={
            "name": "Дракинский карьер",
            "tags": {"natural": "water"},
        },
    )
    river_without_geometry = _feature(
        "river-1",
        source="osm",
        feature_class="river",
    )

    collection = build_feature_collection([forest, lake])
    summary = natural_contour_summary(
        [forest, lake, river_without_geometry]
    )

    assert interior_ring_count(FOREST_GEOMETRY) == 1
    assert interior_ring_count(dict(mapping(shape(FOREST_GEOMETRY)))) == 1
    assert collection["features"][0]["properties"]["interior_rings"] == 1
    assert osm_waterbody_type(lake) == "unspecified"
    assert collection["features"][1]["properties"]["waterbody_type"] == (
        "unspecified"
    )
    assert collection["features"][1]["properties"]["waterbody_type_label"] == (
        "Не уточнён (natural=water)"
    )
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
            "label": "Водоёмы",
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
        properties={
            "name": "Волга",
            "tags": {"highway": "motorway", "ref": "М-7"},
        },
    )
    residential = _feature(
        "road-residential",
        source="osm",
        feature_class="road",
        geometry=ROAD_GEOMETRY,
        properties={
            "tags": {
                "highway": "residential",
                "official_ref": "52К-123",
            }
        },
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
    assert osm_road_reference(motorway) == "М-7"
    assert osm_road_reference(residential) == "52К-123"
    assert osm_road_map_label(motorway) == "Волга · М-7"
    assert osm_road_map_label(residential) is None
    assert list(grouped) == ["motorway", "residential", "unknown"]
    assert geojson["features"][0]["properties"]["road_class"] == "motorway"
    assert geojson["features"][0]["properties"]["road_class_label"] == (
        "Автомагистраль"
    )
    assert geojson["features"][0]["properties"]["road_reference"] == "М-7"
    assert geojson["features"][0]["properties"]["road_map_label"] == (
        "Волга · М-7"
    )
    assert summary == [
        {
            "road_class": "motorway",
            "label": "Автомагистраль",
            "objects": 1,
            "with_geometry": 1,
            "with_reference": 1,
        },
        {
            "road_class": "residential",
            "label": "Жилая улица",
            "objects": 1,
            "with_geometry": 1,
            "with_reference": 1,
        },
        {
            "road_class": "unknown",
            "label": "Неизвестный класс",
            "objects": 1,
            "with_geometry": 1,
            "with_reference": 0,
        },
    ]


def test_permanent_road_labels_are_limited_to_major_classes() -> None:
    for road_class in (
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "tertiary",
    ):
        feature = _feature(
            f"road-{road_class}",
            source="osm",
            feature_class="road",
            properties={
                "name": "Тестовая дорога",
                "tags": {"highway": road_class},
            },
        )
        assert osm_road_map_label(feature) == "Тестовая дорога"

    for road_class in ("unclassified", "residential", "service", "track"):
        feature = _feature(
            f"road-{road_class}",
            source="osm",
            feature_class="road",
            properties={
                "name": "Местная дорога",
                "tags": {"highway": road_class, "ref": "52Н-1"},
            },
        )
        assert osm_road_map_label(feature) is None


def test_viewer_cli_defaults_to_localhost() -> None:
    args = build_parser().parse_args([])

    assert args.store == "./case-store"
    assert args.host == "127.0.0.1"
    assert args.port == 8501


def test_viewer_builds_four_analytics_tables() -> None:
    result = AnalysisResult(
        id="analysis-1",
        case_id="case-viewer",
        collection_run_id="run-1",
        aoi_id="aoi-1",
        analytics_version="1.0.0",
        calculated_at=utc_now(),
        parcel_feature_id="parcel-1",
        parcel_area_m2=1000,
        metric_crs="EPSG:32638",
        search_radius_m=1500,
        zouit_summary=IntersectionSummary(
            key="zouit",
            name="ЗОУИТ",
            candidate_count=1,
            intersecting_count=1,
            union_intersection_area_m2=100,
            parcel_coverage_percent=10,
        ),
        zouit_intersections=[
            IntersectionItem(
                feature_id="zone-1",
                source="nspd",
                feature_class="restriction_zone",
                name="Охранная зона",
                geometry_type="Polygon",
                relation="intersects",
                intersection_area_m2=100,
                parcel_coverage_percent=10,
                object_coverage_percent=25,
            )
        ],
        natural_summaries=[
            IntersectionSummary(
                key="forest",
                name="Леса",
                candidate_count=2,
                intersecting_count=1,
                union_intersection_area_m2=50,
                parcel_coverage_percent=5,
            )
        ],
        natural_intersections=[
            IntersectionItem(
                feature_id="forest-1",
                source="osm",
                feature_class="forest",
                name="Тестовый лес",
                geometry_type="Polygon",
                relation="intersects",
                intersection_area_m2=50,
                parcel_coverage_percent=5,
                object_coverage_percent=20,
            )
        ],
        social_nearest=[
            NearestObject(
                group="mandatory",
                group_name="Обязательные услуги",
                category="education",
                category_name="Образование",
                source="dgis",
                status="found",
                candidate_count=3,
                feature_id="school-1",
                object_name="Школа № 1",
                distance_m=420,
            )
        ],
        natural_nearest=[
            NearestObject(
                group="natural",
                group_name="Природные объекты",
                category="forest",
                category_name="Леса",
                source="osm",
                status="not_found_within_aoi",
                candidate_count=0,
            )
        ],
    )

    assert (
        zouit_analysis_rows(result)[0]["Отношение"]
        == "Пересекается"
    )
    assert natural_intersection_rows(result)[0][
        "Площадь пересечения, м²"
    ] == 50
    assert natural_intersection_detail_rows(result)[0]["Объект"] == (
        "Тестовый лес"
    )
    assert (
        social_nearest_rows(result)[0]["Расстояние от участка, м"]
        == 420
    )
    assert natural_nearest_rows(result)[0]["Статус"] == (
        "Не найден в области поиска"
    )
