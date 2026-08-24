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
        "address",
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
    for snapshot_id in (receipt.nspd_snapshot_id, receipt.osm_snapshot_id):
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

    return [
        {
            "label": feature_label(feature),
            "source": feature.source,
            "class": feature.feature_class,
            "source_type": feature.source_type,
            "source_id": feature.source_id,
            "has_geometry": feature.geometry is not None,
            "feature_id": feature.id,
        }
        for feature in features
    ]
