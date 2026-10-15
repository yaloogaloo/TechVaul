-- Persist the on-disk repository folder so renames do not break V2 paths.
ALTER TABLE repositories ADD COLUMN storage_relpath TEXT;
