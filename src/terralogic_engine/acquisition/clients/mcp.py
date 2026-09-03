"""Thin MCP HTTP clients; the package does not import source implementations."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class McpDependencyError(RuntimeError):
    """Raised when the optional MCP SDK is unavailable."""


class McpResponseError(RuntimeError):
    """Raised when an MCP tool result has no parseable structured payload."""


class McpToolTransport(Protocol):
    async def call_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]: ...


class StreamableHttpMcpTransport:
    """Call stateless or stateful Streamable HTTP MCP servers using the SDK."""

    def __init__(self, url: str) -> None:
        self.url = url

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise McpDependencyError(
                "Install the MCP client extra with: "
                "pip install 'terralogic-engine[mcp]'"
            ) from exc

        async with streamable_http_client(self.url) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(name, dict(arguments))
        if getattr(result, "isError", False) or getattr(result, "is_error", False):
            messages = [
                str(text)
                for item in getattr(result, "content", [])
                if (text := getattr(item, "text", None)) is not None
            ]
            raise McpResponseError(
                f"MCP tool {name!r} failed: {'; '.join(messages) or 'unknown error'}"
            )
        structured = getattr(result, "structuredContent", None)
        if structured is None:
            structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            return structured
        for content in getattr(result, "content", []):
            text = getattr(content, "text", None)
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
        raise McpResponseError(f"MCP tool {name!r} returned no structured object")


class McpNspdClient:
    """Map the stable two-tool NSPD contract to the acquisition boundary."""

    def __init__(self, transport: McpToolTransport) -> None:
        self.transport = transport

    async def get_land_parcel_info(
        self, cadastral_number: str, *, detail: str = "full"
    ) -> dict[str, Any]:
        return await self.transport.call_tool(
            "nspd_get_land_parcel_info",
            {"cadastral_number": cadastral_number, "detail": detail},
        )

    async def analyze_land_parcel_layers(
        self,
        cadastral_number: str,
        *,
        blocks: Sequence[str],
        include_geometry: bool,
        limit: int,
        detail: str,
    ) -> dict[str, Any]:
        return await self.transport.call_tool(
            "nspd_analyze_land_parcel_layers",
            {
                "cadastral_number": cadastral_number,
                "blocks": list(blocks),
                "include_geometry": include_geometry,
                "group_related": True,
                "limit": limit,
                "detail": detail,
            },
        )


class McpOsmClient:
    """Map the target contour-based OSM contract to the acquisition boundary."""

    def __init__(self, transport: McpToolTransport) -> None:
        self.transport = transport

    async def analyze_area(
        self,
        geometry: Mapping[str, Any],
        *,
        source_crs: str,
        margin_m: int,
        blocks: Sequence[str],
        limit_per_block: int,
        include_geometry: bool,
    ) -> dict[str, Any]:
        return await self.transport.call_tool(
            "osm_analyze_area",
            {
                "geometry": dict(geometry),
                "source_crs": source_crs,
                "margin_m": margin_m,
                "blocks": list(blocks),
                "limit_per_block": limit_per_block,
                "include_geometry": include_geometry,
            },
        )


class McpDgisClient:
    """Map the focused two-tool 2GIS contract to the acquisition boundary."""

    def __init__(self, transport: McpToolTransport) -> None:
        self.transport = transport

    async def analyze_social_infrastructure(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_m: int,
        mode: str,
        limit_per_category: int,
    ) -> dict[str, Any]:
        return await self.transport.call_tool(
            "dgis_analyze_social_infrastructure",
            {
                "latitude": latitude,
                "longitude": longitude,
                "radius_m": radius_m,
                "mode": mode,
                "limit_per_category": limit_per_category,
            },
        )

    async def analyze_transport_infrastructure(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_m: int,
        mode: str,
        limit_per_category: int,
    ) -> dict[str, Any]:
        return await self.transport.call_tool(
            "dgis_analyze_transport_infrastructure",
            {
                "latitude": latitude,
                "longitude": longitude,
                "radius_m": radius_m,
                "mode": mode,
                "limit_per_category": limit_per_category,
            },
        )


RGIS_ADAPTER_VERSION = "pyrgis-agents/0.4.0"
RGIS_SOURCES = ["RGIS MO (Геопортал Подмосковья)"]


class McpRgisClient:
    """Map the focused two-tool RGIS contract to the acquisition boundary."""

    def __init__(self, transport: McpToolTransport) -> None:
        self.transport = transport

    @staticmethod
    def _not_applicable(cadastral_number: str) -> dict[str, Any]:
        return {
            "ok": True,
            "data": {
                "applicable": False,
                "cadastral_number": cadastral_number,
                "reason": "RGIS MO covers cadastral region 50 only",
            },
            "error": None,
            "metadata": {
                "adapter_version": RGIS_ADAPTER_VERSION,
                "sources": RGIS_SOURCES,
            },
        }

    async def get_land_parcel_info(
        self, cadastral_number: str, *, detail: str = "standard"
    ) -> dict[str, Any]:
        if not cadastral_number.startswith("50:"):
            return self._not_applicable(cadastral_number)
        return await self.transport.call_tool(
            "rgis_get_land_parcel_info",
            {"cadastral_number": cadastral_number, "detail": detail},
        )

    async def analyze_land_parcel_layers(
        self,
        cadastral_number: str,
        *,
        blocks: Sequence[str],
        include_geometry: bool,
        limit_per_layer: int,
        zoom: int,
    ) -> dict[str, Any]:
        if not cadastral_number.startswith("50:"):
            return self._not_applicable(cadastral_number)
        return await self.transport.call_tool(
            "rgis_analyze_land_parcel_layers",
            {
                "cadastral_number": cadastral_number,
                "blocks": list(blocks),
                "include_geometry": include_geometry,
                "limit_per_layer": limit_per_layer,
                "zoom": zoom,
            },
        )
