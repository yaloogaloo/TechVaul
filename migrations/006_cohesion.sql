CREATE TABLE release_attempts (
    id INTEGER PRIMARY KEY,
    release_id INTEGER NOT NULL REFERENCES releases(id),
    status TEXT NOT NULL,
    failure_code TEXT,
    failure_message TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    CHECK (status IN ('ARCHIVING', 'COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED'))
);

CREATE TABLE release_asset_catalog (
    id INTEGER PRIMARY KEY,
    release_id INTEGER NOT NULL REFERENCES releases(id),
    github_asset_id INTEGER,
    name TEXT NOT NULL,
    source_url TEXT,
    expected_size INTEGER,
    upstream_digest TEXT,
    github_state TEXT,
    content_type TEXT
);

ALTER TABLE releases ADD COLUMN artifact_attempt_id INTEGER NULL REFERENCES release_attempts(id);

ALTER TABLE release_files ADD COLUMN attempt_id INTEGER NULL REFERENCES release_attempts(id);

ALTER TABLE release_files ADD COLUMN archive_root TEXT;

ALTER TABLE source_snapshots ADD COLUMN archive_root TEXT;

ALTER TABLE offline_pages ADD COLUMN archive_root TEXT;

DROP INDEX ux_release_files_asset;

DROP INDEX ux_release_files_source_kind;

CREATE UNIQUE INDEX ux_release_files_asset
    ON release_files (release_id, attempt_id, github_asset_id)
    WHERE github_asset_id IS NOT NULL;

CREATE UNIQUE INDEX ux_release_files_source_kind
    ON release_files (release_id, attempt_id, kind)
    WHERE github_asset_id IS NULL;

INSERT INTO release_attempts (release_id, status, created_at, finished_at)
SELECT
    releases.id,
    'COMPLETED',
    COALESCE(releases.updated_at_local, releases.created_at_local),
    COALESCE(releases.updated_at_local, releases.created_at_local)
FROM releases
WHERE EXISTS (
    SELECT 1 FROM release_files
    WHERE release_files.release_id = releases.id AND release_files.state = 'COMPLETED'
);

UPDATE release_files
SET attempt_id = (
    SELECT release_attempts.id
    FROM release_attempts
    WHERE release_attempts.release_id = release_files.release_id
    ORDER BY release_attempts.id
    LIMIT 1
)
WHERE state = 'COMPLETED' AND attempt_id IS NULL;

UPDATE releases
SET artifact_attempt_id = (
    SELECT release_attempts.id
    FROM release_attempts
    WHERE release_attempts.release_id = releases.id AND release_attempts.status = 'COMPLETED'
    ORDER BY release_attempts.id
    LIMIT 1
)
WHERE id IN (
    SELECT current_release_id FROM repositories WHERE current_release_id IS NOT NULL
)
AND artifact_attempt_id IS NULL;

UPDATE source_snapshots
SET archive_root = (SELECT value FROM settings WHERE key = 'archive_root')
WHERE archive_root IS NULL
  AND EXISTS (SELECT 1 FROM settings WHERE key = 'archive_root');

UPDATE release_files
SET archive_root = (SELECT value FROM settings WHERE key = 'archive_root')
WHERE archive_root IS NULL
  AND EXISTS (SELECT 1 FROM settings WHERE key = 'archive_root');

UPDATE offline_pages
SET archive_root = (SELECT value FROM settings WHERE key = 'archive_root')
WHERE archive_root IS NULL
  AND EXISTS (SELECT 1 FROM settings WHERE key = 'archive_root');
