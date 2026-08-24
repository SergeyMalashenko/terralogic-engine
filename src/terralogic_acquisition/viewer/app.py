"""Streamlit application for inspecting a local CaseStore."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import folium
import streamlit as st
from shapely.geometry import shape
from streamlit_folium import st_folium

from terralogic_acquisition.domain.models import CollectionReceipt, GeoFeature
from terralogic_acquisition.store.local import LocalCaseStore
from terralogic_acquisition.viewer.data import (
    build_feature_collection,
    feature_label,
    feature_table_rows,
    group_features,
    load_receipt_features,
)

SOURCE_COLORS = {"nspd": "#7c3aed", "osm": "#047857"}


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


def _add_feature_layers(
    map_object: folium.Map,
    features: list[GeoFeature],
) -> None:
    for (source, feature_class), values in group_features(features).items():
        collection = build_feature_collection(values)
        geometry_count = len(collection["features"])
        if geometry_count == 0:
            continue
        color = SOURCE_COLORS.get(source, "#334155")
        layer = folium.FeatureGroup(
            name=f"{source.upper()} · {feature_class} ({geometry_count})",
            show=True,
        )
        folium.GeoJson(
            collection,
            style_function=lambda _feature, color=color: {
                "color": color,
                "weight": 2,
                "fillColor": color,
                "fillOpacity": 0.25,
            },
            marker=folium.CircleMarker(
                radius=5,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
            ),
            tooltip=folium.GeoJsonTooltip(
                fields=["label", "source", "feature_class"],
                aliases=["Объект", "Источник", "Класс"],
                localize=True,
            ),
        ).add_to(layer)
        layer.add_to(map_object)


def _render_map(
    store: LocalCaseStore,
    receipt: CollectionReceipt,
    features: list[GeoFeature],
) -> None:
    if receipt.aoi_id is None:
        st.warning(
            "Для выбранного запуска не сохранена "
            "область анализа."
        )
        return
    aoi = store.get_area_of_interest(receipt.case_id, receipt.aoi_id)
    longitude, latitude = aoi.representative_point
    map_object = folium.Map(
        location=[latitude, longitude],
        zoom_start=14,
        control_scale=True,
        tiles="OpenStreetMap",
    )

    _add_reference_geometry(
        map_object,
        name="Контур земельного участка",
        geometry=aoi.parcel_geometry,
        color="#dc2626",
        fill_opacity=0.08,
    )
    _add_reference_geometry(
        map_object,
        name="Область поиска OSM",
        geometry=aoi.query_geometry,
        color="#d97706",
        fill_opacity=0.03,
        dash_array="8 6",
    )
    _add_feature_layers(map_object, features)
    min_x, min_y, max_x, max_y = shape(aoi.query_geometry).bounds
    map_object.fit_bounds([[min_y, min_x], [max_y, max_x]])
    folium.LayerControl(collapsed=False).add_to(map_object)
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
        for value in (receipt.nspd_snapshot_id, receipt.osm_snapshot_id)
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
    visible_features = [
        feature
        for feature in all_features
        if feature.source in selected_sources
        and feature.feature_class in selected_classes
    ]

    case_column, run_column, feature_column = st.columns(3)
    case_column.metric(
        "Кадастровый номер",
        selected_case.cadastral_number,
    )
    run_column.metric("Статус запуска", receipt.status)
    feature_column.metric("Объектов", len(visible_features))

    if receipt.warnings:
        st.warning("\n".join(receipt.warnings))
    if receipt.errors:
        st.error("\n".join(receipt.errors))

    map_tab, objects_tab, provenance_tab, history_tab = st.tabs(
        ["Карта", "Объекты", "Источники", "История"]
    )
    with map_tab:
        _render_map(store, receipt, visible_features)

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
