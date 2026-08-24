from __future__ import annotations

import json
import sqlite3

from terralogic_acquisition.acquisition.geometry import build_area_of_interest
from terralogic_acquisition.acquisition.normalize import parcel_feature
from terralogic_acquisition.domain.models import (
    CollectionReceipt,
    CollectionRequest,
    utc_now,
)
from terralogic_acquisition.store.local import LocalCaseStore

from .fakes import PARCEL_GEOMETRY


def test_local_case_store_survives_reopen_and_preserves_raw_snapshot(tmp_path) -> None:
    root = tmp_path / "case-store"
    store = LocalCaseStore(root)
    request = CollectionRequest(
        case_id="case-001",
        cadastral_number="52:26:0040002:3823",
        refresh_policy="always",
    )
    case = store.create_case(
        case_id=request.case_id,
        cadastral_number=request.cadastral_number,
        report_profile=request.profile,
    )
    store.begin_run(request, "run-001")
    payload = b'{"ok":true,"source":"nspd"}'
    snapshot = store.save_snapshot(
        case_id=case.case_id,
        run_id="run-001",
        source="nspd",
        payload=payload,
        adapter_version="test-1",
    )
    aoi = build_area_of_interest(
        case_id=case.case_id,
        source_snapshot_id=snapshot.id,
        parcel_geojson=PARCEL_GEOMETRY,
    )
    store.save_area_of_interest(aoi)
    feature = parcel_feature(
        case_id=case.case_id,
        snapshot_id=snapshot.id,
        parcel={"cadastral_number": request.cadastral_number},
        geometry=aoi.parcel_geometry,
    )
    store.save_features(case.case_id, [feature])
    now = utc_now()
    receipt = CollectionReceipt(
        case_id=case.case_id,
        run_id="run-001",
        status="partial",
        nspd_snapshot_id=snapshot.id,
        aoi_id=aoi.id,
        feature_counts={"nspd.parcel": 1},
        started_at=now,
        completed_at=now,
    )
    store.finish_run(receipt)

    reopened = LocalCaseStore(root)

    assert reopened.get_case(case.case_id).status == "partial"
    assert reopened.load_snapshot(case.case_id, snapshot.id) == payload
    assert reopened.get_area_of_interest(case.case_id, aoi.id).geometry_hash == (
        aoi.geometry_hash
    )
    assert reopened.load_features(case.case_id)[0].geometry == aoi.parcel_geometry
    assert reopened.get_latest_collection_receipt(case.case_id) == receipt
    assert reopened.list_collection_receipts(case.case_id) == [receipt]
    manifest = json.loads(
        (root / "cases" / case.case_id / "manifest.json").read_text("utf-8")
    )
    assert manifest["status"] == "partial"
    with sqlite3.connect(root / "cases" / case.case_id / "case.sqlite") as database:
        tables = {
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "case_info",
        "runs",
        "snapshots",
        "areas_of_interest",
        "features",
        "facts",
        "entity_links",
        "metrics",
        "findings",
        "report_sections",
        "artifacts",
    } <= tables


def test_create_case_is_idempotent_but_rejects_other_parcel(tmp_path) -> None:
    store = LocalCaseStore(tmp_path / "store")
    first = store.create_case(
        case_id="case-001",
        cadastral_number="52:26:0040002:3823",
        report_profile="standard_land_report",
    )
    second = store.create_case(
        case_id="case-001",
        cadastral_number="52:26:0040002:3823",
        report_profile="standard_land_report",
    )

    assert second == first

    try:
        store.create_case(
            case_id="case-001",
            cadastral_number="52:18:0080038:124",
            report_profile="standard_land_report",
        )
    except ValueError as exc:
        assert "another cadastral number" in str(exc)
    else:
        raise AssertionError("A case id must not be silently reused for another parcel")
