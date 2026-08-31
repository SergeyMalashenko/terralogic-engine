"""Streamlit application for inspecting a local CaseStore."""

from __future__ import annotations

import argparse
import sys
from html import escape
from pathlib import Path
from typing import Any

import folium
import streamlit as st
from branca.element import Element
from folium.plugins import Fullscreen
from shapely.geometry import shape
from streamlit_folium import st_folium

from terralogic_acquisition.domain.models import CollectionReceipt, GeoFeature
from terralogic_acquisition.store.local import LocalCaseStore
from terralogic_acquisition.viewer.data import (
    ROAD_CLASS_LABELS,
    build_feature_collection,
    dgis_map_label,
    feature_label,
    feature_table_rows,
    group_features,
    group_road_features,
    load_receipt_features,
    natural_contour_summary,
    natural_intersection_detail_rows,
    natural_intersection_rows,
    natural_nearest_rows,
    osm_road_map_label,
    road_class_summary,
    source_summary_rows,
    social_nearest_rows,
    zouit_analysis_rows,
)

SOURCE_COLORS = {
    "nspd": "#7c3aed",
    "osm": "#047857",
    "dgis": "#2563eb",
}
FEATURE_CLASS_LABELS = {
    "parcel": "Земельный участок",
    "restriction_zone": "Ограничения ЗОУИТ",
    "forest": "Леса",
    "lake": "Водоёмы",
    "river": "Реки",
    "stream": "Ручьи",
    "road": "Дороги",
}
CLASS_STYLES: dict[str, dict[str, Any]] = {
    "parcel": {
        "color": "#dc2626",
        "fillColor": "#dc2626",
        "weight": 3,
        "fillOpacity": 0.08,
    },
    "restriction_zone": {
        "color": "#7e22ce",
        "fillColor": "#a855f7",
        "weight": 2,
        "fillOpacity": 0.28,
    },
    "forest": {
        "color": "#166534",
        "fillColor": "#22c55e",
        "weight": 2,
        "fillOpacity": 0.38,
        "fillRule": "evenodd",
    },
    "lake": {
        "color": "#1d4ed8",
        "fillColor": "#60a5fa",
        "weight": 2,
        "fillOpacity": 0.46,
        "fillRule": "evenodd",
    },
    "river": {
        "color": "#0e7490",
        "fillColor": "#22d3ee",
        "weight": 2,
        "fillOpacity": 0.46,
        "fillRule": "evenodd",
    },
    "stream": {
        "color": "#0284c7",
        "weight": 4,
        "fillOpacity": 0,
        "dashArray": "7 5",
    },
}
ROAD_CLASS_STYLES: dict[str, dict[str, Any]] = {
    "motorway": {"color": "#991b1b", "weight": 7, "fillOpacity": 0},
    "trunk": {"color": "#dc2626", "weight": 7, "fillOpacity": 0},
    "primary": {"color": "#ea580c", "weight": 6, "fillOpacity": 0},
    "secondary": {"color": "#f59e0b", "weight": 6, "fillOpacity": 0},
    "tertiary": {"color": "#eab308", "weight": 5, "fillOpacity": 0},
    "unclassified": {"color": "#64748b", "weight": 4, "fillOpacity": 0},
    "residential": {"color": "#2563eb", "weight": 4, "fillOpacity": 0},
    "living_street": {"color": "#7c3aed", "weight": 4, "fillOpacity": 0},
    "service": {"color": "#6b7280", "weight": 3, "fillOpacity": 0},
    "track": {
        "color": "#92400e",
        "weight": 3,
        "fillOpacity": 0,
        "dashArray": "8 5",
    },
    "unknown": {
        "color": "#111827",
        "weight": 3,
        "fillOpacity": 0,
        "dashArray": "3 5",
    },
}


def _store_path() -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--store", default="./case-store")
    args, _ = parser.parse_known_args(sys.argv[1:])
    return Path(args.store).expanduser().resolve()


def _receipt_label(receipt: CollectionReceipt) -> str:
    started = receipt.started_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return f"{started} · {receipt.status} · {receipt.run_id[:8]}"


def _feature_option(feature: GeoFeature) -> str:
    return f"{feature_label(feature)} [{feature.source} · {feature.feature_class}]"


def _add_reference_geometry(
    map_object: folium.Map,
    *,
    name: str,
    geometry: dict[str, Any],
    color: str,
    fill_opacity: float,
    dash_array: str | None = None,
) -> None:
    style = {
        "color": color,
        "weight": 3,
        "fillColor": color,
        "fillOpacity": fill_opacity,
    }
    if dash_array is not None:
        style["dashArray"] = dash_array
    folium.GeoJson(
        {"type": "Feature", "geometry": geometry, "properties": {}},
        name=name,
        style_function=lambda _feature, style=style: style,
    ).add_to(map_object)


def _add_geojson_feature_layer(
    map_object: folium.Map,
    *,
    name: str,
    values: list[GeoFeature],
    style: dict[str, Any],
    show_road_class: bool = False,
    show_waterbody_type: bool = False,
    show_dgis_labels: bool = False,
) -> None:
    collection = build_feature_collection(values)
    geometry_count = len(collection["features"])
    if geometry_count == 0:
        return
    color = str(style["color"])
    layer = folium.FeatureGroup(
        name=f"{name} ({geometry_count})",
        show=True,
    )
    tooltip_fields = ["label", "source", "feature_class"]
    tooltip_aliases = ["Объект", "Источник", "Класс объекта"]
    if show_road_class:
        tooltip_fields.extend(
            ["road_reference", "road_class_label", "road_class"]
        )
        tooltip_aliases.extend(
            ["Номер дороги", "Класс дороги", "Тег highway"]
        )
    if show_waterbody_type:
        tooltip_fields.extend(["waterbody_type_label", "waterbody_type"])
        tooltip_aliases.extend(["Тип водоёма", "Тег water"])
    tooltip_fields.extend(
        [
            "category",
            "geometry_type",
            "interior_rings",
            "distance_m",
            "relation",
        ]
    )
    tooltip_aliases.extend(
        [
            "Категория",
            "Геометрия",
            "Внутренних контуров",
            "Расстояние, м",
            "Отношение к участку",
        ]
    )
    folium.GeoJson(
        collection,
        style_function=lambda _feature, style=style: dict(style),
        highlight_function=lambda _feature, style=style: {
            **style,
            "weight": int(style.get("weight", 2)) + 2,
            "fillOpacity": min(
                0.65,
                float(style.get("fillOpacity", 0)) + 0.12,
            ),
        },
        marker=folium.CircleMarker(
            radius=5,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.8,
        ),
        tooltip=folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=tooltip_aliases,
            localize=True,
        ),
    ).add_to(layer)
    if show_road_class:
        for feature in values:
            road_label = osm_road_map_label(feature)
            if road_label is None or feature.geometry is None:
                continue
            try:
                label_point = shape(feature.geometry).representative_point()
            except (KeyError, TypeError, ValueError):
                continue
            safe_label = escape(road_label)
            label_html = (
                "<div style='display:inline-block;transform:translateX(-50%);"
                "white-space:nowrap;background:rgba(255,255,255,.92);"
                f"border:2px solid {color};border-radius:4px;padding:1px 4px;"
                "color:#111827;font-size:11px;font-weight:700;line-height:14px;"
                "box-shadow:0 1px 2px rgba(0,0,0,.25);'>"
                f"{safe_label}</div>"
            )
            folium.Marker(
                location=[float(label_point.y), float(label_point.x)],
                icon=folium.DivIcon(
                    html=label_html,
                    icon_size=(0, 0),
                    icon_anchor=(0, 0),
                    class_name="terralogic-road-reference",
                ),
                tooltip=f"Дорога: {safe_label}",
            ).add_to(layer)
    if show_dgis_labels:
        for feature in values:
            map_label = dgis_map_label(feature)
            if map_label is None or feature.geometry is None:
                continue
            try:
                label_point = shape(feature.geometry).representative_point()
            except (KeyError, TypeError, ValueError):
                continue
            safe_label = escape(map_label)
            category = feature.properties.get("category_name")
            safe_category = escape(str(category)) if category else "Объект 2GIS"
            label_html = (
                "<div title='"
                f"{safe_category}' style='display:inline-block;"
                "transform:translate(8px,-50%);white-space:nowrap;"
                "max-width:220px;overflow:hidden;text-overflow:ellipsis;"
                "background:rgba(255,255,255,.94);border:1px solid #2563eb;"
                "border-radius:4px;padding:2px 5px;color:#1e3a8a;"
                "font-size:11px;font-weight:600;line-height:15px;"
                "box-shadow:0 1px 2px rgba(0,0,0,.2);'>"
                f"{safe_label}</div>"
            )
            folium.Marker(
                location=[float(label_point.y), float(label_point.x)],
                icon=folium.DivIcon(
                    html=label_html,
                    icon_size=(0, 0),
                    icon_anchor=(0, 0),
                    class_name="terralogic-dgis-label",
                ),
                tooltip=f"2GIS: {safe_label}",
            ).add_to(layer)
    layer.add_to(map_object)


def _add_feature_layers(
    map_object: folium.Map,
    features: list[GeoFeature],
    *,
    show_dgis_labels: bool,
) -> None:
    for (source, feature_class), values in group_features(features).items():
        if source == "osm" and feature_class == "road":
            for road_class, road_values in group_road_features(values).items():
                _add_geojson_feature_layer(
                    map_object,
                    name=(
                        "OSM · Дороги · "
                        f"{ROAD_CLASS_LABELS[road_class]} [{road_class}]"
                    ),
                    values=road_values,
                    style=ROAD_CLASS_STYLES[road_class],
                    show_road_class=True,
                )
            continue
        fallback_color = SOURCE_COLORS.get(source, "#334155")
        style = CLASS_STYLES.get(
            feature_class,
            {
                "color": fallback_color,
                "fillColor": fallback_color,
                "weight": 2,
                "fillOpacity": 0.3,
            },
        )
        class_label = FEATURE_CLASS_LABELS.get(feature_class, feature_class)
        _add_geojson_feature_layer(
            map_object,
            name=f"{source.upper()} · {class_label}",
            values=values,
            style=style,
            show_waterbody_type=(source == "osm" and feature_class == "lake"),
            show_dgis_labels=(source == "dgis" and show_dgis_labels),
        )


def _add_map_legend(
    map_object: folium.Map,
    features: list[GeoFeature],
) -> None:
    visible_classes = {feature.feature_class for feature in features}
    entries: list[tuple[str, str, bool]] = [
        ("Участок", "#dc2626", False),
        ("Область анализа", "#d97706", True),
    ]
    for feature_class in (
        "restriction_zone",
        "forest",
        "lake",
        "river",
        "stream",
    ):
        if feature_class not in visible_classes:
            continue
        style = CLASS_STYLES[feature_class]
        entries.append(
            (
                FEATURE_CLASS_LABELS[feature_class],
                str(style["color"]),
                feature_class == "stream",
            )
        )
    for row in road_class_summary(features):
        road_class = str(row["road_class"])
        entries.append(
            (
                f"Дороги · {row['label']}",
                str(ROAD_CLASS_STYLES[road_class]["color"]),
                True,
            )
        )
    if any(feature.source == "dgis" for feature in features):
        entries.append(("Объекты 2GIS", SOURCE_COLORS["dgis"], False))

    rows = []
    for label, color, is_line in entries:
        symbol_style = (
            f"border-top:4px solid {color};height:0;margin-top:8px;"
            if is_line
            else f"background:{color};height:12px;border:1px solid {color};"
        )
        rows.append(
            "<div style='display:flex;align-items:center;gap:8px;margin:4px 0;'>"
            f"<span style='display:inline-block;width:22px;{symbol_style}'></span>"
            f"<span>{label}</span></div>"
        )
    legend = (
        "<div style='position:fixed;bottom:28px;left:28px;z-index:9999;"
        "background:rgba(255,255,255,.94);border:1px solid #cbd5e1;"
        "border-radius:6px;padding:10px 12px;font-size:13px;"
        "max-height:55vh;overflow-y:auto;"
        "box-shadow:0 1px 4px rgba(0,0,0,.25);'>"
        "<strong>Слои карты</strong>"
        + "".join(rows)
        + "</div>"
    )
    map_object.get_root().html.add_child(Element(legend))


def _render_natural_contour_summary(features: list[GeoFeature]) -> None:
    summary = natural_contour_summary(features)
    columns = st.columns(3)
    for column, row in zip(columns, summary, strict=True):
        column.metric(
            str(row["label"]),
            int(row["contours"]),
            help=(
                "Количество Polygon/MultiPolygon GeoJSON, доступных для "
                "отрисовки"
            ),
        )
    missing = sum(int(row["objects"]) - int(row["contours"]) for row in summary)
    holes = sum(int(row["interior_rings"]) for row in summary)
    st.caption(
        f"Внутренних контуров сохранено: {holes}. "
        "Они отображаются как вырезы в полигонах."
    )
    if missing:
        st.warning(
            f"Для {missing} природных объектов отсутствует "
            "полигональная геометрия."
        )


def _render_road_class_summary(features: list[GeoFeature]) -> None:
    summary = road_class_summary(features)
    if not summary:
        return
    with st.expander("Классы дорог OSM", expanded=False):
        st.dataframe(
            [
                {
                    "Класс": row["label"],
                    "highway": row["road_class"],
                    "Объектов": row["objects"],
                    "С геометрией": row["with_geometry"],
                    "С номером": row["with_reference"],
                    "Цвет": ROAD_CLASS_STYLES[str(row["road_class"])]["color"],
                }
                for row in summary
            ],
            use_container_width=True,
            hide_index=True,
        )


def _render_map(
    store: LocalCaseStore,
    receipt: CollectionReceipt,
    features: list[GeoFeature],
    *,
    show_dgis_labels: bool,
) -> None:
    if receipt.aoi_id is None:
        st.warning(
            "Для выбранного запуска не сохранена "
            "область анализа."
        )
        return
    aoi = store.get_area_of_interest(receipt.case_id, receipt.aoi_id)
    _render_natural_contour_summary(features)
    _render_road_class_summary(features)
    longitude, latitude = aoi.representative_point
    map_object = folium.Map(
        location=[latitude, longitude],
        zoom_start=14,
        control_scale=True,
        tiles="OpenStreetMap",
    )
    Fullscreen(
        position="topleft",
        title="Развернуть карту",
        title_cancel="Выйти из полноэкранного режима",
        force_separate_button=True,
    ).add_to(map_object)

    _add_reference_geometry(
        map_object,
        name="Контур земельного участка",
        geometry=aoi.parcel_geometry,
        color="#dc2626",
        fill_opacity=0.08,
    )
    _add_reference_geometry(
        map_object,
        name="Область анализа OSM и 2GIS",
        geometry=aoi.query_geometry,
        color="#d97706",
        fill_opacity=0.03,
        dash_array="8 6",
    )
    _add_feature_layers(
        map_object,
        features,
        show_dgis_labels=show_dgis_labels,
    )
    _add_map_legend(map_object, features)
    min_x, min_y, max_x, max_y = shape(aoi.query_geometry).bounds
    map_object.fit_bounds([[min_y, min_x], [max_y, max_x]])
    folium.LayerControl(collapsed=False).add_to(map_object)
    st.caption(
        "Минимальный радиус участка: "
        f"{aoi.parcel_minimum_radius_m:,.1f} м · "
        f"отступ: {aoi.margin_m:,} м · "
        f"радиус анализа: {aoi.search_radius_m:,.1f} м"
    )
    st_folium(
        map_object,
        height=650,
        use_container_width=True,
        returned_objects=[],
    )


def _snapshot_rows(
    store: LocalCaseStore,
    receipt: CollectionReceipt,
) -> list[dict[str, Any]]:
    selected_ids = {
        value
        for value in (
            receipt.nspd_snapshot_id,
            receipt.osm_snapshot_id,
            receipt.dgis_snapshot_id,
        )
        if value is not None
    }
    return [
        {
            "source": snapshot.source,
            "snapshot_id": snapshot.id,
            "retrieved_at": snapshot.retrieved_at,
            "adapter_version": snapshot.adapter_version,
            "sha256": snapshot.content_sha256,
            "raw_file": snapshot.relative_path,
        }
        for snapshot in store.list_snapshots(receipt.case_id)
        if snapshot.id in selected_ids
    ]


def _render_analytics(
    store: LocalCaseStore,
    receipt: CollectionReceipt,
) -> None:
    result = store.get_analysis_result(receipt.case_id, receipt.run_id)
    if result is None:
        st.info(
            "Для выбранного запуска аналитика ещё не рассчитана. "
            "Выполните: "
            f"`terralogic-analyze {receipt.case_id} --run-id {receipt.run_id} "
            "--store <путь-к-case-store>`"
        )
        return

    area_column, radius_column, version_column = st.columns(3)
    area_column.metric(
        "Геометрическая площадь участка",
        f"{result.parcel_area_m2:,.1f} м²",
    )
    radius_column.metric(
        "Радиус поиска",
        f"{result.search_radius_m:,.1f} м",
    )
    version_column.metric("Версия аналитики", result.analytics_version)
    st.caption(
        "Расстояния рассчитаны от контура участка. Площади классов "
        "получены после объединения их полигонов, поэтому перекрытия "
        "не считаются дважды."
    )
    if result.warnings:
        st.warning("\n".join(result.warnings))

    st.subheader("1. Пересечение с ЗОУИТ")
    zone_summary = result.zouit_summary
    zone_columns = st.columns(3)
    zone_columns[0].metric(
        "Зон в области поиска",
        zone_summary.candidate_count,
    )
    zone_columns[1].metric(
        "Пересекают участок",
        zone_summary.intersecting_count,
    )
    zone_columns[2].metric(
        "Покрытие участка",
        f"{zone_summary.parcel_coverage_percent or 0:,.4f} %",
        help=(
            "Доля объединённой площади всех пересечений "
            "без двойного счёта"
        ),
    )
    zone_rows = zouit_analysis_rows(result)
    if zone_rows:
        st.dataframe(zone_rows, use_container_width=True, hide_index=True)
    else:
        st.info(
            "Зоны ЗОУИТ в выбранном снимке не найдены."
        )

    st.subheader(
        "2. Пересечение с лесами и водными ресурсами"
    )
    st.dataframe(
        natural_intersection_rows(result),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "Для лесов, озёр/водоёмов и рек рассчитывается площадь. "
        "Для линейных ручьёв рассчитывается длина внутри "
        "участка."
    )
    natural_detail_rows = natural_intersection_detail_rows(result)
    if natural_detail_rows:
        with st.expander("Детализация по природным объектам"):
            st.dataframe(
                natural_detail_rows,
                use_container_width=True,
                hide_index=True,
            )

    st.subheader("3. Ближайшая социальная инфраструктура")
    st.dataframe(
        social_nearest_rows(result),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("4. Ближайшие леса и водные ресурсы")
    st.dataframe(
        natural_nearest_rows(result),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "Результат «не найден» означает только отсутствие объекта "
        "в собранных данных внутри текущей окружности поиска."
    )


def run() -> None:
    st.set_page_config(
        page_title="TerraLogic Case Explorer",
        page_icon="🗺️",
        layout="wide",
    )
    st.title("TerraLogic Case Explorer")
    store_path = _store_path()
    if not (store_path / "cases").is_dir():
        st.error(f"CaseStore не найден: {store_path}")
        st.stop()

    store = LocalCaseStore(store_path)
    cases = store.list_cases()
    if not cases:
        st.info(f"В CaseStore пока нет дел: {store_path}")
        st.stop()

    st.sidebar.header("Выбор данных")
    case_by_id = {case.case_id: case for case in cases}
    selected_case_id = st.sidebar.selectbox(
        "Дело",
        options=list(case_by_id),
        format_func=lambda value: (
            f"{case_by_id[value].cadastral_number} · {value}"
        ),
    )
    selected_case = case_by_id[selected_case_id]
    receipts = store.list_collection_receipts(selected_case_id)
    if not receipts:
        st.warning(
            "В выбранном деле нет завершённых "
            "запусков сбора."
        )
        st.stop()
    receipt = st.sidebar.selectbox(
        "Запуск",
        options=receipts,
        format_func=_receipt_label,
    )

    all_features = load_receipt_features(store, receipt)
    sources = sorted({feature.source for feature in all_features})
    selected_sources = st.sidebar.multiselect(
        "Источники",
        options=sources,
        default=sources,
    )
    classes = sorted(
        {
            feature.feature_class
            for feature in all_features
            if feature.source in selected_sources
        }
    )
    selected_classes = st.sidebar.multiselect(
        "Классы объектов",
        options=classes,
        default=classes,
    )
    show_dgis_labels = st.sidebar.checkbox(
        "Подписи объектов 2GIS",
        value=True,
        help="Показывать постоянные текстовые подписи рядом с точками 2GIS",
        disabled="dgis" not in selected_sources,
    )
    visible_features = [
        feature
        for feature in all_features
        if feature.source in selected_sources
        and feature.feature_class in selected_classes
    ]

    case_column, run_column, feature_column, source_column = st.columns(4)
    case_column.metric(
        "Кадастровый номер",
        selected_case.cadastral_number,
    )
    run_column.metric("Статус запуска", receipt.status)
    feature_column.metric("Объектов", len(visible_features))
    source_column.metric("Источников", len(selected_sources))

    if receipt.warnings:
        st.warning("\n".join(receipt.warnings))
    if receipt.errors:
        st.error("\n".join(receipt.errors))

    (
        summary_tab,
        analytics_tab,
        map_tab,
        objects_tab,
        provenance_tab,
        history_tab,
    ) = st.tabs(
        [
            "Сводка",
            "Аналитика",
            "Карта",
            "Объекты",
            "Источники",
            "История",
        ]
    )
    with summary_tab:
        st.subheader("Собранные данные")
        st.dataframe(
            source_summary_rows(visible_features),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "НСПД: участок и ограничения · OSM: леса, водоёмы, реки, "
            "ручьи и дороги · 2GIS: социальная и транспортная "
            "инфраструктура"
        )

    with analytics_tab:
        _render_analytics(store, receipt)

    with map_tab:
        _render_map(
            store,
            receipt,
            visible_features,
            show_dgis_labels=show_dgis_labels,
        )

    with objects_tab:
        st.dataframe(
            feature_table_rows(visible_features),
            use_container_width=True,
            hide_index=True,
        )
        if visible_features:
            selected_feature = st.selectbox(
                "Атрибуты объекта",
                options=visible_features,
                format_func=_feature_option,
            )
            st.json(
                {
                    "id": selected_feature.id,
                    "source": selected_feature.source,
                    "feature_class": selected_feature.feature_class,
                    "source_type": selected_feature.source_type,
                    "source_id": selected_feature.source_id,
                    "crs": selected_feature.crs,
                    "properties": selected_feature.properties,
                }
            )

    with provenance_tab:
        st.dataframe(
            _snapshot_rows(store, receipt),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(f"CaseStore: {store_path}")

    with history_tab:
        st.dataframe(
            [
                {
                    "run_id": item.run_id,
                    "status": item.status,
                    "started_at": item.started_at,
                    "completed_at": item.completed_at,
                    "features": sum(item.feature_counts.values()),
                    "warnings": len(item.warnings),
                    "errors": len(item.errors),
                    "reused": item.reused,
                }
                for item in receipts
            ],
            use_container_width=True,
            hide_index=True,
        )


run()
