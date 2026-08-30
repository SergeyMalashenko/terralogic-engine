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

FOREST_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [43.6120, 56.0620],
            [43.6160, 56.0620],
            [43.6160, 56.0660],
            [43.6120, 56.0660],
            [43.6120, 56.0620],
        ],
        [
            [43.6130, 56.0630],
            [43.6140, 56.0630],
            [43.6140, 56.0640],
            [43.6130, 56.0640],
            [43.6130, 56.0630],
        ],
    ],
}

LAKE_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [43.5920, 56.0620],
            [43.5940, 56.0620],
            [43.5940, 56.0640],
            [43.5920, 56.0640],
            [43.5920, 56.0620],
        ]
    ],
}

RIVER_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [
        [
            [43.5960, 56.0500],
            [43.5980, 56.0500],
            [43.5980, 56.0800],
            [43.5960, 56.0800],
            [43.5960, 56.0500],
        ]
    ],
}

STREAM_GEOMETRY = {
    "type": "LineString",
    "coordinates": [[43.5900, 56.0650], [43.6200, 56.0650]],
}

ROAD_GEOMETRY = {
    "type": "LineString",
    "coordinates": [[43.6050, 56.0500], [43.6050, 56.0800]],
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
    block_features = {
        "forests": (
            7001,
            "Тестовый лес",
            {"landuse": "forest"},
            FOREST_GEOMETRY,
        ),
        "lakes": (
            7002,
            "Тестовое озеро",
            {"natural": "water", "water": "lake"},
            LAKE_GEOMETRY,
        ),
        "rivers": (
            7003,
            "Тестовая река",
            {"natural": "water", "water": "river"},
            RIVER_GEOMETRY,
        ),
        "streams": (
            7004,
            "Тестовый ручей",
            {"waterway": "stream"},
            STREAM_GEOMETRY,
        ),
        "roads": (
            7005,
            "Тестовая дорога",
            {"highway": "service"},
            ROAD_GEOMETRY,
        ),
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
                    "block": block,
                    "returned_count": 1,
                    "features": [
                        {
                            "element_type": "way",
                            "osm_id": values[0],
                            "name": values[1],
                            "tags": values[2],
                            "distance_to_parcel_m": 200.0,
                            "geojson": deepcopy(values[3]),
                        }
                    ],
                }
                for block, values in block_features.items()
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


def dgis_result(
    *, analysis_type: str, group: str, category: str, object_id: str
) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "analysis_type": analysis_type,
            "source_complete": True,
            "response_limited": False,
            "groups": [
                {
                    "key": group,
                    "name": f"Группа {group}",
                    "categories": [
                        {
                            "key": category,
                            "name": f"Категория {category}",
                            "objects": [
                                {
                                    "id": object_id,
                                    "name": f"Объект {object_id}",
                                    "type": "branch",
                                    "latitude": 56.066,
                                    "longitude": 43.606,
                                    "distance_to_search_point_m": 350.0,
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        "error": None,
        "metadata": {"adapter_version": "py2gis-agents-test"},
    }


class FakeDgisClient:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.social_calls = 0
        self.transport_calls = 0
        self.social_arguments: dict[str, Any] = {}
        self.transport_arguments: dict[str, Any] = {}

    async def analyze_social_infrastructure(self, **kwargs: Any) -> Mapping[str, Any]:
        self.social_calls += 1
        self.social_arguments = deepcopy(kwargs)
        if self.failure is not None:
            raise self.failure
        return deepcopy(
            dgis_result(
                analysis_type="social",
                group="mandatory_services",
                category="education",
                object_id="school-1",
            )
        )

    async def analyze_transport_infrastructure(
        self, **kwargs: Any
    ) -> Mapping[str, Any]:
        self.transport_calls += 1
        self.transport_arguments = deepcopy(kwargs)
        if self.failure is not None:
            raise self.failure
        return deepcopy(
            dgis_result(
                analysis_type="transport",
                group="public_transport",
                category="public_transport_stops",
                object_id="stop-1",
            )
        )
