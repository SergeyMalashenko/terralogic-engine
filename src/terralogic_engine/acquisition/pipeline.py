"""Deterministic orchestration of NSPD, OSM, and 2GIS collection."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from shapely.geometry import mapping

from terralogic_engine.acquisition.clients.base import (
    DgisSourceClient,
    NspdSourceClient,
    OsmSourceClient,
)
from terralogic_engine.acquisition.geometry import (
    build_area_of_interest,
    prepare_parcel_geometry,
)
from terralogic_engine.acquisition.normalize import (
    count_features,
    dgis_features,
    nspd_layer_features,
    osm_features,
    parcel_feature,
)
from terralogic_engine.acquisition.profiles import (
    CollectionProfile,
    get_collection_profile,
)
from terralogic_engine.domain.models import (
    CollectionReceipt,
    CollectionRequest,
    GeoFeature,
    ReceiptStatus,
    utc_now,
)
from terralogic_engine.store.base import CaseStore


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")


def _tool_error(payload: Mapping[str, Any], tool: str) -> str:
    error = payload.get("error")
    if isinstance(error, Mapping):
        code = error.get("code", "source_error")
        message = error.get("message", "Unknown source error")
        return f"{tool}: {code}: {message}"
    return f"{tool}: source returned ok=false"


def _adapter_version(payload: Mapping[str, Any]) -> str:
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        value = metadata.get("adapter_version") or metadata.get("version")
        if value is not None:
            return str(value)
    return "unknown"


def _coverage_is_partial(payload: Mapping[str, Any]) -> bool:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return False
    coverage = data.get("coverage")
    if isinstance(coverage, Mapping) and coverage.get("partial") is True:
        return True
    return any(
        (
            data.get("global_limit_reached") is True,
            data.get("source_complete") is False,
            data.get("response_limited") is True,
        )
    )


def _exception_payload(exc: BaseException) -> dict[str, str]:
    return {
        "transport_error": str(exc),
        "exception_type": type(exc).__name__,
    }


class AcquisitionPipeline:
    """Collect source data, persist snapshots, and return a compact receipt."""

    def __init__(
        self,
        *,
        store: CaseStore,
        nspd: NspdSourceClient,
        osm: OsmSourceClient,
        dgis: DgisSourceClient,
        profile_resolver: Callable[[str, str], CollectionProfile] = (
            get_collection_profile
        ),
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self.nspd = nspd
        self.osm = osm
        self.dgis = dgis
        self.profile_resolver = profile_resolver
        self.clock = clock

    async def collect(self, request: CollectionRequest) -> CollectionReceipt:
        """Execute one collection or reuse a fresh successful prior result."""

        profile = self.profile_resolver(request.profile, request.profile_version)
        self.store.create_case(
            case_id=request.case_id,
            cadastral_number=request.cadastral_number,
            report_profile=request.profile,
        )
        reusable = self._reusable_receipt(request, profile)
        if reusable is not None:
            return reusable

        run_id = f"run-{uuid4().hex}"
        started_at = self.clock()
        self.store.begin_run(request, run_id)
        nspd_snapshot_id: str | None = None
        osm_snapshot_id: str | None = None
        dgis_snapshot_id: str | None = None
        aoi_id: str | None = None
        all_features: list[GeoFeature] = []
        warnings: list[str] = []
        errors: list[str] = []

        try:
            parcel_info = dict(
                await self.nspd.get_land_parcel_info(
                    request.cadastral_number, detail="full"
                )
            )
            if parcel_info.get("ok") is not True:
                snapshot = self.store.save_snapshot(
                    case_id=request.case_id,
                    run_id=run_id,
                    source="nspd",
                    payload=_json_bytes({"parcel_info": parcel_info}),
                    adapter_version=_adapter_version(parcel_info),
                    metadata={"tools": ["nspd_get_land_parcel_info"]},
                )
                nspd_snapshot_id = snapshot.id
                errors.append(_tool_error(parcel_info, "nspd_get_land_parcel_info"))
                return self._finish(
                    request=request,
                    run_id=run_id,
                    status="failed",
                    started_at=started_at,
                    nspd_snapshot_id=nspd_snapshot_id,
                    warnings=warnings,
                    errors=errors,
                )

            parcel_data = parcel_info.get("data")
            parcel = (
                parcel_data.get("parcel")
                if isinstance(parcel_data, Mapping)
                else None
            )
            if not isinstance(parcel, Mapping) or not isinstance(
                parcel.get("geojson"), dict
            ):
                raise ValueError(
                    "nspd_get_land_parcel_info(detail='full') returned no parcel "
                    "GeoJSON"
                )

            parcel_geometry, geometry_warnings = prepare_parcel_geometry(
                parcel["geojson"]
            )
            normalized_geometry = dict(mapping(parcel_geometry))
            warnings.extend(geometry_warnings)
            margin_m = (
                request.margin_m
                if request.margin_m is not None
                else profile.margin_m
            )
            provisional_aoi = build_area_of_interest(
                case_id=request.case_id,
                source_snapshot_id="pending",
                parcel_geojson=normalized_geometry,
                margin_m=margin_m,
            )
            longitude, latitude = provisional_aoi.representative_point
            radius_m = math.ceil(provisional_aoi.search_radius_m)

            (
                layer_result,
                osm_result,
                social_result,
                transport_result,
            ) = await asyncio.gather(
                self.nspd.analyze_land_parcel_layers(
                    request.cadastral_number,
                    blocks=profile.nspd_blocks,
                    include_geometry=True,
                    limit=profile.nspd_limit_per_layer,
                    detail="full",
                ),
                self.osm.analyze_area(
                    normalized_geometry,
                    source_crs="EPSG:4326",
                    margin_m=margin_m,
                    blocks=profile.osm_blocks,
                    limit_per_block=profile.osm_limit_per_block,
                    include_geometry=True,
                ),
                self.dgis.analyze_social_infrastructure(
                    latitude=latitude,
                    longitude=longitude,
                    radius_m=radius_m,
                    mode=profile.dgis_mode,
                    limit_per_category=profile.dgis_limit_per_category,
                ),
                self.dgis.analyze_transport_infrastructure(
                    latitude=latitude,
                    longitude=longitude,
                    radius_m=radius_m,
                    mode=profile.dgis_mode,
                    limit_per_category=profile.dgis_limit_per_category,
                ),
                return_exceptions=True,
            )

            layer_envelope, stored_layer_result = self._source_result(
                layer_result,
                tool="nspd_analyze_land_parcel_layers",
                partial_warning="NSPD restriction coverage is partial",
                warnings=warnings,
                errors=errors,
            )
            nspd_snapshot = self.store.save_snapshot(
                case_id=request.case_id,
                run_id=run_id,
                source="nspd",
                payload=_json_bytes(
                    {
                        "parcel_info": parcel_info,
                        "restriction_analysis": stored_layer_result,
                    }
                ),
                adapter_version=_adapter_version(parcel_info),
                metadata={
                    "tools": [
                        "nspd_get_land_parcel_info",
                        "nspd_analyze_land_parcel_layers",
                    ],
                    "blocks": list(profile.nspd_blocks),
                    "profile": profile.name,
                    "profile_version": profile.version,
                },
            )
            nspd_snapshot_id = nspd_snapshot.id
            aoi = provisional_aoi.model_copy(
                update={"source_snapshot_id": nspd_snapshot.id}
            )
            self.store.save_area_of_interest(aoi)
            aoi_id = aoi.id

            all_features.append(
                parcel_feature(
                    case_id=request.case_id,
                    snapshot_id=nspd_snapshot.id,
                    parcel=dict(parcel),
                    geometry=aoi.parcel_geometry,
                )
            )
            all_features.extend(
                nspd_layer_features(
                    case_id=request.case_id,
                    snapshot_id=nspd_snapshot.id,
                    envelope=layer_envelope,
                )
            )

            osm_envelope, _stored_osm = self._source_result(
                osm_result,
                tool="osm_analyze_area",
                partial_warning="OSM collection reached a completeness limit",
                warnings=warnings,
                errors=errors,
            )
            if osm_envelope is not None:
                osm_snapshot = self.store.save_snapshot(
                    case_id=request.case_id,
                    run_id=run_id,
                    source="osm",
                    payload=_json_bytes(osm_envelope),
                    adapter_version=_adapter_version(osm_envelope),
                    metadata={
                        "tools": ["osm_analyze_area"],
                        "geometry_hash": aoi.geometry_hash,
                        "margin_m": margin_m,
                        "search_radius_m": aoi.search_radius_m,
                        "blocks": list(profile.osm_blocks),
                        "profile": profile.name,
                        "profile_version": profile.version,
                    },
                )
                osm_snapshot_id = osm_snapshot.id
                all_features.extend(
                    osm_features(
                        case_id=request.case_id,
                        snapshot_id=osm_snapshot.id,
                        envelope=osm_envelope,
                    )
                )

            social_envelope, stored_social_result = self._source_result(
                social_result,
                tool="dgis_analyze_social_infrastructure",
                partial_warning="2GIS social infrastructure coverage is partial",
                warnings=warnings,
                errors=errors,
            )
            transport_envelope, stored_transport_result = self._source_result(
                transport_result,
                tool="dgis_analyze_transport_infrastructure",
                partial_warning="2GIS transport infrastructure coverage is partial",
                warnings=warnings,
                errors=errors,
            )
            dgis_payload = {
                "social_infrastructure": stored_social_result,
                "transport_infrastructure": stored_transport_result,
            }
            dgis_adapter_payload = social_envelope or transport_envelope or {}
            dgis_snapshot = self.store.save_snapshot(
                case_id=request.case_id,
                run_id=run_id,
                source="dgis",
                payload=_json_bytes(dgis_payload),
                adapter_version=_adapter_version(dgis_adapter_payload),
                metadata={
                    "tools": [
                        "dgis_analyze_social_infrastructure",
                        "dgis_analyze_transport_infrastructure",
                    ],
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius_m": radius_m,
                    "mode": profile.dgis_mode,
                    "profile": profile.name,
                    "profile_version": profile.version,
                },
            )
            dgis_snapshot_id = dgis_snapshot.id
            all_features.extend(
                dgis_features(
                    case_id=request.case_id,
                    snapshot_id=dgis_snapshot.id,
                    envelopes=[social_envelope, transport_envelope],
                )
            )

            self.store.save_features(request.case_id, all_features)
            status = self._result_status(
                errors=errors,
                warnings=warnings,
                allow_partial=request.allow_partial,
            )
            return self._finish(
                request=request,
                run_id=run_id,
                status=status,
                started_at=started_at,
                nspd_snapshot_id=nspd_snapshot_id,
                osm_snapshot_id=osm_snapshot_id,
                dgis_snapshot_id=dgis_snapshot_id,
                aoi_id=aoi_id,
                features=all_features,
                warnings=warnings,
                errors=errors,
            )
        except Exception as exc:  # noqa: BLE001 - application-service boundary
            errors.append(f"acquisition: {type(exc).__name__}: {exc}")
            return self._finish(
                request=request,
                run_id=run_id,
                status="failed",
                started_at=started_at,
                nspd_snapshot_id=nspd_snapshot_id,
                osm_snapshot_id=osm_snapshot_id,
                dgis_snapshot_id=dgis_snapshot_id,
                aoi_id=aoi_id,
                features=all_features,
                warnings=warnings,
                errors=errors,
            )

    @staticmethod
    def _source_result(
        result: Any,
        *,
        tool: str,
        partial_warning: str,
        warnings: list[str],
        errors: list[str],
    ) -> tuple[dict[str, Any] | None, Any]:
        if isinstance(result, BaseException):
            errors.append(f"{tool}: {type(result).__name__}: {result}")
            payload = _exception_payload(result)
            return None, payload
        envelope = dict(result)
        if envelope.get("ok") is not True:
            errors.append(_tool_error(envelope, tool))
        elif _coverage_is_partial(envelope):
            warnings.append(partial_warning)
        return envelope, envelope

    def _reusable_receipt(
        self, request: CollectionRequest, profile: CollectionProfile
    ) -> CollectionReceipt | None:
        if request.refresh_policy == "always":
            return None
        latest = self.store.get_latest_collection_receipt(request.case_id)
        if latest is None or latest.status not in {"complete", "partial"}:
            return None
        requested_margin = (
            request.margin_m
            if request.margin_m is not None
            else profile.margin_m
        )
        if (
            latest.profile != request.profile
            or latest.profile_version != request.profile_version
            or latest.margin_m != requested_margin
        ):
            return None
        if request.refresh_policy == "never":
            return latest.model_copy(update={"reused": True})
        age = self.clock() - latest.completed_at
        if age <= timedelta(seconds=profile.stale_after_seconds):
            return latest.model_copy(update={"reused": True})
        return None

    @staticmethod
    def _result_status(
        *, errors: list[str], warnings: list[str], allow_partial: bool
    ) -> ReceiptStatus:
        if errors:
            return "partial" if allow_partial else "failed"
        if warnings:
            return "partial"
        return "complete"

    def _finish(
        self,
        *,
        request: CollectionRequest,
        run_id: str,
        status: ReceiptStatus,
        started_at: datetime,
        nspd_snapshot_id: str | None = None,
        osm_snapshot_id: str | None = None,
        dgis_snapshot_id: str | None = None,
        aoi_id: str | None = None,
        features: list[GeoFeature] | None = None,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
    ) -> CollectionReceipt:
        receipt = CollectionReceipt(
            case_id=request.case_id,
            run_id=run_id,
            status=status,
            profile=request.profile,
            profile_version=request.profile_version,
            margin_m=(
                request.margin_m
                if request.margin_m is not None
                else self.profile_resolver(
                    request.profile, request.profile_version
                ).margin_m
            ),
            nspd_snapshot_id=nspd_snapshot_id,
            osm_snapshot_id=osm_snapshot_id,
            dgis_snapshot_id=dgis_snapshot_id,
            aoi_id=aoi_id,
            feature_counts=count_features(features or []),
            warnings=warnings or [],
            errors=errors or [],
            started_at=started_at,
            completed_at=self.clock(),
        )
        self.store.finish_run(receipt)
        return receipt
