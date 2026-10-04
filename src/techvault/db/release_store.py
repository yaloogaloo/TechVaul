"""Persist repository and official-release metadata. Lookup never changes current_release_id."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from techvault.errors import DatabaseError
from techvault.github.models import LookupOutcome, ReleaseLookupResult, ReleaseMetadata, RepositoryMetadata


def save_release_lookup(connection: sqlite3.Connection, result: ReleaseLookupResult) -> None:
    if result.repository is None:
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        connection.execute("BEGIN")
        repository_id = _upsert_repository(connection, result.repository, now)
        _replace_topics(connection, repository_id, result.repository)
        if result.outcome == LookupOutcome.STABLE and result.release is not None:
            release_id = _upsert_release(connection, repository_id, result.release, now)
            _replace_catalog(connection, release_id, result.release)
            if _artifact_attempt_id(connection, release_id) is None:
                _replace_assets(connection, release_id, result.release, now)
        connection.execute("COMMIT")
    except sqlite3.Error as exc:
        _rollback(connection)
        raise DatabaseError("Release lookup could not be saved") from exc


def _upsert_repository(
    connection: sqlite3.Connection,
    repository: RepositoryMetadata,
    now: str,
) -> int:
    connection.execute(
        """
        INSERT INTO repositories (
            github_repo_id, owner, name, full_name, description, html_url,
            default_branch, stargazers_count, license_spdx, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(github_repo_id) DO UPDATE SET
            owner = excluded.owner,
            name = excluded.name,
            full_name = excluded.full_name,
            description = excluded.description,
            html_url = excluded.html_url,
            default_branch = excluded.default_branch,
            stargazers_count = excluded.stargazers_count,
            license_spdx = excluded.license_spdx,
            updated_at = excluded.updated_at
        """,
        (
            repository.id,
            repository.owner,
            repository.name,
            repository.full_name,
            repository.description,
            repository.html_url,
            repository.default_branch,
            repository.stargazers_count,
            repository.license,
            now,
            now,
        ),
    )
    row = connection.execute(
        "SELECT id FROM repositories WHERE github_repo_id = ?",
        (repository.id,),
    ).fetchone()
    if row is None:
        raise DatabaseError("Repository row was not saved")
    return int(row["id"])


def _replace_topics(
    connection: sqlite3.Connection,
    repository_id: int,
    repository: RepositoryMetadata,
) -> None:
    if repository.topics is None:
        return
    connection.execute(
        "DELETE FROM repository_topics WHERE repository_id = ?",
        (repository_id,),
    )
    for name in repository.topics:
        connection.execute(
            "INSERT INTO repository_topics (repository_id, name) VALUES (?, ?)",
            (repository_id, name),
        )


def _upsert_release(
    connection: sqlite3.Connection,
    repository_id: int,
    release: ReleaseMetadata,
    now: str,
) -> int:
    existing = connection.execute(
        """
        SELECT id FROM releases
        WHERE repository_id = ? AND github_release_id = ?
        """,
        (repository_id, release.github_release_id),
    ).fetchone()
    values = (
        release.tag_name,
        release.name,
        release.body,
        release.html_url,
        release.published_at,
        release.created_at,
        int(release.prerelease),
        int(release.draft),
        release.tarball_url,
        release.zipball_url,
        LookupOutcome.STABLE,
        now,
    )
    if existing is None:
        connection.execute(
            """
            INSERT INTO releases (
                github_release_id, repository_id, tag_name, name, body, html_url,
                published_at, created_at, prerelease, draft, tarball_url, zipball_url,
                lookup_outcome, created_at_local, updated_at_local
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (release.github_release_id, repository_id, *values, now),
        )
        inserted = connection.execute("SELECT last_insert_rowid()").fetchone()
        return int(inserted[0])
    connection.execute(
        """
        UPDATE releases SET
            tag_name = ?,
            name = ?,
            body = ?,
            html_url = ?,
            published_at = ?,
            created_at = ?,
            prerelease = ?,
            draft = ?,
            tarball_url = ?,
            zipball_url = ?,
            lookup_outcome = ?,
            updated_at_local = ?
        WHERE id = ?
        """,
        (*values, int(existing["id"])),
    )
    return int(existing["id"])


def _artifact_attempt_id(connection: sqlite3.Connection, release_id: int) -> int | None:
    row = connection.execute(
        "SELECT artifact_attempt_id FROM releases WHERE id = ?",
        (release_id,),
    ).fetchone()
    if row is None or row["artifact_attempt_id"] is None:
        return None
    return int(row["artifact_attempt_id"])


def _replace_catalog(
    connection: sqlite3.Connection,
    release_id: int,
    release: ReleaseMetadata,
) -> None:
    connection.execute("DELETE FROM release_asset_catalog WHERE release_id = ?", (release_id,))
    for asset in release.assets:
        connection.execute(
            """
            INSERT INTO release_asset_catalog (
                release_id, github_asset_id, name, source_url, expected_size,
                upstream_digest, github_state, content_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                release_id,
                asset.github_asset_id,
                asset.name,
                asset.browser_download_url,
                asset.size,
                asset.digest,
                asset.state,
                asset.content_type,
            ),
        )


def _replace_assets(
    connection: sqlite3.Connection,
    release_id: int,
    release: ReleaseMetadata,
    now: str,
) -> None:
    connection.execute("DELETE FROM release_files WHERE release_id = ?", (release_id,))
    for asset in release.assets:
        connection.execute(
            """
            INSERT INTO release_files (
                release_id, kind, github_asset_id, name, source_url, expected_size,
                upstream_digest, state, content_type, created_at, updated_at
            ) VALUES (?, 'asset', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                release_id,
                asset.github_asset_id,
                asset.name,
                asset.browser_download_url,
                asset.size,
                asset.digest,
                asset.state,
                asset.content_type,
                now,
                now,
            ),
        )


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        return
