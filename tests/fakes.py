from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

PARCEL_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [43.6000, 56.0600],
            [43.6100, 56.0600],
            [43.6100, 56.0700],
            [43.6000, 56.0700],
            [43.6000, 56.0600],
        ]
    ],
}

ZONE_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [43.6020, 56.0620],
            [43.6060, 56.0620],
            [43.6060, 56.0660],
            [43.6020, 56.0660],
            [43.6020, 56.0620],
        ]
    ],
}

BUILDING_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [43.6120, 56.0620],
            [43.6130, 56.0620],
            [43.6130, 56.0630],
            [43.6120, 56.0630],
            [43.6120, 56.0620],
        ]
    ],
}

SEARCH_AREA_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [43.5900, 56.0500],
            [43.6200, 56.0500],
            [43.6200, 56.0800],
            [43.5900, 56.0800],
            [43.5900, 56.0500],
        ]
    ],
}


def parcel_info_result() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "parcel": {
                "nspd_id": 101,
                "cadastral_number": "52:26:0040002:3823",
                "address": "Тестовый участок",
                "area_m2": 650000.0,
                "geometry": {"available": True, "type": "Polygon"},
                "geojson": deepcopy(PARCEL_GEOMETRY),
            },
            "coverage": {"partial": False},
        },
        "error": None,
        "metadata": {"adapter_version": "pynspd-agents-test"},
    }


def layer_result() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "parcel": {"cadastral_number": "52:26:0040002:3823"},
            "blocks": {
                "zouit": {
                    "layers": {
                        "zouit": {
                            "zones": [
                                {
                                    "nspd_id": 501,
                                    "registry_number": "52:26-6.1",
                                    "name": "Тестовая зона",
                                    "relation": {
                                        "kind": "zone_inside_parcel",
                                        "intersection_area_m2": 100.0,
                                    },
                                    "geojson": deepcopy(ZONE_GEOMETRY),
                                }
                            ]
                        }
                    }
                }
            },
            "coverage": {"partial": False},
        },
        "error": None,
        "metadata": {"adapter_version": "pynspd-agents-test"},
    }


def osm_result() -> dict[str, Any]:
    feature = {
        "element_type": "way",
        "osm_id": 7001,
        "name": "Склад",
        "tags": {"building": "warehouse"},
        "distance_to_parcel_m": 200.0,
        "geojson": deepcopy(BUILDING_GEOMETRY),
    }
    return {
        "ok": True,
        "data": {
            "search_area": {
                "parcel_minimum_radius_m": 650.0,
                "margin_m": 1000,
                "search_radius_m": 1650.0,
                "geojson": deepcopy(SEARCH_AREA_GEOMETRY),
            },
            "global_limit_reached": False,
            "blocks": [
                {
                    "block": "buildings",
                    "returned_count": 1,
                    "features": [feature],
                },
                {
                    "block": "poi",
                    "returned_count": 1,
                    "features": [feature],
                },
            ],
            "warnings": [],
        },
        "error": None,
        "metadata": {"adapter_version": "pyosm-agents-test"},
    }


class FakeNspdClient:
    def __init__(self, *, info: dict[str, Any] | None = None) -> None:
        self.info = info or parcel_info_result()
        self.info_calls = 0
        self.layer_calls = 0
        self.layer_arguments: dict[str, Any] = {}

    async def get_land_parcel_info(
        self, cadastral_number: str, *, detail: str = "full"
    ) -> Mapping[str, Any]:
        self.info_calls += 1
        assert detail == "full"
        return deepcopy(self.info)

    async def analyze_land_parcel_layers(
        self,
        cadastral_number: str,
        *,
        blocks: Sequence[str],
        include_geometry: bool,
        limit: int,
        detail: str,
    ) -> Mapping[str, Any]:
        self.layer_calls += 1
        self.layer_arguments = {
            "blocks": list(blocks),
            "include_geometry": include_geometry,
            "limit": limit,
            "detail": detail,
        }
        return deepcopy(layer_result())


class FakeOsmClient:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls = 0
        self.arguments: dict[str, Any] = {}

    async def analyze_area(
        self,
        geometry: Mapping[str, Any],
        *,
        source_crs: str,
        margin_m: int,
        blocks: Sequence[str],
        limit_per_block: int,
        include_geometry: bool,
    ) -> Mapping[str, Any]:
        self.calls += 1
        self.arguments = {
            "geometry": deepcopy(dict(geometry)),
            "source_crs": source_crs,
            "margin_m": margin_m,
            "blocks": list(blocks),
            "limit_per_block": limit_per_block,
            "include_geometry": include_geometry,
        }
        if self.failure is not None:
            raise self.failure
        return deepcopy(osm_result())
