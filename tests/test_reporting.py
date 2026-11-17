from __future__ import annotations

import hashlib

import pytest

from terralogic_engine.acquisition.pipeline import AcquisitionPipeline
from terralogic_engine.analytics.pipeline import AnalysisPipeline
from terralogic_engine.domain.models import CollectionRequest
from terralogic_engine.reporting.context import build_report_context
from terralogic_engine.reporting.service import ReportingService
from terralogic_engine.reporting.template_registry import (
    ReportStructureError,
)
from terralogic_engine.store.local import LocalCaseStore

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
    assert context.transport_inventory[0].category == ("public_transport_stops")
    assert context.transport_inventory[0].distance_basis == "search_point"
    assert context.road_inventory[0].road_class == "service"
    assert context.context_version == "1.3"
    assert context.documents is None
    assert context.urban_planning.collected is False
    assert context.urban_planning.parcel_zones == []
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
    assert template.version == "1.1"
    assert template.sections[2].key == "urban_planning"
    assert "{{ urban_planning_section }}" in template.markdown_skeleton
    assert any("Не вычисляй" in rule for rule in template.generation_rules)
    assert "{{ cadastral_number }}" in template.markdown_skeleton
    previous_template = service.get_report_template(template_version="1.0")
    assert previous_template.version == "1.0"
    assert all(
        section.key != "urban_planning" for section in previous_template.sections
    )


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
    assert report.template_version == "1.1"
    assert len(report.template_sha256) == 64
    assert report.markdown.endswith("\n")
    assert (
        report.content_sha256
        == hashlib.sha256(report.markdown.encode("utf-8")).hexdigest()
    )
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
        f"{section.heading}\n\nСодержимое." for section in reversed(template.sections)
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


async def test_template_v1_2_adds_documents_subsection(tmp_path) -> None:
    store, acquisition, receipt, _analysis = await _collected_store(tmp_path)
    service = ReportingService(store=store, acquisition=acquisition)

    default_template = service.get_report_template()
    assert default_template.version == "1.1"
    template = service.get_report_template(template_version="1.2")
    assert template.template_id == "full_land_report"
    assert template.version == "1.2"
    assert [section.order for section in template.sections] == list(range(1, 11))
    assert template.sections[2].key == "urban_planning"
    assert template.sections[3].key == "documents"
    assert template.sections[3].heading == "### 3.1. Документы и ВРИ (ПЗЗ/НПА)"
    assert "{{ documents_section }}" in template.markdown_skeleton
    assert "{{ urban_planning_section }}" in template.markdown_skeleton
    assert len(template.content_sha256) == 64
    assert any("documents=null" in rule for rule in template.generation_rules)

    sections = "\n\n".join(
        f"{section.heading}\n\nПроверенный текст раздела."
        for section in template.sections
    )
    service.template_registry.validate_markdown(
        template, f"# Отчёт о земельном участке\n\n{sections}"
    )

    previous_sections = "\n\n".join(
        f"{section.heading}\n\nПроверенный текст раздела."
        for section in default_template.sections
    )
    with pytest.raises(ReportStructureError) as excinfo:
        service.template_registry.validate_markdown(
            template,
            f"# Отчёт о земельном участке\n\n{previous_sections}",
        )
    assert "missing required headings" in str(excinfo.value)

    saved = service.save_report(
        "case-report",
        f"# Отчёт о земельном участке\n\n{sections}",
        collection_run_id=receipt.run_id,
        template_version="1.2",
    )
    assert saved.template_version == "1.2"
    assert saved.template_sha256 == template.content_sha256


def test_zone_display_name_numeric_id_falls_back_to_layer():
    """Числовой id вместо имени зоны → читаемое имя слоя РГИС."""
    from terralogic_engine.domain.models import GeoFeature
    from terralogic_engine.reporting.context import _zone_display_name

    feature = GeoFeature(
        id="f-1",
        case_id="case-x",
        snapshot_id="snap-x",
        source="rgis",
        source_type="drinking_water_protection",
        feature_class="restriction_zone",
    )
    assert _zone_display_name("5183673830", feature) == "Зона санитарной охраны"
    assert _zone_display_name("5183673830", None) == "5183673830"
    assert _zone_display_name("Водоохранная зона", feature) == "Водоохранная зона"


async def test_prepare_case_source_not_found_raises_parcel_not_found(tmp_path) -> None:
    """Конверт not_found источника → ParcelNotFoundError (не временный сбой)."""
    from terralogic_engine.domain.models import CollectionReceipt, utc_now
    from terralogic_engine.reporting.service import (
        CasePreparationError,
        ParcelNotFoundError,
    )

    class _FailedAcquisition:
        def __init__(self, errors: list[str]) -> None:
            self._errors = errors

        async def collect(self, request, *, progress=None):
            return CollectionReceipt(
                case_id=request.case_id,
                run_id="run-x",
                status="failed",
                aoi_id=None,
                errors=self._errors,
                started_at=utc_now(),
                completed_at=utc_now(),
            )

    store = LocalCaseStore(tmp_path / "store")
    service = ReportingService(
        store=store,
        acquisition=_FailedAcquisition(
            [
                (
                    "nspd_get_land_parcel_info: not_found: "
                    "Land parcel 50:21:0030210:123 was not found"
                )
            ]
        ),
    )
    with pytest.raises(ParcelNotFoundError):
        await service.prepare_case("50:21:0030210:123")

    service = ReportingService(
        store=store,
        acquisition=_FailedAcquisition(["nspd: connection timeout"]),
    )
    with pytest.raises(CasePreparationError) as exc_info:
        await service.prepare_case("50:21:0030210:123")
    assert not isinstance(exc_info.value, ParcelNotFoundError)


class _FakeParcelResolver:
    def __init__(self, envelope: dict) -> None:
        self.envelope = envelope
        self.urls: list[str] = []
        self.coords: list[str] = []

    async def resolve_cadastral_number(self, url: str) -> dict:
        self.urls.append(url)
        return dict(self.envelope)

    async def resolve_cadastral_number_at_coords(self, coordinates: str) -> dict:
        self.coords.append(coordinates)
        return dict(self.envelope)


_RESOLVED_ENVELOPE = {
    "ok": True,
    "data": {
        "listing": {
            "listing_id": 8110049401,
            "title": "Участок 10 сот. (ИЖС)",
            "address": "Московская обл., Домодедово, мкр-н Барыбино",
            "price": 900000.0,
            "latitude": 55.265,
            "longitude": 37.885,
        },
        "cadastral_number": "50:28:0040126:62",
        "warnings": ["area_mismatch: ..."],
    },
    "error": None,
}


async def test_prepare_case_from_listing_requires_resolver(tmp_path) -> None:
    """Без подключённого контура резолюции — понятная ListingResolutionError."""
    from terralogic_engine.reporting.service import ListingResolutionError

    store = LocalCaseStore(tmp_path / "store")
    service = ReportingService(store=store, acquisition=object())
    with pytest.raises(ListingResolutionError, match="not configured"):
        await service.prepare_case_from_listing("https://www.avito.ru/x_1")


async def test_prepare_case_from_listing_no_candidates(tmp_path) -> None:
    """Резолвер вернул пустой cadastral_number — ошибка с warnings резолюции."""
    from terralogic_engine.reporting.service import ListingResolutionError

    envelope = {
        "ok": True,
        "data": {
            "listing": {"listing_id": 1},
            "cadastral_number": None,
            "warnings": ["no_landplots_at_coordinates: ..."],
        },
        "error": None,
    }
    store = LocalCaseStore(tmp_path / "store")
    service = ReportingService(
        store=store,
        acquisition=object(),
        parcel_resolver=_FakeParcelResolver(envelope),
    )
    with pytest.raises(ListingResolutionError, match="no_landplots_at_coordinates"):
        await service.prepare_case_from_listing("https://www.avito.ru/x_1")


async def test_prepare_case_from_listing_happy_path(tmp_path) -> None:
    """Успешная резолюция делегирует в обычный prepare_case с найденным КН."""
    from terralogic_engine.reporting.models import PrepareCaseResult

    store = LocalCaseStore(tmp_path / "store")
    resolver = _FakeParcelResolver(_RESOLVED_ENVELOPE)
    service = ReportingService(
        store=store,
        acquisition=object(),
        parcel_resolver=resolver,
    )

    captured: dict = {}

    async def _fake_prepare_case(cadastral_number: str, **kwargs):
        captured["cadastral_number"] = cadastral_number
        captured.update(kwargs)
        return PrepareCaseResult(
            case_id="case-50-28-0040126-62",
            collection_run_id="run-1",
            collection_status="complete",
            analysis_id="analysis-1",
        )

    service.prepare_case = _fake_prepare_case  # type: ignore[method-assign]

    result = await service.prepare_case_from_listing(
        "https://www.avito.ru/domodedovo/zemelnye_uchastki/x_8110049401",
        refresh_policy="always",
    )

    assert resolver.urls == [
        "https://www.avito.ru/domodedovo/zemelnye_uchastki/x_8110049401"
    ]
    assert captured["cadastral_number"] == "50:28:0040126:62"
    assert captured["refresh_policy"] == "always"
    assert result.resolution.cadastral_number == "50:28:0040126:62"
    assert result.resolution.listing_id == 8110049401
    assert result.resolution.warnings == ["area_mismatch: ..."]
    assert result.case.case_id == "case-50-28-0040126-62"


async def test_prepare_case_at_coords_requires_resolver(tmp_path) -> None:
    """Без подключённого контура резолюции — понятная ListingResolutionError."""
    from terralogic_engine.reporting.service import ListingResolutionError

    store = LocalCaseStore(tmp_path / "store")
    service = ReportingService(store=store, acquisition=object())
    with pytest.raises(ListingResolutionError, match="not configured"):
        await service.prepare_case_at_coords("55.488626, 38.009447")


async def test_prepare_case_at_coords_no_candidates_nearby(tmp_path) -> None:
    """Пустая точка: ошибка с warnings и nearby-участками в details."""
    from terralogic_engine.reporting.service import ListingResolutionError

    envelope = {
        "ok": True,
        "data": {
            "latitude": 55.488626,
            "longitude": 38.009447,
            "cadastral_number": None,
            "warnings": ["no_landplots_at_coordinates: ..."],
            "nearby_parcels": [
                {
                    "cadastral_number": "50:23:0000000:120934",
                    "distance_m": 100.0,
                    "area_m2": 900.0,
                }
            ],
        },
        "error": None,
    }
    store = LocalCaseStore(tmp_path / "store")
    service = ReportingService(
        store=store,
        acquisition=object(),
        parcel_resolver=_FakeParcelResolver(envelope),
    )
    with pytest.raises(ListingResolutionError, match="no_landplots") as excinfo:
        await service.prepare_case_at_coords("55.488626, 38.009447")
    assert excinfo.value.nearby == [
        {
            "cadastral_number": "50:23:0000000:120934",
            "distance_m": 100.0,
            "area_m2": 900.0,
        }
    ]


async def test_prepare_case_at_coords_happy_path(tmp_path) -> None:
    """Успешная резолюция координат делегирует в prepare_case с найденным КН."""
    from terralogic_engine.reporting.models import PrepareCaseResult

    envelope = {
        "ok": True,
        "data": {
            "latitude": 55.488626,
            "longitude": 38.009447,
            "cadastral_number": "50:23:0000000:120934",
            "parcels": [{"cadastral_number": "50:23:0000000:120934"}],
            "nearby_parcels": [],
            "warnings": [],
        },
        "error": None,
    }
    store = LocalCaseStore(tmp_path / "store")
    resolver = _FakeParcelResolver(envelope)
    service = ReportingService(
        store=store,
        acquisition=object(),
        parcel_resolver=resolver,
    )

    captured: dict = {}

    async def _fake_prepare_case(cadastral_number: str, **kwargs):
        captured["cadastral_number"] = cadastral_number
        captured.update(kwargs)
        return PrepareCaseResult(
            case_id="case-50-23-0000000-120934",
            collection_run_id="run-1",
            collection_status="complete",
            analysis_id="analysis-1",
        )

    service.prepare_case = _fake_prepare_case  # type: ignore[method-assign]

    result = await service.prepare_case_at_coords("55.488626, 38.009447")

    assert resolver.coords == ["55.488626, 38.009447"]
    assert captured["cadastral_number"] == "50:23:0000000:120934"
    assert result.resolution.cadastral_number == "50:23:0000000:120934"
    assert result.resolution.source == "coordinates"
    assert result.resolution.url == "55.488626, 38.009447"
    assert result.resolution.latitude == 55.488626
    assert result.resolution.longitude == 38.009447
    assert result.case.case_id == "case-50-23-0000000-120934"


async def test_prepare_case_from_listing_transport_failure(tmp_path) -> None:
    """Сбой вызова резолвера оборачивается в ListingResolutionError."""
    from terralogic_engine.reporting.service import ListingResolutionError

    class _BrokenResolver:
        async def resolve_cadastral_number(self, url: str) -> dict:
            raise ConnectionError("parcel-resolver-mcp is down")

    store = LocalCaseStore(tmp_path / "store")
    service = ReportingService(
        store=store,
        acquisition=object(),
        parcel_resolver=_BrokenResolver(),
    )
    with pytest.raises(ListingResolutionError, match="parcel-resolver-mcp is down"):
        await service.prepare_case_from_listing("https://www.avito.ru/x_1")


async def test_prepare_case_from_listing_unavailable_is_preparation_error(
    tmp_path,
) -> None:
    """Retryable сбой резолвера → CasePreparationError (не ListingResolution)."""
    from terralogic_engine.acquisition.clients.mcp import (
        ParcelResolverUnavailable,
    )
    from terralogic_engine.reporting.service import (
        CasePreparationError,
        ListingResolutionError,
    )

    class _UnavailableResolver:
        async def resolve_cadastral_number(self, url: str) -> dict:
            raise ParcelResolverUnavailable("anti-bot stub")

    store = LocalCaseStore(tmp_path / "store")
    service = ReportingService(
        store=store,
        acquisition=object(),
        parcel_resolver=_UnavailableResolver(),
    )
    with pytest.raises(CasePreparationError) as exc_info:
        await service.prepare_case_from_listing("https://www.avito.ru/x_1")
    assert not isinstance(exc_info.value, ListingResolutionError)
