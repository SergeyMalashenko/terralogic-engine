"""Normalize source envelopes into canonical GeoFeature records."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from terralogic_acquisition.domain.models import GeoFeature

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
    source_id = str(
        parcel.get("nspd_id") or parcel.get("cadastral_number") or "parcel"
    )
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
                        id=_feature_id(
                            snapshot_id, f"nspd:{layer_name}:{source_id}"
                        ),
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


def _osm_feature_class(block: str, tags: dict[str, Any]) -> str:
    if block == "buildings":
        return "building"
    if block == "transport":
        if "railway" in tags:
            return "railway"
        if "waterway" in tags:
            return "waterway"
        return "road"
    if block == "infrastructure":
        if "pipeline" in tags or tags.get("man_made") == "pipeline":
            return "pipeline"
        if tags.get("power") == "line":
            return "power_line"
        if tags.get("power") in {"substation", "plant"}:
            return "power_substation"
        return "infrastructure"
    if block == "landuse":
        if tags.get("natural") == "water":
            return "waterbody"
        if tags.get("landuse") == "forest" or tags.get("natural") == "wood":
            return "forest"
        return "landuse"
    return "social_object" if block == "poi" else block


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
            tags = value.get("tags") if isinstance(value.get("tags"), dict) else {}
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


def count_features(features: list[GeoFeature]) -> dict[str, int]:
    result: dict[str, int] = {}
    for feature in features:
        key = f"{feature.source}.{feature.feature_class}"
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))
