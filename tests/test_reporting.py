from __future__ import annotations

import hashlib

from terralogic_acquisition.acquisition.pipeline import AcquisitionPipeline
from terralogic_acquisition.analytics.pipeline import AnalysisPipeline
from terralogic_acquisition.domain.models import CollectionRequest
from terralogic_acquisition.reporting.context import build_report_context
from terralogic_acquisition.reporting.service import ReportingService
from terralogic_acquisition.reporting.template_registry import (
    ReportStructureError,
)
from terralogic_acquisition.store.local import LocalCaseStore

from .fakes import FakeDgisClient, FakeNspdClient, FakeOsmClient


def _valid_markdown(service: ReportingService) -> str:
    template = service.get_report_template()
    sections = "\n\n".join(
        f"{section.heading}\n\nПроверенный текст раздела."
        for section in template.sections
    )
    return f"# Отчёт о земельном участке\n\n{sections}"


async def _collected_store(tmp_path):
    store = LocalCaseStore(tmp_path / "store")
    acquisition = AcquisitionPipeline(
        store=store,
        nspd=FakeNspdClient(),
        osm=FakeOsmClient(),
        dgis=FakeDgisClient(),
    )
    receipt = await acquisition.collect(
        CollectionRequest(
            case_id="case-report",
            cadastral_number="52:26:0040002:3823",
            refresh_policy="always",
        )
    )
    analysis = AnalysisPipeline(store=store).analyze("case-report")
    return store, acquisition, receipt, analysis


async def test_report_context_is_bounded_and_keeps_provenance(tmp_path) -> None:
    store, _acquisition, receipt, analysis = await _collected_store(tmp_path)

    context = build_report_context(
        store,
        "case-report",
        collection_run_id=receipt.run_id,
    )

    assert context.analysis_id == analysis.id
    assert context.parcel.cadastral_number == "52:26:0040002:3823"
    assert context.parcel.address == "Тестовый участок"
    assert context.parcel.calculated_area_m2 > 0
    assert context.zouit[0].registry_number == "52:26-6.1"
    assert context.zouit[0].intersection_area_m2 > 0
    assert context.social_nearest[0].category == "education"
    assert context.transport_inventory[0].category == (
        "public_transport_stops"
    )
    assert context.transport_inventory[0].distance_basis == "search_point"
    assert context.road_inventory[0].road_class == "service"
    assert {source.source for source in context.sources} == {
        "nspd",
        "osm",
        "dgis",
    }
    dumped = context.model_dump_json()
    assert '"coordinates"' not in dumped
    service = ReportingService(store=store, acquisition=_acquisition)
    template = service.get_report_template()
    assert template.template_id == "full_land_report"
    assert template.version == "1.0"
    assert any("Не вычисляй" in rule for rule in template.generation_rules)
    assert "{{ cadastral_number }}" in template.markdown_skeleton


async def test_reporting_service_persists_and_reopens_markdown(tmp_path) -> None:
    store, acquisition, receipt, analysis = await _collected_store(tmp_path)
    service = ReportingService(store=store, acquisition=acquisition)

    report = service.save_report(
        "case-report",
        _valid_markdown(service),
        collection_run_id=receipt.run_id,
        model_name="gemma4:31b",
    )

    assert report.analysis_id == analysis.id
    assert report.template_id == "full_land_report"
    assert len(report.template_sha256) == 64
    assert report.markdown.endswith("\n")
    assert report.content_sha256 == hashlib.sha256(
        report.markdown.encode("utf-8")
    ).hexdigest()
    reopened = LocalCaseStore(tmp_path / "store")
    loaded = reopened.get_latest_generated_report(
        "case-report",
        receipt.run_id,
    )
    assert loaded == report
    assert (
        tmp_path / "store" / "cases" / "case-report" / report.relative_path
    ).read_text("utf-8") == report.markdown


async def test_reporting_service_rejects_invalid_template_structure(
    tmp_path,
) -> None:
    store, acquisition, receipt, _analysis = await _collected_store(tmp_path)
    service = ReportingService(store=store, acquisition=acquisition)

    try:
        service.save_report(
            "case-report",
            "# Отчёт без обязательных разделов",
            collection_run_id=receipt.run_id,
        )
    except ReportStructureError as exc:
        assert "missing required headings" in str(exc)
    else:
        raise AssertionError("Invalid Markdown structure must be rejected")


async def test_reporting_service_rejects_reordered_template_sections(
    tmp_path,
) -> None:
    store, acquisition, receipt, _analysis = await _collected_store(tmp_path)
    service = ReportingService(store=store, acquisition=acquisition)
    template = service.get_report_template()
    reversed_sections = "\n\n".join(
        f"{section.heading}\n\nСодержимое."
        for section in reversed(template.sections)
    )

    try:
        service.save_report(
            "case-report",
            f"# Отчёт\n\n{reversed_sections}",
            collection_run_id=receipt.run_id,
        )
    except ReportStructureError as exc:
        assert "not in template order" in str(exc)
    else:
        raise AssertionError("Reordered Markdown sections must be rejected")
