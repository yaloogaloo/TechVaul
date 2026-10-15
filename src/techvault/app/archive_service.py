"""Archive one repository through the existing lookup, download, and page pipeline."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from techvault.app.assets import AssetKind, load_asset_records
from techvault.app.messages import user_message
from techvault.app.settings import SettingsService
from techvault.archive.identity import adopt_legacy_tree
from techvault.archive.paths import PathLayout
from techvault.archive.storage import repository_dir_for
from techvault.archive.release_archive import JobStatus, archive_release, reopen_failed_files
from techvault.archive.source_snapshot import archive_source_snapshot
from techvault.db.release_store import save_release_lookup
from techvault.errors import KeyringUnavailableError, TechVaultError
from techvault.github.client import GitHubClient
from techvault.github.models import LookupOutcome
from techvault.github.releases import lookup_official_release
from techvault.github.urls import parse_github_repository_url
from techvault.logging_setup import get_logger
from techvault.offline.pages import PageStatus, build_offline_pages
from techvault.security.token_store import load_token

logger = get_logger()

_BLOCKED_OUTCOMES = {
    LookupOutcome.REPO_NOT_VISIBLE,
    LookupOutcome.AUTH_INVALID,
    LookupOutcome.FORBIDDEN,
    LookupOutcome.API_ERROR,
}
_SOURCE_ONLY_OUTCOMES = {
    LookupOutcome.NO_RELEASES,
    LookupOutcome.ONLY_PRERELEASE_OR_DRAFT,
    LookupOutcome.NO_OFFICIAL_LATEST,
}


@dataclass(frozen=True)
class ArchiveResult:
    status: str
    message: str
    repository_name: str | None = None
    html_url: str | None = None
    release_tag: str | None = None
    offline_page: str | None = None
    archive_folder: str | None = None
    current_release_id: int | None = None


class ArchiveService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def archive_repository(
        self,
        url: str,
        *,
        cancel_event: Event | None = None,
        on_progress=None,
        on_detail=None,
    ) -> ArchiveResult:
        def report(operation: str, current_file: str, completed: int, total: int) -> None:
            if on_progress is not None:
                on_progress(operation, current_file, completed, total)

        if cancel_event is not None and cancel_event.is_set():
            return ArchiveResult(JobStatus.CANCELLED, user_message(JobStatus.CANCELLED))

        try:
            ref = parse_github_repository_url(url)
        except TechVaultError as exc:
            logger.warning("Archive stopped code=%s", exc.code)
            return ArchiveResult(exc.code, user_message(exc.code))

        root = SettingsService(self._connection).get_archive_root()
        if root is None:
            return ArchiveResult("FAILED", "请先在设置中选择项目资料位置。")
        layout = PathLayout(root)
        report("Looking up repository", "", 0, 0)
        token = _token_or_none()
        try:
            with GitHubClient(token=token) as client:
                if cancel_event is not None and cancel_event.is_set():
                    return ArchiveResult(JobStatus.CANCELLED, user_message(JobStatus.CANCELLED))
                lookup = lookup_official_release(client, ref.owner, ref.repo)
                save_release_lookup(self._connection, lookup)
                if lookup.repository is None or lookup.outcome in _BLOCKED_OUTCOMES:
                    return ArchiveResult(lookup.outcome, user_message(lookup.outcome))
                stored = _repository_row(self._connection, lookup.repository.id)
                if stored is None:
                    return ArchiveResult("FAILED", user_message("FAILED"))
                if cancel_event is not None and cancel_event.is_set():
                    return _result(self._connection, stored, JobStatus.CANCELLED, layout)
                adopt_legacy_tree(
                    self._connection,
                    layout,
                    owner=stored["owner"],
                    name=stored["name"],
                    github_repo_id=int(stored["github_repo_id"]),
                )
                report("Archiving source snapshot", "source-code.zip", 0, 1)
                snapshot_status = archive_source_snapshot(
                    self._connection,
                    client,
                    layout,
                    repository_id=int(stored["id"]),
                    cancel_event=cancel_event,
                    on_detail=on_detail,
                )
                if snapshot_status == JobStatus.CANCELLED:
                    return _result(self._connection, stored, JobStatus.CANCELLED, layout)
                if lookup.outcome == LookupOutcome.STABLE and lookup.release is not None:
                    release_row = self._connection.execute(
                        """
                        SELECT id FROM releases
                        WHERE repository_id = ? AND github_release_id = ?
                        """,
                        (int(stored["id"]), lookup.release.github_release_id),
                    ).fetchone()
                    if release_row is None:
                        return ArchiveResult("FAILED", user_message("FAILED"))
                    report("Archiving release", "", 0, 1)
                    status = archive_release(
                        self._connection,
                        client,
                        layout,
                        repository_id=int(stored["id"]),
                        release_id=int(release_row["id"]),
                        cancel_event=cancel_event,
                        on_progress=on_progress,
                        on_detail=on_detail,
                    )
                    if status == JobStatus.CANCELLED:
                        return _result(self._connection, stored, JobStatus.CANCELLED, layout)
                    if status != JobStatus.COMPLETED or snapshot_status != JobStatus.COMPLETED:
                        if status == JobStatus.COMPLETED or snapshot_status == JobStatus.COMPLETED:
                            return _result(self._connection, stored, JobStatus.PARTIAL, layout)
                        return _result(self._connection, stored, status, layout)
                elif lookup.outcome in _SOURCE_ONLY_OUTCOMES:
                    if snapshot_status != JobStatus.COMPLETED:
                        return _result(self._connection, stored, snapshot_status, layout)
                else:
                    return ArchiveResult(lookup.outcome, user_message(lookup.outcome))
                report("Building offline page", "pages/index.html", 0, 1)
                page_status = build_offline_pages(
                    self._connection,
                    client,
                    layout,
                    int(stored["id"]),
                )
        except TechVaultError as exc:
            logger.warning("Archive stopped code=%s", exc.code)
            return ArchiveResult(exc.code, user_message(exc.code))
        except Exception:
            logger.exception("Archive failed")
            return ArchiveResult("FAILED", user_message("FAILED"))

        stored = _repository_row(self._connection, lookup.repository.id)
        if stored is None:
            return ArchiveResult("FAILED", user_message("FAILED"))
        if page_status != PageStatus.COMPLETED:
            return _result(self._connection, stored, "OFFLINE_PAGE_FAILED", layout)
        index = layout.page_index_at(
            repository_dir_for(self._connection, layout, repository_id=int(stored["id"]))
        )
        if not index.is_file():
            return _result(self._connection, stored, "OFFLINE_PAGE_FAILED", layout)
        report("Building offline page", index.name, 1, 1)
        return _result(self._connection, stored, JobStatus.COMPLETED, layout, offline_page=index)

    def retry_failed_asset(
        self,
        repository_id: int,
        asset: str,
        *,
        cancel_event: Event | None = None,
        on_progress=None,
        on_detail=None,
    ) -> ArchiveResult:
        """Re-run one failed asset. Successful snapshot and release pointers stay put."""

        def report(operation: str, current_file: str, completed: int, total: int) -> None:
            if on_progress is not None:
                on_progress(operation, current_file, completed, total)

        stored = self._connection.execute(
            """
            SELECT id, owner, name, full_name, github_repo_id, html_url, current_release_id
            FROM repositories WHERE id = ?
            """,
            (repository_id,),
        ).fetchone()
        if stored is None:
            return ArchiveResult("FAILED", user_message("FAILED"))
        record = next((item for item in load_asset_records(self._connection, repository_id) if item.kind == asset), None)
        if record is None or not record.retryable:
            return ArchiveResult("FAILED", "这项资料没有失败，不需要重新保存。")
        root = SettingsService(self._connection).get_archive_root()
        if root is None:
            return ArchiveResult("FAILED", "请先在设置中选择项目资料位置。")
        layout = PathLayout(root)
        if cancel_event is not None and cancel_event.is_set():
            return ArchiveResult(JobStatus.CANCELLED, user_message(JobStatus.CANCELLED))
        try:
            with GitHubClient(token=_token_or_none()) as client:
                if asset == AssetKind.README:
                    report("Building offline page", "README", 0, 1)
                    page_status = build_offline_pages(self._connection, client, layout, int(stored["id"]))
                    if page_status != PageStatus.COMPLETED:
                        return _result(self._connection, stored, "OFFLINE_PAGE_FAILED", layout)
                    index = layout.page_index_at(
                        repository_dir_for(self._connection, layout, repository_id=int(stored["id"]))
                    )
                    if not index.is_file():
                        return _result(self._connection, stored, "OFFLINE_PAGE_FAILED", layout)
                    report("Building offline page", index.name, 1, 1)
                    return _result(self._connection, stored, JobStatus.COMPLETED, layout, offline_page=index)
                if asset == AssetKind.SOURCE:
                    report("Archiving source snapshot", "source-code.zip", 0, 1)
                    status = archive_source_snapshot(
                        self._connection,
                        client,
                        layout,
                        repository_id=int(stored["id"]),
                        cancel_event=cancel_event,
                        on_detail=on_detail,
                    )
                    return _result(self._connection, stored, status, layout)
                if asset == AssetKind.RELEASE:
                    release_row = self._connection.execute(
                        """
                        SELECT id FROM releases
                        WHERE repository_id = ? AND lookup_outcome = 'STABLE'
                        ORDER BY id DESC LIMIT 1
                        """,
                        (int(stored["id"]),),
                    ).fetchone()
                    if release_row is None:
                        return ArchiveResult("FAILED", user_message("NO_RELEASES"))
                    reopen_failed_files(self._connection, int(release_row["id"]))
                    report("Archiving release", "", 0, 1)
                    status = archive_release(
                        self._connection,
                        client,
                        layout,
                        repository_id=int(stored["id"]),
                        release_id=int(release_row["id"]),
                        cancel_event=cancel_event,
                        on_progress=on_progress,
                        on_detail=on_detail,
                    )
                    return _result(self._connection, stored, status, layout)
        except TechVaultError as exc:
            logger.warning("Asset retry stopped code=%s", exc.code)
            return ArchiveResult(exc.code, user_message(exc.code))
        except Exception:
            logger.exception("Asset retry failed")
            return ArchiveResult("FAILED", user_message("FAILED"))
        return ArchiveResult("FAILED", user_message("FAILED"))


def _token_or_none() -> str | None:
    try:
        return load_token()
    except KeyringUnavailableError:
        logger.warning("Archive continuing without a token code=KEYRING_UNAVAILABLE")
        return None


def _repository_row(connection: sqlite3.Connection, github_repo_id: int) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, owner, name, full_name, github_repo_id, html_url, current_release_id
        FROM repositories
        WHERE github_repo_id = ?
        """,
        (github_repo_id,),
    ).fetchone()


def _result(
    connection: sqlite3.Connection,
    stored: sqlite3.Row,
    status: str,
    layout: PathLayout,
    offline_page: Path | None = None,
) -> ArchiveResult:
    refreshed = _repository_row(connection, int(stored["github_repo_id"]))
    if refreshed is not None:
        stored = refreshed
    current_id = stored["current_release_id"]
    current = connection.execute(
        """
        SELECT releases.tag_name AS current_tag
        FROM repositories
        LEFT JOIN releases ON releases.id = repositories.current_release_id
        WHERE repositories.id = ?
        """,
        (int(stored["id"]),),
    ).fetchone()
    tag = None
    if current_id is not None and current is not None:
        tag = current["current_tag"]
    folder = repository_dir_for(connection, layout, repository_id=int(stored["id"]))
    return ArchiveResult(
        status=status,
        message=user_message(status),
        repository_name=stored["full_name"],
        html_url=stored["html_url"],
        release_tag=tag,
        offline_page=None if offline_page is None else str(offline_page),
        archive_folder=str(folder),
        current_release_id=None if current_id is None else int(current_id),
    )
