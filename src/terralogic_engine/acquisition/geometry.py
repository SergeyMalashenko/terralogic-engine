"""Validation and deterministic AOI construction for parcel contours."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import uuid4

from pyproj import CRS, Transformer
from shapely import (
    make_valid,
    minimum_bounding_circle,
    minimum_bounding_radius,
    normalize,
)
from shapely.geometry import (
    GeometryCollection,
    MultiPolygon,
    Polygon,
    mapping,
    shape,
)
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union

from terralogic_engine.domain.models import AreaOfInterest


class ParcelGeometryError(ValueError):
    """Raised when NSPD did not provide a usable WGS84 parcel polygon."""


def _polygonal(geometry: BaseGeometry) -> BaseGeometry:
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        polygons = [
            part
            for part in geometry.geoms
            if isinstance(part, (Polygon, MultiPolygon)) and not part.is_empty
        ]
        if polygons:
            return unary_union(polygons)
    raise ParcelGeometryError("Parcel geometry must be a Polygon or MultiPolygon")


def prepare_parcel_geometry(
    value: dict[str, Any],
) -> tuple[BaseGeometry, list[str]]:
    """Return a valid WGS84 parcel polygon and technical warnings."""

    raw_geometry = value.get("geometry") if value.get("type") == "Feature" else value
    if not isinstance(raw_geometry, dict):
        raise ParcelGeometryError("Parcel GeoJSON does not contain a geometry")
    try:
        geometry = shape(raw_geometry)
    except (TypeError, ValueError, KeyError) as exc:
        raise ParcelGeometryError("Parcel GeoJSON is invalid") from exc
    if geometry.is_empty:
        raise ParcelGeometryError("Parcel geometry is empty")

    warnings: list[str] = []
    if not geometry.is_valid:
        geometry = make_valid(geometry)
        warnings.append("Invalid parcel geometry was repaired with shapely.make_valid")
    geometry = _polygonal(geometry)
    if geometry.is_empty:
        raise ParcelGeometryError("Parcel geometry is empty after validation")
    min_x, min_y, max_x, max_y = geometry.bounds
    if min_x < -180 or max_x > 180 or min_y < -90 or max_y > 90:
        raise ParcelGeometryError(
            "Parcel coordinates must use WGS84 longitude/latitude"
        )
    return geometry, warnings


def local_metric_crs(geometry: BaseGeometry) -> str:
    """Return a stable local equal-area CRS centred on the parcel."""

    point = geometry.centroid
    crs = CRS.from_proj4(
        "+proj=laea "
        f"+lat_0={point.y:.12f} +lon_0={point.x:.12f} "
        "+datum=WGS84 +units=m +no_defs"
    )
    return crs.to_string()


def build_area_of_interest(
    *,
    case_id: str,
    source_snapshot_id: str,
    parcel_geojson: dict[str, Any],
    margin_m: int = 1000,
) -> AreaOfInterest:
    """Build one metric minimum-radius circle shared by OSM and 2GIS."""

    if not 0 <= margin_m <= 10_000:
        raise ValueError("margin_m must be between 0 and 10000")

    geometry, warnings = prepare_parcel_geometry(parcel_geojson)
    canonical = normalize(geometry)
    digest = sha256(canonical.wkb).hexdigest()
    metric_crs = local_metric_crs(geometry)
    forward = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    inverse = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)
    projected = transform(forward.transform, geometry)
    minimum_circle = minimum_bounding_circle(projected)
    minimum_radius = float(minimum_bounding_radius(projected))
    projected_center = minimum_circle.centroid
    search_radius = minimum_radius + margin_m
    query_geometry = transform(
        inverse.transform,
        projected_center.buffer(search_radius, quad_segs=32),
    )
    center = transform(inverse.transform, projected_center)
    normalized_geojson = dict(mapping(geometry))
    return AreaOfInterest(
        id=f"aoi-{uuid4().hex}",
        case_id=case_id,
        parcel_geometry=normalized_geojson,
        query_geometry=dict(mapping(query_geometry)),
        bbox=tuple(float(value) for value in query_geometry.bounds),
        representative_point=(float(center.x), float(center.y)),
        parcel_minimum_radius_m=round(minimum_radius, 3),
        margin_m=margin_m,
        search_radius_m=round(search_radius, 3),
        source_snapshot_id=source_snapshot_id,
        geometry_hash=digest,
        source_crs="EPSG:4326",
        metric_crs=metric_crs,
        validation_warnings=warnings,
    )
