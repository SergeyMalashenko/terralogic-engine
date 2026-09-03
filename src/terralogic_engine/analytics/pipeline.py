"""Spatial calculations over immutable NSPD, OSM, 2GIS, and RGIS snapshots."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from typing import Literal

from pyproj import Transformer
from pyproj.exceptions import ProjError
from shapely import make_valid
from shapely.errors import GEOSException
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union

from terralogic_engine.analytics.models import (
    AnalysisResult,
    IntersectionItem,
    IntersectionSummary,
    NearestObject,
    SpatialRelation,
)
from terralogic_engine.domain.models import (
    CollectionReceipt,
    GeoFeature,
    utc_now,
)
from terralogic_engine.store.base import CaseStore

ANALYTICS_VERSION = "1.1.0"

SOCIAL_CATEGORIES: tuple[tuple[str, str, str, str], ...] = (
    (
        "mandatory",
        "Обязательные услуги",
        "education",
        "Образование",
    ),
    (
        "mandatory",
        "Обязательные услуги",
        "healthcare",
        "Здравоохранение",
    ),
    (
        "mandatory",
        "Обязательные услуги",
        "emergency_services",
        "Экстренные службы",
    ),
    ("everyday", "Повседневные услуги", "shops", "Магазины"),
    ("everyday", "Повседневные услуги", "pharmacies", "Аптеки"),
    ("everyday", "Повседневные услуги", "banks", "Банки"),
    ("everyday", "Повседневные услуги", "post", "Почта"),
    (
        "everyday",
        "Повседневные услуги",
        "government_services",
        "Государственные услуги",
    ),
    (
        "leisure",
        "Досуг и качество среды",
        "sports",
        "Спортивные объекты",
    ),
    (
        "leisure",
        "Досуг и качество среды",
        "culture",
        "Учреждения культуры",
    ),
    (
        "leisure",
        "Досуг и качество среды",
        "parks",
        "Парки и зоны отдыха",
    ),
)

NATURAL_CLASSES: tuple[tuple[str, str], ...] = (
    ("forest", "Леса"),
    ("lake", "Озёра и водоёмы"),
    ("river", "Реки"),
    ("stream", "Ручьи"),
)
WATER_CLASSES = frozenset({"lake", "river", "stream"})
SOCIAL_CATEGORY_KEYS = frozenset(item[2] for item in SOCIAL_CATEGORIES)
ANALYZED_FEATURE_CLASSES = frozenset(
    {"restriction_zone", *(item[0] for item in NATURAL_CLASSES)}
)


class AnalysisInputError(ValueError):
    """Raised when a collection run cannot support spatial analysis."""


def _label(feature: GeoFeature) -> str:
    for key in (
        "name",
        "full_name",
        "registry_number",
        "address",
        "cadastral_number",
        "title",
    ):
        value = feature.properties.get(key)
        if value not in (None, ""):
            return str(value)
    return feature.source_id or feature.id


def _prepared_geometry(feature: GeoFeature) -> BaseGeometry | None:
    if feature.geometry is None:
        return None
    try:
        geometry = shape(feature.geometry)
    except (KeyError, TypeError, ValueError):
        return None
    if geometry.is_empty:
        return None
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    return None if geometry.is_empty else geometry


def _polygonal(geometry: BaseGeometry) -> BaseGeometry | None:
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        parts = [
            part
            for part in geometry.geoms
            if isinstance(part, (Polygon, MultiPolygon)) and not part.is_empty
        ]
        if parts:
            return unary_union(parts)
    return None


def _round_metric(value: float) -> float:
    return round(max(0.0, float(value)), 3)


def _round_percent(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 6)


def _relation(
    parcel: BaseGeometry,
    candidate: BaseGeometry,
    intersection_measure: float,
) -> SpatialRelation:
    # Boundary-only contact deliberately belongs to the disjoint case.
    if intersection_measure <= 1e-6:
        return "disjoint"
    if candidate.covers(parcel):
        return "parcel_inside_object"
    if parcel.covers(candidate):
        return "object_inside_parcel"
    return "intersects"


def _receipt_for_run(
    store: CaseStore,
    case_id: str,
    run_id: str | None,
) -> CollectionReceipt:
    if run_id is None:
        receipt = store.get_latest_collection_receipt(case_id)
        if receipt is None:
            raise AnalysisInputError(f"Case {case_id!r} has no collection receipt")
        return receipt
    for receipt in store.list_collection_receipts(case_id):
        if receipt.run_id == run_id:
            return receipt
    raise AnalysisInputError(f"Collection run {run_id!r} does not exist")


def _receipt_features(store: CaseStore, receipt: CollectionReceipt) -> list[GeoFeature]:
    result: list[GeoFeature] = []
    for snapshot_id in (
        receipt.nspd_snapshot_id,
        receipt.osm_snapshot_id,
        receipt.dgis_snapshot_id,
        receipt.rgis_snapshot_id,
    ):
        if snapshot_id is not None:
            result.extend(store.load_features(receipt.case_id, snapshot_id=snapshot_id))
    return result


def _select_parcel(features: Sequence[GeoFeature]) -> GeoFeature:
    candidates = [
        feature
        for feature in features
        if feature.source == "nspd"
        and feature.feature_class == "parcel"
        and feature.geometry is not None
    ]
    candidates.sort(key=lambda value: value.source_type != "land_parcel")
    if not candidates:
        raise AnalysisInputError("The selected run has no NSPD parcel geometry")
    return candidates[0]


def _inside_aoi(
    geometry: BaseGeometry,
    aoi_geometry: BaseGeometry,
) -> bool:
    return geometry.intersects(aoi_geometry)


def _intersection_item(
    feature: GeoFeature,
    geometry: BaseGeometry,
    parcel: BaseGeometry,
) -> IntersectionItem:
    polygon = _polygonal(geometry)
    if polygon is not None:
        intersection_area = float(parcel.intersection(polygon).area)
        parcel_percent = 100.0 * intersection_area / float(parcel.area)
        object_percent = (
            100.0 * intersection_area / float(polygon.area) if polygon.area > 0 else 0.0
        )
        return IntersectionItem(
            feature_id=feature.id,
            source=feature.source,
            feature_class=feature.feature_class,
            name=_label(feature),
            geometry_type=geometry.geom_type,
            relation=_relation(parcel, polygon, intersection_area),
            intersection_area_m2=_round_metric(intersection_area),
            parcel_coverage_percent=_round_percent(parcel_percent),
            object_coverage_percent=_round_percent(object_percent),
        )

    intersection_length = float(parcel.intersection(geometry).length)
    return IntersectionItem(
        feature_id=feature.id,
        source=feature.source,
        feature_class=feature.feature_class,
        name=_label(feature),
        geometry_type=geometry.geom_type,
        relation=_relation(parcel, geometry, intersection_length),
        intersection_length_m=_round_metric(intersection_length),
    )


def _area_summary(
    *,
    key: str,
    name: str,
    geometries: Sequence[BaseGeometry],
    items: Sequence[IntersectionItem],
    parcel: BaseGeometry,
) -> IntersectionSummary:
    polygons = [
        value
        for value in (_polygonal(item) for item in geometries)
        if value is not None
    ]
    area = 0.0
    if polygons:
        area = float(parcel.intersection(unary_union(polygons)).area)
    return IntersectionSummary(
        key=key,
        name=name,
        candidate_count=len(geometries),
        intersecting_count=sum(item.relation != "disjoint" for item in items),
        union_intersection_area_m2=_round_metric(area),
        parcel_coverage_percent=_round_percent(100.0 * area / float(parcel.area)),
    )


def _line_summary(
    *,
    key: str,
    name: str,
    geometries: Sequence[BaseGeometry],
    items: Sequence[IntersectionItem],
    parcel: BaseGeometry,
) -> IntersectionSummary:
    length = 0.0
    if geometries:
        length = float(parcel.intersection(unary_union(geometries)).length)
    return IntersectionSummary(
        key=key,
        name=name,
        candidate_count=len(geometries),
        intersecting_count=sum(item.relation != "disjoint" for item in items),
        union_intersection_length_m=_round_metric(length),
    )


def _nearest(
    *,
    group: str,
    group_name: str,
    category: str,
    category_name: str,
    source: Literal["osm", "dgis"],
    candidates: Sequence[tuple[GeoFeature, BaseGeometry]],
    parcel: BaseGeometry,
) -> NearestObject:
    if not candidates:
        return NearestObject(
            group=group,
            group_name=group_name,
            category=category,
            category_name=category_name,
            source=source,
            status="not_found_within_aoi",
            candidate_count=0,
        )
    feature, _geometry = min(
        candidates,
        key=lambda pair: parcel.distance(pair[1]),
    )
    return NearestObject(
        group=group,
        group_name=group_name,
        category=category,
        category_name=category_name,
        source=source,
        status="found",
        candidate_count=len(candidates),
        feature_id=feature.id,
        object_name=_label(feature),
        distance_m=_round_metric(parcel.distance(_geometry)),
    )


class AnalysisPipeline:
    """Calculate and persist analytics for one collection run."""

    def __init__(self, *, store: CaseStore) -> None:
        self.store = store

    def analyze(
        self,
        case_id: str,
        *,
        run_id: str | None = None,
    ) -> AnalysisResult:
        receipt = _receipt_for_run(self.store, case_id, run_id)
        if receipt.aoi_id is None:
            raise AnalysisInputError("The selected run has no area of interest")
        aoi = self.store.get_area_of_interest(case_id, receipt.aoi_id)
        features = _receipt_features(self.store, receipt)
        parcel_feature = _select_parcel(features)
        raw_parcel = _prepared_geometry(parcel_feature)
        if raw_parcel is None:
            raise AnalysisInputError("The NSPD parcel geometry is invalid")

        transformer = Transformer.from_crs("EPSG:4326", aoi.metric_crs, always_xy=True)
        parcel = transform(transformer.transform, raw_parcel)
        aoi_geometry = transform(
            transformer.transform,
            make_valid(shape(aoi.query_geometry)),
        )
        if parcel.area <= 0:
            raise AnalysisInputError("The projected parcel has zero area")

        warnings: list[str] = []
        if receipt.status != "complete":
            warnings.append(
                "Аналитика построена по неполному запуску сбора; "
                "отсутствие объекта не доказывает его отсутствие "
                "на местности."
            )
        warnings.extend(receipt.warnings)
        warnings.extend(receipt.errors)

        projected: dict[str, tuple[GeoFeature, BaseGeometry]] = {}
        skipped_geometry_count = 0
        for feature in features:
            geometry = _prepared_geometry(feature)
            if geometry is None:
                if feature.feature_class in ANALYZED_FEATURE_CLASSES or (
                    feature.source == "dgis"
                    and feature.feature_class in SOCIAL_CATEGORY_KEYS
                ):
                    skipped_geometry_count += 1
                continue
            try:
                projected_geometry = transform(
                    transformer.transform,
                    geometry,
                )
            except (GEOSException, ProjError, TypeError, ValueError):
                skipped_geometry_count += 1
                continue
            if _inside_aoi(projected_geometry, aoi_geometry):
                projected[feature.id] = (feature, projected_geometry)
        if skipped_geometry_count:
            warnings.append(
                f"Пропущено объектов без пригодной геометрии: {skipped_geometry_count}."
            )

        zouit_pairs = [
            pair
            for pair in projected.values()
            if pair[0].source in {"nspd", "rgis"}
            and pair[0].feature_class == "restriction_zone"
        ]
        zouit_items = [
            _intersection_item(feature, geometry, parcel)
            for feature, geometry in zouit_pairs
        ]
        zouit_summary = _area_summary(
            key="zouit",
            name="ЗОУИТ",
            geometries=[geometry for _, geometry in zouit_pairs],
            items=zouit_items,
            parcel=parcel,
        )

        natural_items: list[IntersectionItem] = []
        natural_summaries: list[IntersectionSummary] = []
        natural_pairs_by_class: dict[str, list[tuple[GeoFeature, BaseGeometry]]] = {}
        for feature_class, class_name in NATURAL_CLASSES:
            pairs = [
                pair
                for pair in projected.values()
                if pair[0].source == "osm" and pair[0].feature_class == feature_class
            ]
            natural_pairs_by_class[feature_class] = pairs
            items = [
                _intersection_item(feature, geometry, parcel)
                for feature, geometry in pairs
            ]
            natural_items.extend(items)
            if feature_class == "stream":
                summary = _line_summary(
                    key=feature_class,
                    name=class_name,
                    geometries=[geometry for _, geometry in pairs],
                    items=items,
                    parcel=parcel,
                )
            else:
                summary = _area_summary(
                    key=feature_class,
                    name=class_name,
                    geometries=[geometry for _, geometry in pairs],
                    items=items,
                    parcel=parcel,
                )
            natural_summaries.append(summary)

        area_water_pairs = [
            pair
            for feature_class in ("lake", "river")
            for pair in natural_pairs_by_class[feature_class]
        ]
        area_water_ids = {feature.id for feature, _ in area_water_pairs}
        natural_summaries.append(
            _area_summary(
                key="water_resources",
                name="Водные ресурсы (площадные)",
                geometries=[geometry for _, geometry in area_water_pairs],
                items=[
                    item for item in natural_items if item.feature_id in area_water_ids
                ],
                parcel=parcel,
            )
        )

        social_nearest: list[NearestObject] = []
        for group, group_name, category, category_name in SOCIAL_CATEGORIES:
            pairs = [
                pair
                for pair in projected.values()
                if pair[0].source == "dgis" and pair[0].feature_class == category
            ]
            social_nearest.append(
                _nearest(
                    group=group,
                    group_name=group_name,
                    category=category,
                    category_name=category_name,
                    source="dgis",
                    candidates=pairs,
                    parcel=parcel,
                )
            )

        natural_nearest = [
            _nearest(
                group="natural",
                group_name="Природные объекты",
                category=feature_class,
                category_name=class_name,
                source="osm",
                candidates=natural_pairs_by_class[feature_class],
                parcel=parcel,
            )
            for feature_class, class_name in NATURAL_CLASSES
        ]
        water_pairs = [
            pair
            for feature_class, pairs in natural_pairs_by_class.items()
            if feature_class in WATER_CLASSES
            for pair in pairs
        ]
        natural_nearest.append(
            _nearest(
                group="natural",
                group_name="Природные объекты",
                category="water_resources",
                category_name="Ближайший водный ресурс",
                source="osm",
                candidates=water_pairs,
                parcel=parcel,
            )
        )

        identity = f"{case_id}:{receipt.run_id}:{ANALYTICS_VERSION}"
        result = AnalysisResult(
            id=f"analysis-{sha256(identity.encode()).hexdigest()[:24]}",
            case_id=case_id,
            collection_run_id=receipt.run_id,
            aoi_id=aoi.id,
            analytics_version=ANALYTICS_VERSION,
            calculated_at=utc_now(),
            parcel_feature_id=parcel_feature.id,
            parcel_area_m2=_round_metric(parcel.area),
            metric_crs=aoi.metric_crs,
            search_radius_m=aoi.search_radius_m,
            zouit_summary=zouit_summary,
            zouit_intersections=zouit_items,
            natural_summaries=natural_summaries,
            natural_intersections=natural_items,
            social_nearest=social_nearest,
            natural_nearest=natural_nearest,
            warnings=list(dict.fromkeys(warnings)),
        )
        self.store.save_analysis_result(result)
        return result
