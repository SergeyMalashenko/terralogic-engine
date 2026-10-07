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
            "Дракинский карьер",
            {"natural": "water"},
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


def rgis_parcel_info_result() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "applicable": True,
            "cadastral_number": "50:32:0000000:38218",
            "parcel": {
                "cadnum": "50:32:0000000:38218",
                "geometry": deepcopy(PARCEL_GEOMETRY),
            },
        },
        "error": None,
        "metadata": {"adapter_version": "pyrgis-agents-test"},
    }


def rgis_layer_analysis_result() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "applicable": True,
            "cadastral_number": "50:32:0000000:38218",
            "blocks": {
                "restrictions_and_special": {
                    "layers": {
                        "parcel_zouit": {
                            "title": "ЗОУИТ по карточке участка RGIS",
                            "zones": [
                                {
                                    "name": "Тестовая охранная зона",
                                    "code": "ТЗ-1",
                                    "area": 100.0,
                                    "percent": 0.02,
                                    "zone_code": 218020020006,
                                    "geometry": deepcopy(ZONE_GEOMETRY),
                                }
                            ],
                        }
                    }
                },
                "urban_planning": {
                    "layers": {
                        "parcel_usage": {
                            "title": "Территориальные зоны и ВРИ участка",
                            "objects": [
                                {
                                    "zone": "СХ-3",
                                    "name": "Зона сельскохозяйственного производства",
                                    "area": 650000.0,
                                    "percent": 100,
                                    "usages": [
                                        {
                                            "code": "1.1",
                                            "name": "Растениеводство",
                                            "area_min": "20000",
                                            "area_max": "Не подлежит установлению",
                                            "margin": "3",
                                            "building_percentage": "0%",
                                        }
                                    ],
                                    "geometry": deepcopy(PARCEL_GEOMETRY),
                                }
                            ],
                        },
                        "gpzu": {
                            "title": "Градостроительные планы земельного участка",
                            "objects": [
                                {
                                    "id": 101,
                                    "relation": "intersects",
                                    "properties": {
                                        "label": "РФ-50-TEST-001 04.03.2021"
                                    },
                                    "geometry": deepcopy(PARCEL_GEOMETRY),
                                }
                            ],
                        },
                        "territorial_zones": {
                            "title": "Территориальные зоны ПЗЗ",
                            "objects": [
                                {
                                    "id": 102,
                                    "relation": "parcel_inside_object",
                                    "properties": {"label": "СХ-3"},
                                    "geometry": deepcopy(PARCEL_GEOMETRY),
                                }
                            ],
                        },
                        "planning_projects": {
                            "title": "Проекты планировки территории",
                            "objects": [
                                {
                                    "id": 103,
                                    "relation": "intersects",
                                    "properties": {"label": "ППТ-TEST"},
                                    "geometry": deepcopy(ZONE_GEOMETRY),
                                }
                            ],
                        },
                        "surveying_projects": {
                            "title": "Проекты межевания территории",
                            "objects": [
                                {
                                    "id": 104,
                                    "relation": "object_inside_parcel",
                                    "properties": {"label": "ПМТ-TEST"},
                                    "geometry": deepcopy(ZONE_GEOMETRY),
                                }
                            ],
                        },
                    }
                },
            },
            "coverage": {"partial": False},
        },
        "error": None,
        "metadata": {"adapter_version": "pyrgis-agents-test"},
    }


class FakeRgisClient:
    def __init__(
        self,
        *,
        info_result: dict[str, Any] | None = None,
        layer_result: dict[str, Any] | None = None,
    ) -> None:
        self.info_result = info_result or rgis_parcel_info_result()
        self.layer_result = layer_result or rgis_layer_analysis_result()
        self.info_calls = 0
        self.layer_calls = 0
        self.info_arguments: dict[str, Any] = {}
        self.layer_arguments: dict[str, Any] = {}

    async def get_land_parcel_info(
        self, cadastral_number: str, *, detail: str
    ) -> Mapping[str, Any]:
        self.info_calls += 1
        self.info_arguments = {
            "cadastral_number": cadastral_number,
            "detail": detail,
        }
        return deepcopy(self.info_result)

    async def analyze_land_parcel_layers(
        self,
        cadastral_number: str,
        *,
        blocks,
        include_geometry: bool,
        limit_per_layer: int,
        zoom: int,
    ) -> Mapping[str, Any]:
        self.layer_calls += 1
        self.layer_arguments = {
            "cadastral_number": cadastral_number,
            "blocks": blocks,
            "include_geometry": include_geometry,
            "limit_per_layer": limit_per_layer,
            "zoom": zoom,
        }
        return deepcopy(self.layer_result)


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


def rgis_documents_sync_result() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "applicable": True,
            "cadastral_number": "50:32:0000000:38218",
            "zone_codes": ["218020020006"],
            "documents": [
                {
                    "version_id": 9001,
                    "doc_type": "pzz",
                    "municipality": "Тестовый район",
                    "number": "ПЗЗ-Т-592",
                    "version_date": "2021-04-03",
                    "role": "main",
                    "status": "registered_pending_fetch",
                    "sources": ["https://rgis.test/card/9001"],
                    "files": [],
                },
                {
                    "version_id": 9002,
                    "doc_type": "general_plan",
                    "municipality": "Тестовый район",
                    "number": "ГП-Т-15",
                    "version_date": "2019-11-20",
                    "role": "main",
                    "status": "downloaded",
                    "sources": ["https://rgis.test/card/9002"],
                    "files": ["gp-15.pdf"],
                },
            ],
            "warnings": [],
            "partial": False,
        },
        "error": None,
        "metadata": {"adapter_version": "pyrgis-agents-test"},
    }


def geodocs_acquire_result() -> dict[str, Any]:
    """Успешный ответ geodocs acquire_documents по ПЗЗ-Т-592 (version 9001)."""

    return {
        "status": "acquired",
        "refs": [
            {
                "municipality": "Тестовый район",
                "doc_type": "pzz",
                "number": "ПЗЗ-Т-592",
                "version_date": "2021-04-03",
                "role": "unknown",
                "title": None,
                "issuer": None,
                "region_code": None,
                "source": "rgis",
                "source_object_id": "9001",
                "amendment_number": None,
                "version_id": 9001,
            }
        ],
        "warnings": [],
    }


def geodocs_vri_query_result() -> dict[str, Any]:
    """Ответ geodocs query_documents (static:vri) по таблицам ВРИ зон."""

    return {
        "status": "success",
        "data": {
            "zones": [
                {
                    "zone_code": "218020020006",
                    "found": True,
                    "extractions": [
                        {
                            "version_id": 9001,
                            "status": "extracted",
                            "extractor": "docx_table",
                            "table": {
                                "zone_code": "218020020006",
                                "zone_name": None,
                                "zone_description": None,
                                "items": [
                                    {
                                        "row": "1",
                                        "code": "1.1",
                                        "name": "Растениеводство",
                                        "area_min": "20000",
                                        "area_max": "Не подлежит установлению",
                                        "building_percentage": "0%",
                                        "margin": "3",
                                        "raw": "1 1.1 Растениеводство",
                                    },
                                    {
                                        "row": "2",
                                        "code": "2.1",
                                        "name": "Садоводство",
                                        # int-значения, как из parse_number
                                        "area_min": 600,
                                        "area_max": 1200,
                                        "building_percentage": "10%",
                                        "margin": "3",
                                        "raw": "2 2.1 Садоводство",
                                    },
                                ],
                                "counts": {"items": 2},
                                "source_file": "pzz-592.docx",
                                "confidence": 0.93,
                            },
                        }
                    ],
                },
                {
                    "zone_code": "218020020099",
                    "found": False,
                    "extractions": [],
                },
            ]
        },
        "evidence": [
            {
                "version_id": 9001,
                "file": "pzz-592.docx",
                "page": None,
                "section": "218020020006",
                "quote": "Таблица ВРИ зоны 218020020006",
            }
        ],
        "answer_text": "Таблицы ВРИ извлечены детерминированно",
        "documents": [
            {
                "version_id": 9001,
                "municipality": "Тестовый район",
                "doc_type": "pzz",
                "number": "ПЗЗ-Т-592",
                "version_date": "2021-04-03",
                "title": None,
            }
        ],
        "executor": "static:vri",
        "warnings": [],
        "duration_seconds": 0.01,
    }


def geodocs_zouit_query_result() -> dict[str, Any]:
    """Ответ geodocs query_documents (static:zouit) по режимам ЗОУИТ."""

    return {
        "status": "success",
        "data": {
            "regimes": [
                {
                    "version_id": 8001,
                    "zone_code": "50:32-6.1",
                    "registry_number": "50:32-6.1",
                    "name": "Тестовая охранная зона",
                    "zone_type": "охранная зона",
                    "registration_date": "2015-05-12",
                    "restrictions": "Запрет строительства",
                    "relation_kind": "zone_inside_parcel",
                    "parcel_coverage_percent": 0.02,
                }
            ]
        },
        "evidence": [
            {
                "version_id": 8001,
                "file": None,
                "page": None,
                "section": "50:32-6.1",
                "quote": "Тестовая охранная зона",
            }
        ],
        "answer_text": "Режимы ЗОУИТ взяты из готовых extractions: 1 шт.",
        "documents": [
            {
                "version_id": 8001,
                "municipality": "Тестовый район",
                "doc_type": "zouit_regime",
                "number": "Постановление № 111",
                "version_date": "2015-05-12",
                "title": None,
            }
        ],
        "executor": "static:zouit",
        "warnings": [],
        "duration_seconds": 0.01,
    }


def nspd_documents_sync_result() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "cadastral_number": "50:32:0000000:38218",
            "municipality": "Тестовый район",
            "territorial_zones": [],
            "zouit_zones": ["50:32-6.1"],
            "documents": [
                {
                    "version_id": 8001,
                    "number": "Постановление № 111",
                    "version_date": "2015-05-12",
                    "status": "registered_pending_fetch",
                    "sources": ["https://nspd.test/doc/8001"],
                    "zone_codes": ["50:32-6.1"],
                }
            ],
            "warnings": [],
            "partial": False,
        },
        "error": None,
        "metadata": {"adapter_version": "pynspd-agents-test"},
    }


class FakeRgisDocumentsClient:
    def __init__(
        self,
        *,
        sync_result: dict[str, Any] | None = None,
        sync_failure: Exception | None = None,
    ) -> None:
        self.sync_result = sync_result or rgis_documents_sync_result()
        self.sync_failure = sync_failure
        self.sync_calls = 0

    async def sync_parcel_documents(self, cadastral_number: str) -> Mapping[str, Any]:
        self.sync_calls += 1
        if self.sync_failure is not None:
            raise self.sync_failure
        return deepcopy(self.sync_result)


class FakeNspdDocumentsClient:
    def __init__(
        self,
        *,
        sync_result: dict[str, Any] | None = None,
        sync_failure: Exception | None = None,
    ) -> None:
        self.sync_result = sync_result or nspd_documents_sync_result()
        self.sync_failure = sync_failure
        self.sync_calls = 0

    async def sync_parcel_documents(self, cadastral_number: str) -> Mapping[str, Any]:
        self.sync_calls += 1
        if self.sync_failure is not None:
            raise self.sync_failure
        return deepcopy(self.sync_result)


class FakeGeodocsClient:
    """Fake второго контура: acquire по кандидатам + query ВРИ/ЗОУИТ."""

    def __init__(
        self,
        *,
        acquire_result: dict[str, Any] | None = None,
        vri_result: dict[str, Any] | None = None,
        regimes_result: dict[str, Any] | None = None,
        acquire_failure: Exception | None = None,
        vri_failure: Exception | None = None,
        regimes_failure: Exception | None = None,
    ) -> None:
        self.acquire_result = acquire_result or geodocs_acquire_result()
        self.vri_result = vri_result or geodocs_vri_query_result()
        self.regimes_result = regimes_result or geodocs_zouit_query_result()
        self.acquire_failure = acquire_failure
        self.vri_failure = vri_failure
        self.regimes_failure = regimes_failure
        self.acquire_calls: list[dict[str, Any]] = []
        self.query_calls: list[dict[str, Any]] = []

    async def acquire_documents(
        self,
        municipality: str,
        doc_type: str,
        *,
        number: str | None = None,
        version_date: str | None = None,
        title: str | None = None,
    ) -> Mapping[str, Any]:
        self.acquire_calls.append(
            {
                "municipality": municipality,
                "doc_type": doc_type,
                "number": number,
                "version_date": version_date,
                "title": title,
            }
        )
        if self.acquire_failure is not None:
            raise self.acquire_failure
        return deepcopy(self.acquire_result)

    async def query_documents(
        self,
        version_ids,
        query: str,
        *,
        response_schema=None,
    ) -> Mapping[str, Any]:
        self.query_calls.append(
            {
                "version_ids": list(version_ids),
                "query": query,
                "response_schema": response_schema,
            }
        )
        if "ВРИ" in query:
            if self.vri_failure is not None:
                raise self.vri_failure
            return deepcopy(self.vri_result)
        if self.regimes_failure is not None:
            raise self.regimes_failure
        return deepcopy(self.regimes_result)
