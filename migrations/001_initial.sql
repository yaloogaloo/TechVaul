CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE repositories (
    id INTEGER PRIMARY KEY,
    github_repo_id INTEGER NOT NULL UNIQUE,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    description TEXT,
    html_url TEXT NOT NULL,
    default_branch TEXT,
    stargazers_count INTEGER,
    license_spdx TEXT,
    private INTEGER NOT NULL DEFAULT 0,
    relative_path TEXT,
    archive_status TEXT NOT NULL DEFAULT 'NOT_ARCHIVED',
    current_release_id INTEGER NULL REFERENCES releases(id),
    is_favorite INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT,
    last_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE releases (
    id INTEGER PRIMARY KEY,
    github_release_id INTEGER NOT NULL,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    tag_name TEXT NOT NULL,
    name TEXT,
    body TEXT,
    body_html TEXT,
    html_url TEXT NOT NULL,
    published_at TEXT,
    created_at TEXT,
    prerelease INTEGER NOT NULL DEFAULT 0,
    draft INTEGER NOT NULL DEFAULT 0,
    tarball_url TEXT,
    zipball_url TEXT,
    lookup_outcome TEXT NOT NULL,
    created_at_local TEXT NOT NULL,
    updated_at_local TEXT NOT NULL,
    UNIQUE (repository_id, github_release_id)
);

CREATE TABLE release_files (
    id INTEGER PRIMARY KEY,
    release_id INTEGER NOT NULL REFERENCES releases(id),
    kind TEXT NOT NULL,
    github_asset_id INTEGER NULL,
    name TEXT NOT NULL,
    source_url TEXT,
    expected_size INTEGER NULL,
    actual_size INTEGER NULL,
    upstream_digest TEXT NULL,
    local_sha256 TEXT NULL,
    state TEXT NOT NULL,
    failure_code TEXT NULL,
    failure_message TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        local_sha256 IS NULL
        OR (
            length(local_sha256) = 64
            AND local_sha256 = lower(local_sha256)
            AND local_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    )
);

CREATE UNIQUE INDEX ux_release_files_asset
    ON release_files (release_id, github_asset_id)
    WHERE github_asset_id IS NOT NULL;

CREATE UNIQUE INDEX ux_release_files_source_kind
    ON release_files (release_id, kind)
    WHERE github_asset_id IS NULL;

CREATE TABLE offline_pages (
    id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL UNIQUE REFERENCES repositories(id),
    index_relative_path TEXT,
    release_relative_path TEXT,
    status TEXT NOT NULL,
    failure_code TEXT,
    failure_message TEXT,
    built_at TEXT
);

CREATE TABLE page_assets (
    id INTEGER PRIMARY KEY,
    offline_page_id INTEGER NOT NULL REFERENCES offline_pages(id),
    source_url TEXT NOT NULL,
    relative_path TEXT,
    sha256 TEXT,
    status TEXT NOT NULL,
    failure_code TEXT,
    failure_message TEXT
);

CREATE TABLE categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    category_id INTEGER NULL REFERENCES categories(id),
    is_favorite INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE document_tags (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (document_id, tag_id)
);

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id),
    target_release_id INTEGER NULL REFERENCES releases(id),
    status TEXT NOT NULL,
    failure_code TEXT NULL,
    failure_message TEXT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT NULL,
    finished_at TEXT NULL
);
