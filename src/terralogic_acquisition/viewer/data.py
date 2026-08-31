"""Pure data transformations shared by the viewer and its tests."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from terralogic_acquisition.analytics.models import AnalysisResult
from terralogic_acquisition.domain.models import (
    CollectionReceipt,
    GeoFeature,
)
from terralogic_acquisition.store.base import CaseStore

NATURAL_CONTOUR_CLASSES = ("forest", "lake", "river")
NATURAL_CONTOUR_LABELS = {
    "forest": "Леса",
    "lake": "Водоёмы",
    "river": "Реки",
}
NATURAL_RESOURCE_LABELS = {
    **NATURAL_CONTOUR_LABELS,
    "stream": "Ручьи",
}
WATERBODY_TYPE_LABELS = {
    "unspecified": "Не уточнён (natural=water)",
    "basin": "Бассейн",
    "lagoon": "Лагуна",
    "lake": "Озеро",
    "oxbow": "Старица",
    "pond": "Пруд",
    "reservoir": "Водохранилище",
}
ROAD_CLASS_ORDER = (
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "track",
    "unknown",
)
ROAD_CLASS_LABELS = {
    "motorway": "Автомагистраль",
    "trunk": "Магистральная дорога",
    "primary": "Основная дорога",
    "secondary": "Второстепенная дорога",
    "tertiary": "Дорога третьего класса",
    "unclassified": "Неклассифицированная дорога",
    "residential": "Жилая улица",
    "living_street": "Жилая зона",
    "service": "Подъездная/служебная дорога",
    "track": "Грунтовая или лесная дорога",
    "unknown": "Неизвестный класс",
}
ROAD_REFERENCE_TAGS = ("ref", "official_ref", "nat_ref", "int_ref")
ROAD_LABEL_CLASSES = frozenset(
    {"motorway", "trunk", "primary", "secondary", "tertiary"}
)
RELATION_LABELS = {
    "disjoint": "Не пересекается",
    "intersects": "Пересекается",
    "object_inside_parcel": "Объект внутри участка",
    "parcel_inside_object": "Участок внутри объекта",
}


def interior_ring_count(geometry: dict[str, Any] | None) -> int:
    """Count holes in Polygon and MultiPolygon GeoJSON without altering it."""

    if not isinstance(geometry, dict):
        return 0
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, Sequence):
        return 0
    if geometry.get("type") == "Polygon":
        return max(0, len(coordinates) - 1)
    if geometry.get("type") == "MultiPolygon":
        return sum(
            max(0, len(polygon) - 1)
            for polygon in coordinates
            if isinstance(polygon, Sequence)
        )
    return 0


def osm_road_class(feature: GeoFeature) -> str | None:
    """Return the exact OSM highway class for one normalized road."""

    if feature.source != "osm" or feature.feature_class != "road":
        return None
    tags = feature.properties.get("tags")
    value = tags.get("highway") if isinstance(tags, dict) else None
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    normalized = value.strip().lower()
    return normalized if normalized in ROAD_CLASS_LABELS else "unknown"


def osm_road_reference(feature: GeoFeature) -> str | None:
    """Return a displayable road number from standard OSM reference tags."""

    if feature.source != "osm" or feature.feature_class != "road":
        return None
    tags = feature.properties.get("tags")
    if not isinstance(tags, dict):
        return None
    for key in ROAD_REFERENCE_TAGS:
        value = tags.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def osm_waterbody_type(feature: GeoFeature) -> str | None:
    """Return the OSM water subtype, retaining valid untyped water areas."""

    if feature.source != "osm" or feature.feature_class != "lake":
        return None
    tags = feature.properties.get("tags")
    if not isinstance(tags, dict) or tags.get("natural") != "water":
        return None
    value = tags.get("water")
    if not isinstance(value, str) or not value.strip():
        return "unspecified"
    return value.strip().lower()


def osm_road_map_label(
    feature: GeoFeature,
    *,
    max_length: int = 80,
) -> str | None:
    """Build a label only for major OSM road classes."""

    if osm_road_class(feature) not in ROAD_LABEL_CLASSES or max_length < 2:
        return None
    value = feature.properties.get("name")
    name = " ".join(value.split()) if isinstance(value, str) else ""
    reference = osm_road_reference(feature)
    if name and reference and reference.casefold() not in name.casefold():
        label = f"{name} · {reference}"
    else:
        label = name or reference
    if not label:
        return None
    if len(label) <= max_length:
        return label
    return f"{label[: max_length - 1].rstrip()}…"


def dgis_map_label(feature: GeoFeature, *, max_length: int = 60) -> str | None:
    """Build a bounded permanent-map label for one normalized 2GIS object."""

    if feature.source != "dgis" or max_length < 2:
        return None
    value = feature.properties.get("name")
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1].rstrip()}…"


def feature_label(feature: GeoFeature) -> str:
    """Build a compact human-readable label without assuming one source schema."""

    for key in (
        "name",
        "full_name",
        "address",
        "address_name",
        "registry_number",
        "cadastral_number",
        "title",
    ):
        value = feature.properties.get(key)
        if value not in (None, ""):
            return str(value)
    source_identity = feature.source_id or feature.id
    return f"{feature.feature_class} · {source_identity}"


def feature_to_geojson(feature: GeoFeature) -> dict[str, Any] | None:
    """Convert one stored feature to a GeoJSON Feature for map rendering."""

    if feature.geometry is None:
        return None
    distance = feature.properties.get("distance_to_parcel_m")
    if distance is None:
        distance = feature.properties.get("distance_to_search_point_m")
    relation = feature.properties.get("relation")
    relation_kind = relation.get("kind") if isinstance(relation, dict) else None
    road_class = osm_road_class(feature)
    road_reference = osm_road_reference(feature)
    road_map_label = osm_road_map_label(feature)
    waterbody_type = osm_waterbody_type(feature)
    map_label = dgis_map_label(feature)
    return {
        "type": "Feature",
        "id": feature.id,
        "geometry": feature.geometry,
        "properties": {
            "feature_id": feature.id,
            "label": feature_label(feature),
            "source": feature.source,
            "feature_class": feature.feature_class,
            "source_type": feature.source_type,
            "source_id": feature.source_id,
            "distance_m": distance,
            "relation": relation_kind,
            "category": feature.properties.get("category_name"),
            "geometry_type": feature.geometry.get("type"),
            "interior_rings": interior_ring_count(feature.geometry),
            "road_class": road_class,
            "road_class_label": (
                ROAD_CLASS_LABELS.get(road_class) if road_class else None
            ),
            "road_reference": road_reference,
            "road_map_label": road_map_label,
            "waterbody_type": waterbody_type,
            "waterbody_type_label": (
                WATERBODY_TYPE_LABELS.get(waterbody_type, waterbody_type)
                if waterbody_type
                else None
            ),
            "map_label": map_label,
        },
    }


def build_feature_collection(features: Iterable[GeoFeature]) -> dict[str, Any]:
    """Create a valid GeoJSON FeatureCollection, omitting attribute-only rows."""

    geojson_features = []
    for feature in features:
        normalized = feature_to_geojson(feature)
        if normalized is not None:
            geojson_features.append(normalized)
    return {"type": "FeatureCollection", "features": geojson_features}


def group_features(
    features: Iterable[GeoFeature],
) -> dict[tuple[str, str], list[GeoFeature]]:
    """Group features into stable map layers by source and canonical class."""

    groups: defaultdict[tuple[str, str], list[GeoFeature]] = defaultdict(list)
    for feature in features:
        groups[(feature.source, feature.feature_class)].append(feature)
    return {
        key: sorted(values, key=lambda item: item.id)
        for key, values in sorted(groups.items())
    }


def group_road_features(
    features: Iterable[GeoFeature],
) -> dict[str, list[GeoFeature]]:
    """Group OSM roads by their exact highway value in a stable order."""

    groups: defaultdict[str, list[GeoFeature]] = defaultdict(list)
    for feature in features:
        road_class = osm_road_class(feature)
        if road_class is not None:
            groups[road_class].append(feature)
    return {
        road_class: sorted(groups[road_class], key=lambda item: item.id)
        for road_class in ROAD_CLASS_ORDER
        if road_class in groups
    }


def load_receipt_features(
    store: CaseStore,
    receipt: CollectionReceipt,
) -> list[GeoFeature]:
    """Load only features referenced by one immutable collection receipt."""

    features: list[GeoFeature] = []
    for snapshot_id in (
        receipt.nspd_snapshot_id,
        receipt.osm_snapshot_id,
        receipt.dgis_snapshot_id,
    ):
        if snapshot_id is not None:
            features.extend(
                store.load_features(
                    receipt.case_id,
                    snapshot_id=snapshot_id,
                )
            )
    return features


def feature_table_rows(features: Iterable[GeoFeature]) -> list[dict[str, Any]]:
    """Build compact rows suitable for Streamlit's dataframe component."""

    result: list[dict[str, Any]] = []
    for feature in features:
        distance = feature.properties.get("distance_to_parcel_m")
        distance_basis = "до участка"
        if distance is None:
            distance = feature.properties.get("distance_to_search_point_m")
            distance_basis = "до центра поиска"
        relation = feature.properties.get("relation")
        road_class = osm_road_class(feature)
        road_reference = osm_road_reference(feature)
        road_map_label = osm_road_map_label(feature)
        waterbody_type = osm_waterbody_type(feature)
        map_label = dgis_map_label(feature)
        result.append(
            {
                "label": feature_label(feature),
                "source": feature.source,
                "class": feature.feature_class,
                "category": feature.properties.get("category_name"),
                "distance_m": distance,
                "distance_basis": distance_basis if distance is not None else None,
                "relation": (
                    relation.get("kind") if isinstance(relation, dict) else None
                ),
                "geometry_type": (
                    feature.geometry.get("type")
                    if isinstance(feature.geometry, dict)
                    else None
                ),
                "interior_rings": interior_ring_count(feature.geometry),
                "road_class": road_class,
                "road_class_label": (
                    ROAD_CLASS_LABELS.get(road_class) if road_class else None
                ),
                "road_reference": road_reference,
                "road_map_label": road_map_label,
                "waterbody_type": waterbody_type,
                "waterbody_type_label": (
                    WATERBODY_TYPE_LABELS.get(waterbody_type, waterbody_type)
                    if waterbody_type
                    else None
                ),
                "map_label": map_label,
                "source_type": feature.source_type,
                "source_id": feature.source_id,
                "has_geometry": feature.geometry is not None,
                "feature_id": feature.id,
            }
        )
    return result


def source_summary_rows(features: Iterable[GeoFeature]) -> list[dict[str, Any]]:
    """Count stored objects per source and semantic feature class."""

    counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    geometry_counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    for feature in features:
        key = (feature.source, feature.feature_class)
        counts[key] += 1
        if feature.geometry is not None:
            geometry_counts[key] += 1
    return [
        {
            "source": source,
            "class": feature_class,
            "objects": count,
            "with_geometry": geometry_counts[(source, feature_class)],
        }
        for (source, feature_class), count in sorted(counts.items())
    ]


def natural_contour_summary(
    features: Iterable[GeoFeature],
) -> list[dict[str, Any]]:
    """Summarize renderable OSM forest, waterbody, and river contours."""

    result = {
        feature_class: {
            "class": feature_class,
            "label": NATURAL_CONTOUR_LABELS[feature_class],
            "objects": 0,
            "contours": 0,
            "interior_rings": 0,
        }
        for feature_class in NATURAL_CONTOUR_CLASSES
    }
    for feature in features:
        if feature.source != "osm" or feature.feature_class not in result:
            continue
        row = result[feature.feature_class]
        row["objects"] += 1
        geometry_type = (
            feature.geometry.get("type")
            if isinstance(feature.geometry, dict)
            else None
        )
        if geometry_type in {"Polygon", "MultiPolygon"}:
            row["contours"] += 1
            row["interior_rings"] += interior_ring_count(feature.geometry)
    return [result[feature_class] for feature_class in NATURAL_CONTOUR_CLASSES]


def road_class_summary(features: Iterable[GeoFeature]) -> list[dict[str, Any]]:
    """Count roads by OSM highway class and geometry availability."""

    grouped = group_road_features(features)
    return [
        {
            "road_class": road_class,
            "label": ROAD_CLASS_LABELS[road_class],
            "objects": len(values),
            "with_geometry": sum(
                feature.geometry is not None for feature in values
            ),
            "with_reference": sum(
                osm_road_reference(feature) is not None for feature in values
            ),
        }
        for road_class, values in grouped.items()
    ]


def zouit_analysis_rows(result: AnalysisResult) -> list[dict[str, Any]]:
    """Build the per-zone relative-intersection table."""

    return [
        {
            "Зона": item.name,
            "Отношение": RELATION_LABELS[item.relation],
            "Площадь пересечения, м²": item.intersection_area_m2,
            "Доля площади участка, %": item.parcel_coverage_percent,
            "Доля площади зоны, %": item.object_coverage_percent,
            "ID объекта": item.feature_id,
        }
        for item in result.zouit_intersections
    ]


def natural_intersection_rows(result: AnalysisResult) -> list[dict[str, Any]]:
    """Build non-double-counted intersection rows by natural-resource class."""

    return [
        {
            "Ресурс": item.name,
            "Объектов в области поиска": item.candidate_count,
            "Пересекают участок": item.intersecting_count,
            "Площадь пересечения, м²": (
                item.union_intersection_area_m2
            ),
            "Доля площади участка, %": item.parcel_coverage_percent,
            "Длина внутри участка, м": (
                item.union_intersection_length_m
            ),
        }
        for item in result.natural_summaries
    ]


def natural_intersection_detail_rows(
    result: AnalysisResult,
) -> list[dict[str, Any]]:
    """Build per-object natural-resource intersection rows."""

    return [
        {
            "Ресурс": NATURAL_RESOURCE_LABELS.get(
                item.feature_class,
                item.feature_class,
            ),
            "Объект": item.name,
            "Отношение": RELATION_LABELS[item.relation],
            "Площадь пересечения, м²": item.intersection_area_m2,
            "Доля площади участка, %": item.parcel_coverage_percent,
            "Доля площади объекта, %": item.object_coverage_percent,
            "Длина внутри участка, м": item.intersection_length_m,
            "ID объекта": item.feature_id,
        }
        for item in result.natural_intersections
    ]


def social_nearest_rows(result: AnalysisResult) -> list[dict[str, Any]]:
    """Build nearest-social-object rows for all configured 2GIS categories."""

    return [
        {
            "Блок": item.group_name,
            "Категория": item.category_name,
            "Ближайший объект": item.object_name,
            "Расстояние от участка, м": item.distance_m,
            "Кандидатов в области поиска": item.candidate_count,
            "Статус": (
                "Найден"
                if item.status == "found"
                else "Не найден в области поиска"
            ),
        }
        for item in result.social_nearest
    ]


def natural_nearest_rows(result: AnalysisResult) -> list[dict[str, Any]]:
    """Build nearest-forest and nearest-water-resource rows."""

    return [
        {
            "Ресурс": item.category_name,
            "Ближайший объект": item.object_name,
            "Расстояние от участка, м": item.distance_m,
            "Кандидатов в области поиска": item.candidate_count,
            "Статус": (
                "Найден"
                if item.status == "found"
                else "Не найден в области поиска"
            ),
        }
        for item in result.natural_nearest
    ]
