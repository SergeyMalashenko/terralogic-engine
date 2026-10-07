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
    """Document discovery contour of the RGIS MO MCP server (region 50 only).

    Sync only registers documents in the shared geodocs store; files are
    downloaded by the second contour (geodocs-mcp), not by this client.
    """

    async def sync_parcel_documents(
        self, cadastral_number: str
    ) -> Mapping[str, Any]: ...


class NspdDocumentsClient(Protocol):
    """Document discovery contour of the NSPD MCP server (federal coverage)."""

    async def sync_parcel_documents(
        self, cadastral_number: str
    ) -> Mapping[str, Any]: ...


class GeodocsClient(Protocol):
    """Second document contour (geodocs-mcp): acquisition and extraction.

    Responses are plain geodocs payloads (status/refs/warnings for acquire,
    status/data/evidence for query), not ok/data/error tool envelopes.
    """

    async def acquire_documents(
        self,
        municipality: str,
        doc_type: str,
        *,
        number: str | None = None,
        version_date: str | None = None,
        title: str | None = None,
    ) -> Mapping[str, Any]: ...

    async def query_documents(
        self,
        version_ids: Sequence[int],
        query: str,
        *,
        response_schema: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...
