"""Тесты сводки сбора (collection_summary) и семантики refresh_policy='never'."""

from __future__ import annotations

import pytest

from terralogic_engine.acquisition.pipeline import (
    AcquisitionPipeline,
    CaseNotCollectedError,
)
from terralogic_engine.domain.models import CollectionRequest
from terralogic_engine.mcp import _tool_failure
from terralogic_engine.reporting.collection_summary import build_collection_summary
from terralogic_engine.reporting.service import ReportingService
from terralogic_engine.store.local import LocalCaseStore

from .fakes import (
    FakeDgisClient,
    FakeGeodocsClient,
    FakeNspdClient,
    FakeNspdDocumentsClient,
    FakeOsmClient,
    FakeRgisClient,
    FakeRgisDocumentsClient,
)

_KN = "50:32:0000000:38218"


def _pipeline(store: LocalCaseStore, **overrides) -> AcquisitionPipeline:
    clients = {
        "store": store,
        "nspd": FakeNspdClient(),
        "osm": FakeOsmClient(),
        "dgis": FakeDgisClient(),
        "rgis": FakeRgisClient(),
        "rgis_documents": FakeRgisDocumentsClient(),
        "nspd_documents": FakeNspdDocumentsClient(),
        "geodocs": FakeGeodocsClient(),
    }
    clients.update(overrides)
    return AcquisitionPipeline(**clients)


def _request(**overrides) -> CollectionRequest:
    params = {
        "case_id": "case-summary",
        "cadastral_number": _KN,
        "profile_version": "3.1",
        "refresh_policy": "always",
    }
    params.update(overrides)
    return CollectionRequest(**params)


async def test_summary_full_success(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    pipeline = _pipeline(store)
    receipt = await pipeline.collect(_request())
    assert receipt.status == "complete"

    summary = build_collection_summary(store, receipt)

    sources = {entry.source: entry for entry in summary.sources}
    assert set(sources) == {"nspd", "rgis", "osm", "dgis", "documents_sync", "geodocs"}
    assert all(entry.status == "ok" for entry in sources.values())
    assert summary.duration_s is not None

    documents = {doc.number: doc for doc in summary.documents}
    # ПЗЗ: sync pending → acquire acquired → ВРИ извлечены
    assert documents["ПЗЗ-Т-592"].status == "extracted"
    assert documents["ПЗЗ-Т-592"].doc_type == "pzz"
    # генплан: уже скачан на sync, acquire не требовался
    assert documents["ГП-Т-15"].status == "downloaded"
    # акт ЗОУИТ: sync pending, режим извлечён documents_regimes-снапшотом
    assert documents["Постановление № 111"].status == "extracted"


async def test_summary_source_failure_and_partial(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    pipeline = _pipeline(
        store,
        osm=FakeOsmClient(failure=TimeoutError("osm timeout")),
        nspd_documents=FakeNspdDocumentsClient(sync_failure=RuntimeError("sync boom")),
    )
    receipt = await pipeline.collect(_request())
    assert receipt.status == "partial"

    summary = build_collection_summary(store, receipt)
    sources = {entry.source: entry for entry in summary.sources}
    assert sources["osm"].status == "failed"
    assert "osm timeout" in (sources["osm"].detail or "")
    # nspd sync упал, rgis sync записался → частичный контур синхронизации
    assert sources["documents_sync"].status == "partial"
    assert sources["nspd"].status == "ok"
    assert sources["geodocs"].status == "ok"


async def test_summary_rgis_not_applicable(tmp_path) -> None:
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
    receipt = await pipeline.collect(_request(case_id="case-na", profile_version="3.0"))

    summary = build_collection_summary(store, receipt)
    sources = {entry.source: entry for entry in summary.sources}
    assert sources["rgis"].status == "not_applicable"
    assert sources["rgis"].detail == "parcel not found"
    # без документальных клиентов контуры помечаются skipped
    assert sources["documents_sync"].status == "skipped"
    assert sources["geodocs"].status == "skipped"
    assert summary.documents == []


async def test_summary_document_not_found_with_reason(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    geodocs = FakeGeodocsClient(
        acquire_result={
            "status": "not_found",
            "refs": [],
            "warnings": ["mosreg: на карточке нет ссылок на файлы"],
        }
    )
    pipeline = _pipeline(store, geodocs=geodocs)
    receipt = await pipeline.collect(_request())

    summary = build_collection_summary(store, receipt)
    documents = {doc.number: doc for doc in summary.documents}
    # acquire без refs матчится на кандидата по doc_type/number
    assert documents["ПЗЗ-Т-592"].status == "not_found"
    assert "нет ссылок" in (documents["ПЗЗ-Т-592"].reason or "")
    assert documents["ГП-Т-15"].status == "downloaded"


async def test_prepare_case_attaches_collection(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    service = ReportingService(store=store, acquisition=_pipeline(store))
    result = await service.prepare_case(_KN, refresh_policy="always")

    assert result.collection is not None
    assert {entry.source for entry in result.collection.sources} == {
        "nspd",
        "rgis",
        "osm",
        "dgis",
        "documents_sync",
        "geodocs",
    }
    assert any(doc.status == "extracted" for doc in result.collection.documents)


async def test_summary_downloaded_pzz_without_extraction_has_reason(tmp_path) -> None:
    """ПЗЗ скачан, но таблица ВРИ не извлечена → явная причина в сводке."""
    store = LocalCaseStore(tmp_path / "store")
    geodocs = FakeGeodocsClient(
        vri_result={
            "status": "success",
            "data": {"zones": []},
            "evidence": [],
            "answer_text": "",
            "documents": [],
            "executor": "static:vri",
            "warnings": [],
            "duration_seconds": 0.0,
        }
    )
    pipeline = _pipeline(store, geodocs=geodocs)
    receipt = await pipeline.collect(_request())

    summary = build_collection_summary(store, receipt)
    documents = {doc.number: doc for doc in summary.documents}
    assert documents["ПЗЗ-Т-592"].status == "downloaded"
    assert documents["ПЗЗ-Т-592"].reason == "таблица ВРИ не извлечена"


async def test_never_without_run_raises_not_collected(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    pipeline = _pipeline(store)
    with pytest.raises(CaseNotCollectedError):
        await pipeline.collect(_request(refresh_policy="never"))


async def test_never_with_existing_run_reuses(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    pipeline = _pipeline(store)
    first = await pipeline.collect(_request())
    reused = await pipeline.collect(_request(refresh_policy="never"))
    assert reused.reused is True
    assert reused.run_id == first.run_id

    summary = build_collection_summary(store, reused)
    assert summary.sources  # сводка читается и из переиспользованного run


def test_tool_failure_maps_not_collected() -> None:
    error = _tool_failure(CaseNotCollectedError("case-x has no run"))
    assert error.code == "not_collected"
    assert error.retryable is False
