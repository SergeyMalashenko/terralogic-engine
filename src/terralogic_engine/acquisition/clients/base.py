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


class RgisDocumentsClient(Protocol):
    """Document contour of the RGIS MO MCP server (region 50 only)."""

    async def sync_parcel_documents(
        self, cadastral_number: str
    ) -> Mapping[str, Any]: ...

    async def fetch_external_document(
        self, cadastral_number: str, *, document_url: str
    ) -> Mapping[str, Any]: ...

    async def get_document_vri(
        self, cadastral_number: str, *, zone_code: str | None = None
    ) -> Mapping[str, Any]: ...


class NspdDocumentsClient(Protocol):
    """Document contour of the NSPD MCP server (federal coverage)."""

    async def sync_parcel_documents(
        self, cadastral_number: str
    ) -> Mapping[str, Any]: ...

    async def get_zouit_regimes(
        self, cadastral_number: str
    ) -> Mapping[str, Any]: ...
