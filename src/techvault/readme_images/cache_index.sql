CREATE TABLE IF NOT EXISTS objects (
    sha256 TEXT PRIMARY KEY,
    extension TEXT NOT NULL,
    mime TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    invalidated INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS urls (
    canonical_url TEXT PRIMARY KEY,
    sha256 TEXT,
    etag TEXT,
    last_modified TEXT,
    verified_at TEXT,
    status TEXT NOT NULL,
    failure_code TEXT,
    failure_message TEXT,
    reuse_hit INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS image_refs (
    repository_id INTEGER NOT NULL,
    github_repo_id INTEGER,
    canonical_url TEXT NOT NULL,
    sha256 TEXT,
    readme_relpath TEXT NOT NULL DEFAULT 'README.md',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (repository_id, canonical_url)
);

CREATE INDEX IF NOT EXISTS ix_image_refs_sha ON image_refs(sha256);
CREATE INDEX IF NOT EXISTS ix_urls_sha ON urls(sha256);

CREATE TABLE IF NOT EXISTS pool_stats (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
