"""События прогресса контура prepare_case → prepare_quickreport.

Контракт ``ProgressCallback`` (percent 0–100, stage): pipeline шлёт
события сбора, ReportingService масштабирует фазы, quickreport
отмечает свои этапы. MCP-слой транслирует их клиенту; бот показывает
только проценты, stage — внутренняя метка.
"""

from __future__ import annotations

import io

from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.domain.models import CollectionRequest
from terralogic_engine.progress import report_progress, scaled_progress
from terralogic_engine.reporting.service import ReportingService
from terralogic_engine.store.local import LocalCaseStore

from .fakes import FakeDgisClient, FakeNspdClient, FakeOsmClient

KN = "52:26:0040002:3823"


def _fake_tile(z: int, x: int, y: int, timeout: float) -> bytes:
    """Однотонный тайл подложки вместо сетевого OSM."""
    from PIL import Image

    image = Image.new("RGB", (256, 256), (238, 240, 233))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _recorder() -> tuple[list[tuple[float, str]], object]:
    events: list[tuple[float, str]] = []

    def record(percent: float, stage: str) -> None:
        events.append((percent, stage))

    return events, record


def _assert_monotonic_percents(events: list[tuple[float, str]]) -> None:
    percents = [percent for percent, _ in events]
    assert percents == sorted(percents)
    assert all(0.0 <= percent <= 100.0 for percent in percents)


def test_report_progress_without_listener_is_noop() -> None:
    report_progress(None, 50.0, "stage")
    assert scaled_progress(None, 0.0, 100.0) is None


def test_scaled_progress_remaps_subrange() -> None:
    events, record = _recorder()
    scaled = scaled_progress(record, 15.0, 100.0)
    assert scaled is not None
    scaled(0.0, "a")
    scaled(50.0, "b")
    scaled(100.0, "c")
    assert [percent for percent, _ in events] == [15.0, 57.5, 100.0]


async def test_collect_reports_progress_events(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    )
    events, record = _recorder()
    request = CollectionRequest(
        case_id="case-progress",
        cadastral_number=KN,
        profile_version="3.1",
        refresh_policy="always",
    )

    receipt = await pipeline.collect(request, progress=record)

    assert receipt.status == "complete"
    _assert_monotonic_percents(events)
    stages = [stage for _, stage in events]
    assert events[0] == (3.0, "collection_start")
    assert "parcel_info" in stages
    assert events[-1] == (90.0, "collection_finalize")


async def test_collect_reuse_reports_single_complete_event(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    pipeline = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    )
    request = CollectionRequest(
        case_id="case-reuse",
        cadastral_number=KN,
        profile_version="3.1",
        refresh_policy="always",
    )
    await pipeline.collect(request)

    events, record = _recorder()
    receipt = await pipeline.collect(
        request.model_copy(update={"refresh_policy": "if_stale"}),
        progress=record,
    )

    assert receipt.reused is True
    assert events == [(100.0, "collection_reused")]


async def test_prepare_case_scales_collection_then_analysis(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    acquisition = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    )
    service = ReportingService(store=store, acquisition=acquisition)
    events, record = _recorder()

    result = await service.prepare_case(KN, progress=record)

    assert result.collection_status == "complete"
    _assert_monotonic_percents(events)
    stages = [stage for _, stage in events]
    assert "analysis" in stages
    assert events[-1] == (100.0, "case_ready")
    # Сбор масштабирован в 0–90: analysis идёт после него.
    analysis_index = stages.index("analysis")
    assert all(percent <= 90.0 for percent, _ in events[:analysis_index])


async def test_prepare_quickreport_reports_stages(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    acquisition = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    )
    await acquisition.collect(
        CollectionRequest(
            case_id="case-quickprogress",
            cadastral_number=KN,
            refresh_policy="always",
        )
    )
    from terralogic_engine.analytics.pipeline import AnalysisPipeline

    AnalysisPipeline(store=store).analyze("case-quickprogress")
    service = ReportingService(
        store=store, acquisition=acquisition, tile_fetcher=_fake_tile
    )
    events, record = _recorder()

    result = service.prepare_quickreport("case-quickprogress", progress=record)

    assert result.case_id == "case-quickprogress"
    _assert_monotonic_percents(events)
    stages = [stage for _, stage in events]
    assert stages == [
        "quick_context",
        "quick_model",
        "quick_narrative",
        "quick_qa",
        "quick_pdf",
        "quick_saved",
    ]
    assert events[-1] == (100.0, "quick_saved")
