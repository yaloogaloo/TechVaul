"""GitHub library bridge — thin adapter over LibraryService / DesktopServices."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtGui import QDesktopServices

from techvault.app.assets import load_asset_records
from techvault.app.library import LibraryService
from techvault.errors import DatabaseError
from techvault.logging_setup import get_logger
from techvault.readme_images.paths import resolve_readme_image_root
from techvault.readme_images.service import present_github_readme
from techvault.ui.bridge._json import err, ok
from techvault.ui.content_render import compose_readme_html

logger = get_logger()


class GitHubBridge(QObject):
    def __init__(
        self,
        connection: sqlite3.Connection,
        database_path: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._connection = connection
        self._database_path = Path(database_path)
        self._library = LibraryService(connection)

    @Slot(result=str)
    def listCategories(self) -> str:  # noqa: N802
        items = [
            {"id": c.id, "name": c.name, "sortOrder": c.sort_order}
            for c in self._library.list_categories()
        ]
        return ok(items)

    @Slot(str, str, str, result=str)
    def listRepositories(self, query: str, category: str, sort: str) -> str:  # noqa: N802
        category_id = None
        uncategorized = False
        if category == "none":
            uncategorized = True
        elif category.strip():
            try:
                category_id = int(category)
            except ValueError:
                return err("INVALID_CATEGORY", "分类无效")
        rows = self._library.list_repositories(
            query=query or "",
            category_id=category_id,
            uncategorized=uncategorized,
            sort=sort or "recent",
        )
        payload = []
        for item in rows:
            local = _local_status(item.state, item.snapshot_ref, item.release_tag)
            update = _update_status(item.operation)
            payload.append(
                {
                    "id": item.id,
                    "owner": item.owner,
                    "name": item.name,
                    "fullName": item.full_name,
                    "description": item.description,
                    "htmlUrl": item.html_url,
                    "categoryId": item.category_id,
                    "categoryName": item.category_name,
                    "topics": [part.strip() for part in item.topics.split("·") if part.strip()]
                    if item.topics
                    else [],
                    "releaseTag": item.release_tag,
                    "snapshotRef": item.snapshot_ref,
                    "localStatus": local,
                    "updateStatus": update,
                    "operation": item.operation,
                    "operationLabel": item.operation_label,
                    "state": item.state,
                }
            )
        archived, matched = self._library.count_archived_repositories(
            query=query or "",
            category_id=category_id,
            uncategorized=uncategorized,
        )
        return ok(
            {
                "items": payload,
                "hasRepositories": self._library.has_repositories(),
                "archivedCount": archived,
                "matchedArchivedCount": matched,
            }
        )

    @Slot(int, result=str)
    def getRepository(self, repository_id: int) -> str:  # noqa: N802
        detail = self._library.get_repository(int(repository_id))
        if detail is None:
            return err("NOT_FOUND", "项目不存在")
        assets = [
            {
                "kind": a.kind,
                "status": a.status,
                "message": a.message,
                "retryable": a.retryable,
            }
            for a in load_asset_records(self._connection, detail.id)
        ]
        kind, body, base = self._library.readme_document(detail.id)
        # Prefer full offline HTML when markdown path returned empty and page exists.
        if kind == "empty" and detail.offline_absolute and Path(detail.offline_absolute).is_file():
            try:
                kind, body, base = (
                    "html",
                    Path(detail.offline_absolute).read_text(encoding="utf-8"),
                    str(Path(detail.offline_absolute).parent),
                )
            except OSError:
                pass
        readme_html, readme_images = _present_readme(
            kind,
            body,
            base=base,
            owner=detail.owner,
            repo=detail.name,
            branch=detail.default_branch,
            image_root=resolve_readme_image_root(self._database_path),
        )
        snapshot = None
        if detail.snapshot is not None:
            snapshot = {
                "ref": detail.snapshot.ref,
                "commitSha": detail.snapshot.commit_sha,
                "archivedAt": detail.snapshot.archived_at,
                "size": detail.snapshot.size,
                "localSha256": detail.snapshot.local_sha256,
                "state": detail.snapshot.state,
            }
        files = [
            {
                "name": f.name,
                "state": f.state,
                "localSha256": f.local_sha256,
                "digestStatus": f.digest_status,
            }
            for f in detail.release_files
        ]
        return ok(
            {
                "id": detail.id,
                "owner": detail.owner,
                "name": detail.name,
                "fullName": f"{detail.owner}/{detail.name}",
                "description": detail.description,
                "htmlUrl": detail.html_url,
                "defaultBranch": detail.default_branch,
                "topics": list(detail.topics),
                "categoryId": detail.category_id,
                "categoryName": detail.category_name,
                "localStatus": _local_status(
                    detail.state,
                    detail.snapshot.ref if detail.snapshot else "",
                    detail.release_tag,
                ),
                "updateStatus": _update_status(detail.last_job_status),
                "operationLabel": detail.operation_label,
                "lastJobStatus": detail.last_job_status,
                "lastJobMessage": detail.last_job_message,
                "releaseTag": detail.release_tag,
                "releaseName": detail.release_name,
                "releasePublishedAt": detail.release_published_at,
                "releaseFiles": files,
                "snapshot": snapshot,
                "offlinePage": detail.offline_page,
                "offlineBuiltAt": detail.offline_built_at,
                "offlineAbsolute": detail.offline_absolute,
                "archiveFolder": detail.archive_folder,
                "assets": assets,
                "readmeHtml": readme_html,
                "readmeAvailable": bool(readme_html.strip()),
                "readmeImages": readme_images,
            }
        )

    @Slot(str, result=str)
    def createCategory(self, name: str) -> str:  # noqa: N802
        try:
            created = self._library.create_category(name)
        except DatabaseError as exc:
            return err("CATEGORY_ERROR", str(exc) or "无法创建分类")
        return ok({"id": created.id, "name": created.name, "sortOrder": created.sort_order})

    @Slot(int, str, result=str)
    def renameCategory(self, category_id: int, name: str) -> str:  # noqa: N802
        try:
            self._library.rename_category(int(category_id), name)
        except DatabaseError as exc:
            return err("CATEGORY_ERROR", str(exc) or "无法重命名分类")
        return ok({"id": int(category_id), "name": name.strip()})

    @Slot(int, result=str)
    def deleteCategory(self, category_id: int) -> str:  # noqa: N802
        try:
            self._library.delete_category(int(category_id))
        except DatabaseError as exc:
            return err("CATEGORY_ERROR", str(exc) or "无法删除分类")
        return ok({"id": int(category_id)})

    @Slot(int, str, result=str)
    def setRepositoryCategory(self, repository_id: int, category: str) -> str:  # noqa: N802
        if category in {"", "null", "none"}:
            value = None
        else:
            try:
                value = int(category)
            except (TypeError, ValueError):
                return err("INVALID_CATEGORY", "分类无效")
        try:
            self._library.set_repository_category(int(repository_id), value)
        except DatabaseError as exc:
            return err("CATEGORY_ERROR", _category_error(str(exc)))
        detail = self._library.get_repository(int(repository_id))
        return ok(
            {
                "repositoryId": int(repository_id),
                "categoryId": None if detail is None else detail.category_id,
                "categoryName": "" if detail is None else detail.category_name,
            }
        )

    @Slot(int, result=str)
    def openGitHub(self, repository_id: int) -> str:  # noqa: N802
        detail = self._library.get_repository(int(repository_id))
        if detail is None or not detail.html_url:
            return err("NOT_FOUND", "无法打开 GitHub")
        QDesktopServices.openUrl(QUrl(detail.html_url))
        return ok({"url": detail.html_url})

    @Slot(int, result=str)
    def openLocalFolder(self, repository_id: int) -> str:  # noqa: N802
        detail = self._library.get_repository(int(repository_id))
        if detail is None or not detail.archive_folder or not Path(detail.archive_folder).is_dir():
            return err("NOT_FOUND", "本地目录不可用")
        QDesktopServices.openUrl(QUrl.fromLocalFile(detail.archive_folder))
        return ok({"path": detail.archive_folder})

    @Slot(int, result=str)
    def openOfflinePage(self, repository_id: int) -> str:  # noqa: N802
        detail = self._library.get_repository(int(repository_id))
        if detail is None or not detail.offline_absolute or not Path(detail.offline_absolute).is_file():
            return err("NOT_FOUND", "离线页面不可用")
        QDesktopServices.openUrl(QUrl.fromLocalFile(detail.offline_absolute))
        return ok({"path": detail.offline_absolute})


def _present_readme(
    kind: str,
    body: str,
    *,
    base: str,
    owner: str,
    repo: str,
    branch: str,
    image_root,
) -> tuple[str, dict[str, int]]:
    empty = {"total": 0, "cached": 0, "failed": 0, "pending": 0}
    try:
        if kind in {"markdown", "html"} and body.strip():
            return present_github_readme(
                kind,
                body,
                owner=owner,
                repo=repo,
                branch=branch,
                image_root=image_root,
            )
    except Exception:
        logger.exception("README image presentation failed")
    html = compose_readme_html(kind, body, base=Path(base) if base else None)
    return html, empty


def _category_error(message: str) -> str:
    if "category does not exist" in message:
        return "分类不存在"
    if "Repository does not exist" in message:
        return "项目不存在"
    if "already exists" in message:
        return "分类已存在"
    return "无法保存分类"


def _local_status(state: str, snapshot_ref: str, release_tag: str) -> str:
    if state == "部分完成" or (snapshot_ref and not release_tag and state != "已完成"):
        if snapshot_ref or release_tag:
            return "partial"
    if snapshot_ref or release_tag or state == "已完成":
        return "archived"
    if state in {"部分完成"}:
        return "partial"
    return "none"


def _update_status(operation: str) -> str:
    mapping = {
        "ARCHIVING": "running",
        "COMPLETED": "success",
        "FAILED": "failed",
        "PARTIAL": "failed",
        "CANCELLED": "failed",
        "": "never",
    }
    return mapping.get(operation or "", "never")
