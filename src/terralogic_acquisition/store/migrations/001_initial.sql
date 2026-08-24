PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS case_info (
    case_id TEXT PRIMARY KEY,
    cadastral_number TEXT NOT NULL,
    report_profile TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    receipt_json TEXT,
    FOREIGN KEY (case_id) REFERENCES case_info(case_id)
);

CREATE INDEX IF NOT EXISTS idx_runs_case_started
    ON runs(case_id, started_at DESC);

CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    metadata_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES case_info(case_id),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_case_source
    ON snapshots(case_id, source, retrieved_at DESC);

CREATE TABLE IF NOT EXISTS areas_of_interest (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    source_snapshot_id TEXT NOT NULL,
    geometry_hash TEXT NOT NULL,
    parcel_geometry_wkb BLOB NOT NULL,
    query_geometry_wkb BLOB NOT NULL,
    source_crs TEXT NOT NULL,
    metric_crs TEXT NOT NULL,
    min_x REAL NOT NULL,
    min_y REAL NOT NULL,
    max_x REAL NOT NULL,
    max_y REAL NOT NULL,
    representative_x REAL NOT NULL,
    representative_y REAL NOT NULL,
    warnings_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES case_info(case_id),
    FOREIGN KEY (source_snapshot_id) REFERENCES snapshots(id)
);

CREATE TABLE IF NOT EXISTS features (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT,
    source_id TEXT,
    feature_class TEXT NOT NULL,
    geometry_wkb BLOB,
    crs TEXT NOT NULL DEFAULT 'EPSG:4326',
    min_x REAL,
    min_y REAL,
    max_x REAL,
    max_y REAL,
    properties_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES case_info(case_id),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
);

CREATE INDEX IF NOT EXISTS idx_features_case_class
    ON features(case_id, feature_class);
CREATE INDEX IF NOT EXISTS idx_features_snapshot
    ON features(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_features_bbox
    ON features(case_id, min_x, min_y, max_x, max_y);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    subject_feature_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_json TEXT NOT NULL,
    unit TEXT,
    snapshot_id TEXT NOT NULL,
    quality TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES case_info(case_id)
);

CREATE TABLE IF NOT EXISTS entity_links (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    subject_feature_id TEXT NOT NULL,
    object_feature_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    score REAL,
    method TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    review_status TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES case_info(case_id)
);

CREATE TABLE IF NOT EXISTS metrics (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    subject_feature_id TEXT NOT NULL,
    object_feature_id TEXT,
    value_json TEXT NOT NULL,
    unit TEXT,
    method TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    analytics_version TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES case_info(case_id)
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    fact_refs_json TEXT NOT NULL,
    metric_refs_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES case_info(case_id)
);

CREATE TABLE IF NOT EXISTS report_sections (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    section_key TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES case_info(case_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES case_info(case_id)
);

PRAGMA user_version = 1;
