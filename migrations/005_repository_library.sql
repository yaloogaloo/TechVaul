CREATE TABLE repository_categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX ux_repository_categories_name
    ON repository_categories(name COLLATE NOCASE);

CREATE TABLE repository_topics (
    repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    PRIMARY KEY (repository_id, name)
);

CREATE TABLE source_snapshots (
    id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    ref TEXT NOT NULL,
    default_branch TEXT,
    commit_sha TEXT,
    archived_at TEXT NOT NULL,
    size INTEGER,
    local_sha256 TEXT,
    relative_path TEXT,
    state TEXT NOT NULL,
    failure_code TEXT,
    failure_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (state IN ('PENDING', 'DOWNLOADING', 'VERIFYING', 'COMPLETED', 'FAILED', 'CANCELLED')),
    CHECK (
        local_sha256 IS NULL
        OR (
            length(local_sha256) = 64
            AND local_sha256 = lower(local_sha256)
            AND local_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    )
);

CREATE INDEX ix_source_snapshots_repository ON source_snapshots(repository_id);

ALTER TABLE repositories ADD COLUMN category_id INTEGER NULL REFERENCES repository_categories(id);

ALTER TABLE repositories ADD COLUMN current_snapshot_id INTEGER NULL REFERENCES source_snapshots(id);

ALTER TABLE jobs ADD COLUMN archive_kind TEXT NOT NULL DEFAULT 'RELEASE';

ALTER TABLE jobs ADD COLUMN target_snapshot_id INTEGER NULL REFERENCES source_snapshots(id);
