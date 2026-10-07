"""Boundary adapter between the geodocs second contour and legacy envelopes.

geodocs-mcp answers with plain status/data payloads. The rest of the engine
(normalize, reporting, viewer) consumes the former per-source envelopes of
the deleted tools ``rgis_get_document_vri`` and ``nspd_get_zouit_regimes``;
this module projects the geodocs payloads back into those envelope shapes so
``normalize.py`` stays unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

GEODOCS_ADAPTER_VERSION = "geodocs-mcp"
GEODOCS_SOURCES = ["geodocs-mcp (второй контур документов)"]

# Sync-report statuses of versions whose files are not in the local store yet.
# The reports speak their own vocabulary: raw fetch statuses (pending,
# search_failed) surface as registered_pending_fetch (new refs) or
# linked_existing (already known refs).
ACQUIRE_CANDIDATE_STATUSES = frozenset(
    {
        "pending",
        "registered_pending_fetch",
        "linked_existing",
        "not_found",
        "search_failed",
    }
)
ACQUIRE_DOC_TYPES = frozenset({"pzz", "general_plan"})
ACQUIRED_STATUSES = frozenset({"cached", "acquired"})
FAILED_QUERY_STATUS = "failed"

VRI_QUERY_TEMPLATE = (
    "Верни таблицы видов разрешённого использования (ВРИ) территориальных зон"
    " {zones} из указанных документов ПЗЗ"
)
VRI_QUERY_ALL_ZONES = (
    "Верни таблицы видов разрешённого использования (ВРИ) всех"
    " территориальных зон из указанных документов ПЗЗ"
)
ZOUIT_REGIMES_QUERY = (
    "Верни режимы и ограничения ЗОУИТ (название зоны, вид зоны, ограничения,"
    " реквизиты правового акта) из указанных документов"
)


def _documents_list(envelope: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(envelope, Mapping):
        return []
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        return []
    documents = data.get("documents")
    if not isinstance(documents, list):
        return []
    return [document for document in documents if isinstance(document, Mapping)]


def acquire_candidates(
    sync_envelopes: Iterable[Mapping[str, Any] | None],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Pending/not_found PZZ and general-plan versions from sync reports."""

    candidates: list[dict[str, Any]] = []
    for envelope in sync_envelopes:
        for document in _documents_list(envelope):
            doc_type = str(document.get("doc_type") or "")
            if doc_type not in ACQUIRE_DOC_TYPES:
                continue
            if str(document.get("status") or "") not in ACQUIRE_CANDIDATE_STATUSES:
                continue
            number = document.get("number")
            municipality = document.get("municipality")
            if not number or not municipality:
                continue
            candidates.append(
                {
                    "version_id": document.get("version_id"),
                    "doc_type": doc_type,
                    "municipality": str(municipality),
                    "number": str(number),
                    "version_date": (
                        str(document["version_date"])
                        if document.get("version_date") not in (None, "")
                        else None
                    ),
                }
            )
            if len(candidates) >= limit:
                return candidates
    return candidates


def sync_zone_codes(envelope: Mapping[str, Any] | None) -> list[str]:
    """Territorial zone codes discovered by the RGIS sync report."""

    if not isinstance(envelope, Mapping):
        return []
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        return []
    zone_codes = data.get("zone_codes")
    if not isinstance(zone_codes, list):
        return []
    return [str(zone) for zone in zone_codes if zone not in (None, "")]


def sync_version_ids(envelope: Mapping[str, Any] | None) -> list[int]:
    """All version identifiers of one sync report, in report order."""

    result: list[int] = []
    for document in _documents_list(envelope):
        version_id = document.get("version_id")
        if isinstance(version_id, int) and version_id not in result:
            result.append(version_id)
    return result


def downloaded_pzz_version_ids(
    rgis_sync_envelope: Mapping[str, Any] | None,
    acquire_attempts: Sequence[Mapping[str, Any]],
) -> list[int]:
    """PZZ versions usable for queries: downloaded at sync or just acquired."""

    result: list[int] = []
    for document in _documents_list(rgis_sync_envelope):
        if str(document.get("doc_type") or "") != "pzz":
            continue
        if str(document.get("status") or "") != "downloaded":
            continue
        version_id = document.get("version_id")
        if isinstance(version_id, int) and version_id not in result:
            result.append(version_id)
    for attempt in acquire_attempts:
        candidate = attempt.get("candidate")
        if not isinstance(candidate, Mapping) or candidate.get("doc_type") != "pzz":
            continue
        result_payload = attempt.get("result")
        if not isinstance(result_payload, Mapping):
            continue
        if str(result_payload.get("status") or "") not in ACQUIRED_STATUSES:
            continue
        refs = result_payload.get("refs")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            version_id = ref.get("version_id")
            if isinstance(version_id, int) and version_id not in result:
                result.append(version_id)
    return result


def vri_query(zone_codes: Sequence[str]) -> str:
    """Query text for the geodocs static:vri fast-path (zone codes drive it)."""

    if zone_codes:
        return VRI_QUERY_TEMPLATE.format(zones=", ".join(zone_codes))
    return VRI_QUERY_ALL_ZONES


def _documents_by_version(
    result: Mapping[str, Any],
) -> dict[int, Mapping[str, Any]]:
    documents = result.get("documents")
    if not isinstance(documents, list):
        return {}
    by_version: dict[int, Mapping[str, Any]] = {}
    for document in documents:
        if not isinstance(document, Mapping):
            continue
        version_id = document.get("version_id")
        if isinstance(version_id, int):
            by_version[version_id] = document
    return by_version


def _adapter_version(result: Mapping[str, Any]) -> str:
    executor = result.get("executor")
    if isinstance(executor, str) and executor:
        return f"{GEODOCS_ADAPTER_VERSION}/{executor}"
    return GEODOCS_ADAPTER_VERSION


def _warnings_list(result: Mapping[str, Any]) -> list[str]:
    warnings = result.get("warnings")
    if not isinstance(warnings, list):
        return []
    return [str(warning) for warning in warnings]


def _base_envelope(
    result: Mapping[str, Any],
    *,
    cadastral_number: str,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    status = str(result.get("status") or FAILED_QUERY_STATUS)
    ok = status != FAILED_QUERY_STATUS
    error: dict[str, Any] | None = None
    if not ok:
        message = result.get("error")
        if not isinstance(message, str) or not message:
            message = "; ".join(_warnings_list(result)) or "query_documents failed"
        error = {"code": "geodocs_query_failed", "message": message}
    return {
        "ok": ok,
        "data": {
            "applicable": True,
            "cadastral_number": cadastral_number,
            **data,
            "warnings": _warnings_list(result),
            "partial": status == "partial",
        },
        "error": error,
        "metadata": {
            "adapter_version": _adapter_version(result),
            "sources": GEODOCS_SOURCES,
            "executor": result.get("executor"),
        },
    }


def vri_envelope_from_query(
    result: Mapping[str, Any],
    *,
    cadastral_number: str,
) -> dict[str, Any]:
    """Project a geodocs query result into the rgis_get_document_vri envelope."""

    documents = _documents_by_version(result)
    raw_data = result.get("data")
    raw_zones = raw_data.get("zones") if isinstance(raw_data, Mapping) else None
    zones: list[dict[str, Any]] = []
    for zone in raw_zones if isinstance(raw_zones, list) else []:
        if not isinstance(zone, Mapping):
            continue
        extractions: list[dict[str, Any]] = []
        raw_extractions = zone.get("extractions")
        for item in raw_extractions if isinstance(raw_extractions, list) else []:
            if not isinstance(item, Mapping):
                continue
            version_id = item.get("version_id")
            document = documents.get(version_id, {})
            table = item.get("table")
            table = table if isinstance(table, Mapping) else {}
            extractions.append(
                {
                    "document": {
                        "version_id": version_id,
                        "doc_type": document.get("doc_type"),
                        "municipality": document.get("municipality"),
                        "number": document.get("number"),
                        "version_date": document.get("version_date"),
                    },
                    "source_file": table.get("source_file"),
                    "extractor": item.get("extractor") or result.get("executor"),
                    "confidence": table.get("confidence"),
                    "counts": table.get("counts") or {},
                    "items": table.get("items") or [],
                    "status": item.get("status"),
                    "detail": item.get("detail"),
                }
            )
        # normalize.document_vri_facts reads extractions[0]: the extraction
        # carrying the VRI table must come first.
        extractions.sort(key=lambda extraction: not extraction["items"])
        zones.append(
            {
                "zone_code": zone.get("zone_code"),
                "found": zone.get("found") is True,
                "extractions": extractions,
            }
        )
    return _base_envelope(
        result,
        cadastral_number=cadastral_number,
        data={
            "zones": zones,
        },
    )


def zouit_regimes_envelope_from_query(
    result: Mapping[str, Any],
    *,
    cadastral_number: str,
) -> dict[str, Any]:
    """Project a geodocs query result into the nspd_get_zouit_regimes envelope."""

    documents = _documents_by_version(result)
    raw_data = result.get("data")
    raw_regimes = raw_data.get("regimes") if isinstance(raw_data, Mapping) else None
    regimes: list[dict[str, Any]] = []
    for regime in raw_regimes if isinstance(raw_regimes, list) else []:
        if not isinstance(regime, Mapping):
            continue
        version_id = regime.get("version_id")
        document = documents.get(version_id, {})
        regimes.append(
            {
                "registry_number": regime.get("registry_number"),
                "name": regime.get("name"),
                "zone_type": regime.get("zone_type"),
                "registration_date": regime.get("registration_date"),
                "restrictions": regime.get("restrictions"),
                "relation_kind": regime.get("relation_kind"),
                "parcel_coverage_percent": regime.get("parcel_coverage_percent"),
                "document": {
                    "version_id": version_id,
                    "number": document.get("number"),
                    "version_date": document.get("version_date"),
                },
                "extractor": regime.get("extractor") or result.get("executor"),
            }
        )
    return _base_envelope(
        result,
        cadastral_number=cadastral_number,
        data={
            "regimes": regimes,
        },
    )
