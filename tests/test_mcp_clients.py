from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from terralogic_engine.acquisition.clients.mcp import (
    McpNspdDocumentsClient,
    McpRgisClient,
    McpRgisDocumentsClient,
    McpToolError,
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


async def test_rgis_documents_client_calls_exact_three_tool_contract() -> None:
    transport = RecordingTransport()
    client = McpRgisDocumentsClient(transport)

    await client.sync_parcel_documents("50:32:0000000:38218")
    await client.fetch_external_document(
        "50:32:0000000:38218",
        document_url="https://docs.cntd.ru/document/123456",
    )
    await client.get_document_vri("50:32:0000000:38218")
    await client.get_document_vri("50:32:0000000:38218", zone_code="218020020006")

    assert transport.calls == [
        (
            "rgis_sync_parcel_documents",
            {"cadastral_number": "50:32:0000000:38218"},
        ),
        (
            "rgis_fetch_external_document",
            {
                "cadastral_number": "50:32:0000000:38218",
                "document_url": "https://docs.cntd.ru/document/123456",
            },
        ),
        (
            "rgis_get_document_vri",
            {"cadastral_number": "50:32:0000000:38218"},
        ),
        (
            "rgis_get_document_vri",
            {
                "cadastral_number": "50:32:0000000:38218",
                "zone_code": "218020020006",
            },
        ),
    ]


async def test_rgis_documents_client_skips_transport_outside_region_50() -> None:
    transport = RecordingTransport()
    client = McpRgisDocumentsClient(transport)

    sync = await client.sync_parcel_documents("52:24:0000000:2216")
    vri = await client.get_document_vri("52:24:0000000:2216")

    assert sync["data"]["applicable"] is False
    assert vri["data"]["applicable"] is False
    assert transport.calls == []


async def test_rgis_documents_client_raises_tool_error_on_failed_envelope() -> None:
    client = McpRgisDocumentsClient(FailingTransport())

    with pytest.raises(McpToolError) as excinfo:
        await client.sync_parcel_documents("50:32:0000000:38218")

    assert "rgis_sync_parcel_documents" in str(excinfo.value)
    assert "access_blocked" in str(excinfo.value)


async def test_nspd_documents_client_calls_exact_two_tool_contract() -> None:
    transport = RecordingTransport()
    client = McpNspdDocumentsClient(transport)

    await client.sync_parcel_documents("52:26:0040002:3823")
    await client.get_zouit_regimes("52:26:0040002:3823")

    assert transport.calls == [
        (
            "nspd_sync_parcel_documents",
            {"cadastral_number": "52:26:0040002:3823"},
        ),
        (
            "nspd_get_zouit_regimes",
            {"cadastral_number": "52:26:0040002:3823"},
        ),
    ]


async def test_nspd_documents_client_raises_tool_error_on_failed_envelope() -> None:
    client = McpNspdDocumentsClient(FailingTransport())

    with pytest.raises(McpToolError) as excinfo:
        await client.get_zouit_regimes("52:26:0040002:3823")

    assert "nspd_get_zouit_regimes" in str(excinfo.value)
    assert "access_blocked" in str(excinfo.value)
