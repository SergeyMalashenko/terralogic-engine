"""SQLite plus filesystem implementation of the CaseStore boundary."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import uuid4

from shapely.geometry import mapping, shape
from shapely.wkb import loads as load_wkb

from terralogic_acquisition.analytics.models import AnalysisResult
from terralogic_acquisition.domain.models import (
    AreaOfInterest,
    CaseInfo,
    CollectionReceipt,
    CollectionRequest,
    GeoFeature,
    SourceName,
    SourceSnapshot,
    utc_now,
)


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


class LocalCaseStore:
    """Store every case in an isolated SQLite database and file directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.cases_root = self.root / "cases"
        self.cases_root.mkdir(parents=True, exist_ok=True)

    def _case_dir(self, case_id: str) -> Path:
        CollectionRequest.validate_case_id(case_id)
        return self.cases_root / case_id

    def _database_path(self, case_id: str) -> Path:
        return self._case_dir(case_id) / "case.sqlite"

    def _connect(self, case_id: str) -> sqlite3.Connection:
        database = self._database_path(case_id)
        if not database.exists():
            raise KeyError(f"Case {case_id!r} does not exist")
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema(connection)
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        """Apply additive migrations to previously created CaseStores."""

        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "areas_of_interest" not in tables:
            return
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(areas_of_interest)")
        }
        changed = False
        if "metrics_json" not in columns:
            connection.execute(
                "ALTER TABLE areas_of_interest "
                "ADD COLUMN metrics_json TEXT NOT NULL DEFAULT '{}'"
            )
            changed = True
        if "analysis_results" not in tables:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS analysis_results (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    collection_run_id TEXT NOT NULL,
                    analytics_version TEXT NOT NULL,
                    calculated_at TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    FOREIGN KEY (case_id) REFERENCES case_info(case_id),
                    FOREIGN KEY (collection_run_id) REFERENCES runs(id),
                    UNIQUE (case_id, collection_run_id, analytics_version)
                );
                CREATE INDEX IF NOT EXISTS idx_analysis_results_case_run
                    ON analysis_results(
                        case_id, collection_run_id, calculated_at DESC
                    );
                """
            )
            changed = True
        if changed:
            connection.commit()

    @contextmanager
    def _connection(self, case_id: str) -> Iterator[sqlite3.Connection]:
        connection = self._connect(case_id)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _migration_sql() -> str:
        resource = files("terralogic_acquisition.store.migrations").joinpath(
            "001_initial.sql"
        )
        return resource.read_text(encoding="utf-8")

    def create_case(
        self,
        *,
        case_id: str,
        cadastral_number: str,
        report_profile: str,
    ) -> CaseInfo:
        request = CollectionRequest(
            case_id=case_id,
            cadastral_number=cadastral_number,
            profile=report_profile,
        )
        case_dir = self._case_dir(request.case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "raw" / "nspd").mkdir(parents=True, exist_ok=True)
        (case_dir / "raw" / "osm").mkdir(parents=True, exist_ok=True)
        (case_dir / "raw" / "dgis").mkdir(parents=True, exist_ok=True)
        (case_dir / "maps").mkdir(exist_ok=True)
        (case_dir / "reports").mkdir(exist_ok=True)

        database = self._database_path(request.case_id)
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(self._migration_sql())
            self._ensure_schema(connection)
            existing = connection.execute(
                "SELECT * FROM case_info WHERE case_id = ?", (request.case_id,)
            ).fetchone()
            if existing is not None:
                if existing["cadastral_number"] != request.cadastral_number:
                    raise ValueError(
                        f"Case {request.case_id!r} belongs to another cadastral number"
                    )
                return self._case_from_row(existing)

            now = utc_now()
            case = CaseInfo(
                case_id=request.case_id,
                cadastral_number=request.cadastral_number,
                report_profile=request.profile,
                status="running",
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                """
                INSERT INTO case_info(
                    case_id, cadastral_number, report_profile, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    case.case_id,
                    case.cadastral_number,
                    case.report_profile,
                    case.status,
                    case.created_at.isoformat(),
                    case.updated_at.isoformat(),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        self._write_manifest(case)
        return case

    @staticmethod
    def _case_from_row(row: sqlite3.Row) -> CaseInfo:
        return CaseInfo(
            case_id=row["case_id"],
            cadastral_number=row["cadastral_number"],
            report_profile=row["report_profile"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _write_manifest(self, case: CaseInfo) -> None:
        target = self._case_dir(case.case_id) / "manifest.json"
        payload = (
            case.model_dump_json(indent=2).encode("utf-8") + b"\n"
        )
        self._atomic_write(target, payload)

    @staticmethod
    def _atomic_write(target: Path, payload: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=f".{target.name}.", delete=False
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def get_case(self, case_id: str) -> CaseInfo:
        with self._connection(case_id) as connection:
            row = connection.execute(
                "SELECT * FROM case_info WHERE case_id = ?", (case_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Case {case_id!r} does not exist")
        return self._case_from_row(row)

    def list_cases(self) -> list[CaseInfo]:
        result: list[CaseInfo] = []
        for database in sorted(self.cases_root.glob("*/case.sqlite")):
            try:
                result.append(self.get_case(database.parent.name))
            except (KeyError, sqlite3.Error):
                continue
        return sorted(result, key=lambda item: item.updated_at, reverse=True)

    def begin_run(self, request: CollectionRequest, run_id: str) -> None:
        started_at = utc_now()
        with self._connection(request.case_id) as connection:
            connection.execute(
                """
                INSERT INTO runs(
                    id, case_id, request_json, status, started_at
                ) VALUES (?, ?, ?, 'running', ?)
                """,
                (
                    run_id,
                    request.case_id,
                    request.model_dump_json(),
                    started_at.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE case_info SET status = 'running', updated_at = ? "
                "WHERE case_id = ?",
                (started_at.isoformat(), request.case_id),
            )

    def finish_run(self, receipt: CollectionReceipt) -> None:
        with self._connection(receipt.case_id) as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET status = ?, completed_at = ?, receipt_json = ?
                WHERE id = ? AND case_id = ?
                """,
                (
                    receipt.status,
                    receipt.completed_at.isoformat(),
                    receipt.model_dump_json(),
                    receipt.run_id,
                    receipt.case_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Run {receipt.run_id!r} does not exist")
            connection.execute(
                "UPDATE case_info SET status = ?, updated_at = ? WHERE case_id = ?",
                (
                    receipt.status,
                    receipt.completed_at.isoformat(),
                    receipt.case_id,
                ),
            )
        self._write_manifest(self.get_case(receipt.case_id))

    def get_latest_collection_receipt(
        self, case_id: str
    ) -> CollectionReceipt | None:
        with self._connection(case_id) as connection:
            row = connection.execute(
                """
                SELECT receipt_json FROM runs
                WHERE case_id = ? AND receipt_json IS NOT NULL
                ORDER BY started_at DESC LIMIT 1
                """,
                (case_id,),
            ).fetchone()
        if row is None:
            return None
        return CollectionReceipt.model_validate_json(row["receipt_json"])

    def list_collection_receipts(
        self, case_id: str
    ) -> list[CollectionReceipt]:
        """Return completed collection runs, newest first."""

        with self._connection(case_id) as connection:
            rows = connection.execute(
                """
                SELECT receipt_json FROM runs
                WHERE case_id = ? AND receipt_json IS NOT NULL
                ORDER BY started_at DESC
                """,
                (case_id,),
            ).fetchall()
        return [
            CollectionReceipt.model_validate_json(row["receipt_json"])
            for row in rows
        ]

    def save_snapshot(
        self,
        *,
        case_id: str,
        run_id: str,
        source: SourceName,
        payload: bytes,
        adapter_version: str,
        metadata: dict[str, object] | None = None,
    ) -> SourceSnapshot:
        retrieved_at = utc_now()
        digest = hashlib.sha256(payload).hexdigest()
        snapshot_id = f"{source}-{uuid4().hex}"
        relative_path = Path("raw") / source / f"{snapshot_id}.json.gz"
        target = self._case_dir(case_id) / relative_path
        self._atomic_write(target, gzip.compress(payload, compresslevel=6, mtime=0))
        snapshot = SourceSnapshot(
            id=snapshot_id,
            case_id=case_id,
            run_id=run_id,
            source=source,
            retrieved_at=retrieved_at,
            adapter_version=adapter_version,
            content_sha256=digest,
            relative_path=relative_path.as_posix(),
            metadata=metadata or {},
        )
        try:
            with self._connection(case_id) as connection:
                connection.execute(
                    """
                    INSERT INTO snapshots(
                        id, case_id, run_id, source, retrieved_at,
                        adapter_version, content_sha256, relative_path, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.id,
                        snapshot.case_id,
                        snapshot.run_id,
                        snapshot.source,
                        snapshot.retrieved_at.isoformat(),
                        snapshot.adapter_version,
                        snapshot.content_sha256,
                        snapshot.relative_path,
                        _json_dumps(snapshot.metadata),
                    ),
                )
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return snapshot

    def load_snapshot(self, case_id: str, snapshot_id: str) -> bytes:
        with self._connection(case_id) as connection:
            row = connection.execute(
                "SELECT relative_path FROM snapshots WHERE id = ? AND case_id = ?",
                (snapshot_id, case_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Snapshot {snapshot_id!r} does not exist")
        stored = self._case_dir(case_id) / row["relative_path"]
        return gzip.decompress(stored.read_bytes())

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> SourceSnapshot:
        return SourceSnapshot(
            id=row["id"],
            case_id=row["case_id"],
            run_id=row["run_id"],
            source=row["source"],
            retrieved_at=row["retrieved_at"],
            adapter_version=row["adapter_version"],
            content_sha256=row["content_sha256"],
            relative_path=row["relative_path"],
            metadata=json.loads(row["metadata_json"]),
        )

    def list_snapshots(
        self, case_id: str, source: SourceName | None = None
    ) -> list[SourceSnapshot]:
        sql = "SELECT * FROM snapshots WHERE case_id = ?"
        parameters: list[object] = [case_id]
        if source is not None:
            sql += " AND source = ?"
            parameters.append(source)
        sql += " ORDER BY retrieved_at DESC"
        with self._connection(case_id) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._snapshot_from_row(row) for row in rows]

    def save_area_of_interest(self, aoi: AreaOfInterest) -> None:
        parcel = shape(aoi.parcel_geometry)
        query = shape(aoi.query_geometry)
        with self._connection(aoi.case_id) as connection:
            connection.execute(
                """
                INSERT INTO areas_of_interest(
                    id, case_id, source_snapshot_id, geometry_hash,
                    parcel_geometry_wkb, query_geometry_wkb, source_crs,
                    metric_crs, min_x, min_y, max_x, max_y,
                    representative_x, representative_y, warnings_json,
                    metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aoi.id,
                    aoi.case_id,
                    aoi.source_snapshot_id,
                    aoi.geometry_hash,
                    parcel.wkb,
                    query.wkb,
                    aoi.source_crs,
                    aoi.metric_crs,
                    *aoi.bbox,
                    *aoi.representative_point,
                    _json_dumps(aoi.validation_warnings),
                    _json_dumps(
                        {
                            "parcel_minimum_radius_m": (
                                aoi.parcel_minimum_radius_m
                            ),
                            "margin_m": aoi.margin_m,
                            "search_radius_m": aoi.search_radius_m,
                        }
                    ),
                ),
            )

    def get_area_of_interest(
        self, case_id: str, aoi_id: str
    ) -> AreaOfInterest:
        with self._connection(case_id) as connection:
            row = connection.execute(
                "SELECT * FROM areas_of_interest WHERE id = ? AND case_id = ?",
                (aoi_id, case_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Area of interest {aoi_id!r} does not exist")
        metrics = json.loads(row["metrics_json"] or "{}")
        return AreaOfInterest(
            id=row["id"],
            case_id=row["case_id"],
            parcel_geometry=mapping(load_wkb(row["parcel_geometry_wkb"])),
            query_geometry=mapping(load_wkb(row["query_geometry_wkb"])),
            bbox=(row["min_x"], row["min_y"], row["max_x"], row["max_y"]),
            representative_point=(
                row["representative_x"],
                row["representative_y"],
            ),
            parcel_minimum_radius_m=float(
                metrics.get("parcel_minimum_radius_m", 0.0)
            ),
            margin_m=int(metrics.get("margin_m", 0)),
            search_radius_m=float(metrics.get("search_radius_m", 0.0)),
            source_snapshot_id=row["source_snapshot_id"],
            geometry_hash=row["geometry_hash"],
            source_crs=row["source_crs"],
            metric_crs=row["metric_crs"],
            validation_warnings=json.loads(row["warnings_json"]),
        )

    def save_features(
        self, case_id: str, features: Sequence[GeoFeature]
    ) -> None:
        rows: list[tuple[object, ...]] = []
        for feature in features:
            if feature.case_id != case_id:
                raise ValueError("All features must belong to the target case")
            geometry_wkb: bytes | None = None
            bounds: tuple[float | None, ...] = (None, None, None, None)
            if feature.geometry is not None:
                geometry = shape(feature.geometry)
                geometry_wkb = geometry.wkb
                bounds = tuple(float(value) for value in geometry.bounds)
            rows.append(
                (
                    feature.id,
                    feature.case_id,
                    feature.snapshot_id,
                    feature.source,
                    feature.source_type,
                    feature.source_id,
                    feature.feature_class,
                    geometry_wkb,
                    feature.crs,
                    *bounds,
                    _json_dumps(feature.properties),
                )
            )
        if not rows:
            return
        with self._connection(case_id) as connection:
            connection.executemany(
                """
                INSERT INTO features(
                    id, case_id, snapshot_id, source, source_type, source_id,
                    feature_class, geometry_wkb, crs,
                    min_x, min_y, max_x, max_y, properties_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load_features(
        self,
        case_id: str,
        *,
        source: SourceName | None = None,
        snapshot_id: str | None = None,
        feature_classes: Sequence[str] | None = None,
    ) -> list[GeoFeature]:
        sql = "SELECT * FROM features WHERE case_id = ?"
        parameters: list[object] = [case_id]
        if source is not None:
            sql += " AND source = ?"
            parameters.append(source)
        if snapshot_id is not None:
            sql += " AND snapshot_id = ?"
            parameters.append(snapshot_id)
        if feature_classes:
            placeholders = ",".join("?" for _ in feature_classes)
            sql += f" AND feature_class IN ({placeholders})"
            parameters.extend(feature_classes)
        sql += " ORDER BY source, feature_class, id"
        with self._connection(case_id) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._feature_from_row(row) for row in rows]

    @staticmethod
    def _feature_from_row(row: sqlite3.Row) -> GeoFeature:
        geometry = (
            mapping(load_wkb(row["geometry_wkb"]))
            if row["geometry_wkb"] is not None
            else None
        )
        return GeoFeature(
            id=row["id"],
            case_id=row["case_id"],
            snapshot_id=row["snapshot_id"],
            source=row["source"],
            source_type=row["source_type"],
            source_id=row["source_id"],
            feature_class=row["feature_class"],
            geometry=geometry,
            crs=row["crs"],
            properties=json.loads(row["properties_json"]),
        )

    def save_analysis_result(self, result: AnalysisResult) -> None:
        """Persist one replaceable result for a collection run and version."""

        with self._connection(result.case_id) as connection:
            connection.execute(
                """
                INSERT INTO analysis_results(
                    id, case_id, collection_run_id, analytics_version,
                    calculated_at, result_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id, collection_run_id, analytics_version)
                DO UPDATE SET
                    id = excluded.id,
                    calculated_at = excluded.calculated_at,
                    result_json = excluded.result_json
                """,
                (
                    result.id,
                    result.case_id,
                    result.collection_run_id,
                    result.analytics_version,
                    result.calculated_at.isoformat(),
                    result.model_dump_json(),
                ),
            )

    def get_analysis_result(
        self,
        case_id: str,
        collection_run_id: str,
        *,
        analytics_version: str | None = None,
    ) -> AnalysisResult | None:
        """Load the newest matching analytics result for one collection run."""

        sql = (
            "SELECT result_json FROM analysis_results "
            "WHERE case_id = ? AND collection_run_id = ?"
        )
        parameters: list[object] = [case_id, collection_run_id]
        if analytics_version is not None:
            sql += " AND analytics_version = ?"
            parameters.append(analytics_version)
        sql += " ORDER BY calculated_at DESC LIMIT 1"
        with self._connection(case_id) as connection:
            row = connection.execute(sql, parameters).fetchone()
        if row is None:
            return None
        return AnalysisResult.model_validate_json(row["result_json"])

    def list_analysis_results(self, case_id: str) -> list[AnalysisResult]:
        """Return all persisted analytics results, newest first."""

        with self._connection(case_id) as connection:
            rows = connection.execute(
                """
                SELECT result_json FROM analysis_results
                WHERE case_id = ?
                ORDER BY calculated_at DESC
                """,
                (case_id,),
            ).fetchall()
        return [
            AnalysisResult.model_validate_json(row["result_json"])
            for row in rows
        ]
