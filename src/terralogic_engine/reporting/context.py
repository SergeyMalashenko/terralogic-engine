"""Build a bounded LLM report context from one immutable case run."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from terralogic_engine.analytics.models import AnalysisResult
from terralogic_engine.domain.models import CollectionReceipt, GeoFeature
from terralogic_engine.reporting.models import (
    InfrastructureObjectContext,
    LegalActReportContext,
    ParcelReportContext,
    ReportContext,
    RoadClassContext,
    SearchAreaReportContext,
    SourceEvidenceContext,
    TransportCategoryContext,
    ZouitReportContext,
)
from terralogic_engine.store.base import CaseStore

TRANSPORT_TAXONOMY: tuple[tuple[str, str, str, str], ...] = (
    (
        "public_transport",
        "Общественный транспорт",
        "public_transport_stops",
        "Остановки",
    ),
    (
        "public_transport",
        "Общественный транспорт",
        "railway_stations_platforms",
        "Железнодорожные станции и платформы",
    ),
    (
        "public_transport",
        "Общественный транспорт",
        "bus_stations",
        "Автовокзалы и автостанции",
    ),
    (
        "transport_hubs",
        "Транспортные узлы",
        "railway_objects",
        "Железнодорожные объекты",
    ),
    ("transport_hubs", "Транспортные узлы", "airports", "Аэропорты"),
    ("transport_hubs", "Транспортные узлы", "ports", "Порты"),
    (
        "transport_hubs",
        "Транспортные узлы",
        "logistics_terminals",
        "Логистические терминалы",
    ),
)
TRANSPORT_CATEGORIES = frozenset(item[2] for item in TRANSPORT_TAXONOMY)
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
class ReportContextError(ValueError):
    """Raised when a selected run has no data required for a report."""


def collection_receipt_for_run(
    store: CaseStore,
    case_id: str,
    collection_run_id: str | None,
) -> CollectionReceipt:
    """Resolve an explicit or latest collection receipt."""

    if collection_run_id is None:
        receipt = store.get_latest_collection_receipt(case_id)
        if receipt is None:
            raise ReportContextError(f"Case {case_id!r} has no collection run")
        return receipt
    for receipt in store.list_collection_receipts(case_id):
        if receipt.run_id == collection_run_id:
            return receipt
    raise ReportContextError(
        f"Collection run {collection_run_id!r} does not exist in case {case_id!r}"
    )


def _features_for_receipt(
    store: CaseStore,
    receipt: CollectionReceipt,
) -> list[GeoFeature]:
    result: list[GeoFeature] = []
    for snapshot_id in (
        receipt.nspd_snapshot_id,
        receipt.osm_snapshot_id,
        receipt.dgis_snapshot_id,
    ):
        if snapshot_id is not None:
            result.extend(
                store.load_features(
                    receipt.case_id,
                    snapshot_id=snapshot_id,
                )
            )
    return result


def _feature_name(feature: GeoFeature) -> str:
    for key in (
        "name",
        "full_name",
        "address",
        "registry_number",
        "cadastral_number",
        "title",
    ):
        value = feature.properties.get(key)
        if value not in (None, ""):
            return str(value)
    return feature.source_id or feature.id


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parcel_context(
    features: Iterable[GeoFeature],
    analysis: AnalysisResult,
) -> ParcelReportContext:
    parcels = [
        feature
        for feature in features
        if feature.source == "nspd"
        and feature.feature_class == "parcel"
        and feature.geometry is not None
    ]
    parcels.sort(key=lambda value: value.source_type != "land_parcel")
    if not parcels:
        raise ReportContextError("The selected run has no NSPD parcel feature")
    parcel = parcels[0]
    properties = parcel.properties
    geometry_type = str(parcel.geometry.get("type", "unknown"))
    return ParcelReportContext(
        cadastral_number=str(properties.get("cadastral_number") or ""),
        address=(
            str(properties["address"])
            if properties.get("address") not in (None, "")
            else None
        ),
        status=(
            str(properties["status"])
            if properties.get("status") not in (None, "")
            else None
        ),
        declared_area_m2=_optional_float(properties.get("area_m2")),
        calculated_area_m2=analysis.parcel_area_m2,
        land_category=(
            str(properties["land_category"])
            if properties.get("land_category") not in (None, "")
            else None
        ),
        permitted_use=(
            str(properties["permitted_use"])
            if properties.get("permitted_use") not in (None, "")
            else None
        ),
        cadastral_value_rub=_optional_float(
            properties.get("cadastral_value_rub")
        ),
        geometry_type=geometry_type,
    )


def _zouit_context(
    features: Iterable[GeoFeature],
    analysis: AnalysisResult,
) -> list[ZouitReportContext]:
    by_id = {feature.id: feature for feature in features}
    result: list[ZouitReportContext] = []
    for item in analysis.zouit_intersections:
        feature = by_id.get(item.feature_id)
        properties = feature.properties if feature is not None else {}
        raw_legal_act = properties.get("legal_act")
        legal_act = None
        if isinstance(raw_legal_act, dict):
            legal_act = LegalActReportContext.model_validate(raw_legal_act)
        result.append(
            ZouitReportContext(
                feature_id=item.feature_id,
                name=item.name,
                registry_number=(
                    str(properties["registry_number"])
                    if properties.get("registry_number") not in (None, "")
                    else None
                ),
                zone_type=(
                    str(properties["zone_type"])
                    if properties.get("zone_type") not in (None, "")
                    else None
                ),
                registration_date=(
                    str(properties["registration_date"])
                    if properties.get("registration_date") not in (None, "")
                    else None
                ),
                restrictions=(
                    str(properties["restrictions"])
                    if properties.get("restrictions") not in (None, "")
                    else None
                ),
                legal_act=legal_act,
                relation=item.relation,
                intersection_area_m2=item.intersection_area_m2 or 0.0,
                parcel_coverage_percent=item.parcel_coverage_percent or 0.0,
                zone_coverage_percent=item.object_coverage_percent or 0.0,
            )
        )
    return result


def _transport_context(
    features: Iterable[GeoFeature],
) -> list[TransportCategoryContext]:
    grouped: defaultdict[str, list[GeoFeature]] = defaultdict(list)
    for feature in features:
        if (
            feature.source != "dgis"
            or feature.feature_class not in TRANSPORT_CATEGORIES
        ):
            continue
        grouped[feature.feature_class].append(feature)

    result: list[TransportCategoryContext] = []
    for group, group_name, category, category_name in TRANSPORT_TAXONOMY:
        values = grouped[category]
        ordered = sorted(
            values,
            key=lambda feature: (
                _optional_float(
                    feature.properties.get("distance_to_search_point_m")
                )
                is None,
                _optional_float(
                    feature.properties.get("distance_to_search_point_m")
                )
                or 0.0,
                feature.id,
            ),
        )
        result.append(
            TransportCategoryContext(
                group=group,
                group_name=group_name,
                category=category,
                category_name=category_name,
                object_count=len(values),
                examples=[
                    InfrastructureObjectContext(
                        feature_id=feature.id,
                        name=_feature_name(feature),
                        distance_to_search_point_m=_optional_float(
                            feature.properties.get(
                                "distance_to_search_point_m"
                            )
                        ),
                    )
                    for feature in ordered[:3]
                ],
            )
        )
    return result


def _road_context(features: Iterable[GeoFeature]) -> list[RoadClassContext]:
    grouped: defaultdict[str, list[GeoFeature]] = defaultdict(list)
    for feature in features:
        if feature.source != "osm" or feature.feature_class != "road":
            continue
        tags = feature.properties.get("tags")
        value = tags.get("highway") if isinstance(tags, dict) else None
        road_class = str(value).strip().lower() if value else "unknown"
        grouped[road_class].append(feature)

    return [
        RoadClassContext(
            road_class=road_class,
            road_class_name=ROAD_CLASS_LABELS.get(road_class, "Неизвестный класс"),
            object_count=len(values),
            named_examples=list(
                dict.fromkeys(
                    _feature_name(feature)
                    for feature in values
                    if feature.properties.get("name") not in (None, "")
                )
            )[:5],
        )
        for road_class, values in sorted(grouped.items())
    ]


def build_report_context(
    store: CaseStore,
    case_id: str,
    *,
    collection_run_id: str | None = None,
) -> ReportContext:
    """Build a compact factual prompt payload without source GeoJSON."""

    receipt = collection_receipt_for_run(store, case_id, collection_run_id)
    analysis = store.get_analysis_result(case_id, receipt.run_id)
    if analysis is None:
        raise ReportContextError(
            "The selected run has no analytics result; run analysis first"
        )
    if receipt.aoi_id is None:
        raise ReportContextError("The selected run has no area of interest")
    aoi = store.get_area_of_interest(case_id, receipt.aoi_id)
    features = _features_for_receipt(store, receipt)
    selected_snapshot_ids = {
        value
        for value in (
            receipt.nspd_snapshot_id,
            receipt.osm_snapshot_id,
            receipt.dgis_snapshot_id,
        )
        if value is not None
    }
    sources = [
        SourceEvidenceContext(
            source=snapshot.source,
            snapshot_id=snapshot.id,
            retrieved_at=snapshot.retrieved_at,
            adapter_version=snapshot.adapter_version,
            content_sha256=snapshot.content_sha256,
        )
        for snapshot in store.list_snapshots(case_id)
        if snapshot.id in selected_snapshot_ids
    ]
    warnings = list(
        dict.fromkeys([*receipt.warnings, *receipt.errors, *analysis.warnings])
    )
    return ReportContext(
        case_id=case_id,
        collection_run_id=receipt.run_id,
        analysis_id=analysis.id,
        analytics_version=analysis.analytics_version,
        collection_status=receipt.status,
        collected_at=receipt.completed_at,
        analyzed_at=analysis.calculated_at,
        parcel=_parcel_context(features, analysis),
        search_area=SearchAreaReportContext(
            parcel_minimum_radius_m=aoi.parcel_minimum_radius_m,
            margin_m=aoi.margin_m,
            search_radius_m=aoi.search_radius_m,
        ),
        zouit_summary=analysis.zouit_summary,
        zouit=_zouit_context(features, analysis),
        natural_intersections=analysis.natural_summaries,
        natural_nearest=analysis.natural_nearest,
        social_nearest=analysis.social_nearest,
        transport_inventory=_transport_context(features),
        road_inventory=_road_context(features),
        sources=sources,
        warnings=warnings,
    )
