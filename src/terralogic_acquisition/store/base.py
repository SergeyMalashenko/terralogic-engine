"""Storage boundary used by acquisition and future application services."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from terralogic_acquisition.domain.models import (
    AreaOfInterest,
    CaseInfo,
    CollectionReceipt,
    CollectionRequest,
    GeoFeature,
    SourceName,
    SourceSnapshot,
)


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

    def list_collection_receipts(
        self, case_id: str
    ) -> list[CollectionReceipt]: ...

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

    def get_area_of_interest(
        self, case_id: str, aoi_id: str
    ) -> AreaOfInterest: ...

    def save_features(
        self, case_id: str, features: Sequence[GeoFeature]
    ) -> None: ...

    def load_features(
        self,
        case_id: str,
        *,
        source: SourceName | None = None,
        snapshot_id: str | None = None,
        feature_classes: Sequence[str] | None = None,
    ) -> list[GeoFeature]: ...
