"""Постатусная сводка сбора run'а: источники и нормативные документы.

Агрегируется из CaseStore (receipt + снапшоты текущего run) без обращений
к внешним сервисам. Заполняет ``PrepareCaseResult.collection`` и обслуживает
команду /status в delivery-интерфейсах (Telegram-бот).

Вывод статусов детерминирован: явного per-source статуса в CaseStore нет,
он восстанавливается из ``receipt.{*_snapshot_id, errors, warnings}`` и
конвертов снапшотов. Документы стыкуются по ``version_id`` (либо по
``doc_type/number/version_date``, когда version_id отсутствует) из снапшотов
``documents_sync`` → ``document_acquire`` → ``documents_vri``.
"""

from __future__ import annotations

import json
from typing import Any

from terralogic_engine.domain.models import CollectionReceipt
from terralogic_engine.store.base import CaseStore

from .models import CollectionSummary, DocumentStatusEntry, SourceStatusEntry

_DOCUMENT_SYNC = "documents_sync"
_DOCUMENT_ACQUIRE = "document_acquire"
_DOCUMENT_VRI = "documents_vri"
_DOCUMENT_REGIMES = "documents_regimes"

# tool (префикс ошибки «<tool>: …») → источник сводки
_TOOL_SOURCES: tuple[tuple[str, str], ...] = (
    ("nspd_get_land_parcel_info", "nspd"),
    ("nspd_analyze_land_parcel_layers", "nspd"),
    ("osm_", "osm"),
    ("dgis_", "dgis"),
    ("rgis_get_", "rgis"),
    ("rgis_analyze_", "rgis"),
    ("nspd_sync_parcel_documents", "documents_sync"),
    ("rgis_sync_parcel_documents", "documents_sync"),
    ("geodocs ", "geodocs"),
)

_MAIN_SNAPSHOT_IDS = {
    "nspd": "nspd_snapshot_id",
    "osm": "osm_snapshot_id",
    "dgis": "dgis_snapshot_id",
    "rgis": "rgis_snapshot_id",
}


def _source_for_tool(tool: str) -> str | None:
    for prefix, source in _TOOL_SOURCES:
        if tool.startswith(prefix):
            return source
    return None


def _errors_by_source(errors: list[str]) -> dict[str, list[str]]:
    by_source: dict[str, list[str]] = {}
    for error in errors:
        tool = error.split(":", 1)[0]
        source = _source_for_tool(tool)
        if source is not None:
            by_source.setdefault(source, []).append(error)
    return by_source


def _run_snapshots(store: CaseStore, receipt: CollectionReceipt):
    return [
        snapshot
        for snapshot in store.list_snapshots(receipt.case_id)
        if snapshot.run_id == receipt.run_id
    ]


def _snapshot_payload(store: CaseStore, snapshot) -> dict[str, Any]:
    try:
        return json.loads(store.load_snapshot(snapshot.case_id, snapshot.id))
    except Exception:  # noqa: BLE001 — битый снапшот не должен ломать сводку
        return {}


def _sources(
    store: CaseStore,
    receipt: CollectionReceipt,
    run_snapshots,
    errors_by_source: dict[str, list[str]],
) -> list[SourceStatusEntry]:
    entries: list[SourceStatusEntry] = []
    typed: dict[str, list] = {}
    for snapshot in run_snapshots:
        snapshot_type = (snapshot.metadata or {}).get("snapshot_type")
        if snapshot_type in (
            _DOCUMENT_SYNC,
            _DOCUMENT_ACQUIRE,
            _DOCUMENT_VRI,
            _DOCUMENT_REGIMES,
        ):
            key = (
                "documents_sync"
                if snapshot_type == _DOCUMENT_SYNC
                else "geodocs"
            )
            typed.setdefault(key, []).append(snapshot)

    def has_snapshot(source: str) -> bool:
        if source in ("documents_sync", "geodocs"):
            return bool(typed.get(source))
        return getattr(receipt, _MAIN_SNAPSHOT_IDS[source]) is not None

    for source in ("nspd", "rgis", "osm", "dgis", "documents_sync", "geodocs"):
        errors = errors_by_source.get(source, [])
        present = has_snapshot(source)
        if errors and present:
            status, detail = "partial", "; ".join(errors)
        elif errors:
            status, detail = "failed", "; ".join(errors)
        elif present:
            status, detail = "ok", None
        else:
            status, detail = "skipped", None
        if source == "rgis" and present and not errors:
            payload = _snapshot_payload_by_id(store, receipt)
            if payload is not None:
                envelopes = [
                    payload.get(key) or {}
                    for key in ("parcel_info", "layer_analysis")
                ] or [payload]
                for envelope in envelopes:
                    data = envelope.get("data") or {}
                    if data.get("applicable") is False:
                        status = "not_applicable"
                        detail = data.get("reason")
                        break
        entries.append(SourceStatusEntry(source=source, status=status, detail=detail))
    return entries


def _snapshot_payload_by_id(
    store: CaseStore, receipt: CollectionReceipt
) -> dict[str, Any] | None:
    if receipt.rgis_snapshot_id is None:
        return None
    try:
        return json.loads(
            store.load_snapshot(receipt.case_id, receipt.rgis_snapshot_id)
        )
    except Exception:  # noqa: BLE001
        return None


def _doc_key(doc: dict[str, Any]) -> str:
    version_id = doc.get("version_id")
    if version_id is not None:
        return str(version_id)
    return "{}:{}:{}".format(
        doc.get("doc_type"), doc.get("number"), doc.get("version_date")
    )


def _slot(entries: dict[str, dict[str, Any]], key: str, base: dict[str, Any]):
    """Слот записи документа: создание или ДОПОЛНЕНИЕ недостающих полей
    (снапшоты читаются в порядке regimes/vri → acquire → sync)."""
    slot = entries.setdefault(key, {})
    for field, value in base.items():
        if value is not None and slot.get(field) is None:
            slot[field] = value
    return slot


def _merge_document(entries: dict[str, dict[str, Any]], store: CaseStore, snapshot) -> None:
    """Добавляет в entries записи одного снапшота документного контура."""
    payload = _snapshot_payload(store, snapshot)
    snapshot_type = (snapshot.metadata or {}).get("snapshot_type")
    if snapshot_type == _DOCUMENT_SYNC:
        data = payload.get("data") or {}
        for doc in data.get("documents") or []:
            slot = _slot(
                entries,
                _doc_key(doc),
                {
                    "doc_type": doc.get("doc_type"),
                    "number": doc.get("number"),
                    "version_date": doc.get("version_date"),
                    "municipality": doc.get("municipality"),
                },
            )
            slot["sync_status"] = doc.get("status")
    elif snapshot_type == _DOCUMENT_ACQUIRE:
        for attempt in payload.get("attempts") or []:
            candidate = attempt.get("candidate") or {}
            if "error" in attempt:
                error = attempt["error"] or {}
                reason = error.get("transport_error") or str(error)
                slot = _slot(
                    entries,
                    _doc_key(candidate),
                    {
                        "doc_type": candidate.get("doc_type"),
                        "number": candidate.get("number"),
                        "version_date": candidate.get("version_date"),
                        "municipality": candidate.get("municipality"),
                    },
                )
                slot["acquire_status"] = "failed"
                slot["reason"] = reason
                continue
            result = attempt.get("result") or {}
            refs = result.get("refs") or []
            warnings = [str(w) for w in result.get("warnings") or []]
            targets = refs or [candidate]
            for ref in targets:
                slot = _slot(
                    entries,
                    _doc_key(ref),
                    {
                        "doc_type": ref.get("doc_type") or candidate.get("doc_type"),
                        "number": ref.get("number") or candidate.get("number"),
                        "version_date": (
                            ref.get("version_date") or candidate.get("version_date")
                        ),
                        "municipality": (
                            ref.get("municipality") or candidate.get("municipality")
                        ),
                    },
                )
                slot["acquire_status"] = result.get("status")
                if warnings:
                    slot["reason"] = "; ".join(warnings)
    elif snapshot_type == _DOCUMENT_VRI:
        data = payload.get("data") or {}
        for zone in data.get("zones") or []:
            for extraction in zone.get("extractions") or []:
                version_id = extraction.get("version_id")
                if version_id is None:
                    continue
                slot = _slot(entries, str(version_id), {})
                if extraction.get("status") == "extracted":
                    slot["extracted"] = True
                elif slot.get("reason") is None:
                    detail = extraction.get("detail")
                    if detail:
                        slot["reason"] = str(detail)
    elif snapshot_type == _DOCUMENT_REGIMES:
        data = payload.get("data") or {}
        for regime in data.get("regimes") or []:
            version_id = regime.get("version_id")
            if version_id is not None:
                _slot(entries, str(version_id), {})["extracted"] = True


def _final_status(slot: dict[str, Any]) -> str:
    if slot.get("extracted"):
        return "extracted"
    if slot.get("acquire_status") in {"acquired", "cached"}:
        return "downloaded"
    if slot.get("sync_status") in {"downloaded", "linked_existing"}:
        return "downloaded"
    if slot.get("acquire_status") == "not_found" or slot.get("sync_status") == "not_found":
        return "not_found"
    if (
        slot.get("acquire_status") == "failed"
        or slot.get("sync_status") == "download_failed"
    ):
        return "failed"
    return "pending"


def _documents(store: CaseStore, run_snapshots) -> list[DocumentStatusEntry]:
    entries: dict[str, dict[str, Any]] = {}
    vri_attempted = any(
        (snapshot.metadata or {}).get("snapshot_type") == _DOCUMENT_VRI
        for snapshot in run_snapshots
    )
    for snapshot in run_snapshots:
        snapshot_type = (snapshot.metadata or {}).get("snapshot_type")
        if snapshot_type in (
            _DOCUMENT_SYNC,
            _DOCUMENT_ACQUIRE,
            _DOCUMENT_VRI,
            _DOCUMENT_REGIMES,
        ):
            _merge_document(entries, store, snapshot)
    documents = [
        DocumentStatusEntry(
            doc_type=slot.get("doc_type"),
            number=slot.get("number"),
            version_date=slot.get("version_date"),
            municipality=slot.get("municipality"),
            status=_final_status(slot),
            reason=slot.get("reason"),
        )
        for slot in entries.values()
    ]
    if vri_attempted:
        # ПЗЗ скачан, но таблица ВРИ не извлечена (скан, нет раздела и т.п.) —
        # важная для /status развилка «файл есть, данных нет».
        for doc in documents:
            if doc.doc_type == "pzz" and doc.status == "downloaded":
                doc.reason = doc.reason or "таблица ВРИ не извлечена"
    order = {"extracted": 0, "downloaded": 1, "pending": 2, "not_found": 3, "failed": 4}
    documents.sort(key=lambda doc: (order[doc.status], doc.doc_type or ""))
    return documents


def build_collection_summary(
    store: CaseStore, receipt: CollectionReceipt
) -> CollectionSummary:
    """Сводка сбора для PrepareCaseResult: источники + документы run'а."""
    run_snapshots = _run_snapshots(store, receipt)
    errors_by_source = _errors_by_source(receipt.errors)
    duration = None
    if receipt.started_at and receipt.completed_at:
        duration = (receipt.completed_at - receipt.started_at).total_seconds()
    return CollectionSummary(
        duration_s=round(duration, 1) if duration is not None else None,
        sources=_sources(store, receipt, run_snapshots, errors_by_source),
        documents=_documents(store, run_snapshots),
    )
