"""Download a STABLE release and promote current_release_id only when it is complete."""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from techvault.archive.downloader import (
    DownloadRequest,
    FailureCode,
    FileState,
    download_release_file,
)
from techvault.archive.paths import PathLayout
from techvault.archive.progress import TransferPlan
from techvault.errors import DatabaseError, UnsafePathError
from techvault.github.client import GitHubClient
from techvault.github.models import LookupOutcome
from techvault.logging_setup import get_logger

logger = get_logger()

SOURCE_ZIP = "SOURCE_ZIP"
SOURCE_TARBALL = "SOURCE_TARBALL"
_ASSET = "asset"


class JobStatus:
    ARCHIVING = "ARCHIVING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def reopen_failed_files(connection: sqlite3.Connection, release_id: int) -> None:
    """Let a later attempt download files that failed, and leave completed files alone."""
    connection.execute(
        """
        UPDATE release_files
        SET state = 'PENDING', failure_code = NULL, failure_message = NULL
        WHERE release_id = ? AND state = 'FAILED'
        """,
        (release_id,),
    )


def archive_release(
    connection: sqlite3.Connection,
    client: GitHubClient,
    layout: PathLayout,
    *,
    repository_id: int,
    release_id: int,
    cancel_event: Event | None = None,
    after_stream=None,
    on_progress=None,
    on_detail=None,
) -> str:
    release = connection.execute(
        """
        SELECT
            releases.*,
            repositories.owner AS repo_owner,
            repositories.name AS repo_name,
            repositories.github_repo_id AS repo_github_id
        FROM releases
        JOIN repositories ON repositories.id = releases.repository_id
        WHERE releases.id = ? AND releases.repository_id = ?
        """,
        (release_id, repository_id),
    ).fetchone()
    if release is None:
        raise DatabaseError("Release does not belong to the repository")
    if release["lookup_outcome"] != LookupOutcome.STABLE:
        raise DatabaseError("Only an official stable release can be archived")

    now = _now()
    cursor = connection.execute(
        """
        INSERT INTO jobs (
            repository_id, target_release_id, status, created_at, started_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (repository_id, release_id, JobStatus.ARCHIVING, now, now),
    )
    job_id = int(cursor.lastrowid)
    prior_artifact = release["artifact_attempt_id"]
    attempt_id = _begin_attempt(connection, release)
    _prepare_files(connection, release, attempt_id)
    files = connection.execute(
        """
        SELECT * FROM release_files
        WHERE release_id = ? AND attempt_id = ?
        ORDER BY CASE kind WHEN 'SOURCE_ZIP' THEN 0 WHEN 'SOURCE_TARBALL' THEN 1 ELSE 2 END,
                 id
        """,
        (release_id, attempt_id),
    ).fetchall()
    pending = [record for record in files if record["state"] == FileState.PENDING]
    pending_total = len(pending)
    completed = 0
    plan = TransferPlan(
        [
            int(record["expected_size"])
            if record["kind"] == _ASSET and record["expected_size"] is not None
            else None
            for record in pending
        ]
    )

    for record in files:
        if record["state"] != FileState.PENDING:
            continue
        if cancel_event is not None and cancel_event.is_set():
            _mark(connection, int(record["id"]), FileState.CANCELLED, None, "Download cancelled")
            plan.begin()
            plan.finish(0)
            continue
        if on_progress is not None:
            on_progress("Downloading", record["name"], completed, pending_total)
        _mark(connection, int(record["id"]), FileState.DOWNLOADING, None, None)
        plan.begin()
        try:
            request = _request_for(layout, release, record, str(job_id), attempt_id)
        except UnsafePathError as exc:
            _mark(connection, int(record["id"]), FileState.FAILED, FailureCode.UNSAFE_PATH, exc.message)
            plan.finish(0)
            continue

        def on_bytes(
            downloaded: int,
            expected: int | None,
            force: bool = False,
            file_name: str = record["name"],
            file_id: int = int(record["id"]),
            done: int = completed,
        ) -> None:
            if on_detail is None:
                return
            event = plan.observe(
                downloaded,
                expected,
                force=force,
                job_id=job_id,
                file_id=file_id,
                current_file=file_name,
                completed_files=done,
                total_files=pending_total,
            )
            if event is not None:
                on_detail(event)

        result = download_release_file(
            client,
            request,
            cancel_event,
            after_stream=after_stream,
            on_verifying=lambda file_id=int(record["id"]), file_name=record["name"], done=completed: (
                _mark(connection, file_id, FileState.VERIFYING, None, None),
                _emit_phase(
                    on_detail,
                    plan,
                    phase="verifying",
                    job_id=job_id,
                    file_id=file_id,
                    current_file=file_name,
                    completed_files=done,
                    total_files=pending_total,
                ),
            ),
            on_bytes=on_bytes,
        )
        plan.finish(result.actual_size or 0)
        relative = None
        if result.state == FileState.COMPLETED:
            relative = str(request.final_path.relative_to(layout.archive_root))
            if prior_artifact is None:
                _mirror(request.final_path, _canonical_path(layout, release, record))
        _store_result(connection, int(record["id"]), result, relative, str(layout.archive_root))
        completed += 1
        if on_progress is not None:
            on_progress("Downloading", record["name"], completed, pending_total)

    status = _aggregate_status(connection, release_id, attempt_id, cancel_event)
    if status != JobStatus.COMPLETED:
        _finish_attempt(connection, attempt_id, status)
    if status == JobStatus.COMPLETED:
        stored_files = connection.execute(
            "SELECT * FROM release_files WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchall()
        if prior_artifact is not None:
            _mirror_attempt(layout, release, stored_files)
        _emit_phase(
            on_detail,
            plan,
            phase="committing",
            job_id=job_id,
            file_id=None,
            current_file="",
            completed_files=completed,
            total_files=pending_total,
        )
        if _promote_current(connection, repository_id, release_id, job_id, attempt_id):
            return JobStatus.COMPLETED
        recorded = connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if recorded is not None and recorded["status"] == JobStatus.FAILED:
            _finish_attempt(connection, attempt_id, JobStatus.FAILED)
            return JobStatus.FAILED
        _finish_attempt(connection, attempt_id, JobStatus.PARTIAL)
        _finish_job(connection, job_id, JobStatus.PARTIAL)
        return JobStatus.PARTIAL
    _finish_job(connection, job_id, status)
    return status


def _prepare_files(connection: sqlite3.Connection, release: sqlite3.Row, attempt_id: int) -> None:
    now = _now()
    connection.execute(
        """
        UPDATE release_files
        SET github_state = state
        WHERE release_id = ? AND attempt_id = ? AND kind = ? AND github_state IS NULL
        """,
        (release["id"], attempt_id, _ASSET),
    )
    connection.execute(
        """
        UPDATE release_files
        SET state = ?, failure_code = ?, failure_message = ?, updated_at = ?
        WHERE release_id = ? AND attempt_id = ? AND kind = ? AND github_state IS NOT NULL
          AND github_state != 'uploaded'
        """,
        (
            FileState.FAILED,
            FailureCode.ASSET_NOT_UPLOADED,
            "Asset is not uploaded",
            now,
            release["id"],
            attempt_id,
            _ASSET,
        ),
    )
    connection.execute(
        """
        UPDATE release_files
        SET state = ?, failure_code = NULL, failure_message = NULL, updated_at = ?
        WHERE release_id = ? AND attempt_id = ? AND kind = ? AND github_state = 'uploaded' AND state = 'uploaded'
        """,
        (FileState.PENDING, now, release["id"], attempt_id, _ASSET),
    )
    _ensure_source(
        connection,
        int(release["id"]),
        attempt_id,
        SOURCE_ZIP,
        "source-code.zip",
        release["zipball_url"],
        now,
    )
    _ensure_source(
        connection,
        int(release["id"]),
        attempt_id,
        SOURCE_TARBALL,
        "source-code.tar.gz",
        release["tarball_url"],
        now,
    )


def _ensure_source(
    connection: sqlite3.Connection,
    release_id: int,
    attempt_id: int,
    kind: str,
    name: str,
    url: str | None,
    now: str,
) -> None:
    existing = connection.execute(
        "SELECT id FROM release_files WHERE release_id = ? AND attempt_id = ? AND kind = ?",
        (release_id, attempt_id, kind),
    ).fetchone()
    if existing is not None:
        return
    if not url:
        connection.execute(
            """
            INSERT INTO release_files (
                release_id, attempt_id, kind, name, state, failure_code, failure_message,
                upstream_digest, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                release_id,
                attempt_id,
                kind,
                name,
                FileState.FAILED,
                FailureCode.API_ERROR,
                "Source URL is missing",
                now,
                now,
            ),
        )
        return
    connection.execute(
        """
        INSERT INTO release_files (
            release_id, attempt_id, kind, name, source_url, expected_size, upstream_digest,
            state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
        """,
        (release_id, attempt_id, kind, name, url, FileState.PENDING, now, now),
    )


def _request_for(
    layout: PathLayout,
    release: sqlite3.Row,
    record: sqlite3.Row,
    job_id: str,
    attempt_id: int,
) -> DownloadRequest:
    owner = release["repo_owner"]
    name = release["repo_name"]
    repo_id = int(release["repo_github_id"])
    github_release_id = int(release["github_release_id"])
    if record["kind"] == SOURCE_ZIP:
        final_path = layout.release_attempt_source_file(
            owner, name, repo_id, github_release_id, attempt_id, "source-code.zip"
        )
    elif record["kind"] == SOURCE_TARBALL:
        final_path = layout.release_attempt_source_file(
            owner, name, repo_id, github_release_id, attempt_id, "source-code.tar.gz"
        )
    else:
        final_path = layout.release_attempt_asset_file(
            owner,
            name,
            repo_id,
            github_release_id,
            attempt_id,
            int(record["github_asset_id"]),
            record["name"],
        )
    metadata_size = record["expected_size"]
    if record["kind"] in {SOURCE_ZIP, SOURCE_TARBALL}:
        metadata_size = None
    return DownloadRequest(
        url=record["source_url"],
        part_path=layout.part_file(job_id, int(record["id"])),
        final_path=final_path,
        metadata_size=metadata_size,
        upstream_digest=record["upstream_digest"],
    )


def _store_result(
    connection: sqlite3.Connection,
    file_id: int,
    result,
    relative: str | None,
    archive_root: str,
) -> None:
    connection.execute(
        """
        UPDATE release_files SET
            state = ?,
            failure_code = ?,
            failure_message = ?,
            actual_size = ?,
            expected_size = COALESCE(?, expected_size),
            local_sha256 = ?,
            digest_status = ?,
            relative_path = ?,
            archive_root = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            result.state,
            result.failure_code,
            result.failure_message,
            result.actual_size,
            result.expected_size,
            result.local_sha256,
            result.digest_status,
            relative,
            None if relative is None else archive_root,
            _now(),
            file_id,
        ),
    )


def _mark(
    connection: sqlite3.Connection,
    file_id: int,
    state: str,
    failure_code: str | None,
    message: str | None,
) -> None:
    connection.execute(
        """
        UPDATE release_files
        SET state = ?, failure_code = ?, failure_message = ?, updated_at = ?
        WHERE id = ?
        """,
        (state, failure_code, message, _now(), file_id),
    )


def _aggregate_status(
    connection: sqlite3.Connection,
    release_id: int,
    attempt_id: int,
    cancel_event: Event | None,
) -> str:
    if cancel_event is not None and cancel_event.is_set():
        return JobStatus.CANCELLED
    rows = _file_rows(connection, release_id, attempt_id)
    if _release_is_complete(rows):
        return JobStatus.COMPLETED
    if any(row["state"] == FileState.COMPLETED for row in rows):
        return JobStatus.PARTIAL
    return JobStatus.FAILED


def _promote_current(
    connection: sqlite3.Connection,
    repository_id: int,
    release_id: int,
    job_id: int,
    attempt_id: int,
) -> bool:
    now = _now()
    try:
        connection.execute("BEGIN")
        rows = _file_rows(connection, release_id, attempt_id)
        if not _release_is_complete(rows):
            connection.execute("ROLLBACK")
            return False
        connection.execute(
            """
            UPDATE release_attempts
            SET status = ?, finished_at = ?, failure_code = NULL, failure_message = NULL
            WHERE id = ?
            """,
            (JobStatus.COMPLETED, now, attempt_id),
        )
        connection.execute(
            """
            UPDATE releases
            SET artifact_attempt_id = ?, updated_at_local = ?
            WHERE id = ? AND repository_id = ?
            """,
            (attempt_id, now, release_id, repository_id),
        )
        connection.execute(
            """
            UPDATE repositories
            SET current_release_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (release_id, now, repository_id),
        )
        connection.execute(
            """
            UPDATE jobs
            SET status = ?, finished_at = ?, failure_code = NULL, failure_message = NULL
            WHERE id = ?
            """,
            (JobStatus.COMPLETED, now, job_id),
        )
        connection.execute("COMMIT")
    except sqlite3.Error as exc:
        _rollback(connection)
        logger.error("Current release promotion failed code=DATABASE")
        _finish_job(connection, job_id, JobStatus.FAILED, "DATABASE", "Database promotion failed")
        return False
    return True


def _finish_job(
    connection: sqlite3.Connection,
    job_id: int,
    status: str,
    failure_code: str | None = None,
    message: str | None = None,
) -> None:
    connection.execute(
        """
        UPDATE jobs
        SET status = ?, finished_at = ?, failure_code = ?, failure_message = ?
        WHERE id = ?
        """,
        (status, _now(), failure_code, message, job_id),
    )


def _file_rows(connection: sqlite3.Connection, release_id: int, attempt_id: int) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT kind, state, failure_code, github_state
        FROM release_files
        WHERE release_id = ? AND attempt_id = ?
        """,
        (release_id, attempt_id),
    ).fetchall()


def _release_is_complete(rows: list[sqlite3.Row]) -> bool:
    if any(row["failure_code"] == FailureCode.ASSET_NOT_UPLOADED for row in rows):
        return False
    required = [
        row
        for row in rows
        if row["kind"] in {SOURCE_ZIP, SOURCE_TARBALL} or row["github_state"] == "uploaded"
    ]
    if len([row for row in required if row["kind"] == SOURCE_ZIP]) != 1:
        return False
    if len([row for row in required if row["kind"] == SOURCE_TARBALL]) != 1:
        return False
    uploaded = [row for row in required if row["github_state"] == "uploaded"]
    if any(row["state"] != FileState.COMPLETED for row in required):
        return False
    if any(row["state"] != FileState.COMPLETED for row in uploaded):
        return False
    return True


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        return


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_attempt(connection: sqlite3.Connection, release_id: int, now: str) -> int:
    cursor = connection.execute(
        """
        INSERT INTO release_attempts (release_id, status, created_at)
        VALUES (?, 'ARCHIVING', ?)
        """,
        (release_id, now),
    )
    return int(cursor.lastrowid)


def _begin_attempt(connection: sqlite3.Connection, release: sqlite3.Row) -> int:
    now = _now()
    release_id = int(release["id"])
    if release["artifact_attempt_id"] is None:
        existing = connection.execute(
            """
            SELECT id FROM release_attempts
            WHERE release_id = ? AND status IN ('ARCHIVING', 'PARTIAL', 'FAILED', 'CANCELLED')
            ORDER BY id DESC
            LIMIT 1
            """,
            (release_id,),
        ).fetchone()
        if existing is not None and _attempt_has_files(connection, int(existing["id"])):
            attempt_id = int(existing["id"])
        else:
            attempt_id = _insert_attempt(connection, release_id, now)
        connection.execute(
            """
            UPDATE release_files
            SET attempt_id = ?
            WHERE release_id = ? AND attempt_id IS NULL
            """,
            (attempt_id, release_id),
        )
        connection.execute(
            """
            UPDATE release_attempts
            SET status = 'ARCHIVING', failure_code = NULL, failure_message = NULL, finished_at = NULL
            WHERE id = ?
            """,
            (attempt_id,),
        )
        return attempt_id
    attempt_id = _insert_attempt(connection, release_id, now)
    _seed_assets(connection, release_id, attempt_id, now)
    return attempt_id


def _attempt_has_files(connection: sqlite3.Connection, attempt_id: int) -> bool:
    row = connection.execute(
        "SELECT 1 FROM release_files WHERE attempt_id = ? LIMIT 1",
        (attempt_id,),
    ).fetchone()
    return row is not None


def _seed_assets(connection: sqlite3.Connection, release_id: int, attempt_id: int, now: str) -> None:
    rows = connection.execute(
        "SELECT * FROM release_asset_catalog WHERE release_id = ? ORDER BY id",
        (release_id,),
    ).fetchall()
    for row in rows:
        connection.execute(
            """
            INSERT INTO release_files (
                release_id, attempt_id, kind, github_asset_id, name, source_url, expected_size,
                upstream_digest, state, content_type, created_at, updated_at
            ) VALUES (?, ?, 'asset', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                release_id,
                attempt_id,
                row["github_asset_id"],
                row["name"],
                row["source_url"],
                row["expected_size"],
                row["upstream_digest"],
                row["github_state"] or "uploaded",
                row["content_type"],
                now,
                now,
            ),
        )


def _finish_attempt(connection: sqlite3.Connection, attempt_id: int, status: str) -> None:
    if status == JobStatus.COMPLETED:
        return
    connection.execute(
        """
        UPDATE release_attempts
        SET status = ?, finished_at = ?
        WHERE id = ?
        """,
        (status, _now(), attempt_id),
    )


def _canonical_path(layout: PathLayout, release: sqlite3.Row, record: sqlite3.Row) -> Path:
    owner = release["repo_owner"]
    name = release["repo_name"]
    repo_id = int(release["repo_github_id"])
    github_release_id = int(release["github_release_id"])
    if record["kind"] == SOURCE_ZIP:
        return layout.release_source_file(owner, name, repo_id, github_release_id, "source-code.zip")
    if record["kind"] == SOURCE_TARBALL:
        return layout.release_source_file(owner, name, repo_id, github_release_id, "source-code.tar.gz")
    return layout.release_asset_file(
        owner,
        name,
        repo_id,
        github_release_id,
        int(record["github_asset_id"]),
        record["name"],
    )


def _mirror_attempt(layout: PathLayout, release: sqlite3.Row, files: list[sqlite3.Row]) -> None:
    for record in files:
        if record["state"] != FileState.PENDING and record["relative_path"]:
            source = Path(layout.archive_root) / record["relative_path"]
            if source.is_file():
                _mirror(source, _canonical_path(layout, release, record))


def _mirror(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f"{destination.name}.mirror"
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def _emit_phase(
    on_detail,
    plan,
    *,
    phase: str,
    job_id: int,
    file_id: int | None,
    current_file: str,
    completed_files: int,
    total_files: int,
) -> None:
    if on_detail is None:
        return
    event = plan.snapshot(
        job_id=job_id,
        file_id=file_id,
        current_file=current_file,
        completed_files=completed_files,
        total_files=total_files,
    )
    on_detail(replace(event, phase=phase))
