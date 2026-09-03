from __future__ import annotations

from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.analytics.pipeline import AnalysisPipeline
from terralogic_engine.domain.models import CollectionRequest
from terralogic_engine.store.local import LocalCaseStore

from .fakes import FakeDgisClient, FakeNspdClient, FakeOsmClient


async def test_analytics_calculates_and_persists_spatial_metrics(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    receipt = await AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    ).collect(
        CollectionRequest(
            case_id="case-analytics",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
        )
    )

    result = AnalysisPipeline(store=store).analyze("case-analytics")

    assert result.collection_run_id == receipt.run_id
    assert result.parcel_area_m2 > 0
    assert result.zouit_summary.candidate_count == 1
    assert result.zouit_summary.intersecting_count == 1
    assert result.zouit_summary.union_intersection_area_m2 > 0
    assert result.zouit_summary.parcel_coverage_percent > 0
    assert result.zouit_intersections[0].relation == "object_inside_parcel"

    summaries = {item.key: item for item in result.natural_summaries}
    assert summaries["forest"].union_intersection_area_m2 == 0
    assert summaries["lake"].union_intersection_area_m2 == 0
    assert summaries["river"].union_intersection_area_m2 == 0
    assert summaries["water_resources"].union_intersection_area_m2 == 0
    assert summaries["stream"].union_intersection_area_m2 is None
    assert summaries["stream"].union_intersection_length_m > 0

    social = {item.category: item for item in result.social_nearest}
    assert social["education"].status == "found"
    assert social["education"].distance_m == 0
    assert social["healthcare"].status == "not_found_within_aoi"
    assert "public_transport_stops" not in social

    natural = {item.category: item for item in result.natural_nearest}
    assert natural["forest"].distance_m > 0
    assert natural["stream"].distance_m == 0
    assert natural["water_resources"].distance_m == 0

    assert store.get_analysis_result("case-analytics", receipt.run_id) == result
    rerun = AnalysisPipeline(store=store).analyze(
        "case-analytics", run_id=receipt.run_id
    )
    assert rerun.id == result.id
    assert len(store.list_analysis_results("case-analytics")) == 1
