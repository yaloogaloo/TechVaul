"""Bind repositories to a stable on-disk folder (V2 storage_relpath)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from techvault.archive.paths import PathLayout, repo_root_from_relative


def ensure_storage_relpath(
    connection: sqlite3.Connection,
    layout: PathLayout,
    *,
    repository_id: int,
) -> str:
    row = connection.execute(
        "SELECT id, owner, name, github_repo_id, storage_relpath FROM repositories WHERE id = ?",
        (repository_id,),
    ).fetchone()
    if row is None:
        raise LookupError("Repository does not exist")
    if row["storage_relpath"]:
        return str(row["storage_relpath"]).replace("\\", "/")

    inferred = _infer_from_records(connection, int(row["id"]))
    if inferred:
        _set(connection, int(row["id"]), inferred)
        return inferred

    v1 = layout.v1_repository_dir(int(row["github_repo_id"]))
    if v1.is_dir():
        rel = v1.relative_to(layout.archive_root).as_posix()
        _set(connection, int(row["id"]), rel)
        return rel

    legacy = layout.legacy_repository_dir(row["owner"], row["name"], int(row["github_repo_id"]))
    if legacy.is_dir():
        rel = legacy.relative_to(layout.archive_root).as_posix()
        _set(connection, int(row["id"]), rel)
        return rel

    rel = layout.repository_relpath(row["owner"], row["name"], int(row["github_repo_id"]))
    _set(connection, int(row["id"]), rel)
    return rel


def repository_dir_for(
    connection: sqlite3.Connection,
    layout: PathLayout,
    *,
    repository_id: int,
) -> Path:
    rel = ensure_storage_relpath(connection, layout, repository_id=repository_id)
    return layout.repository_dir_from_relpath(rel)


def _infer_from_records(connection: sqlite3.Connection, repository_id: int) -> str | None:
    queries = (
        """
        SELECT relative_path FROM offline_pages
        WHERE repository_id = ? AND relative_path IS NOT NULL LIMIT 1
        """,
        """
        SELECT relative_path FROM source_snapshots
        WHERE repository_id = ? AND relative_path IS NOT NULL LIMIT 1
        """,
        """
        SELECT rf.relative_path FROM release_files rf
        JOIN releases r ON r.id = rf.release_id
        WHERE r.repository_id = ? AND rf.relative_path IS NOT NULL LIMIT 1
        """,
    )
    for sql in queries:
        row = connection.execute(sql, (repository_id,)).fetchone()
        if row is None or not row["relative_path"]:
            continue
        root = repo_root_from_relative(row["relative_path"])
        if root:
            return root
    return None


def _set(connection: sqlite3.Connection, repository_id: int, relpath: str) -> None:
    connection.execute(
        "UPDATE repositories SET storage_relpath = ? WHERE id = ?",
        (relpath, repository_id),
    )
