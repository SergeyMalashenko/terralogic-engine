"""Геометрическая проверка размещения тестового контура дома (методика 6.1, п. 4.4).

Из полигона участка вычитаются применимые отступы (буфер внутрь), в полученной
области ищется размещение контрольного квадрата 12 × 12 м и тестового
10 × 10 м (перенос + поворот). Поиск детерминирован: сетка с фиксированным
шагом по bbox области, углы — 0°, 90° и ориентация минимального повёрнутого
прямоугольника участка (±90°); при отрицательном результате — подтверждающий
проход с мелкой сеткой и дополнительными углами 45°/135°. Полное геометрическое
включение проверяется ``covers``; библиотека — shapely.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import shapely
from pyproj import Transformer
from shapely import affinity
from shapely.geometry import box, shape
from shapely.ops import transform as shapely_transform

LOGGER = logging.getLogger(__name__)

_MAX_CANDIDATES = 120_000  # предохранитель сетки: шаг растёт на больших участках


@dataclass(frozen=True)
class ConfigurationOutcome:
    """Результат геометрической проверки: C и клиентская формулировка."""

    c: int  # 0 — с резервом, 5 — впритык, 10 — не размещается
    detail: str  # протокол: отступ, сетка, углы, результат


def _orientation_angles(polygon: Any) -> tuple[float, float]:
    """Углы ориентации минимального повёрнутого прямоугольника (0 и +90°)."""
    try:
        rect = polygon.minimum_rotated_rectangle
        coords = list(rect.exterior.coords)
        if len(coords) < 2:
            return 0.0, 90.0
        dx = coords[1][0] - coords[0][0]
        dy = coords[1][1] - coords[0][1]
        angle = math.degrees(math.atan2(dy, dx)) % 180.0
        return angle, (angle + 90.0) % 180.0
    except Exception:  # noqa: BLE001 — degenerate геометрия: только осевые углы
        return 0.0, 90.0


def _fits(region: Any, side: float, angles: tuple[float, ...], step: float) -> bool:
    """Квадрат side×side размещается в region: сетка переносов × углы поворота."""
    if region.is_empty:
        return False
    min_x, min_y, max_x, max_y = region.bounds
    span_x = max_x - min_x
    span_y = max_y - min_y
    if span_x <= 0 or span_y <= 0:
        return False
    # шаг растёт, чтобы число кандидатов не превышало предохранитель
    while (span_x / step + 1) * (span_y / step + 1) * len(angles) > _MAX_CANDIDATES:
        step *= 2.0
    half = side / 2.0
    # кроме сетки — позиции с выравниванием по границам/центру области
    # (точное вписывание «контур в контур» сетка может не накрыть)
    anchors_x = {min_x + half, max_x - half, (min_x + max_x) / 2.0}
    anchors_y = {min_y + half, max_y - half, (min_y + max_y) / 2.0}
    xs = sorted(anchors_x | {min_x + step / 2.0 + k * step for k in range(int(span_x / step) + 2)})
    ys = sorted(anchors_y | {min_y + step / 2.0 + k * step for k in range(int(span_y / step) + 2)})
    for y in ys:
        for x in xs:
            if x - half < min_x or x + half > max_x or y - half < min_y or y + half > max_y:
                continue
            cell = box(x - half, y - half, x + half, y + half)
            if region.covers(cell):
                return True
            for angle in angles:
                if (
                    angle % 180.0
                    and region.covers(affinity.rotate(cell, angle, origin=(x, y)))
                ):
                    return True
    return False


def evaluate_configuration(
    parcel_geometry: dict[str, Any],
    margin_m: float,
    *,
    source_crs: str = "EPSG:4326",
    metric_crs: str,
    test_m: float,
    control_m: float,
    grid_step_m: float,
    fine_grid_step_m: float,
    tight_c: int,
    no_fit_c: int,
) -> ConfigurationOutcome | None:
    """C по методике 4.4; None — контур недостоверен, тест не выполнен.

    Геометрия хранится в source_crs (EPSG:4326); отступы и тестовые контуры
    метрические — перед проверкой контур переводится в metric_crs кейса.
    """
    try:
        polygon = shape(parcel_geometry)
    except Exception:  # noqa: BLE001 — битый GeoJSON
        return None
    if polygon.is_empty or polygon.area <= 0:
        return None
    if not polygon.is_valid:
        polygon = polygon.buffer(0)  # геометрическое исправление фиксируется в detail
    if polygon.is_empty or polygon.area <= 0:
        return None
    try:
        transformer = Transformer.from_crs(source_crs, metric_crs, always_xy=True)
        polygon = shapely_transform(transformer.transform, polygon)
    except Exception:  # noqa: BLE001 — без проекции метрический тест невозможен
        return None
    base_angles = _orientation_angles(polygon)
    region = polygon.buffer(-margin_m)
    detail = (
        f"отступ {margin_m:g} м; сетка {grid_step_m:g} м; углы "
        f"0/90/{base_angles[0]:.1f}°; shapely {shapely.__version__}"
    )
    if region.is_empty:
        return ConfigurationOutcome(c=no_fit_c, detail=f"{detail}; область пуста")
    # контрольный контур с резервом
    if _fits(region, control_m, (0.0, 90.0, *base_angles), grid_step_m):
        return ConfigurationOutcome(c=0, detail=f"{detail}; 12×12 размещается")
    # тестовый контур
    if _fits(region, test_m, (0.0, 90.0, *base_angles), grid_step_m):
        return ConfigurationOutcome(c=tight_c, detail=f"{detail}; 10×10 размещается")
    # подтверждающий поиск мелкой сеткой с диагональными углами
    fine_angles = (0.0, 45.0, 90.0, 135.0, *base_angles)
    if _fits(region, test_m, fine_angles, fine_grid_step_m):
        return ConfigurationOutcome(
            c=tight_c,
            detail=f"{detail}; 10×10 размещается (мелкая сетка {fine_grid_step_m:g} м)",
        )
    return ConfigurationOutcome(
        c=no_fit_c,
        detail=f"{detail}; 10×10 не размещается (подтверждено мелкой сеткой)",
    )
