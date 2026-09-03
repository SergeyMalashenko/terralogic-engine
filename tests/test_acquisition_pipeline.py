from __future__ import annotations

import json
import math

from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.analytics.pipeline import AnalysisPipeline
from terralogic_engine.domain.models import CollectionRequest
from terralogic_engine.reporting.context import build_report_context
from terralogic_engine.store.local import LocalCaseStore
from terralogic_engine.viewer.data import load_receipt_features

from .fakes import FakeDgisClient, FakeNspdClient, FakeOsmClient, FakeRgisClient


async def test_pipeline_collects_sources_and_passes_exact_contour_to_osm(
    tmp_path,
) -> None:
    store = LocalCaseStore(tmp_path / "store")
    nspd = FakeNspdClient()
    osm = FakeOsmClient()
    dgis = FakeDgisClient()
    pipeline = AcquisitionPipeline(store=store, nspd=nspd, osm=osm, dgis=dgis)
    request = CollectionRequest(
        case_id="case-complete",
        cadastral_number="52:26:0040002:3823",
        refresh_policy="always",
    )

    receipt = await pipeline.collect(request)

    assert receipt.status == "complete"
    assert receipt.nspd_snapshot_id is not None
    assert receipt.osm_snapshot_id is not None
    assert receipt.dgis_snapshot_id is not None
    assert receipt.aoi_id is not None
    assert receipt.feature_counts == {
        "dgis.education": 1,
        "dgis.public_transport_stops": 1,
        "nspd.parcel": 1,
        "nspd.restriction_zone": 1,
        "osm.forest": 1,
        "osm.lake": 1,
        "osm.river": 1,
        "osm.road": 1,
        "osm.stream": 1,
    }
    assert nspd.info_calls == 1
    assert nspd.layer_calls == 1
    assert nspd.layer_arguments["blocks"] == ["zouit"]
    assert nspd.layer_arguments["include_geometry"] is True
    assert osm.calls == 1
    assert osm.arguments["geometry"]["type"] == "Polygon"
    assert osm.arguments["source_crs"] == "EPSG:4326"
    assert osm.arguments["margin_m"] == 1000
    assert osm.arguments["blocks"] == [
        "forests",
        "lakes",
        "rivers",
        "streams",
        "roads",
    ]
    assert osm.arguments["include_geometry"] is True
    assert dgis.social_calls == 1
    assert dgis.transport_calls == 1
    assert dgis.social_arguments["radius_m"] > 1000
    assert dgis.social_arguments["radius_m"] == dgis.transport_arguments["radius_m"]
    assert dgis.social_arguments["latitude"] == dgis.transport_arguments["latitude"]
    assert dgis.social_arguments["longitude"] == dgis.transport_arguments["longitude"]

    snapshots = store.list_snapshots(request.case_id)
    assert {snapshot.source for snapshot in snapshots} == {"nspd", "osm", "dgis"}
    raw_nspd = json.loads(
        store.load_snapshot(request.case_id, receipt.nspd_snapshot_id)
    )
    assert set(raw_nspd) == {"restriction_analysis", "parcel_info"}
    features = store.load_features(request.case_id)
    assert len(features) == 9
    assert all(feature.geometry is not None for feature in features)
    forest = next(feature for feature in features if feature.feature_class == "forest")
    assert len(forest.geometry["coordinates"]) == 2
    aoi = store.get_area_of_interest(request.case_id, receipt.aoi_id)
    assert aoi.query_geometry != aoi.parcel_geometry
    assert aoi.margin_m == 1000
    assert aoi.search_radius_m == aoi.parcel_minimum_radius_m + 1000


async def test_if_stale_reuses_latest_receipt_without_source_calls(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    nspd = FakeNspdClient()
    osm = FakeOsmClient()
    dgis = FakeDgisClient()
    pipeline = AcquisitionPipeline(store=store, nspd=nspd, osm=osm, dgis=dgis)
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
    assert dgis.social_calls == 1
    assert dgis.transport_calls == 1


async def test_osm_failure_preserves_nspd_as_partial_result(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    nspd = FakeNspdClient()
    osm = FakeOsmClient(failure=TimeoutError("Overpass timed out"))
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=nspd,
        osm=osm,
        dgis=FakeDgisClient(),
    )

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
    assert receipt.dgis_snapshot_id is not None
    assert any("Overpass timed out" in error for error in receipt.errors)
    assert len(store.load_features("case-partial", source="nspd")) == 2
    assert store.load_features("case-partial", source="osm") == []


async def test_strict_collection_marks_source_failure_as_failed(tmp_path) -> None:
    pipeline = AcquisitionPipeline(
        store=LocalCaseStore(tmp_path / "store"),
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(failure=TimeoutError("Overpass timed out")),
        dgis=FakeDgisClient(),
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


async def test_margin_override_is_shared_by_osm_and_dgis(tmp_path) -> None:
    osm = FakeOsmClient()
    dgis = FakeDgisClient()
    pipeline = AcquisitionPipeline(
        store=LocalCaseStore(tmp_path / "store"),
        nspd=FakeNspdClient(),
        osm=osm,
        dgis=dgis,
    )

    receipt = await pipeline.collect(
        CollectionRequest(
            case_id="case-margin",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
            margin_m=2500,
        )
    )

    assert receipt.status == "complete"
    assert osm.arguments["margin_m"] == 2500
    aoi = pipeline.store.get_area_of_interest("case-margin", receipt.aoi_id)
    assert aoi.margin_m == 2500
    assert dgis.social_arguments["radius_m"] == math.ceil(aoi.search_radius_m)


async def test_if_stale_does_not_reuse_a_different_margin(tmp_path) -> None:
    nspd = FakeNspdClient()
    osm = FakeOsmClient()
    dgis = FakeDgisClient()
    pipeline = AcquisitionPipeline(
        store=LocalCaseStore(tmp_path / "store"),
        nspd=nspd,
        osm=osm,
        dgis=dgis,
    )

    first = await pipeline.collect(
        CollectionRequest(
            case_id="case-margin-refresh",
            cadastral_number="52:26:0040002:3823",
            margin_m=1000,
        )
    )
    second = await pipeline.collect(
        CollectionRequest(
            case_id="case-margin-refresh",
            cadastral_number="52:26:0040002:3823",
            margin_m=2000,
        )
    )

    assert first.run_id != second.run_id
    assert second.reused is False
    assert nspd.info_calls == 2
    assert osm.calls == 2
    assert dgis.social_calls == 2


async def test_pipeline_collects_rgis_source(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    rgis = FakeRgisClient()
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis=rgis,
    )
    request = CollectionRequest(
        case_id="case-rgis",
        cadastral_number="50:32:0000000:38218",
        refresh_policy="always",
    )

    receipt = await pipeline.collect(request)

    assert receipt.status == "complete"
    assert receipt.rgis_snapshot_id is not None
    assert receipt.feature_counts["rgis.restriction_zone"] == 1
    assert receipt.feature_counts["rgis.territorial_zone"] == 1
    assert rgis.info_calls == 1
    assert rgis.layer_calls == 1
    assert rgis.info_arguments["detail"] == "full"
    assert rgis.layer_arguments["include_geometry"] is True

    features = load_receipt_features(store, receipt)
    assert {item.source for item in features} == {"nspd", "osm", "dgis", "rgis"}
    analysis = AnalysisPipeline(store=store).analyze("case-rgis", run_id=receipt.run_id)
    assert {item.source for item in analysis.zouit_intersections} == {"nspd", "rgis"}
    context = build_report_context(store, "case-rgis", collection_run_id=receipt.run_id)
    assert {item.source for item in context.sources} == {"nspd", "osm", "dgis", "rgis"}
    assert {item.source for item in context.zouit} == {"nspd", "rgis"}

    receipt2 = await pipeline.collect(
        CollectionRequest(
            case_id="case-rgis",
            cadastral_number="50:32:0000000:38218",
            refresh_policy="never",
        )
    )
    assert receipt2.reused is True
    assert receipt2.rgis_snapshot_id == receipt.rgis_snapshot_id


async def test_pipeline_rgis_not_applicable_is_not_an_error(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    rgis = FakeRgisClient(
        info_result={
            "ok": True,
            "data": {"applicable": False, "reason": "parcel not found"},
            "error": None,
            "metadata": {"adapter_version": "pyrgis-agents-test"},
        },
        layer_result={
            "ok": True,
            "data": {"applicable": False, "blocks": {}},
            "error": None,
            "metadata": {"adapter_version": "pyrgis-agents-test"},
        },
    )
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis=rgis,
    )
    receipt = await pipeline.collect(
        CollectionRequest(
            case_id="case-rgis-na",
            cadastral_number="50:32:0000000:38218",
            refresh_policy="always",
        )
    )

    assert receipt.status == "complete"
    assert receipt.rgis_snapshot_id is not None
    assert "rgis.restriction_zone" not in receipt.feature_counts


async def test_pipeline_does_not_call_rgis_outside_region_50(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    rgis = FakeRgisClient()
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis=rgis,
    )

    receipt = await pipeline.collect(
        CollectionRequest(
            case_id="case-rgis-region-gate",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
        )
    )

    assert receipt.status == "complete"
    assert receipt.rgis_snapshot_id is None
    assert rgis.info_calls == 0
    assert rgis.layer_calls == 0


async def test_pipeline_preserves_rgis_tool_error_as_partial_result(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    rgis = FakeRgisClient(
        info_result={
            "ok": False,
            "data": None,
            "error": {
                "code": "access_blocked",
                "message": "RGIS access is blocked",
                "retryable": True,
            },
            "metadata": {"adapter_version": "pyrgis-agents-test"},
        }
    )
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis=rgis,
    )

    receipt = await pipeline.collect(
        CollectionRequest(
            case_id="case-rgis-error",
            cadastral_number="50:32:0000000:38218",
            refresh_policy="always",
        )
    )

    assert receipt.status == "partial"
    assert receipt.rgis_snapshot_id is not None
    assert any("access_blocked: RGIS access is blocked" in item for item in receipt.errors)
    raw = json.loads(store.load_snapshot(receipt.case_id, receipt.rgis_snapshot_id))
    assert raw["parcel_info"]["error"]["code"] == "access_blocked"
    assert receipt.feature_counts["rgis.restriction_zone"] == 1


async def test_reuse_is_invalidated_when_rgis_configuration_changes(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    request = CollectionRequest(
        case_id="case-rgis-config",
        cadastral_number="50:32:0000000:38218",
    )
    first = await AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    ).collect(request)
    rgis = FakeRgisClient()

    second = await AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis=rgis,
    ).collect(request)

    assert second.reused is False
    assert second.run_id != first.run_id
    assert second.rgis_snapshot_id is not None
    assert rgis.info_calls == 1


async def test_pipeline_without_rgis_keeps_old_behaviour(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    )
    receipt = await pipeline.collect(
        CollectionRequest(
            case_id="case-no-rgis",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
        )
    )

    assert receipt.status == "complete"
    assert receipt.rgis_snapshot_id is None
    assert not any(k.startswith("rgis.") for k in receipt.feature_counts)
