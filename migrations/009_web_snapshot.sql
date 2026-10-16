-- Web archive MHTML snapshot metadata (additive; reading edition unchanged).

ALTER TABLE web_articles ADD COLUMN snapshot_status TEXT NOT NULL DEFAULT 'NOT_CAPTURED';
ALTER TABLE web_articles ADD COLUMN relative_snapshot TEXT;
ALTER TABLE web_articles ADD COLUMN snapshot_bytes INTEGER;
ALTER TABLE web_articles ADD COLUMN snapshot_sha256 TEXT;
ALTER TABLE web_articles ADD COLUMN snapshot_captured_at TEXT;
ALTER TABLE web_articles ADD COLUMN snapshot_failure TEXT;

ALTER TABLE web_captures ADD COLUMN snapshot_status TEXT NOT NULL DEFAULT 'NOT_CAPTURED';
ALTER TABLE web_captures ADD COLUMN relative_snapshot TEXT;
ALTER TABLE web_captures ADD COLUMN snapshot_bytes INTEGER;
ALTER TABLE web_captures ADD COLUMN snapshot_sha256 TEXT;
ALTER TABLE web_captures ADD COLUMN snapshot_failure TEXT;
