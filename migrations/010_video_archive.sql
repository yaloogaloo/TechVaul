-- Video archive is its own domain. GitHub and web tables are unchanged.

CREATE TABLE video_items (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    platform_video_id TEXT NOT NULL,
    part_key TEXT NOT NULL DEFAULT '',
    part_index INTEGER,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    uploader TEXT NOT NULL DEFAULT '',
    duration_seconds INTEGER,
    thumbnail_relpath TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    current_download_id INTEGER,
    UNIQUE (platform, platform_video_id, part_key)
);

CREATE TABLE video_downloads (
    id INTEGER PRIMARY KEY,
    video_item_id INTEGER NOT NULL REFERENCES video_items(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_quality TEXT NOT NULL,
    actual_quality TEXT,
    container TEXT,
    vcodec TEXT,
    acodec TEXT,
    width INTEGER,
    height INTEGER,
    duration_seconds INTEGER,
    has_video INTEGER NOT NULL DEFAULT 0,
    has_audio INTEGER NOT NULL DEFAULT 0,
    relative_path TEXT,
    archive_root TEXT,
    file_size INTEGER,
    resumed INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_code TEXT,
    error_message TEXT
);

CREATE INDEX ix_video_downloads_item ON video_downloads(video_item_id, id DESC);
