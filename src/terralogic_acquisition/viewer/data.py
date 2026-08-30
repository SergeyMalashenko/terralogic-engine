"""Pure data transformations shared by the viewer and its tests."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from terralogic_acquisition.domain.models import (
    CollectionReceipt,
    GeoFeature,
)
from terralogic_acquisition.store.base import CaseStore


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
