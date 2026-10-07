"""Deterministic orchestration of NSPD, OSM, 2GIS, and optional RGIS."""

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
    GeodocsClient,
    NspdDocumentsClient,
    NspdSourceClient,
    OsmSourceClient,
    RgisDocumentsClient,
    RgisSourceClient,
)
from terralogic_engine.acquisition.geodocs_adapter import (
    GEODOCS_ADAPTER_VERSION,
    ZOUIT_REGIMES_QUERY,
    acquire_candidates,
    downloaded_pzz_version_ids,
    sync_version_ids,
    sync_zone_codes,
    vri_envelope_from_query,
    vri_query,
    zouit_regimes_envelope_from_query,
)
from terralogic_engine.acquisition.geometry import (
    build_area_of_interest,
    prepare_parcel_geometry,
)
from terralogic_engine.acquisition.normalize import (
    count_features,
    dgis_features,
    document_vri_facts,
    nspd_layer_features,
    osm_features,
    parcel_feature,
    rgis_features,
    zouit_regime_facts,
)
from terralogic_engine.acquisition.profiles import (
    CollectionProfile,
    get_collection_profile,
)
from terralogic_engine.domain.models import (
    CaseFact,
    CollectionReceipt,
    CollectionRequest,
    GeoFeature,
    ReceiptStatus,
    SourceName,
    utc_now,
)
from terralogic_engine.store.base import CaseStore

DOCUMENT_SYNC_SNAPSHOT_TYPE = "documents_sync"
DOCUMENT_VRI_SNAPSHOT_TYPE = "documents_vri"
DOCUMENT_REGIMES_SNAPSHOT_TYPE = "documents_regimes"
DOCUMENT_ACQUIRE_SNAPSHOT_TYPE = "document_acquire"
DOCUMENTS_SNAPSHOT_TYPE_PREFIX = "document"
EXTERNAL_DOCUMENT_WARNING = "external document source not configured"


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
            data.get("partial") is True,
        )
    )


def _exception_payload(exc: BaseException) -> dict[str, str]:
    return {
        "transport_error": str(exc),
        "exception_type": type(exc).__name__,
    }


def _external_document_warning(envelope: Mapping[str, Any]) -> str | None:
    """Detect a not_found PZZ whose full text lives on an external source."""

    data = envelope.get("data")
    if not isinstance(data, Mapping):
        return None
    documents = data.get("documents")
    if not isinstance(documents, list):
        return None
    for document in documents:
        if not isinstance(document, Mapping):
            continue
        if str(document.get("doc_type") or "") != "pzz":
            continue
        if document.get("status") != "not_found":
            continue
        sources = document.get("sources")
        if isinstance(sources, list) and any(
            isinstance(source, str) and source.startswith(("http://", "https://"))
            for source in sources
        ):
            return EXTERNAL_DOCUMENT_WARNING
    return None


class AcquisitionPipeline:
    """Collect source data, persist snapshots, and return a compact receipt."""

    def __init__(
        self,
        *,
        store: CaseStore,
        nspd: NspdSourceClient,
        osm: OsmSourceClient,
        dgis: DgisSourceClient,
        rgis: RgisSourceClient | None = None,
        rgis_documents: RgisDocumentsClient | None = None,
        nspd_documents: NspdDocumentsClient | None = None,
        geodocs: GeodocsClient | None = None,
        profile_resolver: Callable[[str, str], CollectionProfile] = (
            get_collection_profile
        ),
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self.nspd = nspd
        self.osm = osm
        self.dgis = dgis
        self.rgis = rgis
        self.rgis_documents = rgis_documents
        self.nspd_documents = nspd_documents
        self.geodocs = geodocs
        self.profile_resolver = profile_resolver
        self.clock = clock

    async def _collect_nspd_documents(self, cadastral_number: str) -> Any:
        if self.nspd_documents is None:
            return None
        return await self.nspd_documents.sync_parcel_documents(cadastral_number)

    async def _collect_rgis_documents(self, cadastral_number: str) -> Any:
        """RGIS document sync (discovery only; no downloads on this contour)."""

        if self.rgis_documents is None:
            return None
        return await self.rgis_documents.sync_parcel_documents(cadastral_number)

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
        rgis_snapshot_id: str | None = None
        aoi_id: str | None = None
        all_features: list[GeoFeature] = []
        case_facts: list[CaseFact] = []
        warnings: list[str] = []
        errors: list[str] = []
        rgis_sync_envelope: dict[str, Any] | None = None
        nspd_sync_envelope: dict[str, Any] | None = None

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
                parcel_data.get("parcel") if isinstance(parcel_data, Mapping) else None
            )
            if not isinstance(parcel, Mapping) or not isinstance(
                parcel.get("geojson"), dict
            ):
                raise TypeError(
                    "nspd_get_land_parcel_info(detail='full') returned no parcel "
                    "GeoJSON"
                )

            parcel_geometry, geometry_warnings = prepare_parcel_geometry(
                parcel["geojson"]
            )
            normalized_geometry = dict(mapping(parcel_geometry))
            warnings.extend(geometry_warnings)
            margin_m = (
                request.margin_m if request.margin_m is not None else profile.margin_m
            )
            provisional_aoi = build_area_of_interest(
                case_id=request.case_id,
                source_snapshot_id="pending",
                parcel_geojson=normalized_geometry,
                margin_m=margin_m,
            )
            longitude, latitude = provisional_aoi.representative_point
            radius_m = math.ceil(provisional_aoi.search_radius_m)

            async def _collect_rgis() -> Any:
                if self.rgis is None or not request.cadastral_number.startswith("50:"):
                    return None
                return await asyncio.gather(
                    self.rgis.get_land_parcel_info(
                        request.cadastral_number,
                        detail="full",
                    ),
                    self.rgis.analyze_land_parcel_layers(
                        request.cadastral_number,
                        blocks=profile.rgis_blocks,
                        include_geometry=True,
                        limit_per_layer=profile.rgis_limit_per_layer,
                        zoom=profile.rgis_zoom,
                    ),
                    self._collect_rgis_documents(request.cadastral_number),
                    return_exceptions=True,
                )

            (
                layer_result,
                osm_result,
                social_result,
                transport_result,
                rgis_result,
                nspd_documents_result,
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
                _collect_rgis(),
                self._collect_nspd_documents(request.cadastral_number),
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

            parcel_record = parcel_feature(
                case_id=request.case_id,
                snapshot_id=nspd_snapshot.id,
                parcel=dict(parcel),
                geometry=aoi.parcel_geometry,
            )
            all_features.append(parcel_record)
            all_features.extend(
                nspd_layer_features(
                    case_id=request.case_id,
                    snapshot_id=nspd_snapshot.id,
                    envelope=layer_envelope,
                )
            )

            if nspd_documents_result is not None:
                if isinstance(nspd_documents_result, BaseException):
                    self._source_result(
                        nspd_documents_result,
                        tool="nspd_sync_parcel_documents",
                        partial_warning="NSPD document sync is partial",
                        warnings=warnings,
                        errors=errors,
                    )
                else:
                    nspd_sync_envelope, _stored_nspd_sync = self._source_result(
                        nspd_documents_result,
                        tool="nspd_sync_parcel_documents",
                        partial_warning="NSPD document sync is partial",
                        warnings=warnings,
                        errors=errors,
                    )
                    if nspd_sync_envelope is not None:
                        self.store.save_snapshot(
                            case_id=request.case_id,
                            run_id=run_id,
                            source="nspd",
                            payload=_json_bytes(nspd_sync_envelope),
                            adapter_version=_adapter_version(nspd_sync_envelope),
                            metadata={
                                "snapshot_type": DOCUMENT_SYNC_SNAPSHOT_TYPE,
                                "tools": ["nspd_sync_parcel_documents"],
                                "profile": profile.name,
                                "profile_version": profile.version,
                            },
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

            if rgis_result is not None:
                rgis_info_result, rgis_layer_result, rgis_documents_result = rgis_result
                rgis_info_envelope, stored_rgis_info = self._source_result(
                    rgis_info_result,
                    tool="rgis_get_land_parcel_info",
                    partial_warning="RGIS parcel information is partial",
                    warnings=warnings,
                    errors=errors,
                )
                rgis_layer_envelope, stored_rgis_layers = self._source_result(
                    rgis_layer_result,
                    tool="rgis_analyze_land_parcel_layers",
                    partial_warning="RGIS layer coverage is partial",
                    warnings=warnings,
                    errors=errors,
                )
                rgis_snapshot = self.store.save_snapshot(
                    case_id=request.case_id,
                    run_id=run_id,
                    source="rgis",
                    payload=_json_bytes(
                        {
                            "parcel_info": stored_rgis_info,
                            "layer_analysis": stored_rgis_layers,
                        }
                    ),
                    adapter_version=_adapter_version(
                        rgis_info_envelope or rgis_layer_envelope or {}
                    ),
                    metadata={
                        "tools": [
                            "rgis_get_land_parcel_info",
                            "rgis_analyze_land_parcel_layers",
                        ],
                        "blocks": list(profile.rgis_blocks),
                        "profile": profile.name,
                        "profile_version": profile.version,
                    },
                )
                rgis_snapshot_id = rgis_snapshot.id
                all_features.extend(
                    rgis_features(
                        case_id=request.case_id,
                        snapshot_id=rgis_snapshot.id,
                        envelope=rgis_layer_envelope,
                    )
                )
                rgis_sync_envelope = self._store_rgis_documents_sync(
                    request=request,
                    run_id=run_id,
                    profile=profile,
                    documents_result=rgis_documents_result,
                    warnings=warnings,
                    errors=errors,
                )

            await self._run_geodocs_contour(
                request=request,
                run_id=run_id,
                profile=profile,
                parcel_feature_id=parcel_record.id,
                rgis_sync_envelope=rgis_sync_envelope,
                nspd_sync_envelope=nspd_sync_envelope,
                case_facts=case_facts,
                warnings=warnings,
                errors=errors,
            )

            if case_facts:
                self.store.save_facts(request.case_id, case_facts)
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
                rgis_snapshot_id=rgis_snapshot_id,
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
                rgis_snapshot_id=rgis_snapshot_id,
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

    def _store_rgis_documents_sync(
        self,
        *,
        request: CollectionRequest,
        run_id: str,
        profile: CollectionProfile,
        documents_result: Any,
        warnings: list[str],
        errors: list[str],
    ) -> dict[str, Any] | None:
        """Store the RGIS sync snapshot and return its envelope for the contour."""

        if documents_result is None:
            return None
        if isinstance(documents_result, BaseException):
            self._source_result(
                documents_result,
                tool="rgis_sync_parcel_documents",
                partial_warning="RGIS document sync is partial",
                warnings=warnings,
                errors=errors,
            )
            return None
        sync_envelope, _stored_sync = self._source_result(
            documents_result,
            tool="rgis_sync_parcel_documents",
            partial_warning="RGIS document sync is partial",
            warnings=warnings,
            errors=errors,
        )
        if sync_envelope is None:
            return None
        self.store.save_snapshot(
            case_id=request.case_id,
            run_id=run_id,
            source="rgis",
            payload=_json_bytes(sync_envelope),
            adapter_version=_adapter_version(sync_envelope),
            metadata={
                "snapshot_type": DOCUMENT_SYNC_SNAPSHOT_TYPE,
                "tools": ["rgis_sync_parcel_documents"],
                "profile": profile.name,
                "profile_version": profile.version,
            },
        )
        return sync_envelope

    async def _run_geodocs_contour(
        self,
        *,
        request: CollectionRequest,
        run_id: str,
        profile: CollectionProfile,
        parcel_feature_id: str,
        rgis_sync_envelope: dict[str, Any] | None,
        nspd_sync_envelope: dict[str, Any] | None,
        case_facts: list[CaseFact],
        warnings: list[str],
        errors: list[str],
    ) -> None:
        """Second document contour: acquire pending versions, then query facts.

        Without a configured geodocs client (or with the contour disabled by
        the collection profile) the pipeline keeps the old behaviour: sync
        snapshots only plus the external-document warning.
        """

        if self.geodocs is None or not (
            profile.documents_acquire or profile.documents_query
        ):
            # Без второго контура (нет клиента или контур выключен профилем)
            # остаётся прежнее предупреждение про недобранный внешний документ.
            warning = _external_document_warning(rgis_sync_envelope or {})
            if warning is not None:
                warnings.append(warning)
            return

        acquire_attempts: list[dict[str, Any]] = []
        if profile.documents_acquire:
            candidates = acquire_candidates(
                [rgis_sync_envelope, nspd_sync_envelope],
                limit=profile.documents_acquire_limit,
            )
            for candidate in candidates:
                try:
                    acquire_result = await self.geodocs.acquire_documents(
                        candidate["municipality"],
                        candidate["doc_type"],
                        number=candidate["number"],
                        version_date=candidate["version_date"],
                    )
                except Exception as exc:  # noqa: BLE001 - handled via _source_result
                    self._source_result(
                        exc,
                        tool="geodocs acquire_documents",
                        partial_warning="Geodocs document acquisition is partial",
                        warnings=warnings,
                        errors=errors,
                    )
                    acquire_attempts.append(
                        {"candidate": candidate, "error": _exception_payload(exc)}
                    )
                    continue
                acquire_attempts.append(
                    {"candidate": candidate, "result": dict(acquire_result)}
                )
        # The acquire snapshot is stored even with zero attempts: it marks the
        # contour as executed in this run, which the receipt-reuse check needs.
        self.store.save_snapshot(
            case_id=request.case_id,
            run_id=run_id,
            source="geodocs",
            payload=_json_bytes(
                {"tool": "acquire_documents", "attempts": acquire_attempts}
            ),
            adapter_version=GEODOCS_ADAPTER_VERSION,
            metadata={
                "snapshot_type": DOCUMENT_ACQUIRE_SNAPSHOT_TYPE,
                "tools": ["acquire_documents"],
                "documents_acquire": profile.documents_acquire,
                "documents_query": profile.documents_query,
                "profile": profile.name,
                "profile_version": profile.version,
            },
        )

        pzz_version_ids = downloaded_pzz_version_ids(
            rgis_sync_envelope, acquire_attempts
        )
        if not pzz_version_ids:
            # PZZ остался недобран даже агентным ярусом (или второго контура
            # нет вовсе) — прежнее предупреждение про внешний источник.
            warning = _external_document_warning(rgis_sync_envelope or {})
            if warning is not None:
                warnings.append(warning)
        if not profile.documents_query:
            return

        if pzz_version_ids:
            await self._query_document_vri(
                request=request,
                run_id=run_id,
                profile=profile,
                parcel_feature_id=parcel_feature_id,
                pzz_version_ids=pzz_version_ids,
                zone_codes=sync_zone_codes(rgis_sync_envelope),
                case_facts=case_facts,
                warnings=warnings,
                errors=errors,
            )
        nspd_version_ids = sync_version_ids(nspd_sync_envelope)
        if nspd_version_ids:
            await self._query_zouit_regimes(
                request=request,
                run_id=run_id,
                profile=profile,
                parcel_feature_id=parcel_feature_id,
                nspd_version_ids=nspd_version_ids,
                case_facts=case_facts,
                warnings=warnings,
                errors=errors,
            )

    async def _query_document_vri(
        self,
        *,
        request: CollectionRequest,
        run_id: str,
        profile: CollectionProfile,
        parcel_feature_id: str,
        pzz_version_ids: list[int],
        zone_codes: list[str],
        case_facts: list[CaseFact],
        warnings: list[str],
        errors: list[str],
    ) -> None:
        """VRI tables via geodocs query_documents, projected into the old envelope."""

        assert self.geodocs is not None
        try:
            query_result = await self.geodocs.query_documents(
                pzz_version_ids,
                vri_query(zone_codes),
            )
        except Exception as exc:  # noqa: BLE001 - handled via _source_result
            self._source_result(
                exc,
                tool="geodocs query_documents (document_vri)",
                partial_warning="Geodocs document VRI extraction is partial",
                warnings=warnings,
                errors=errors,
            )
            return
        vri_envelope, _stored_vri = self._source_result(
            vri_envelope_from_query(
                query_result,
                cadastral_number=request.cadastral_number,
            ),
            tool="geodocs query_documents (document_vri)",
            partial_warning="Geodocs document VRI extraction is partial",
            warnings=warnings,
            errors=errors,
        )
        if vri_envelope is None:
            return
        vri_snapshot = self.store.save_snapshot(
            case_id=request.case_id,
            run_id=run_id,
            source="geodocs",
            payload=_json_bytes(vri_envelope),
            adapter_version=_adapter_version(vri_envelope),
            metadata={
                "snapshot_type": DOCUMENT_VRI_SNAPSHOT_TYPE,
                "tools": ["query_documents"],
                "profile": profile.name,
                "profile_version": profile.version,
            },
        )
        if vri_envelope.get("ok") is True:
            case_facts.extend(
                document_vri_facts(
                    case_id=request.case_id,
                    subject_feature_id=parcel_feature_id,
                    snapshot_id=vri_snapshot.id,
                    envelope=vri_envelope,
                )
            )

    async def _query_zouit_regimes(
        self,
        *,
        request: CollectionRequest,
        run_id: str,
        profile: CollectionProfile,
        parcel_feature_id: str,
        nspd_version_ids: list[int],
        case_facts: list[CaseFact],
        warnings: list[str],
        errors: list[str],
    ) -> None:
        """ZOUIT regimes via geodocs query_documents, projected as before."""

        assert self.geodocs is not None
        try:
            query_result = await self.geodocs.query_documents(
                nspd_version_ids,
                ZOUIT_REGIMES_QUERY,
            )
        except Exception as exc:  # noqa: BLE001 - handled via _source_result
            self._source_result(
                exc,
                tool="geodocs query_documents (zouit_regime)",
                partial_warning="Geodocs ZOUIT regime coverage is partial",
                warnings=warnings,
                errors=errors,
            )
            return
        regimes_envelope, _stored_regimes = self._source_result(
            zouit_regimes_envelope_from_query(
                query_result,
                cadastral_number=request.cadastral_number,
            ),
            tool="geodocs query_documents (zouit_regime)",
            partial_warning="Geodocs ZOUIT regime coverage is partial",
            warnings=warnings,
            errors=errors,
        )
        if regimes_envelope is None:
            return
        regimes_snapshot = self.store.save_snapshot(
            case_id=request.case_id,
            run_id=run_id,
            source="geodocs",
            payload=_json_bytes(regimes_envelope),
            adapter_version=_adapter_version(regimes_envelope),
            metadata={
                "snapshot_type": DOCUMENT_REGIMES_SNAPSHOT_TYPE,
                "tools": ["query_documents"],
                "profile": profile.name,
                "profile_version": profile.version,
            },
        )
        if regimes_envelope.get("ok") is True:
            case_facts.extend(
                zouit_regime_facts(
                    case_id=request.case_id,
                    subject_feature_id=parcel_feature_id,
                    snapshot_id=regimes_snapshot.id,
                    envelope=regimes_envelope,
                )
            )

    def _reusable_receipt(
        self, request: CollectionRequest, profile: CollectionProfile
    ) -> CollectionReceipt | None:
        if request.refresh_policy == "always":
            return None
        latest = self.store.get_latest_collection_receipt(request.case_id)
        if latest is None or latest.status not in {"complete", "partial"}:
            return None
        requested_margin = (
            request.margin_m if request.margin_m is not None else profile.margin_m
        )
        if (
            latest.profile != request.profile
            or latest.profile_version != request.profile_version
            or latest.margin_m != requested_margin
        ):
            return None
        expects_rgis = self.rgis is not None and request.cadastral_number.startswith(
            "50:"
        )
        if expects_rgis != (latest.rgis_snapshot_id is not None):
            return None
        expects_rgis_documents = (
            self.rgis_documents is not None
            and request.cadastral_number.startswith("50:")
        )
        if expects_rgis_documents != self._run_has_documents_snapshot(
            request.case_id, latest.run_id, source="rgis"
        ):
            return None
        if (self.nspd_documents is not None) != self._run_has_documents_snapshot(
            request.case_id, latest.run_id, source="nspd"
        ):
            return None
        expects_geodocs = self.geodocs is not None and (
            profile.documents_acquire or profile.documents_query
        )
        if expects_geodocs != self._run_has_documents_snapshot(
            request.case_id, latest.run_id, source="geodocs"
        ):
            return None
        if request.refresh_policy == "never":
            return latest.model_copy(update={"reused": True})
        age = self.clock() - latest.completed_at
        if age <= timedelta(seconds=profile.stale_after_seconds):
            return latest.model_copy(update={"reused": True})
        return None

    def _run_has_documents_snapshot(
        self, case_id: str, run_id: str, *, source: SourceName
    ) -> bool:
        return any(
            snapshot.run_id == run_id
            and str(snapshot.metadata.get("snapshot_type") or "").startswith(
                DOCUMENTS_SNAPSHOT_TYPE_PREFIX
            )
            for snapshot in self.store.list_snapshots(case_id, source=source)
        )

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
        rgis_snapshot_id: str | None = None,
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
            rgis_snapshot_id=rgis_snapshot_id,
            aoi_id=aoi_id,
            feature_counts=count_features(features or []),
            warnings=warnings or [],
            errors=errors or [],
            started_at=started_at,
            completed_at=self.clock(),
        )
        self.store.finish_run(receipt)
        return receipt
