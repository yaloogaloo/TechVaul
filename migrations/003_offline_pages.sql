CREATE TABLE offline_pages_next (
    id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    page_type TEXT NOT NULL,
    relative_path TEXT,
    source_url TEXT,
    status TEXT NOT NULL,
    failure_code TEXT,
    failure_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    built_at TEXT,
    UNIQUE (repository_id, page_type),
    CHECK (page_type IN ('REPOSITORY', 'RELEASE')),
    CHECK (status IN ('PENDING', 'BUILDING', 'COMPLETED', 'FAILED'))
);

INSERT INTO offline_pages_next (
    id, repository_id, page_type, relative_path, source_url, status,
    failure_code, failure_message, created_at, updated_at, built_at
)
SELECT
    id,
    repository_id,
    'REPOSITORY',
    index_relative_path,
    NULL,
    CASE
        WHEN status IN ('PENDING', 'BUILDING', 'COMPLETED', 'FAILED') THEN status
        ELSE 'FAILED'
    END,
    failure_code,
    failure_message,
    COALESCE(built_at, '1970-01-01T00:00:00+00:00'),
    COALESCE(built_at, '1970-01-01T00:00:00+00:00'),
    built_at
FROM offline_pages;

DROP TABLE offline_pages;

ALTER TABLE offline_pages_next RENAME TO offline_pages;

ALTER TABLE page_assets ADD COLUMN actual_size INTEGER;
