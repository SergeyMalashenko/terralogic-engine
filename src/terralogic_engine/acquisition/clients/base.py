"""Narrow source contracts consumed by AcquisitionPipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class NspdSourceClient(Protocol):
    async def get_land_parcel_info(
        self, cadastral_number: str, *, detail: str = "full"
    ) -> Mapping[str, Any]: ...

    async def analyze_land_parcel_layers(
        self,
        cadastral_number: str,
        *,
        blocks: Sequence[str],
        include_geometry: bool,
        limit: int,
        detail: str,
    ) -> Mapping[str, Any]: ...


class OsmSourceClient(Protocol):
    async def analyze_area(
        self,
        geometry: Mapping[str, Any],
        *,
        source_crs: str,
        margin_m: int,
        blocks: Sequence[str],
        limit_per_block: int,
        include_geometry: bool,
    ) -> Mapping[str, Any]: ...


class DgisSourceClient(Protocol):
    async def analyze_social_infrastructure(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_m: int,
        mode: str,
        limit_per_category: int,
    ) -> Mapping[str, Any]: ...

    async def analyze_transport_infrastructure(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_m: int,
        mode: str,
        limit_per_category: int,
    ) -> Mapping[str, Any]: ...


class RgisSourceClient(Protocol):
    """Focused two-tool RGIS MO contract for cadastral region 50."""

    async def get_land_parcel_info(
        self, cadastral_number: str, *, detail: str
    ) -> Mapping[str, Any]: ...

    async def analyze_land_parcel_layers(
        self,
        cadastral_number: str,
        *,
        blocks: Sequence[str],
        include_geometry: bool,
        limit_per_layer: int,
        zoom: int,
    ) -> Mapping[str, Any]: ...
