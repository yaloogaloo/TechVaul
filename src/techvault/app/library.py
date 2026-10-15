"""Local GitHub repository library. Lists come from SQLite, not the GitHub API."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from html import unescape

from pathlib import Path

from techvault.app.assets import AssetRecord, load_asset_records
from techvault.archive.identity import resolve_stored_path
from techvault.archive.paths import PathLayout
from techvault.archive.storage import repository_dir_for
from techvault.errors import DatabaseError


@dataclass(frozen=True)
class RepositoryCategory:
    id: int
    name: str
    sort_order: int


@dataclass(frozen=True)
class RepositoryListItem:
    id: int
    owner: str
    name: str
    full_name: str
    description: str
    html_url: str
    category_id: int | None
    category_name: str
    archived_at: str
    archive_kind: str
    state: str
    release_tag: str
    snapshot_ref: str
    operation: str
    operation_label: str
    topics: str = ""


@dataclass(frozen=True)
class SnapshotDetail:
    ref: str
    commit_sha: str
    archived_at: str
    size: int | None
    local_sha256: str
    state: str


@dataclass(frozen=True)
class ReleaseFileDetail:
    name: str
    state: str
    local_sha256: str
    digest_status: str


@dataclass(frozen=True)
class RepositoryDetail:
    id: int
    owner: str
    name: str
    description: str
    html_url: str
    default_branch: str
    topics: tuple[str, ...]
    category_id: int | None
    category_name: str
    state: str
    snapshot: SnapshotDetail | None
    release_tag: str
    release_name: str
    release_published_at: str
    release_files: tuple[ReleaseFileDetail, ...]
    offline_page: str
    offline_built_at: str
    offline_absolute: str
    archive_folder: str
    github_repo_id: int
    last_job_status: str
    last_job_message: str
    operation_label: str


class LibraryService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_categories(self) -> list[RepositoryCategory]:
        rows = self._connection.execute(
            "SELECT id, name, sort_order FROM repository_categories ORDER BY sort_order, name COLLATE NOCASE"
        ).fetchall()
        return [RepositoryCategory(int(row["id"]), row["name"], int(row["sort_order"])) for row in rows]

    def create_category(self, name: str) -> RepositoryCategory:
        cleaned = _category_name(name)
        try:
            cursor = self._connection.execute(
                "INSERT INTO repository_categories (name, sort_order) VALUES (?, ?)",
                (cleaned, self._next_sort()),
            )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("Repository category already exists") from exc
        return RepositoryCategory(int(cursor.lastrowid), cleaned, self._next_sort() - 1)

    def rename_category(self, category_id: int, name: str) -> None:
        cleaned = _category_name(name)
        try:
            self._connection.execute(
                "UPDATE repository_categories SET name = ? WHERE id = ?",
                (cleaned, category_id),
            )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError("Repository category already exists") from exc

    def delete_category(self, category_id: int) -> None:
        self._connection.execute(
            "UPDATE repositories SET category_id = NULL WHERE category_id = ?",
            (category_id,),
        )
        self._connection.execute(
            "DELETE FROM repository_categories WHERE id = ?",
            (category_id,),
        )

    def set_repository_category(self, repository_id: int, category_id: int | None) -> None:
        self._connection.execute(
            "UPDATE repositories SET category_id = ? WHERE id = ?",
            (category_id, repository_id),
        )

    def list_repositories(
        self,
        *,
        query: str = "",
        category_id: int | None = None,
        uncategorized: bool = False,
        sort: str = "recent",
    ) -> list[RepositoryListItem]:
        clauses = ["1 = 1"]
        params: list[object] = []
        text = query.strip()
        if text:
            pattern = f"%{_escape_like(text)}%"
            clauses.append(
                """(
                    r.name LIKE ? ESCAPE '\\'
                    OR r.owner LIKE ? ESCAPE '\\'
                    OR IFNULL(r.description, '') LIKE ? ESCAPE '\\'
                    OR IFNULL(c.name, '') LIKE ? ESCAPE '\\'
                    OR EXISTS (
                        SELECT 1 FROM repository_topics topic
                        WHERE topic.repository_id = r.id
                          AND topic.name LIKE ? ESCAPE '\\'
                    )
                )"""
            )
            params.extend([pattern, pattern, pattern, pattern, pattern])
        if uncategorized:
            clauses.append("r.category_id IS NULL")
        elif category_id is not None:
            clauses.append("r.category_id = ?")
            params.append(category_id)
        order = {
            "name": "r.name COLLATE NOCASE, r.owner COLLATE NOCASE",
            "owner": "r.owner COLLATE NOCASE, r.name COLLATE NOCASE",
        }.get(sort, "COALESCE(snap.archived_at, r.updated_at) DESC, r.name COLLATE NOCASE")
        rows = self._connection.execute(
            f"""
            SELECT
                r.id, r.owner, r.name, r.full_name, r.description, r.html_url,
                r.category_id, r.current_release_id, r.current_snapshot_id,
                c.name AS category_name,
                rel.tag_name AS release_tag,
                snap.ref AS snapshot_ref,
                snap.archived_at AS snapshot_archived_at,
                snap.state AS snapshot_state,
                (
                    SELECT group_concat(topic.name, ' · ')
                    FROM repository_topics topic
                    WHERE topic.repository_id = r.id
                ) AS topics,
                (
                    SELECT status FROM jobs
                    WHERE repository_id = r.id AND archive_kind = 'SOURCE'
                    ORDER BY id DESC LIMIT 1
                ) AS source_job_status,
                (
                    SELECT status FROM jobs
                    WHERE repository_id = r.id AND archive_kind = 'RELEASE'
                    ORDER BY id DESC LIMIT 1
                ) AS release_job_status
            FROM repositories r
            LEFT JOIN repository_categories c ON c.id = r.category_id
            LEFT JOIN releases rel ON rel.id = r.current_release_id
            LEFT JOIN source_snapshots snap ON snap.id = r.current_snapshot_id
            WHERE {' AND '.join(clauses)}
            ORDER BY {order}
            """,
            params,
        ).fetchall()
        items: list[RepositoryListItem] = []
        for row in rows:
            kind = ""
            if row["current_release_id"] is not None:
                kind = "正式 Release"
            elif row["current_snapshot_id"] is not None:
                kind = "源码快照"
            operation = _operation_status(row["source_job_status"], row["release_job_status"])
            items.append(
                RepositoryListItem(
                    id=int(row["id"]),
                    owner=row["owner"],
                    name=row["name"],
                    full_name=row["full_name"],
                    description=row["description"] or "",
                    html_url=row["html_url"],
                    category_id=None if row["category_id"] is None else int(row["category_id"]),
                    category_name=row["category_name"] or "",
                    archived_at=row["snapshot_archived_at"] or "",
                    archive_kind=kind,
                    state=_state(
                        operation,
                        row["current_release_id"],
                        row["current_snapshot_id"],
                    ),
                    release_tag=row["release_tag"] or "",
                    snapshot_ref=row["snapshot_ref"] or "",
                    operation=operation or "",
                    operation_label=_operation_label(operation),
                    topics=row["topics"] or "",
                )
            )
        return items

    def get_repository(self, repository_id: int) -> RepositoryDetail | None:
        row = self._connection.execute(
            """
            SELECT
                r.*,
                c.name AS category_name,
                rel.tag_name AS release_tag,
                rel.name AS release_name,
                rel.published_at AS release_published_at,
                rel.artifact_attempt_id AS artifact_attempt_id,
                (
                    SELECT status FROM jobs
                    WHERE repository_id = r.id AND archive_kind = 'SOURCE'
                    ORDER BY id DESC LIMIT 1
                ) AS source_job_status,
                (
                    SELECT status FROM jobs
                    WHERE repository_id = r.id AND archive_kind = 'RELEASE'
                    ORDER BY id DESC LIMIT 1
                ) AS release_job_status,
                (
                    SELECT failure_message FROM jobs
                    WHERE repository_id = r.id AND failure_message IS NOT NULL
                    ORDER BY id DESC LIMIT 1
                ) AS job_message
            FROM repositories r
            LEFT JOIN repository_categories c ON c.id = r.category_id
            LEFT JOIN releases rel ON rel.id = r.current_release_id
            WHERE r.id = ?
            """,
            (repository_id,),
        ).fetchone()
        if row is None:
            return None
        topics = tuple(
            item["name"]
            for item in self._connection.execute(
                "SELECT name FROM repository_topics WHERE repository_id = ? ORDER BY name",
                (repository_id,),
            )
        )
        snapshot = None
        if row["current_snapshot_id"] is not None:
            snap = self._connection.execute(
                "SELECT * FROM source_snapshots WHERE id = ?",
                (row["current_snapshot_id"],),
            ).fetchone()
            if snap is not None:
                snapshot = SnapshotDetail(
                    ref=snap["ref"],
                    commit_sha=snap["commit_sha"] or "",
                    archived_at=snap["archived_at"],
                    size=None if snap["size"] is None else int(snap["size"]),
                    local_sha256=snap["local_sha256"] or "",
                    state=snap["state"],
                )
        files: tuple[ReleaseFileDetail, ...] = ()
        release_root = None
        if row["current_release_id"] is not None:
            file_rows = self._connection.execute(
                """
                SELECT name, state, local_sha256, digest_status, archive_root
                FROM release_files
                WHERE release_id = ?
                  AND (
                    attempt_id = ?
                    OR (? IS NULL AND state = 'COMPLETED')
                  )
                ORDER BY id
                """,
                (
                    row["current_release_id"],
                    row["artifact_attempt_id"],
                    row["artifact_attempt_id"],
                ),
            ).fetchall()
            files = tuple(
                ReleaseFileDetail(
                    name=item["name"],
                    state=item["state"],
                    local_sha256=item["local_sha256"] or "",
                    digest_status=item["digest_status"] or "",
                )
                for item in file_rows
            )
            for item in file_rows:
                if item["archive_root"]:
                    release_root = item["archive_root"]
                    break
        page = self._connection.execute(
            """
            SELECT relative_path, built_at, archive_root FROM offline_pages
            WHERE repository_id = ? AND page_type = 'REPOSITORY'
            """,
            (repository_id,),
        ).fetchone()
        fallback = _settings_root(self._connection)
        offline_absolute = ""
        built_at = ""
        folder_root = fallback
        if page is not None:
            built_at = page["built_at"] or ""
            resolved = resolve_stored_path(page["archive_root"], page["relative_path"], fallback)
            offline_absolute = "" if resolved is None else str(resolved)
            if page["archive_root"]:
                folder_root = Path(page["archive_root"])
        elif snapshot is not None and row["current_snapshot_id"] is not None:
            snap_row = self._connection.execute(
                "SELECT archive_root FROM source_snapshots WHERE id = ?",
                (row["current_snapshot_id"],),
            ).fetchone()
            if snap_row is not None and snap_row["archive_root"]:
                folder_root = Path(snap_row["archive_root"])
        elif release_root:
            folder_root = Path(release_root)
        operation = _operation_status(row["source_job_status"], row["release_job_status"])
        return RepositoryDetail(
            id=int(row["id"]),
            owner=row["owner"],
            name=row["name"],
            description=row["description"] or "",
            html_url=row["html_url"],
            default_branch=row["default_branch"] or "",
            topics=topics,
            category_id=None if row["category_id"] is None else int(row["category_id"]),
            category_name=row["category_name"] or "",
            state=_state(operation, row["current_release_id"], row["current_snapshot_id"]),
            snapshot=snapshot,
            release_tag=row["release_tag"] or "",
            release_name=row["release_name"] or "",
            release_published_at=row["release_published_at"] or "",
            release_files=files,
            offline_page="" if page is None or page["relative_path"] is None else page["relative_path"],
            offline_built_at=built_at,
            offline_absolute=offline_absolute,
            archive_folder=str(
                repository_dir_for(
                    self._connection,
                    PathLayout(folder_root),
                    repository_id=int(row["id"]),
                )
            ),
            github_repo_id=int(row["github_repo_id"]),
            last_job_status=operation or "",
            last_job_message=row["job_message"] or "",
            operation_label=_operation_label(operation),
        )

    def asset_records(self, repository_id: int) -> tuple[AssetRecord, ...]:
        return load_asset_records(self._connection, repository_id)

    def readme_document(self, repository_id: int) -> tuple[str, str, str]:
        """Return (kind, body, resource directory). kind is markdown, html, or empty."""
        detail = self.get_repository(repository_id)
        if detail is None:
            return ("empty", "", "")
        page = self._connection.execute(
            """
            SELECT archive_root FROM offline_pages
            WHERE repository_id = ? AND page_type = 'REPOSITORY'
            """,
            (repository_id,),
        ).fetchone()
        root = Path(page["archive_root"]) if page is not None and page["archive_root"] else _settings_root(self._connection)
        layout = PathLayout(root)
        repo_root = repository_dir_for(self._connection, layout, repository_id=repository_id)
        markdown_path = layout.readme_file_at(repo_root)
        # V1 compat: readme/README.md under repositories/<id>/
        v1_readme = layout.v1_repository_dir(detail.github_repo_id) / "readme" / "README.md"
        if not markdown_path.is_file() and v1_readme.is_file():
            markdown_path = v1_readme
        if markdown_path.is_file():
            try:
                return ("markdown", markdown_path.read_text(encoding="utf-8"), str(markdown_path.parent))
            except OSError:
                return ("empty", "", "")
        if detail.offline_absolute and Path(detail.offline_absolute).is_file():
            try:
                html = Path(detail.offline_absolute).read_text(encoding="utf-8")
            except OSError:
                return ("empty", "", "")
            fragment = _readme_html_fragment(html)
            if fragment:
                return ("html", fragment, str(Path(detail.offline_absolute).parent))
        return ("empty", "", "")

    def readme_text(self, repository_id: int) -> str:
        detail = self.get_repository(repository_id)
        if detail is None or not detail.offline_absolute:
            return ""
        path = Path(detail.offline_absolute)
        if not path.is_file():
            return ""
        try:
            html = path.read_text(encoding="utf-8")
        except OSError:
            return ""
        return _readme_excerpt(html)

    def has_repositories(self) -> bool:
        row = self._connection.execute("SELECT 1 FROM repositories LIMIT 1").fetchone()
        return row is not None

    def _next_sort(self) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_sort FROM repository_categories"
        ).fetchone()
        return int(row["next_sort"])


def _readme_html_fragment(html: str) -> str:
    match = re.search(r"<h2>\s*README\s*</h2>(.*?)<h2>", html, re.IGNORECASE | re.DOTALL)
    fragment = match.group(1).strip() if match else ""
    if "readme unavailable" in fragment.lower():
        return ""
    return fragment


def _readme_excerpt(html: str) -> str:
    match = re.search(r"<h2>\s*README\s*</h2>(.*?)<h2>", html, re.IGNORECASE | re.DOTALL)
    fragment = match.group(1) if match else ""
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = unescape(re.sub(r"\s+", " ", text)).strip()
    if text.lower().startswith("readme unavailable"):
        return ""
    return text[:1500]


def _operation_status(source_status: str | None, release_status: str | None) -> str | None:
    statuses = [status for status in (source_status, release_status) if status]
    if not statuses:
        return None
    if "ARCHIVING" in statuses:
        return "ARCHIVING"
    if "PARTIAL" in statuses:
        return "PARTIAL"
    if "FAILED" in statuses and "COMPLETED" in statuses:
        return "PARTIAL"
    if "CANCELLED" in statuses and "COMPLETED" in statuses:
        return "PARTIAL"
    if "FAILED" in statuses:
        return "FAILED"
    if "CANCELLED" in statuses:
        return "CANCELLED"
    if all(status == "COMPLETED" for status in statuses):
        return "COMPLETED"
    return statuses[-1]


def _operation_label(operation: str | None) -> str:
    labels = {
        "COMPLETED": "最近一次更新：完成",
        "PARTIAL": "最近一次更新：部分完成",
        "FAILED": "最近一次更新：失败",
        "CANCELLED": "最近一次更新：已取消",
        "ARCHIVING": "最近一次更新：进行中",
    }
    if operation is None:
        return "最近一次更新：尚未开始"
    return labels.get(operation, "最近一次更新：尚未开始")


def _state(operation: str | None, current_release_id: object, current_snapshot_id: object) -> str:
    if operation == "ARCHIVING":
        return "更新中"
    if current_release_id is not None or current_snapshot_id is not None:
        return "已完成"
    if operation == "PARTIAL":
        return "部分完成"
    if operation == "CANCELLED":
        return "已取消"
    if operation == "FAILED":
        return "失败"
    return "未保存"


def _settings_root(connection) -> Path:
    row = connection.execute("SELECT value FROM settings WHERE key = 'archive_root'").fetchone()
    if row is None or not str(row["value"]).strip():
        return Path(".")
    return Path(row["value"])


def _category_name(name: str) -> str:
    if not isinstance(name, str):
        raise DatabaseError("Repository category name is empty")
    cleaned = name.strip()
    if cleaned == "":
        raise DatabaseError("Repository category name is empty")
    return cleaned


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
