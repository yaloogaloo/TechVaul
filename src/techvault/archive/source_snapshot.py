"""Download a repository source snapshot. This is not a GitHub Release."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from threading import Event
from urllib.parse import quote

from dataclasses import replace

from techvault.archive.downloader import DownloadRequest, FileState, download_release_file
from techvault.archive.paths import PathLayout
from techvault.archive.progress import TransferPlan
from techvault.archive.release_archive import JobStatus
from techvault.archive.storage import repository_dir_for
from techvault.github.client import API_BASE, GitHubClient


def archive_source_snapshot(
    connection: sqlite3.Connection,
    client: GitHubClient,
    layout: PathLayout,
    *,
    repository_id: int,
    cancel_event: Event | None = None,
    on_detail=None,
) -> str:
    repository = connection.execute(
        "SELECT * FROM repositories WHERE id = ?",
        (repository_id,),
    ).fetchone()
    if repository is None:
        return JobStatus.FAILED
    ref = repository["default_branch"] or "HEAD"
    now = _now()
    cursor = connection.execute(
        """
        INSERT INTO source_snapshots (
            repository_id, ref, default_branch, commit_sha, archived_at,
            state, created_at, updated_at
        ) VALUES (?, ?, ?, NULL, ?, 'PENDING', ?, ?)
        """,
        (repository_id, ref, repository["default_branch"], now, now, now),
    )
    snapshot_id = int(cursor.lastrowid)
    job_cursor = connection.execute(
        """
        INSERT INTO jobs (
            repository_id, target_release_id, status, created_at, started_at,
            archive_kind, target_snapshot_id
        ) VALUES (?, NULL, ?, ?, ?, 'SOURCE', ?)
        """,
        (repository_id, JobStatus.ARCHIVING, now, now, snapshot_id),
    )
    job_id = int(job_cursor.lastrowid)
    if cancel_event is not None and cancel_event.is_set():
        _close_snapshot(connection, snapshot_id, job_id, JobStatus.CANCELLED, None, "已取消")
        return JobStatus.CANCELLED

    repo_root = repository_dir_for(connection, layout, repository_id=repository_id)
    branch = repository["default_branch"] or "HEAD"
    final_path = layout.snapshot_source_file_at(
        repo_root,
        snapshot_id,
        branch=branch,
        commit_sha=None,
        when=datetime.now(timezone.utc),
    )
    url = _zipball_url(repository["owner"], repository["name"], repository["default_branch"])
    request = DownloadRequest(
        url=url,
        part_path=layout.part_file(str(job_id), snapshot_id),
        final_path=final_path,
        metadata_size=None,
        upstream_digest=None,
    )
    connection.execute(
        """
        UPDATE source_snapshots
        SET state = 'DOWNLOADING', updated_at = ?
        WHERE id = ?
        """,
        (_now(), snapshot_id),
    )
    plan = TransferPlan([None])
    plan.begin()

    def on_bytes(downloaded: int, expected: int | None, force: bool = False) -> None:
        if on_detail is None:
            return
        event = plan.observe(
            downloaded,
            expected,
            force=force,
            job_id=job_id,
            file_id=snapshot_id,
            current_file="source-code.zip",
            completed_files=0,
            total_files=1,
        )
        if event is not None:
            on_detail(event)

    result = download_release_file(
        client,
        request,
        cancel_event,
        on_verifying=lambda: (
            connection.execute(
                "UPDATE source_snapshots SET state = 'VERIFYING', updated_at = ? WHERE id = ?",
                (_now(), snapshot_id),
            ),
            on_detail(
                replace(
                    plan.snapshot(
                        job_id=job_id,
                        file_id=snapshot_id,
                        current_file="source-code.zip",
                        completed_files=0,
                        total_files=1,
                    ),
                    phase="verifying",
                )
            )
            if on_detail is not None
            else None,
        ),
        on_bytes=on_bytes,
    )
    if result.state != FileState.COMPLETED:
        status = JobStatus.CANCELLED if result.state == FileState.CANCELLED else JobStatus.FAILED
        _close_snapshot(
            connection,
            snapshot_id,
            job_id,
            status,
            result.failure_code,
            result.failure_message,
        )
        return status
    plan.finish(result.actual_size or 0)
    if on_detail is not None:
        on_detail(
            replace(
                plan.snapshot(
                    job_id=job_id,
                    file_id=snapshot_id,
                    current_file="source-code.zip",
                    completed_files=1,
                    total_files=1,
                ),
                phase="committing",
            )
        )
    relative = str(final_path.relative_to(layout.archive_root))
    finished = _now()
    connection.execute("BEGIN")
    connection.execute(
        """
        UPDATE source_snapshots
        SET state = 'COMPLETED', size = ?, local_sha256 = ?, relative_path = ?,
            archive_root = ?, archived_at = ?, failure_code = NULL, failure_message = NULL,
            updated_at = ?
        WHERE id = ?
        """,
        (
            result.actual_size,
            result.local_sha256,
            relative,
            str(layout.archive_root),
            finished,
            finished,
            snapshot_id,
        ),
    )
    connection.execute(
        """
        UPDATE repositories
        SET current_snapshot_id = ?, archive_status = 'ARCHIVED',
            last_synced_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (snapshot_id, finished, finished, repository_id),
    )
    connection.execute(
        """
        UPDATE jobs
        SET status = ?, finished_at = ?, failure_code = NULL, failure_message = NULL
        WHERE id = ?
        """,
        (JobStatus.COMPLETED, finished, job_id),
    )
    connection.execute("COMMIT")
    return JobStatus.COMPLETED


def _zipball_url(owner: str, repo: str, default_branch: str | None) -> str:
    base = f"{API_BASE}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/zipball"
    if not default_branch:
        return base
    return f"{base}/{quote(default_branch, safe='')}"


def _close_snapshot(
    connection: sqlite3.Connection,
    snapshot_id: int,
    job_id: int,
    status: str,
    failure_code: str | None,
    message: str | None,
) -> None:
    now = _now()
    state = "CANCELLED" if status == JobStatus.CANCELLED else "FAILED"
    connection.execute(
        """
        UPDATE source_snapshots
        SET state = ?, failure_code = ?, failure_message = ?, updated_at = ?
        WHERE id = ? AND state != 'COMPLETED'
        """,
        (state, failure_code, message, now, snapshot_id),
    )
    connection.execute(
        """
        UPDATE jobs
        SET status = ?, finished_at = ?, failure_code = ?, failure_message = ?
        WHERE id = ?
        """,
        (status, now, failure_code, message, job_id),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
