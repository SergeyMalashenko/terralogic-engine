from __future__ import annotations

import json
import math

from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.analytics.pipeline import AnalysisPipeline
from terralogic_engine.domain.models import CollectionRequest
from terralogic_engine.reporting.context import build_report_context
from terralogic_engine.store.local import LocalCaseStore
from terralogic_engine.viewer.data import load_receipt_features

from .fakes import (
    FakeDgisClient,
    FakeNspdClient,
    FakeNspdDocumentsClient,
    FakeOsmClient,
    FakeRgisClient,
    FakeRgisDocumentsClient,
    rgis_document_vri_result,
    rgis_documents_sync_result,
)


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
    assert receipt.feature_counts["rgis.territorial_zone"] == 2
    assert receipt.feature_counts["rgis.gpzu"] == 1
    assert receipt.feature_counts["rgis.planning_projects"] == 1
    assert receipt.feature_counts["rgis.surveying_projects"] == 1
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
    assert context.context_version == "1.3"
    assert context.urban_planning.collected is True
    assert context.urban_planning.provider_completeness_known is False
    assert context.urban_planning.parcel_zones[0].zone == "СХ-3"
    assert context.urban_planning.parcel_zones[0].permitted_uses[0].code == "1.1"
    assert context.urban_planning.gpzu[0].name == "РФ-50-TEST-001 04.03.2021"
    assert context.urban_planning.gpzu[0].relation == "intersects"
    assert context.urban_planning.pzz_territorial_zones[0].name == "СХ-3"
    assert context.urban_planning.planning_projects[0].name == "ППТ-TEST"
    assert context.urban_planning.surveying_projects[0].name == "ПМТ-TEST"
    assert '"coordinates"' not in context.model_dump_json()

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
    assert any(
        "access_blocked: RGIS access is blocked" in item for item in receipt.errors
    )
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


async def test_pipeline_collects_document_contour(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    rgis_documents = FakeRgisDocumentsClient()
    nspd_documents = FakeNspdDocumentsClient()
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis=FakeRgisClient(),
        rgis_documents=rgis_documents,
        nspd_documents=nspd_documents,
    )
    request = CollectionRequest(
        case_id="case-documents",
        cadastral_number="50:32:0000000:38218",
        refresh_policy="always",
    )

    receipt = await pipeline.collect(request)

    assert receipt.status == "complete"
    assert rgis_documents.sync_calls == 1
    assert rgis_documents.vri_calls == 1
    assert rgis_documents.fetch_calls == 0
    assert nspd_documents.sync_calls == 1
    assert nspd_documents.regimes_calls == 1

    snapshots = store.list_snapshots(request.case_id)
    document_snapshots = [
        snapshot
        for snapshot in snapshots
        if str(snapshot.metadata.get("snapshot_type") or "").startswith("documents")
    ]
    assert {snapshot.source for snapshot in document_snapshots} == {"rgis", "nspd"}
    assert {
        (snapshot.source, str(snapshot.metadata.get("snapshot_type")))
        for snapshot in document_snapshots
    } == {
        ("rgis", "documents_sync"),
        ("rgis", "documents_vri"),
        ("nspd", "documents_sync"),
        ("nspd", "documents_regimes"),
    }
    for snapshot in document_snapshots:
        raw_file = (
            tmp_path / "store" / "cases" / request.case_id / snapshot.relative_path
        )
        assert raw_file.is_file()
        payload = json.loads(store.load_snapshot(request.case_id, snapshot.id))
        assert payload["ok"] is True

    facts = store.list_facts(request.case_id)
    assert {fact.fact_type for fact in facts} == {
        "document_vri",
        "zouit_regime",
    }
    vri_fact = next(fact for fact in facts if fact.fact_type == "document_vri")
    assert vri_fact.value["zone_code"] == "218020020006"
    assert vri_fact.value["doc_number"] == "ПЗЗ-Т-592"
    assert vri_fact.value["version_date"] == "2021-04-03"
    assert vri_fact.value["source_file"] == "pzz-592.docx"
    assert len(vri_fact.value["items"]) == 2
    regime_fact = next(fact for fact in facts if fact.fact_type == "zouit_regime")
    assert regime_fact.value["registry_number"] == "50:32-6.1"
    assert regime_fact.value["document_number"] == "Постановление № 111"
    assert regime_fact.value["restrictions"] == "Запрет строительства"

    analysis = AnalysisPipeline(store=store).analyze(
        request.case_id, run_id=receipt.run_id
    )
    context = build_report_context(
        store, request.case_id, collection_run_id=receipt.run_id
    )
    assert context.analysis_id == analysis.id
    assert context.context_version == "1.3"
    assert context.documents is not None
    assert context.documents.pzz is not None
    assert context.documents.pzz.number == "ПЗЗ-Т-592"
    assert context.documents.pzz.files == ["pzz-592.docx"]
    assert [zone.zone_code for zone in context.documents.pzz.zones] == [
        "218020020006"
    ]
    assert context.documents.pzz.zones[0].found is True
    assert context.documents.pzz.zones[0].items[0].code == "1.1"
    assert [plan.number for plan in context.documents.general_plans] == ["ГП-Т-15"]
    assert context.documents.general_plans[0].status == "downloaded"
    assert [regime.name for regime in context.documents.zouit_regimes] == [
        "Тестовая охранная зона"
    ]
    assert context.documents.zouit_regimes[0].document_number == "Постановление № 111"
    assert context.documents.sources == [
        snapshot.id for snapshot in document_snapshots
    ]
    assert context.documents.partial is False
    assert '"coordinates"' not in context.model_dump_json()


async def test_pipeline_without_documents_clients_keeps_old_behaviour(
    tmp_path,
) -> None:
    store = LocalCaseStore(tmp_path / "store")
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis=FakeRgisClient(),
    )
    request = CollectionRequest(
        case_id="case-no-documents",
        cadastral_number="50:32:0000000:38218",
        refresh_policy="always",
    )

    receipt = await pipeline.collect(request)

    assert receipt.status == "complete"
    assert receipt.rgis_snapshot_id is not None
    assert store.list_facts(request.case_id) == []
    snapshots = store.list_snapshots(request.case_id)
    assert not any(
        str(snapshot.metadata.get("snapshot_type") or "").startswith("documents")
        for snapshot in snapshots
    )
    AnalysisPipeline(store=store).analyze(request.case_id, run_id=receipt.run_id)
    context = build_report_context(
        store, request.case_id, collection_run_id=receipt.run_id
    )
    assert context.documents is None


async def test_pipeline_document_tool_error_preserves_other_sources(
    tmp_path,
) -> None:
    store = LocalCaseStore(tmp_path / "store")
    rgis_documents = FakeRgisDocumentsClient(
        vri_failure=TimeoutError("geodocs store timed out")
    )
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis=FakeRgisClient(),
        rgis_documents=rgis_documents,
        nspd_documents=FakeNspdDocumentsClient(),
    )
    request = CollectionRequest(
        case_id="case-documents-error",
        cadastral_number="50:32:0000000:38218",
        refresh_policy="always",
    )

    receipt = await pipeline.collect(request)

    assert receipt.status == "partial"
    assert receipt.nspd_snapshot_id is not None
    assert receipt.osm_snapshot_id is not None
    assert receipt.dgis_snapshot_id is not None
    assert receipt.rgis_snapshot_id is not None
    assert any(
        "rgis_get_document_vri" in error and "geodocs store timed out" in error
        for error in receipt.errors
    )
    snapshots = store.list_snapshots(request.case_id, source="rgis")
    snapshot_types = {
        str(snapshot.metadata.get("snapshot_type"))
        for snapshot in snapshots
    }
    assert "documents_sync" in snapshot_types
    assert "documents_vri" not in snapshot_types
    facts = store.list_facts(request.case_id)
    assert {fact.fact_type for fact in facts} == {"zouit_regime"}


async def test_pipeline_nspd_document_tool_error_still_saves_sync_snapshot(
    tmp_path,
) -> None:
    store = LocalCaseStore(tmp_path / "store")
    nspd_documents = FakeNspdDocumentsClient(
        regimes_failure=TimeoutError("regime extraction timed out")
    )
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        nspd_documents=nspd_documents,
    )
    request = CollectionRequest(
        case_id="case-nspd-documents-error",
        cadastral_number="52:26:0040002:3823",
        refresh_policy="always",
    )

    receipt = await pipeline.collect(request)

    assert receipt.status == "partial"
    assert receipt.osm_snapshot_id is not None
    assert any(
        "nspd_get_zouit_regimes" in error for error in receipt.errors
    )
    snapshots = store.list_snapshots(request.case_id, source="nspd")
    snapshot_types = {
        str(snapshot.metadata.get("snapshot_type"))
        for snapshot in snapshots
    }
    assert "documents_sync" in snapshot_types
    assert "documents_regimes" not in snapshot_types
    assert store.list_facts(request.case_id) == []


async def test_pipeline_external_document_source_skips_vri_with_warning(
    tmp_path,
) -> None:
    store = LocalCaseStore(tmp_path / "store")
    sync_with_missing_pzz = rgis_documents_sync_result()
    pzz_document = sync_with_missing_pzz["data"]["documents"][0]
    pzz_document["status"] = "not_found"
    pzz_document["files"] = []
    pzz_document["sources"] = ["https://docs.cntd.ru/document/123456"]
    rgis_documents = FakeRgisDocumentsClient(sync_result=sync_with_missing_pzz)
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis=FakeRgisClient(),
        rgis_documents=rgis_documents,
    )
    request = CollectionRequest(
        case_id="case-external-document",
        cadastral_number="50:32:0000000:38218",
        refresh_policy="always",
    )

    receipt = await pipeline.collect(request)

    assert receipt.status == "partial"
    assert any(
        "external document source not configured" in warning
        for warning in receipt.warnings
    )
    assert rgis_documents.vri_calls == 0
    assert rgis_documents.fetch_calls == 0
    snapshots = store.list_snapshots(request.case_id, source="rgis")
    snapshot_types = {
        str(snapshot.metadata.get("snapshot_type"))
        for snapshot in snapshots
    }
    assert "documents_sync" in snapshot_types
    assert "documents_vri" not in snapshot_types


async def test_pipeline_documents_not_applicable_outside_region_50(
    tmp_path,
) -> None:
    store = LocalCaseStore(tmp_path / "store")
    rgis_documents = FakeRgisDocumentsClient()
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis_documents=rgis_documents,
        nspd_documents=FakeNspdDocumentsClient(),
    )
    request = CollectionRequest(
        case_id="case-documents-region-gate",
        cadastral_number="52:26:0040002:3823",
        refresh_policy="always",
    )

    receipt = await pipeline.collect(request)

    assert receipt.status == "complete"
    assert rgis_documents.sync_calls == 0
    facts = store.list_facts(request.case_id)
    assert {fact.fact_type for fact in facts} == {"zouit_regime"}


async def test_pipeline_documents_partial_flag_marks_receipt_partial(
    tmp_path,
) -> None:
    store = LocalCaseStore(tmp_path / "store")
    vri_partial = rgis_document_vri_result()
    vri_partial["data"]["partial"] = True
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis=FakeRgisClient(),
        rgis_documents=FakeRgisDocumentsClient(vri_result=vri_partial),
        nspd_documents=FakeNspdDocumentsClient(),
    )
    request = CollectionRequest(
        case_id="case-documents-partial",
        cadastral_number="50:32:0000000:38218",
        refresh_policy="always",
    )

    receipt = await pipeline.collect(request)

    assert receipt.status == "partial"
    assert any(
        "RGIS document VRI extraction is partial" in warning
        for warning in receipt.warnings
    )
    analysis = AnalysisPipeline(store=store).analyze(
        request.case_id, run_id=receipt.run_id
    )
    context = build_report_context(
        store, request.case_id, collection_run_id=receipt.run_id
    )
    assert analysis.id is not None
    assert context.documents is not None
    assert context.documents.partial is True


async def test_reuse_is_invalidated_when_documents_configuration_changes(
    tmp_path,
) -> None:
    store = LocalCaseStore(tmp_path / "store")
    request = CollectionRequest(
        case_id="case-documents-config",
        cadastral_number="50:32:0000000:38218",
    )
    first = await AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis=FakeRgisClient(),
    ).collect(request)
    assert first.status == "complete"
    assert store.list_facts(request.case_id) == []

    rgis_documents = FakeRgisDocumentsClient()
    second = await AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
        rgis=FakeRgisClient(),
        rgis_documents=rgis_documents,
        nspd_documents=FakeNspdDocumentsClient(),
    ).collect(request)

    assert second.reused is False
    assert second.run_id != first.run_id
    assert rgis_documents.sync_calls == 1
    assert {fact.fact_type for fact in store.list_facts(request.case_id)} == {
        "document_vri",
        "zouit_regime",
    }
