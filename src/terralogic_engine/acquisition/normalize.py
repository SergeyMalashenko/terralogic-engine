"""Normalize source envelopes into canonical GeoFeature records."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from terralogic_engine.domain.models import GeoFeature

NSPD_FEATURE_CLASSES = {
    "zouit": "restriction_zone",
    "territorial_zones": "territorial_zone",
    "settlements": "settlement",
    "land_parcels": "parcel",
    "buildings": "building",
    "structures": "structure",
    "unfinished_construction": "unfinished_construction",
    "protected_natural_territories": "protected_area",
    "forestry": "forest",
    "forest_parks": "forest",
    "shorelines": "waterbody_boundary",
}


def _feature_id(snapshot_id: str, identity: str) -> str:
    digest = sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"feature-{snapshot_id}-{digest}"


def _geometry_from_geojson(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("type") == "Feature":
        geometry = value.get("geometry")
        return geometry if isinstance(geometry, dict) else None
    if isinstance(value.get("type"), str) and "coordinates" in value:
        return value
    return None


def parcel_feature(
    *,
    case_id: str,
    snapshot_id: str,
    parcel: dict[str, Any],
    geometry: dict[str, Any],
) -> GeoFeature:
    source_id = str(parcel.get("nspd_id") or parcel.get("cadastral_number") or "parcel")
    properties = dict(parcel)
    properties.pop("geojson", None)
    return GeoFeature(
        id=_feature_id(snapshot_id, f"nspd:land_parcel:{source_id}"),
        case_id=case_id,
        snapshot_id=snapshot_id,
        source="nspd",
        source_type="land_parcel",
        source_id=source_id,
        feature_class="parcel",
        geometry=geometry,
        properties=properties,
    )


def nspd_layer_features(
    *,
    case_id: str,
    snapshot_id: str,
    envelope: dict[str, Any] | None,
) -> list[GeoFeature]:
    if not envelope or envelope.get("ok") is not True:
        return []
    data = envelope.get("data")
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(blocks, dict):
        return []

    result: list[GeoFeature] = []
    for block_name, block_data in blocks.items():
        if not isinstance(block_data, dict):
            continue
        layers = block_data.get("layers")
        if not isinstance(layers, dict):
            continue
        for layer_name, layer_data in layers.items():
            if not isinstance(layer_data, dict):
                continue
            objects = layer_data.get("zones", layer_data.get("objects", []))
            if not isinstance(objects, list):
                continue
            for index, value in enumerate(objects):
                if not isinstance(value, dict):
                    continue
                source_id_value = (
                    value.get("nspd_id")
                    or value.get("registry_number")
                    or value.get("cadastral_number")
                    or f"{layer_name}:{index}"
                )
                source_id = str(source_id_value)
                properties = dict(value)
                properties["block"] = str(block_name)
                properties["layer"] = str(layer_name)
                geometry = _geometry_from_geojson(properties.pop("geojson", None))
                result.append(
                    GeoFeature(
                        id=_feature_id(snapshot_id, f"nspd:{layer_name}:{source_id}"),
                        case_id=case_id,
                        snapshot_id=snapshot_id,
                        source="nspd",
                        source_type=str(layer_name),
                        source_id=source_id,
                        feature_class=NSPD_FEATURE_CLASSES.get(
                            str(layer_name), str(layer_name)
                        ),
                        geometry=geometry,
                        properties=properties,
                    )
                )
    return result


OSM_FEATURE_CLASSES = {
    "forests": "forest",
    "lakes": "lake",
    "rivers": "river",
    "streams": "stream",
    "roads": "road",
}


def _osm_feature_class(block: str, _tags: dict[str, Any]) -> str:
    return OSM_FEATURE_CLASSES.get(block, block)


def osm_features(
    *,
    case_id: str,
    snapshot_id: str,
    envelope: dict[str, Any] | None,
) -> list[GeoFeature]:
    if not envelope or envelope.get("ok") is not True:
        return []
    data = envelope.get("data")
    if not isinstance(data, dict):
        return []
    raw_blocks = data.get("blocks")
    if not isinstance(raw_blocks, list):
        return []

    by_identity: dict[tuple[str, str], GeoFeature] = {}
    for raw_block in raw_blocks:
        if not isinstance(raw_block, dict):
            continue
        block = str(raw_block.get("block") or "osm")
        raw_features = raw_block.get("features", [])
        if not isinstance(raw_features, list):
            continue
        for value in raw_features:
            if not isinstance(value, dict):
                continue
            element_type = str(value.get("element_type") or "unknown")
            osm_id = str(value.get("osm_id") or "unknown")
            identity = (element_type, osm_id)
            existing = by_identity.get(identity)
            if existing is not None:
                blocks = existing.properties.setdefault("blocks", [])
                if block not in blocks:
                    blocks.append(block)
                continue
            properties = dict(value)
            properties["blocks"] = list(
                dict.fromkeys([*properties.get("blocks", []), block])
            )
            geometry = _geometry_from_geojson(properties.pop("geojson", None))
            raw_tags = value.get("tags")
            tags: dict[str, Any] = raw_tags if isinstance(raw_tags, dict) else {}
            by_identity[identity] = GeoFeature(
                id=_feature_id(snapshot_id, f"osm:{element_type}:{osm_id}"),
                case_id=case_id,
                snapshot_id=snapshot_id,
                source="osm",
                source_type=element_type,
                source_id=osm_id,
                feature_class=_osm_feature_class(block, tags),
                geometry=geometry,
                properties=properties,
            )
    return list(by_identity.values())


def dgis_features(
    *,
    case_id: str,
    snapshot_id: str,
    envelopes: list[dict[str, Any] | None],
) -> list[GeoFeature]:
    """Normalize both focused 2GIS reports into category-specific points."""

    result: list[GeoFeature] = []
    seen: set[tuple[str, str]] = set()
    for envelope in envelopes:
        if not envelope or envelope.get("ok") is not True:
            continue
        data = envelope.get("data")
        groups = data.get("groups") if isinstance(data, dict) else None
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_key = str(group.get("key") or "infrastructure")
            group_name = group.get("name")
            categories = group.get("categories")
            if not isinstance(categories, list):
                continue
            for category in categories:
                if not isinstance(category, dict):
                    continue
                category_key = str(category.get("key") or "object")
                category_name = category.get("name")
                objects = category.get("objects")
                if not isinstance(objects, list):
                    continue
                for index, value in enumerate(objects):
                    if not isinstance(value, dict):
                        continue
                    source_id = str(value.get("id") or f"{category_key}:{index}")
                    identity = (category_key, source_id)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    latitude = value.get("latitude")
                    longitude = value.get("longitude")
                    geometry = None
                    try:
                        if not isinstance(latitude, (str, int, float)) or not isinstance(
                            longitude, (str, int, float)
                        ):
                            raise TypeError("coordinates must be numeric")
                        latitude_value = float(latitude)
                        longitude_value = float(longitude)
                        if (
                            -90 <= latitude_value <= 90
                            and -180 <= longitude_value <= 180
                        ):
                            geometry = {
                                "type": "Point",
                                "coordinates": [longitude_value, latitude_value],
                            }
                    except (TypeError, ValueError):
                        pass
                    properties = dict(value)
                    properties.update(
                        {
                            "group": group_key,
                            "group_name": group_name,
                            "category": category_key,
                            "category_name": category_name,
                        }
                    )
                    result.append(
                        GeoFeature(
                            id=_feature_id(
                                snapshot_id,
                                f"dgis:{category_key}:{source_id}",
                            ),
                            case_id=case_id,
                            snapshot_id=snapshot_id,
                            source="dgis",
                            source_type=str(value.get("type") or "place"),
                            source_id=source_id,
                            feature_class=category_key,
                            geometry=geometry,
                            properties=properties,
                        )
                    )
    return result


def rgis_features(
    *,
    case_id: str,
    snapshot_id: str,
    envelope: dict[str, Any] | None,
) -> list[GeoFeature]:
    """Normalize the focused RGIS layer-analysis envelope."""
    if not envelope or envelope.get("ok") is not True:
        return []
    data = envelope.get("data")
    if not isinstance(data, dict) or data.get("applicable") is not True:
        return []
    blocks = data.get("blocks")
    if not isinstance(blocks, dict):
        return []

    result: list[GeoFeature] = []
    class_by_layer = {
        "parcel_zouit": "restriction_zone",
        "parcel_usage": "territorial_zone",
        "territorial_zones": "territorial_zone",
        "state_forest": "forest",
        "forest_quarters": "forest_quarter",
    }
    for block_name, block in blocks.items():
        if not isinstance(block, dict):
            continue
        layers = block.get("layers")
        if not isinstance(layers, dict):
            continue
        for layer_name, layer in layers.items():
            if not isinstance(layer, dict):
                continue
            values = layer.get("zones", layer.get("objects", []))
            if not isinstance(values, list):
                continue
            for index, value in enumerate(values):
                if not isinstance(value, dict):
                    continue
                nested_properties = value.get("properties")
                properties = (
                    dict(nested_properties)
                    if isinstance(nested_properties, dict)
                    else dict(value)
                )
                properties.pop("properties", None)
                properties.pop("geometry", None)
                properties["block"] = str(block_name)
                properties["layer"] = str(layer_name)
                properties["layer_title"] = layer.get("title")
                if value.get("relation") is not None:
                    properties["source_relation"] = value["relation"]
                geometry = _geometry_from_geojson(value.get("geometry"))
                source_id_value = (
                    value.get("id")
                    or properties.get("zone_code")
                    or properties.get("code")
                    or properties.get("feature_id")
                    or f"{layer_name}:{index}"
                )
                source_id = str(source_id_value)
                feature_class = class_by_layer.get(str(layer_name))
                if feature_class is None:
                    feature_class = (
                        "restriction_zone"
                        if block_name == "restrictions_and_special"
                        else str(layer_name)
                    )
                result.append(
                    GeoFeature(
                        id=_feature_id(
                            snapshot_id,
                            f"rgis:{block_name}:{layer_name}:{source_id}:{index}",
                        ),
                        case_id=case_id,
                        snapshot_id=snapshot_id,
                        source="rgis",
                        source_type=str(layer_name),
                        source_id=source_id,
                        feature_class=feature_class,
                        geometry=geometry,
                        properties=properties,
                    )
                )
    return result


def count_features(features: list[GeoFeature]) -> dict[str, int]:
    result: dict[str, int] = {}
    for feature in features:
        key = f"{feature.source}.{feature.feature_class}"
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))
