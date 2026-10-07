"""Тесты статической карты quick-отчёта (quickreport.mapimg) без сети."""

from __future__ import annotations

import io

import pytest

pytest.importorskip("PIL")

from PIL import Image

from terralogic_engine.domain.models import GeoFeature
from terralogic_engine.quickreport import mapimg
from terralogic_engine.quickreport.mapimg import MapLayer

PARCEL = {
    "type": "Polygon",
    "coordinates": [
        [
            [37.3095, 55.8536],
            [37.3105, 55.8536],
            [37.3105, 55.8540],
            [37.3095, 55.8540],
            [37.3095, 55.8536],
        ]
    ],
}

ZONE = {
    "type": "Polygon",
    "coordinates": [
        [
            [37.3085, 55.8530],
            [37.3100, 55.8530],
            [37.3100, 55.8545],
            [37.3085, 55.8545],
            [37.3085, 55.8530],
        ]
    ],
}


def _fake_tile(z: int, x: int, y: int, timeout: float) -> bytes:
    image = Image.new("RGB", (256, 256), (238, 240, 233))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _offline_tile(z: int, x: int, y: int, timeout: float) -> None:
    return None


def _water_layer() -> MapLayer:
    label, fill, outline = mapimg._ZONE_STYLES["water_protection"]
    return MapLayer(label=label, geometry=ZONE, fill=fill, outline=outline)


def test_render_overview_map_with_fake_tiles(tmp_path) -> None:
    out = tmp_path / "map.png"
    result = mapimg.render_overview_map(
        PARCEL, [_water_layer()], out, fetch_tile=_fake_tile
    )
    assert result.basemap_available
    assert result.layer_labels == ["Водоохранная зона"]
    assert result.warnings == []
    image = Image.open(out)
    assert image.size == (1200, 800)
    # в области участка должны найтись красноватые пиксели штриховки
    pixels = image.load()
    found_hatch = any(
        pixels[x, y][0] > pixels[x, y][2] + 30
        for x in range(550, 650, 2)
        for y in range(350, 450, 2)
    )
    assert found_hatch


def test_render_overview_map_offline_keeps_working(tmp_path) -> None:
    out = tmp_path / "map.png"
    result = mapimg.render_overview_map(
        PARCEL, [_water_layer()], out, fetch_tile=_offline_tile
    )
    assert not result.basemap_available
    assert any("подложка" in warning for warning in result.warnings)
    assert out.exists()


def test_render_overview_map_uses_tile_cache(tmp_path) -> None:
    calls: list[tuple[int, int, int]] = []

    def counting_tile(z: int, x: int, y: int, timeout: float) -> bytes:
        calls.append((z, x, y))
        return _fake_tile(z, x, y, timeout)

    cache = tmp_path / "tile-cache"
    mapimg.render_overview_map(
        PARCEL, [], tmp_path / "a.png", cache_dir=cache, fetch_tile=counting_tile
    )
    first = len(calls)
    assert first > 0
    mapimg.render_overview_map(
        PARCEL, [], tmp_path / "b.png", cache_dir=cache, fetch_tile=counting_tile
    )
    assert len(calls) == first  # второй рендер обошёлся кэшем


def _feature(
    source_type: str,
    geometry: dict | None,
    *,
    feature_class: str = "restriction_zone",
    properties: dict | None = None,
) -> GeoFeature:
    return GeoFeature(
        id=f"f-{source_type}",
        case_id="case-x",
        snapshot_id="snap-x",
        source="rgis",
        source_type=source_type,
        feature_class=feature_class,
        geometry=geometry,
        properties=properties or {},
    )


def test_collect_map_layers_filters_full_coverage_and_dedups() -> None:
    features = [
        _feature("water_protection", ZONE),
        _feature("water_protection", ZONE),  # дубль геометрии
        _feature(
            "parcel_zouit", ZONE, properties={"name": "Аэродром", "percent": 100.0}
        ),
        _feature(
            "parcel_zouit", PARCEL, properties={"name": "Полоса", "percent": 20.0}
        ),
        _feature(
            "parcel_zouit",
            ZONE,
            properties={"name": "Водоохранная зона", "percent": 10.0},
        ),
        _feature("territorial_zones", ZONE, feature_class="territorial_zone"),
        _feature("coastal_protection", None),
    ]
    layers = mapimg.collect_map_layers(features)
    labels = [layer.label for layer in layers]
    assert labels.count("Водоохранная зона") == 1
    assert "Аэродром" not in labels  # 100% охват не рисуем
    assert "Полоса" in labels
    # картoчный parcel_zouit-дубль одноимённого слоя не рисуется
    assert all(layer.geometry is not None for layer in layers)


def test_select_visible_layers_drops_region_covering_zones() -> None:
    giant = {
        "type": "Polygon",
        "coordinates": [
            [[37.0, 55.5], [37.6, 55.5], [37.6, 56.1], [37.0, 56.1], [37.0, 55.5]]
        ],
    }
    # зона с полным охватом, но с границей рядом с участком — остаётся
    neighbor = {
        "type": "Polygon",
        "coordinates": [
            [
                [37.3094, 55.8535],
                [37.3110, 55.8535],
                [37.3110, 55.8545],
                [37.3094, 55.8545],
                [37.3094, 55.8535],
            ]
        ],
    }
    layers = [
        _water_layer(),
        MapLayer(
            label="Гигантская зона",
            geometry=giant,
            fill=(255, 193, 7, 80),
            outline=(200, 145, 0),
        ),
        MapLayer(
            label="Соседняя зона",
            geometry=neighbor,
            fill=(0, 150, 136, 70),
            outline=(0, 110, 100),
        ),
    ]
    kept = mapimg.select_visible_layers(layers, PARCEL)
    assert sorted(layer.label for layer in kept) == [
        "Водоохранная зона",
        "Соседняя зона",
    ]


def test_viewport_projects_bbox_center_to_image_center() -> None:
    bbox = mapimg._geometry_bbox(PARCEL)
    assert bbox is not None
    viewport = mapimg._Viewport(bbox, 1200, 800)
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    px, py = viewport.project(cx, cy)
    assert abs(px - 600) < 1
    assert abs(py - 400) < 1
    assert 3 <= viewport.zoom <= 18


def test_geometry_bbox_handles_multipolygon() -> None:
    multi = {"type": "MultiPolygon", "coordinates": [[PARCEL["coordinates"][0]]]}
    assert mapimg._geometry_bbox(multi) == mapimg._geometry_bbox(PARCEL)
    assert mapimg._geometry_bbox({"type": "Point", "coordinates": [1, 2]}) is None
