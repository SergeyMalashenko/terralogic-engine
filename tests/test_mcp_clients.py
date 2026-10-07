from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from terralogic_engine.acquisition.clients.mcp import (
    DEFAULT_GEODOCS_READ_TIMEOUT_SECONDS,
    McpGeodocsClient,
    McpNspdDocumentsClient,
    McpRgisClient,
    McpRgisDocumentsClient,
    McpToolError,
    StreamableHttpMcpTransport,
)


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        values = dict(arguments)
        self.calls.append((name, values))
        return {
            "ok": True,
            "data": {"tool": name, "arguments": values},
            "error": None,
            "metadata": {"adapter_version": "pyrgis-agents/test"},
        }


class FailingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        values = dict(arguments)
        self.calls.append((name, values))
        return {
            "ok": False,
            "data": None,
            "error": {"code": "access_blocked", "message": "source blocked"},
            "metadata": {"adapter_version": "pyrgis-agents/test"},
        }


async def test_rgis_client_skips_transport_outside_region_50() -> None:
    transport = RecordingTransport()
    client = McpRgisClient(transport)

    info = await client.get_land_parcel_info("52:24:0000000:2216")
    layers = await client.analyze_land_parcel_layers(
        "52:24:0000000:2216",
        blocks=["urban_planning"],
        include_geometry=True,
        limit_per_layer=50,
        zoom=14,
    )

    assert info["data"]["applicable"] is False
    assert layers["data"]["applicable"] is False
    assert transport.calls == []


async def test_rgis_client_calls_exact_two_tool_contract_for_region_50() -> None:
    transport = RecordingTransport()
    client = McpRgisClient(transport)

    await client.get_land_parcel_info("50:32:0000000:38218", detail="full")
    await client.analyze_land_parcel_layers(
        "50:32:0000000:38218",
        blocks=["restrictions_and_special", "urban_planning"],
        include_geometry=True,
        limit_per_layer=25,
        zoom=15,
    )

    assert transport.calls == [
        (
            "rgis_get_land_parcel_info",
            {
                "cadastral_number": "50:32:0000000:38218",
                "detail": "full",
            },
        ),
        (
            "rgis_analyze_land_parcel_layers",
            {
                "cadastral_number": "50:32:0000000:38218",
                "blocks": ["restrictions_and_special", "urban_planning"],
                "include_geometry": True,
                "limit_per_layer": 25,
                "zoom": 15,
            },
        ),
    ]


async def test_rgis_documents_client_calls_exact_sync_tool_contract() -> None:
    transport = RecordingTransport()
    client = McpRgisDocumentsClient(transport)

    await client.sync_parcel_documents("50:32:0000000:38218")

    assert transport.calls == [
        (
            "rgis_sync_parcel_documents",
            {"cadastral_number": "50:32:0000000:38218"},
        ),
    ]


async def test_rgis_documents_client_skips_transport_outside_region_50() -> None:
    transport = RecordingTransport()
    client = McpRgisDocumentsClient(transport)

    sync = await client.sync_parcel_documents("52:24:0000000:2216")

    assert sync["data"]["applicable"] is False
    assert transport.calls == []


async def test_rgis_documents_client_raises_tool_error_on_failed_envelope() -> None:
    client = McpRgisDocumentsClient(FailingTransport())

    with pytest.raises(McpToolError) as excinfo:
        await client.sync_parcel_documents("50:32:0000000:38218")

    assert "rgis_sync_parcel_documents" in str(excinfo.value)
    assert "access_blocked" in str(excinfo.value)


async def test_nspd_documents_client_calls_exact_sync_tool_contract() -> None:
    transport = RecordingTransport()
    client = McpNspdDocumentsClient(transport)

    await client.sync_parcel_documents("52:26:0040002:3823")

    assert transport.calls == [
        (
            "nspd_sync_parcel_documents",
            {"cadastral_number": "52:26:0040002:3823"},
        ),
    ]


async def test_nspd_documents_client_raises_tool_error_on_failed_envelope() -> None:
    client = McpNspdDocumentsClient(FailingTransport())

    with pytest.raises(McpToolError) as excinfo:
        await client.sync_parcel_documents("52:26:0040002:3823")

    assert "nspd_sync_parcel_documents" in str(excinfo.value)
    assert "access_blocked" in str(excinfo.value)


async def test_geodocs_client_calls_exact_two_tool_contract() -> None:
    transport = RecordingTransport()
    client = McpGeodocsClient(transport)

    await client.acquire_documents(
        "Тестовый район",
        "pzz",
        number="ПЗЗ-Т-592",
        version_date="2021-04-03",
    )
    await client.acquire_documents("Тестовый район", "general_plan")
    await client.query_documents([9001, 9002], "Верни таблицы ВРИ зон")
    await client.query_documents(
        [8001],
        "Верни режимы ЗОУИТ",
        response_schema={"type": "object"},
    )

    assert transport.calls == [
        (
            "acquire_documents",
            {
                "municipality": "Тестовый район",
                "doc_type": "pzz",
                "number": "ПЗЗ-Т-592",
                "version_date": "2021-04-03",
            },
        ),
        (
            "acquire_documents",
            {"municipality": "Тестовый район", "doc_type": "general_plan"},
        ),
        (
            "query_documents",
            {"version_ids": [9001, 9002], "query": "Верни таблицы ВРИ зон"},
        ),
        (
            "query_documents",
            {
                "version_ids": [8001],
                "query": "Верни режимы ЗОУИТ",
                "response_schema": {"type": "object"},
            },
        ),
    ]


def test_geodocs_client_builds_transport_with_long_read_timeout() -> None:
    client = McpGeodocsClient(url="http://127.0.0.1:8006/mcp")

    assert isinstance(client.transport, StreamableHttpMcpTransport)
    assert client.transport.url == "http://127.0.0.1:8006/mcp"
    assert client.transport.read_timeout_seconds == DEFAULT_GEODOCS_READ_TIMEOUT_SECONDS
    assert client.read_timeout_seconds == DEFAULT_GEODOCS_READ_TIMEOUT_SECONDS


def test_geodocs_client_requires_transport_or_url() -> None:
    with pytest.raises(ValueError, match="transport or a url"):
        McpGeodocsClient()
