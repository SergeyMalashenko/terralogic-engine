"""Storage boundary used by acquisition and future application services."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from terralogic_engine.analytics.models import AnalysisResult
from terralogic_engine.domain.models import (
    AreaOfInterest,
    CaseInfo,
    CollectionReceipt,
    CollectionRequest,
    GeoFeature,
    SourceName,
    SourceSnapshot,
)
from terralogic_engine.reporting.models import GeneratedReport


class CaseStore(Protocol):
    """Implementation-independent storage contract."""

    def create_case(
        self,
        *,
        case_id: str,
        cadastral_number: str,
        report_profile: str,
    ) -> CaseInfo: ...

    def get_case(self, case_id: str) -> CaseInfo: ...

    def list_cases(self) -> list[CaseInfo]: ...

    def begin_run(self, request: CollectionRequest, run_id: str) -> None: ...

    def finish_run(self, receipt: CollectionReceipt) -> None: ...

    def get_latest_collection_receipt(
        self, case_id: str
    ) -> CollectionReceipt | None: ...

    def list_collection_receipts(self, case_id: str) -> list[CollectionReceipt]: ...

    def save_snapshot(
        self,
        *,
        case_id: str,
        run_id: str,
        source: SourceName,
        payload: bytes,
        adapter_version: str,
        metadata: dict[str, object] | None = None,
    ) -> SourceSnapshot: ...

    def load_snapshot(self, case_id: str, snapshot_id: str) -> bytes: ...

    def list_snapshots(
        self, case_id: str, source: SourceName | None = None
    ) -> list[SourceSnapshot]: ...

    def save_area_of_interest(self, aoi: AreaOfInterest) -> None: ...

    def get_area_of_interest(self, case_id: str, aoi_id: str) -> AreaOfInterest: ...

    def save_features(self, case_id: str, features: Sequence[GeoFeature]) -> None: ...

    def load_features(
        self,
        case_id: str,
        *,
        source: SourceName | None = None,
        snapshot_id: str | None = None,
        feature_classes: Sequence[str] | None = None,
    ) -> list[GeoFeature]: ...

    def save_analysis_result(self, result: AnalysisResult) -> None: ...

    def get_analysis_result(
        self,
        case_id: str,
        collection_run_id: str,
        *,
        analytics_version: str | None = None,
    ) -> AnalysisResult | None: ...

    def list_analysis_results(self, case_id: str) -> list[AnalysisResult]: ...

    def save_generated_report(
        self,
        *,
        case_id: str,
        collection_run_id: str,
        analysis_id: str,
        title: str,
        template_id: str,
        template_version: str,
        template_sha256: str,
        markdown: str,
        model_name: str | None = None,
    ) -> GeneratedReport: ...

    def get_latest_generated_report(
        self,
        case_id: str,
        collection_run_id: str,
    ) -> GeneratedReport | None: ...

    def list_generated_reports(
        self,
        case_id: str,
        *,
        collection_run_id: str | None = None,
    ) -> list[GeneratedReport]: ...
