"""Stable archive locations. github_repo_id owns the directory; each file records its root."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from techvault.archive.paths import PathLayout

_PATH_COLUMNS = (
    ("source_snapshots", "relative_path"),
    ("release_files", "relative_path"),
    ("offline_pages", "relative_path"),
    ("page_assets", "relative_path"),
)


def adopt_legacy_tree(
    connection: sqlite3.Connection,
    layout: PathLayout,
    *,
    owner: str,
    name: str,
    github_repo_id: int,
) -> None:
    # Pre-V2 adoption: owner/name__id → repositories/<github_repo_id>
    stable = layout.v1_repository_dir(github_repo_id)
    legacy = layout.legacy_repository_dir(owner, name, github_repo_id)
    if not legacy.is_dir() or stable.exists():
        return
    stable.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(stable)
    old_prefix = legacy.relative_to(layout.archive_root).as_posix()
    new_prefix = stable.relative_to(layout.archive_root).as_posix()
    _rewrite_prefixes(connection, old_prefix, new_prefix)
    parent = legacy.parent
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()


def resolve_stored_path(archive_root: str | None, relative_path: str | None, fallback: Path) -> Path | None:
    if not relative_path:
        return None
    root = Path(archive_root) if archive_root else fallback
    return root / relative_path


def _rewrite_prefixes(connection: sqlite3.Connection, old_prefix: str, new_prefix: str) -> None:
    for table, column in _PATH_COLUMNS:
        rows = connection.execute(f"SELECT id, {column} AS path FROM {table} WHERE {column} IS NOT NULL")
        for row in rows.fetchall():
            updated = _replace_prefix(row["path"], old_prefix, new_prefix)
            if updated != row["path"]:
                connection.execute(
                    f"UPDATE {table} SET {column} = ? WHERE id = ?",
                    (updated, int(row["id"])),
                )


def _replace_prefix(value: str, old_prefix: str, new_prefix: str) -> str:
    normalized = value.replace("\\", "/")
    if normalized != old_prefix and not normalized.startswith(old_prefix + "/"):
        return value
    suffix = normalized[len(old_prefix) :]
    updated = new_prefix + suffix
    if "\\" in value:
        return updated.replace("/", "\\")
    return updated
