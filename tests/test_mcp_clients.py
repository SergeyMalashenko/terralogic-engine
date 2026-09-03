from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from terralogic_engine.acquisition.clients.mcp import McpRgisClient


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
