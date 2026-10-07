"""Статическая карта QuickReport: подложка OSM + участок и зоны (Pillow).

Детерминированный рендер без браузера и тяжёлых геостеков:
Web-Mercator считается формулами, тайлы OSM скачиваются через urllib
с кэшем в ``<case-store>/tile-cache/{z}/{x}/{y}.png``, полигоны,
штриховка участка, легенда, стрелка севера и масштабная линейка
рисуются средствами Pillow. При недоступности сети карта всё равно
строится на нейтральной подложке, а в отчёт уходит предупреждение.
"""

from __future__ import annotations

import json
import math
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

TILE_SIZE = 256
TILE_URL_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_USER_AGENT = "TerraLogicX-quickreport/1.0 (local report renderer)"
MIN_ZOOM = 3
MAX_ZOOM = 19
MISSING_TILE_COLOR = (232, 232, 228)

# минимальная сторона кадра (~250–330 м в зависимости от широты),
# чтобы крошечный участок не превращал карту в квадрат тайла
_MIN_FRAME_SIDE_DEG = 0.003
_FRAME_FACTOR = 2.0

_MAX_FULL_COVERAGE_PERCENT = 99.0

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)

# source_type GeoFeature → (легенда, заливка RGBA, контур RGB)
_ZONE_STYLES: dict[str, tuple[str, tuple[int, int, int, int], tuple[int, int, int]]] = {
    "water_protection": ("Водоохранная зона", (66, 133, 244, 100), (36, 92, 190)),
    "coastal_protection": (
        "Прибрежная защитная полоса",
        (234, 67, 53, 100),
        (180, 40, 30),
    ),
    "drinking_water_protection": (
        "Зона санитарной охраны",
        (0, 150, 136, 100),
        (0, 110, 100),
    ),
    "sanitary_protection": (
        "Санитарно-защитная зона",
        (171, 71, 188, 100),
        (130, 45, 145),
    ),
    "protected_areas": ("ООПТ", (67, 160, 71, 100), (40, 120, 45)),
    "flooding": ("Зона затопления", (3, 169, 244, 100), (2, 130, 190)),
    "pipeline_protection": (
        "Охранная зона трубопроводов",
        (255, 152, 0, 100),
        (210, 120, 0),
    ),
}
_PARCEL_ZOUIT_STYLE = ("Зона с особыми условиями", (255, 193, 7, 110), (200, 145, 0))
_PARCEL_OUTLINE = (198, 40, 40)
_PARCEL_HATCH = (198, 40, 40, 110)
_PARCEL_LABEL = "Земельный участок"


@dataclass
class MapLayer:
    """Один тематический слой карты (зона с особыми условиями)."""

    label: str
    geometry: dict[str, Any]
    fill: tuple[int, int, int, int]
    outline: tuple[int, int, int]


@dataclass
class MapImageResult:
    """Итог рендера карты: файл, доступность подложки, слои, предупреждения."""

    path: Path
    basemap_available: bool
    layer_labels: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _iter_ring_points(geometry: dict[str, Any]) -> Iterable[list[list[float]]]:
    """Все кольца Polygon/MultiPolygon как списки [lon, lat]."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not isinstance(coords, (list, tuple)):
        return
    if gtype == "Polygon":
        for ring in coords:
            if isinstance(ring, (list, tuple)):
                yield ring
    elif gtype == "MultiPolygon":
        for polygon in coords:
            if not isinstance(polygon, (list, tuple)):
                continue
            for ring in polygon:
                if isinstance(ring, (list, tuple)):
                    yield ring


def _geometry_bbox(
    geometry: dict[str, Any],
) -> tuple[float, float, float, float] | None:
    min_lon = min_lat = float("inf")
    max_lon = max_lat = float("-inf")
    found = False
    for ring in _iter_ring_points(geometry):
        for point in ring:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            lon, lat = float(point[0]), float(point[1])
            min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
            min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
            found = True
    if not found:
        return None
    return (min_lon, min_lat, max_lon, max_lat)


def _coverage_percent(properties: dict[str, Any]) -> float | None:
    for key in ("percent", "parcel_coverage_percent"):
        value = properties.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def collect_map_layers(features: Iterable[Any]) -> list[MapLayer]:
    """Выбирает из GeoFeature кейса зоны для карты отчёта.

    Берутся restriction_zone с геометрией; зоны, накрывающие участок
    целиком (охват >= 99% по свойствам источника), не рисуются — сплошная
    заливка всего кадра неинформативна, такие зоны остаются в таблице
    факторов. Дубликаты геометрий (несколько run/снапшотов) схлопываются.
    Картoчные parcel_zouit-зоны пропускаются, если одноимённый тематический
    слой (водоохранная, прибрежная полоса, ...) уже собран — у него
    полная геометрия, а не обрезанная по участку.
    """
    layers: list[MapLayer] = []
    seen: set[str] = set()
    styled_labels: set[str] = set()
    pending_fallback: list[tuple[str, dict[str, Any]]] = []
    for feature in features:
        if feature.feature_class != "restriction_zone":
            continue
        geometry = feature.geometry
        if not isinstance(geometry, dict):
            continue
        properties = feature.properties or {}
        coverage = _coverage_percent(properties)
        if coverage is not None and coverage >= _MAX_FULL_COVERAGE_PERCENT:
            continue
        digest = sha256(
            json.dumps(geometry, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        style = _ZONE_STYLES.get(str(feature.source_type))
        if style is None:
            name = properties.get("name") or properties.get("code")
            label = str(name) if name else "Зона с особыми условиями"
            pending_fallback.append((label, geometry))
            continue
        styled_labels.add(style[0])
        layers.append(
            MapLayer(label=style[0], geometry=geometry, fill=style[1], outline=style[2])
        )
    for label, geometry in pending_fallback:
        if label in styled_labels:
            continue
        layers.append(
            MapLayer(
                label=label,
                geometry=geometry,
                fill=_PARCEL_ZOUIT_STYLE[1],
                outline=_PARCEL_ZOUIT_STYLE[2],
            )
        )

    # большие зоны рисуем первыми, мелкие остаются видимыми поверх
    def _area(layer: MapLayer) -> float:
        bbox = _geometry_bbox(layer.geometry)
        if bbox is None:
            return 0.0
        return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])

    layers.sort(key=_area, reverse=True)
    return layers


def select_visible_layers(
    layers: list[MapLayer],
    parcel_geometry: dict[str, Any],
    *,
    max_coverage: float = 0.99,
    max_area_ratio: float = 1000.0,
) -> list[MapLayer]:
    """Оставляет зоны, которые информативны на кадре карты.

    Зона, накрывающая участок почти целиком (>= 99%) и при этом
    превосходящая его по площади на порядки (>= 1000x), региональна:
    сплошная заливка всего кадра неинформативна и прячет остальные
    слои, поэтому такие зоны (приаэродромные и т.п.) не рисуются —
    они остаются в таблице факторов. Зона полного охвата с границей
    рядом с участком (водоохранная и т.п.) остаётся на карте.
    """
    from shapely.geometry import shape
    from shapely.validation import make_valid

    try:
        parcel = shape(parcel_geometry)
    except (ValueError, TypeError, AttributeError):
        return layers  # битая геометрия участка не мешает карте
    if not parcel.is_valid:
        parcel = make_valid(parcel)
    if parcel.is_empty or parcel.area == 0:
        return layers

    result: list[MapLayer] = []
    for layer in layers:
        try:
            geometry = shape(layer.geometry)
        except (ValueError, TypeError, AttributeError):
            continue  # битая геометрия слоя не мешает карте
        if not geometry.is_valid:
            geometry = make_valid(geometry)
        if geometry.is_empty:
            continue
        coverage = parcel.intersection(geometry).area / parcel.area
        if coverage >= max_coverage and geometry.area / parcel.area >= max_area_ratio:
            continue
        result.append(layer)
    return result


def _square_frame(
    bbox: tuple[float, float, float, float], factor: float = _FRAME_FACTOR
) -> tuple[float, float, float, float]:
    """Квадратный кадр: bbox → квадрат по длинной стороне → factor× от центра."""
    min_lon, min_lat, max_lon, max_lat = bbox
    side = max(max_lon - min_lon, max_lat - min_lat, _MIN_FRAME_SIDE_DEG)
    center_lon = (min_lon + max_lon) / 2
    center_lat = (min_lat + max_lat) / 2
    half = side * factor / 2
    return (
        center_lon - half,
        center_lat - half,
        center_lon + half,
        center_lat + half,
    )


def _mercator(lon: float, lat: float) -> tuple[float, float]:
    """lon/lat → доли мирового квадрата (0..1) в Web-Mercator."""
    lat = max(min(lat, 85.05112878), -85.05112878)
    x = (lon + 180.0) / 360.0
    sin_lat = math.sin(math.radians(lat))
    y = 0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)
    return x, y


class _Viewport:
    """Проекция lon/lat → пикселы изображения для фиксированного zoom."""

    def __init__(
        self, bbox: tuple[float, float, float, float], width: int, height: int
    ):
        self.width = width
        self.height = height
        min_lon, min_lat, max_lon, max_lat = bbox
        x0, y1 = _mercator(min_lon, min_lat)
        x1, y0 = _mercator(max_lon, max_lat)
        self.center_x = (x0 + x1) / 2
        self.center_y = (y0 + y1) / 2
        self.zoom = self._fit_zoom(x1 - x0, y1 - y0, width, height)
        scale = TILE_SIZE * (2**self.zoom)
        self.origin_x = self.center_x * scale - width / 2
        self.origin_y = self.center_y * scale - height / 2
        self.center_lat = (min_lat + max_lat) / 2
        self.bbox = bbox

    @staticmethod
    def _fit_zoom(span_x: float, span_y: float, width: int, height: int) -> int:
        """Минимальный zoom, при котором кадр bbox покрывает холст целиком.

        Излишек затем срезается кадрированием — итоговое изображение
        гарантированно лежит внутри кадра bbox.
        """
        for candidate in range(MIN_ZOOM, MAX_ZOOM + 1):
            scale = TILE_SIZE * (2**candidate)
            if span_x * scale >= width and span_y * scale >= height:
                return candidate
        return MAX_ZOOM

    def crop_box(self) -> tuple[int, int, int, int] | None:
        """Квадратный кадрирующий прямоугольник: пересечение холста с bbox."""
        scale = TILE_SIZE * (2**self.zoom)
        min_lon, min_lat, max_lon, max_lat = self.bbox
        x0, y1 = _mercator(min_lon, min_lat)
        x1, y0 = _mercator(max_lon, max_lat)
        span_w = (x1 - x0) * scale
        span_h = (y1 - y0) * scale
        side = math.floor(min(self.width, self.height, span_w, span_h))
        if side >= min(self.width, self.height):
            return None
        left = round((self.width - side) / 2)
        top = round((self.height - side) / 2)
        return (left, top, left + side, top + side)

    def project(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = _mercator(lon, lat)
        scale = TILE_SIZE * (2**self.zoom)
        return (x * scale - self.origin_x, y * scale - self.origin_y)

    def meters_per_pixel(self) -> float:
        return 156543.03392 * math.cos(math.radians(self.center_lat)) / (2**self.zoom)


def _default_fetch_tile(z: int, x: int, y: int, timeout: float) -> bytes | None:
    request = urllib.request.Request(
        TILE_URL_TEMPLATE.format(z=z, x=x, y=y),
        headers={"User-Agent": TILE_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            return response.read()
    except OSError:
        return None


def _load_tile(
    z: int,
    x: int,
    y: int,
    *,
    cache_dir: Path | None,
    fetch_tile: Callable[[int, int, int, float], bytes | None],
    timeout: float,
) -> Any:
    from PIL import Image

    cache_path = None
    if cache_dir is not None:
        cache_path = cache_dir / str(z) / str(x) / f"{y}.png"
        if cache_path.exists():
            try:
                image = Image.open(cache_path)
                image.load()
                return image.convert("RGB")
            except OSError:
                cache_path.unlink(missing_ok=True)
    payload = fetch_tile(z, x, y, timeout)
    if payload is None:
        return None
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(payload)
    import io

    try:
        image = Image.open(io.BytesIO(payload))
        image.load()
    except OSError:
        return None
    return image.convert("RGB")


def _compose_basemap(
    viewport: _Viewport,
    *,
    cache_dir: Path | None,
    fetch_tile: Callable[[int, int, int, float], bytes | None],
    timeout: float,
) -> tuple[Any, int, int]:
    from PIL import Image

    image = Image.new("RGB", (viewport.width, viewport.height), MISSING_TILE_COLOR)
    x_min = math.floor(viewport.origin_x / TILE_SIZE)
    y_min = math.floor(viewport.origin_y / TILE_SIZE)
    x_max = math.floor((viewport.origin_x + viewport.width) / TILE_SIZE)
    y_max = math.floor((viewport.origin_y + viewport.height) / TILE_SIZE)
    limit = 2**viewport.zoom
    requested = fetched = 0
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            if not (0 <= y < limit):
                continue
            requested += 1
            tile = _load_tile(
                viewport.zoom,
                x % limit,
                y,
                cache_dir=cache_dir,
                fetch_tile=fetch_tile,
                timeout=timeout,
            )
            if tile is None:
                continue
            fetched += 1
            image.paste(
                tile,
                (
                    int(x * TILE_SIZE - viewport.origin_x),
                    int(y * TILE_SIZE - viewport.origin_y),
                ),
            )
    return image, requested, fetched


def _ring_pixels(
    viewport: _Viewport, ring: list[list[float]]
) -> list[tuple[float, float]]:
    points = []
    for point in ring:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            points.append(viewport.project(float(point[0]), float(point[1])))
    return points


def _draw_geometry(
    overlay_draw: Any,
    viewport: _Viewport,
    geometry: dict[str, Any],
    fill: tuple[int, int, int, int],
    outline: tuple[int, int, int],
) -> None:
    gtype = geometry.get("type")
    polygons: list[Any] = []
    coords = geometry.get("coordinates")
    if not isinstance(coords, (list, tuple)):
        return
    if gtype == "Polygon":
        polygons = [coords]
    elif gtype == "MultiPolygon":
        polygons = [p for p in coords if isinstance(p, (list, tuple))]
    for polygon in polygons:
        if not polygon:
            continue
        exterior = _ring_pixels(viewport, polygon[0])
        if len(exterior) < 3:
            continue
        holes = [
            _ring_pixels(viewport, ring)
            for ring in polygon[1:]
            if isinstance(ring, (list, tuple))
        ]
        overlay_draw.polygon(exterior, fill=fill)
        for hole in holes:
            if len(hole) >= 3:
                overlay_draw.polygon(hole, fill=(0, 0, 0, 0))
        overlay_draw.line([*exterior, exterior[0]], fill=(*outline, 255), width=2)


def _draw_parcel(
    base: Any,
    viewport: _Viewport,
    geometry: dict[str, Any],
) -> None:
    """Штриховка участка + контрастный контур (красный с белым гало)."""
    from PIL import Image, ImageDraw

    ui = base.size[0] / 1000
    mask = Image.new("L", base.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    polygons: list[Any] = []
    if isinstance(coords, (list, tuple)):
        if gtype == "Polygon":
            polygons = [coords]
        elif gtype == "MultiPolygon":
            polygons = [p for p in coords if isinstance(p, (list, tuple))]
    outline_draw = ImageDraw.Draw(base)
    outlines: list[list[tuple[float, float]]] = []
    for polygon in polygons:
        if not polygon:
            continue
        exterior = _ring_pixels(viewport, polygon[0])
        if len(exterior) < 3:
            continue
        mask_draw.polygon(exterior, fill=255)
        for ring in polygon[1:]:
            if isinstance(ring, (list, tuple)):
                hole = _ring_pixels(viewport, ring)
                if len(hole) >= 3:
                    mask_draw.polygon(hole, fill=0)
        outlines.append(exterior)
    hatch = Image.new("RGBA", base.size, (0, 0, 0, 0))
    hatch_draw = ImageDraw.Draw(hatch)
    step = max(6, round(9 * ui))
    width, height = base.size
    for offset in range(-height, width, step):
        hatch_draw.line(
            [(offset, 0), (offset + height, height)],
            fill=_PARCEL_HATCH,
            width=max(1, round(ui)),
        )
    hatch_mask = Image.composite(hatch.split()[3], Image.new("L", base.size, 0), mask)
    base.paste(hatch, (0, 0), hatch_mask)
    halo_w = max(4, round(5 * ui))
    line_w = max(2, round(3 * ui))
    for exterior in outlines:
        ring = [*exterior, exterior[0]]
        outline_draw.line(ring, fill=(255, 255, 255, 255), width=halo_w + line_w)
        outline_draw.line(ring, fill=(*_PARCEL_OUTLINE, 255), width=line_w)


def _draw_parcel_label(
    base: Any,
    viewport: _Viewport,
    geometry: dict[str, Any],
    label: str,
) -> None:
    """Подпись участка (кадастровый номер и площадь) над верхней гранью."""
    from PIL import ImageDraw

    ui = base.size[0] / 1000
    outlines = [_ring_pixels(viewport, ring) for ring in _iter_ring_points(geometry)]
    exteriors = [ring for ring in outlines if len(ring) >= 3]
    if not exteriors:
        return
    top_ring = min(exteriors, key=lambda ring: min(y for _, y in ring))
    top_y = min(y for _, y in top_ring)
    center_x = sum(x for x, _ in top_ring) / len(top_ring)

    draw = ImageDraw.Draw(base)
    font = _load_font(max(14, round(20 * ui)))
    text_box = draw.textbbox((0, 0), label, font=font)
    text_w = text_box[2] - text_box[0]
    text_h = text_box[3] - text_box[1]
    pad = round(8 * ui)
    box_w = text_w + 2 * pad
    box_h = text_h + 2 * pad
    width = base.size[0]
    box_x = min(max(center_x - box_w / 2, 4.0), width - box_w - 4.0)
    box_y = top_y - box_h - round(10 * ui)
    if box_y < 4:  # граница у верхнего края — подпись внутрь участка
        box_y = top_y + round(10 * ui)
    draw.rounded_rectangle(
        [box_x, box_y, box_x + box_w, box_y + box_h],
        radius=round(6 * ui),
        fill=(255, 255, 255, 235),
        outline=(198, 40, 40, 255),
        width=max(1, round(2 * ui)),
    )
    draw.text(
        (box_x + pad - text_box[0], box_y + pad - text_box[1]),
        label,
        font=font,
        fill=(60, 30, 30),
    )


def _load_font(size: int) -> Any:
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


_NICE_SCALE_STEPS = (1, 2, 5)


def _nice_scale_length(meters: float) -> float:
    power = 10 ** math.floor(math.log10(max(meters, 1)))
    for step in _NICE_SCALE_STEPS:
        if step * power >= meters:
            return step * power
    return 10 * power


def _format_meters(meters: float) -> str:
    if meters >= 1000:
        value = meters / 1000
        return f"{value:g} км"
    return f"{meters:g} м"


def _draw_furniture(
    base: Any, viewport: _Viewport, entries: list[tuple[str, Any]]
) -> None:
    """Стрелка севера, масштабная линейка, легенда, атрибуция OSM.

    Все размеры масштабируются от ширины холста: при 1800 px подписи
    остаются читаемыми при печати карты на листе A4 (~250 dpi).
    """
    from PIL import ImageDraw

    draw = ImageDraw.Draw(base)
    width, height = base.size
    ui = width / 1000
    font = _load_font(max(12, round(18 * ui)))
    font_small = _load_font(max(10, round(14 * ui)))

    # стрелка севера (карта всегда севером вверх — Web-Mercator)
    arrow_x, arrow_y = width - round(42 * ui), round(18 * ui)
    arrow_half = round(9 * ui)
    arrow_h = round(26 * ui)
    draw.polygon(
        [
            (arrow_x, arrow_y),
            (arrow_x - arrow_half, arrow_y + arrow_h),
            (arrow_x + arrow_half, arrow_y + arrow_h),
        ],
        fill=(40, 40, 40, 255),
    )
    draw.text(
        (arrow_x, arrow_y + arrow_h + round(12 * ui)),
        "N",
        font=font,
        fill=(40, 40, 40),
        anchor="mm",
    )

    # масштабная линейка (слева внизу, над атрибуцией)
    mpp = viewport.meters_per_pixel()
    target_m = mpp * 110 * ui
    length_m = _nice_scale_length(target_m)
    length_px = length_m / mpp
    bar_x, bar_y = round(18 * ui), height - round(34 * ui)
    draw.line(
        [(bar_x, bar_y), (bar_x + length_px, bar_y)],
        fill=(30, 30, 30),
        width=max(2, round(3 * ui)),
    )
    for tick_x in (bar_x, bar_x + length_px / 2, bar_x + length_px):
        draw.line(
            [(tick_x, bar_y - round(5 * ui)), (tick_x, bar_y)],
            fill=(30, 30, 30),
            width=max(1, round(2 * ui)),
        )
    draw.text(
        (bar_x + length_px / 2, bar_y - round(10 * ui)),
        _format_meters(length_m),
        font=font_small,
        fill=(30, 30, 30),
        anchor="mb",
    )

    # атрибуция подложки (обязательна по лицензии OSM)
    draw.text(
        (width - round(8 * ui), height - round(8 * ui)),
        "© OpenStreetMap contributors",
        font=font_small,
        fill=(70, 70, 70),
        anchor="rs",
    )

    if not entries:
        return
    # легенда справа внизу, над атрибуцией
    sample_w, sample_h = round(26 * ui), round(14 * ui)
    line_h, pad = round(24 * ui), round(9 * ui)
    label_widths = []
    for label, _ in entries:
        box = draw.textbbox((0, 0), label, font=font_small)
        label_widths.append(box[2] - box[0])
    box_w = sample_w + round(8 * ui) + max(label_widths) + 2 * pad
    box_h = len(entries) * line_h + 2 * pad
    box_x = width - box_w - round(10 * ui)
    box_y = height - box_h - round(28 * ui)
    draw.rectangle(
        [box_x, box_y, box_x + box_w, box_y + box_h],
        fill=(255, 255, 255, 255),
        outline=(120, 120, 120),
        width=max(1, round(ui)),
    )
    for index, (label, sample) in enumerate(entries):
        row_y = box_y + pad + index * line_h
        sample_x = box_x + pad
        sample_y = row_y + (line_h - sample_h) // 2
        sample(draw, sample_x, sample_y, sample_w, sample_h)
        draw.text(
            (sample_x + sample_w + round(8 * ui), row_y + line_h // 2),
            label,
            font=font_small,
            fill=(30, 30, 30),
            anchor="lm",
        )


def _parcel_sample(draw: Any, x: int, y: int, w: int, h: int) -> None:
    draw.rectangle([x, y, x + w, y + h], outline=(*_PARCEL_OUTLINE, 255), width=2)
    for diag in range(0, w + h, 5):
        x0, y0 = x + max(0, diag - h), y + min(diag, h)
        x1, y1 = x + min(diag, w), y + max(0, diag - w)
        draw.line([(x0, y0), (x1, y1)], fill=(*_PARCEL_OUTLINE, 255), width=1)


def render_overview_map(
    parcel_geometry: dict[str, Any],
    layers: list[MapLayer],
    out_path: Path,
    *,
    cache_dir: Path | None = None,
    size: tuple[int, int] = (1800, 1800),
    fetch_tile: Callable[[int, int, int, float], bytes | None] | None = None,
    timeout: float = 10.0,
    parcel_label: str | None = None,
) -> MapImageResult:
    """Рисует квадратную PNG-карту: подложка OSM + зоны + участок.

    Кадр: bbox участка приводится к квадрату по длинной стороне и
    увеличивается вдвое от центра — участок всегда по центру изображения,
    а итоговая картинка гарантированно лежит внутри этого кадра
    (излишек срезается кадрированием после подбора целочисленного zoom).
    Зоны, выходящие за кадр, просто обрезаются его границами.
    Карта всегда севером вверх (подложка Web-Mercator). ``parcel_label``
    (кадастровый номер, площадь) подписывается над верхней гранью участка.

    ``fetch_tile(z, x, y, timeout) -> bytes | None`` подменяется в тестах;
    ``None`` у тайла означает «нет подложки» — карта строится на
    нейтральном фоне, в результат уходит предупреждение.
    """
    warnings: list[str] = []
    fetcher = fetch_tile or _default_fetch_tile
    layers = select_visible_layers(layers, parcel_geometry)
    parcel_bbox = _geometry_bbox(parcel_geometry)
    if parcel_bbox is None:
        raise ValueError("parcel geometry has no coordinates")
    viewport = _Viewport(_square_frame(parcel_bbox), *size)

    base, requested, fetched = _compose_basemap(
        viewport, cache_dir=cache_dir, fetch_tile=fetcher, timeout=timeout
    )
    basemap_available = requested > 0 and fetched == requested
    if fetched < requested:
        warnings.append(
            f"подложка OSM загружена частично: {fetched}/{requested} тайлов"
        )
    base = base.convert("RGBA")

    from PIL import Image, ImageDraw

    # ImageDraw на RGBA-холсте заменяет пиксели вместе с альфой, поэтому
    # заливки рисуются на прозрачном оверлее и применяются через composite
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    drawn_labels: list[str] = []
    for layer in layers:
        _draw_geometry(
            overlay_draw, viewport, layer.geometry, layer.fill, layer.outline
        )
        if layer.label not in drawn_labels:
            drawn_labels.append(layer.label)
    base = Image.alpha_composite(base, overlay)

    _draw_parcel(base, viewport, parcel_geometry)

    crop = viewport.crop_box()
    if crop is not None:
        base = base.crop(crop)
        # проекция сдвигается вместе с кадрированием
        viewport.origin_x += crop[0]
        viewport.origin_y += crop[1]
        viewport.width = crop[2] - crop[0]
        viewport.height = crop[3] - crop[1]

    if parcel_label:
        _draw_parcel_label(base, viewport, parcel_geometry, parcel_label)

    entries: list[tuple[str, Any]] = [(_PARCEL_LABEL, _parcel_sample)]
    for layer in layers:
        if layer.label not in [label for label, _ in entries]:
            fill = layer.fill
            outline = layer.outline
            # образец в легенде — заливка, смешанная с белым (как на карте)
            alpha = fill[3] / 255
            blended = tuple(
                round(channel * alpha + 255 * (1 - alpha)) for channel in fill[:3]
            )

            def _sample(
                draw: Any,
                x: int,
                y: int,
                w: int,
                h: int,
                fill: tuple = blended,
                outline: tuple = outline,
            ) -> None:
                draw.rectangle([x, y, x + w, y + h], fill=fill, outline=(*outline, 255))

            entries.append((layer.label, _sample))
    _draw_furniture(base, viewport, entries)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out_path, format="PNG")
    return MapImageResult(
        path=out_path,
        basemap_available=basemap_available,
        layer_labels=drawn_labels,
        warnings=warnings,
    )
