from __future__ import annotations

import json

from terralogic_acquisition.acquisition.pipeline import AcquisitionPipeline
from terralogic_acquisition.domain.models import CollectionRequest
from terralogic_acquisition.store.local import LocalCaseStore

from .fakes import FakeNspdClient, FakeOsmClient


async def test_pipeline_collects_sources_and_passes_exact_contour_to_osm(
    tmp_path,
) -> None:
    store = LocalCaseStore(tmp_path / "store")
    nspd = FakeNspdClient()
    osm = FakeOsmClient()
    pipeline = AcquisitionPipeline(store=store, nspd=nspd, osm=osm)
    request = CollectionRequest(
        case_id="case-complete",
        cadastral_number="52:26:0040002:3823",
        refresh_policy="always",
    )

    receipt = await pipeline.collect(request)

    assert receipt.status == "complete"
    assert receipt.nspd_snapshot_id is not None
    assert receipt.osm_snapshot_id is not None
    assert receipt.aoi_id is not None
    assert receipt.feature_counts == {
        "nspd.parcel": 1,
        "nspd.restriction_zone": 1,
        "osm.building": 1,
    }
    assert nspd.info_calls == 1
    assert nspd.layer_calls == 1
    assert nspd.layer_arguments["include_geometry"] is True
    assert osm.calls == 1
    assert osm.arguments["geometry"]["type"] == "Polygon"
    assert osm.arguments["source_crs"] == "EPSG:4326"
    assert osm.arguments["margin_m"] == 1000
    assert osm.arguments["include_geometry"] is True

    snapshots = store.list_snapshots(request.case_id)
    assert {snapshot.source for snapshot in snapshots} == {"nspd", "osm"}
    raw_nspd = json.loads(
        store.load_snapshot(request.case_id, receipt.nspd_snapshot_id)
    )
    assert set(raw_nspd) == {"layer_analysis", "parcel_info"}
    features = store.load_features(request.case_id)
    assert len(features) == 3
    assert all(feature.geometry is not None for feature in features)
    aoi = store.get_area_of_interest(request.case_id, receipt.aoi_id)
    assert aoi.query_geometry != aoi.parcel_geometry


async def test_if_stale_reuses_latest_receipt_without_source_calls(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    nspd = FakeNspdClient()
    osm = FakeOsmClient()
    pipeline = AcquisitionPipeline(store=store, nspd=nspd, osm=osm)
    request = CollectionRequest(
        case_id="case-reused",
        cadastral_number="52:26:0040002:3823",
    )

    first = await pipeline.collect(request)
    second = await pipeline.collect(request)

    assert first.status == "complete"
    assert second.run_id == first.run_id
    assert second.reused is True
    assert nspd.info_calls == 1
    assert nspd.layer_calls == 1
    assert osm.calls == 1


async def test_osm_failure_preserves_nspd_as_partial_result(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    nspd = FakeNspdClient()
    osm = FakeOsmClient(failure=TimeoutError("Overpass timed out"))
    pipeline = AcquisitionPipeline(store=store, nspd=nspd, osm=osm)

    receipt = await pipeline.collect(
        CollectionRequest(
            case_id="case-partial",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
            allow_partial=True,
        )
    )

    assert receipt.status == "partial"
    assert receipt.nspd_snapshot_id is not None
    assert receipt.osm_snapshot_id is None
    assert any("Overpass timed out" in error for error in receipt.errors)
    assert len(store.load_features("case-partial", source="nspd")) == 2
    assert store.load_features("case-partial", source="osm") == []


async def test_strict_collection_marks_source_failure_as_failed(tmp_path) -> None:
    pipeline = AcquisitionPipeline(
        store=LocalCaseStore(tmp_path / "store"),
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(failure=TimeoutError("Overpass timed out")),
    )

    receipt = await pipeline.collect(
        CollectionRequest(
            case_id="case-strict",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
            allow_partial=False,
        )
    )

    assert receipt.status == "failed"
    assert receipt.nspd_snapshot_id is not None
    assert receipt.aoi_id is not None
