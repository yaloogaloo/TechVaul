"""Cache and display GitHub README images without rewriting the saved Markdown."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from threading import Event

import httpx

from techvault.app.library import LibraryService
from techvault.errors import DatabaseError
from techvault.logging_setup import get_logger
from techvault.offline.sanitize import sanitize_html
from techvault.readme_images.html_images import absolutize_images, render_markdown_body
from techvault.readme_images.paths import image_root_for_connection
from techvault.readme_images.pool import FRESH_SECONDS, ImagePool, ImageRecord

logger = get_logger()


@dataclass(frozen=True)
class ImageSyncReport:
    total: int
    cached: int
    failed: int
    reused: int
    pending: int
    cancelled: bool
    message: str

    def as_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "cached": self.cached,
            "failed": self.failed,
            "pending": self.pending,
        }


def cache_repository_readme(
    connection: sqlite3.Connection,
    repository_id: int,
    *,
    image_root=None,
    revalidate: bool = True,
    fresh_seconds: int = FRESH_SECONDS,
    cancel_event: Event | None = None,
    http_client: httpx.Client | None = None,
    on_progress=None,
) -> ImageSyncReport:
    """Download missing README images for one saved repository.

    Does not download source snapshots or releases, and does not change category.
    """
    library = LibraryService(connection)
    detail = library.get_repository(repository_id)
    if detail is None:
        raise DatabaseError("Repository does not exist")
    kind, body, _base = library.readme_document(repository_id)
    root = image_root if image_root is not None else image_root_for_connection(connection)
    if kind not in {"markdown", "html"} or not body.strip():
        _clear_refs_if_present(root, repository_id, detail.github_repo_id)
        return _report([])
    branch = detail.default_branch or "HEAD"
    html = render_markdown_body(body) if kind == "markdown" else body
    _prepared, outcomes = absolutize_images(
        html,
        owner=detail.owner,
        repo=detail.name,
        branch=branch,
        allow_relative=kind == "markdown",
    )
    urls = [url for state, url in outcomes if state == "url" and url]
    blocked = sum(1 for state, _url in outcomes if state == "blocked")
    if cancel_event is not None and cancel_event.is_set():
        return ImageSyncReport(len(outcomes), 0, blocked, 0, 0, True, "已取消 README 图片补全")
    owns_client = http_client is None
    client = http_client or httpx.Client(
        timeout=httpx.Timeout(20.0, connect=8.0),
        follow_redirects=False,
        trust_env=False,
    )
    pool = ImagePool(root)
    try:
        records = pool.sync(
            urls,
            client=client,
            revalidate=revalidate,
            fresh_seconds=fresh_seconds,
            cancel_event=cancel_event,
            on_progress=on_progress,
        )
        pool.replace_refs(repository_id, detail.github_repo_id, records)
        report = _report(records, extra_failed=blocked, cancelled=bool(cancel_event and cancel_event.is_set()))
        logger.info(
            "README image cache repository=%s total=%s cached=%s failed=%s reused=%s",
            repository_id,
            report.total,
            report.cached,
            report.failed,
            report.reused,
        )
        return report
    finally:
        pool.close()
        if owns_client:
            client.close()


def present_github_readme(
    kind: str,
    body: str,
    *,
    owner: str,
    repo: str,
    branch: str,
    image_root,
    repository_id: int | None = None,
) -> tuple[str, dict[str, int]]:
    """Return sanitized HTML whose images point at the local pool when cached.

    This does not download. Original Markdown on disk is left unchanged.
    """
    del repository_id
    empty = {"total": 0, "cached": 0, "failed": 0, "pending": 0}
    if kind not in {"markdown", "html"} or not str(body or "").strip():
        return "", empty
    html = render_markdown_body(body) if kind == "markdown" else body
    prepared, outcomes = absolutize_images(
        html,
        owner=owner,
        repo=repo,
        branch=branch or "HEAD",
        allow_relative=kind == "markdown",
    )
    cleaned = sanitize_html(prepared)
    urls = [url for state, url in outcomes if state == "url" and url]
    blocked = sum(1 for state, _url in outcomes if state == "blocked")
    if not urls:
        return cleaned, {"total": blocked, "cached": 0, "failed": blocked, "pending": 0}
    pool = ImagePool(image_root)
    try:
        rewritten = pool.rewrite_html(cleaned)
        cached = failed = pending = 0
        for url in urls:
            state, _value = pool.cache_state(url)
            if state == "media":
                cached += 1
            elif state == "failed":
                failed += 1
            else:
                pending += 1
        summary = {
            "total": cached + failed + pending + blocked,
            "cached": cached,
            "failed": failed + blocked,
            "pending": pending,
        }
        return rewritten, summary
    finally:
        pool.close()


def _clear_refs_if_present(root, repository_id: int, github_repo_id: int) -> None:
    index = root / "cache_index.sqlite"
    if not index.is_file():
        return
    pool = ImagePool(root)
    try:
        pool.replace_refs(repository_id, github_repo_id, [])
    finally:
        pool.close()


def _report(records: list[ImageRecord], *, extra_failed: int = 0, cancelled: bool = False) -> ImageSyncReport:
    cached = sum(1 for item in records if item.status == "OK")
    failed = sum(1 for item in records if item.status != "OK") + extra_failed
    reused = sum(1 for item in records if item.reused)
    total = len(records) + extra_failed
    if cancelled and cached == 0 and failed == extra_failed:
        message = "已取消 README 图片补全"
    elif total == 0:
        message = "这篇 README 没有需要缓存的图片"
    elif failed == 0:
        message = f"README 图片 {cached}/{total} 已缓存"
    else:
        message = f"README 图片 {cached}/{total} 已缓存，{failed} 张失败"
    return ImageSyncReport(total, cached, failed, reused, 0, cancelled and cached == 0, message)
