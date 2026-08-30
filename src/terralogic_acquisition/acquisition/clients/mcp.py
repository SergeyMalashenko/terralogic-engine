"""Thin MCP HTTP clients; the package does not import source implementations."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


class McpDependencyError(RuntimeError):
    """Raised when the optional MCP SDK is unavailable."""


class McpResponseError(RuntimeError):
    """Raised when an MCP tool result has no parseable structured payload."""


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
                "pip install 'terralogic-acquisition[mcp]'"
            ) from exc

        async with streamable_http_client(self.url) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(name, dict(arguments))
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

    def __init__(self, transport: StreamableHttpMcpTransport) -> None:
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

    def __init__(self, transport: StreamableHttpMcpTransport) -> None:
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

    def __init__(self, transport: StreamableHttpMcpTransport) -> None:
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
