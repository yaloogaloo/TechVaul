ALTER TABLE release_files ADD COLUMN github_state TEXT;
ALTER TABLE release_files ADD COLUMN content_type TEXT;
ALTER TABLE release_files ADD COLUMN digest_status TEXT;
ALTER TABLE release_files ADD COLUMN relative_path TEXT;
