"""Thin MCP HTTP clients; the package does not import source implementations."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any, Protocol


class McpDependencyError(RuntimeError):
    """Raised when the optional MCP SDK is unavailable."""


class McpResponseError(RuntimeError):
    """Raised when an MCP tool result has no parseable structured payload."""


class McpToolError(RuntimeError):
    """Raised when an MCP tool envelope reports ok != True."""


class McpToolTransport(Protocol):
    async def call_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]: ...


def _group_leaves(exc: BaseExceptionGroup) -> list[BaseException]:
    """Листья ExceptionGroup (рекурсивно) для читаемого сообщения об ошибке."""
    leaves: list[BaseException] = []
    for sub in exc.exceptions:
        if isinstance(sub, BaseExceptionGroup):
            leaves.extend(_group_leaves(sub))
        else:
            leaves.append(sub)
    return leaves


class StreamableHttpMcpTransport:
    """Call stateless or stateful Streamable HTTP MCP servers using the SDK."""

    def __init__(self, url: str, *, read_timeout_seconds: float | None = None) -> None:
        self.url = url
        self.read_timeout_seconds = read_timeout_seconds

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

        client_kwargs: dict[str, Any] = {}
        session_kwargs: dict[str, Any] = {}
        if self.read_timeout_seconds is not None:
            import httpx

            read_timeout = timedelta(seconds=self.read_timeout_seconds)
            client_kwargs = {
                "http_client": httpx.AsyncClient(
                    timeout=httpx.Timeout(self.read_timeout_seconds, connect=10.0)
                )
            }
            session_kwargs = {"read_timeout_seconds": read_timeout}
        try:
            async with streamable_http_client(self.url, **client_kwargs) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(
                    read_stream, write_stream, **session_kwargs
                ) as session:
                    await session.initialize()
                    result = await session.call_tool(name, dict(arguments))
        except BaseExceptionGroup as exc:
            # anyio TaskGroup'ы транспорта заворачивают сетевые сбои соединения
            # в ExceptionGroup — расплющиваем до читаемой ошибки
            leaves = "; ".join(str(leaf) for leaf in _group_leaves(exc))
            raise McpResponseError(
                f"MCP tool {name!r} transport failed: {leaves or exc}"
            ) from exc
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


def _checked_tool_result(name: str, envelope: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(envelope)
    if result.get("ok") is not True:
        error = result.get("error")
        if isinstance(error, Mapping):
            code = error.get("code", "tool_error")
            message = error.get("message", "unknown error")
            raise McpToolError(f"MCP tool {name!r} failed: {code}: {message}")
        raise McpToolError(f"MCP tool {name!r} failed: ok != True")
    return result


class McpRgisDocumentsClient:
    """Map the RGIS MO document sync tool to the acquisition boundary."""

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

    async def sync_parcel_documents(self, cadastral_number: str) -> dict[str, Any]:
        if not cadastral_number.startswith("50:"):
            return self._not_applicable(cadastral_number)
        envelope = await self.transport.call_tool(
            "rgis_sync_parcel_documents",
            {"cadastral_number": cadastral_number},
        )
        return _checked_tool_result("rgis_sync_parcel_documents", envelope)


class McpNspdDocumentsClient:
    """Map the NSPD document sync tool to the acquisition boundary."""

    def __init__(self, transport: McpToolTransport) -> None:
        self.transport = transport

    async def sync_parcel_documents(self, cadastral_number: str) -> dict[str, Any]:
        envelope = await self.transport.call_tool(
            "nspd_sync_parcel_documents",
            {"cadastral_number": cadastral_number},
        )
        return _checked_tool_result("nspd_sync_parcel_documents", envelope)


GEODOCS_ADAPTER_VERSION = "geodocs-mcp"
DEFAULT_GEODOCS_READ_TIMEOUT_SECONDS = 3600.0


class McpGeodocsClient:
    """Map the two-tool geodocs contour (second contour) to the boundary.

    The geodocs tools may fall back to the agent tier, so the transport
    needs a long read timeout (default ~30 minutes). geodocs-mcp answers
    with plain status/data payloads, not ok/data/error envelopes, so the
    results pass through without the tool-envelope check.
    """

    def __init__(
        self,
        transport: McpToolTransport | None = None,
        *,
        url: str | None = None,
        read_timeout_seconds: float = DEFAULT_GEODOCS_READ_TIMEOUT_SECONDS,
    ) -> None:
        if transport is None:
            if url is None:
                raise ValueError("McpGeodocsClient requires a transport or a url")
            transport = StreamableHttpMcpTransport(
                url, read_timeout_seconds=read_timeout_seconds
            )
        self.transport = transport
        self.read_timeout_seconds = read_timeout_seconds

    async def acquire_documents(
        self,
        municipality: str,
        doc_type: str,
        *,
        number: str | None = None,
        version_date: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "municipality": municipality,
            "doc_type": doc_type,
        }
        if number is not None:
            arguments["number"] = number
        if version_date is not None:
            arguments["version_date"] = version_date
        if title is not None:
            arguments["title"] = title
        return await self.transport.call_tool("acquire_documents", arguments)

    async def query_documents(
        self,
        version_ids: Sequence[int],
        query: str,
        *,
        response_schema: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "version_ids": [int(version_id) for version_id in version_ids],
            "query": query,
        }
        if response_schema is not None:
            arguments["response_schema"] = dict(response_schema)
        return await self.transport.call_tool("query_documents", arguments)
