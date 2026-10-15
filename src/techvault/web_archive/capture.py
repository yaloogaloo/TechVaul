"""Capture orchestration: fetch → extract → localize → publish."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from urllib.parse import urlparse

from techvault.app.settings import SettingsService
from techvault.web_archive.extract import extract_article
from techvault.web_archive.fetch import fetch_html
from techvault.web_archive.media import localize_article_images
from techvault.web_archive.models import ArchiveStatus, CaptureResult, ExtractedArticle
from techvault.web_archive.adapters.base import AdapterResult
from techvault.web_archive.paths import WebPathLayout, stable_short_id
from techvault.web_archive.sanitize_article import html_to_markdown_rough
from techvault.web_archive.urls import parse_web_url


def archive_url(
    connection: sqlite3.Connection,
    url: str,
    *,
    cancel_event: Event | None = None,
    on_progress=None,
) -> CaptureResult:
    parsed = parse_web_url(url)
    if parsed.kind in {"unsupported", "question"}:
        return CaptureResult(
            ArchiveStatus.FAILED if parsed.kind == "unsupported" else ArchiveStatus.LINK_ONLY,
            parsed.message or "无法归档该链接",
            failure_code="UNSUPPORTED" if parsed.kind == "unsupported" else "QUESTION_HUB",
        )
    if not parsed.canonical:
        return CaptureResult(ArchiveStatus.FAILED, "无法规范化链接", failure_code="INVALID_URL")

    settings = SettingsService(connection)
    root = settings.get_archive_root()
    if root is None:
        return CaptureResult(ArchiveStatus.FAILED, "尚未配置归档目录", failure_code="NO_ARCHIVE_ROOT")
    layout = WebPathLayout(root)

    def report(phase: str, file: str = "", done: int = 0, total: int = 1) -> None:
        if on_progress is not None:
            on_progress(phase, file, done, total)

    if cancel_event is not None and cancel_event.is_set():
        return CaptureResult(ArchiveStatus.CANCELLED, "已取消")

    report("正在获取网页", parsed.canonical)
    fetched = fetch_html(parsed.canonical)
    if not fetched.ok:
        status = (
            ArchiveStatus.NEEDS_LOGIN
            if fetched.failure_code == "NEEDS_LOGIN"
            else ArchiveStatus.FAILED
        )
        return CaptureResult(
            status,
            fetched.failure_message or "获取失败",
            failure_code=fetched.failure_code,
        )

    if cancel_event is not None and cancel_event.is_set():
        return CaptureResult(ArchiveStatus.CANCELLED, "已取消")

    report("正在提取正文")
    extracted = extract_article(parsed, fetched.html, fetched.final_url)
    if isinstance(extracted, AdapterResult):
        status = _status_for_code(extracted.failure_code)
        return CaptureResult(
            status,
            extracted.failure_message or "提取失败",
            failure_code=extracted.failure_code,
            title=extracted.title or None,
        )
    assert isinstance(extracted, ExtractedArticle)

    domain = _domain(extracted.canonical_url)
    short_id = stable_short_id(extracted.canonical_url)
    article_root = layout.article_dir(domain, extracted.title, short_id)
    storage_rel = layout.article_relpath(domain, extracted.title, short_id)

    # Preserve previous successful tree when re-archiving.
    existing = connection.execute(
        "SELECT * FROM web_articles WHERE canonical_url = ?",
        (extracted.canonical_url,),
    ).fetchone()
    if existing is not None and existing["storage_relpath"] and existing["status"] in {
        ArchiveStatus.COMPLETE,
        ArchiveStatus.PARTIAL,
    }:
        prev_root = Path(existing["archive_root"] or root) / existing["storage_relpath"]
        if prev_root.is_dir() and prev_root.resolve() != article_root.resolve():
            # Prefer existing bound folder for rename stability.
            article_root = prev_root
            storage_rel = existing["storage_relpath"].replace("\\", "/")
        elif prev_root.is_dir():
            _stash_version(layout, article_root)

    job_id = f"web-{short_id}-{_now_stamp()}"
    report("正在保存图片")
    body_html, media_total, media_missing = localize_article_images(
        extracted.body_html,
        layout,
        article_root,
        job_id=job_id,
        cancel_check=(lambda: cancel_event.is_set()) if cancel_event else None,
    )
    if cancel_event is not None and cancel_event.is_set():
        return CaptureResult(ArchiveStatus.CANCELLED, "已取消")

    wrapped = _wrap_document(extracted.title, extracted.author, body_html, extracted.canonical_url)
    content_hash = hashlib.sha256(wrapped.encode("utf-8")).hexdigest()
    md_text = html_to_markdown_rough(body_html, extracted.title)
    meta = {
        "canonical_url": extracted.canonical_url,
        "source_url": extracted.source_url,
        "title": extracted.title,
        "author": extracted.author,
        "published_at": extracted.published_at,
        "site_key": extracted.site_key,
        "site_label": extracted.site_label,
        "extractor": extracted.extractor,
        "captured_at": _now(),
        "content_hash": content_hash,
        "media_total": media_total,
        "media_missing": media_missing,
        "warnings": extracted.warnings,
    }

    report("正在写入文件")
    try:
        article_root.mkdir(parents=True, exist_ok=True)
        html_path = layout.article_html(article_root)
        md_path = layout.article_md(article_root)
        meta_path = layout.metadata_json(article_root)
        html_path.write_text(wrapped, encoding="utf-8", newline="\n")
        md_path.write_text(md_text, encoding="utf-8", newline="\n")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return CaptureResult(
            ArchiveStatus.FAILED,
            "无法写入归档文件",
            failure_code="IO_ERROR",
            title=extracted.title,
        )

    if not html_path.is_file() or html_path.stat().st_size < 20:
        return CaptureResult(ArchiveStatus.FAILED, "正文文件未正确生成", failure_code="INTEGRITY")

    status = ArchiveStatus.COMPLETE if media_missing == 0 else ArchiveStatus.PARTIAL
    message = "网页已归档" if status == ArchiveStatus.COMPLETE else "正文已保存，但部分图片缺失"
    captured_at = _now()
    article_id = _upsert_article(
        connection,
        existing=existing,
        extracted=extracted,
        status=status,
        storage_rel=storage_rel,
        archive_root=str(root),
        relative_html=f"{storage_rel}/article.html",
        relative_md=f"{storage_rel}/article.md",
        relative_meta=f"{storage_rel}/metadata.json",
        plain_text=extracted.plain_text,
        media_total=media_total,
        media_missing=media_missing,
        content_hash=content_hash,
        captured_at=captured_at,
    )
    report("完成", extracted.title, 1, 1)
    return CaptureResult(
        status,
        message,
        article_id=article_id,
        title=extracted.title,
        archive_folder=str(article_root),
    )


def _upsert_article(
    connection: sqlite3.Connection,
    *,
    existing,
    extracted: ExtractedArticle,
    status: str,
    storage_rel: str,
    archive_root: str,
    relative_html: str,
    relative_md: str,
    relative_meta: str,
    plain_text: str,
    media_total: int,
    media_missing: int,
    content_hash: str,
    captured_at: str,
) -> int:
    now = captured_at
    if existing is None:
        cursor = connection.execute(
            """
            INSERT INTO web_articles (
                canonical_url, source_url, site_key, site_label, title, author, published_at,
                captured_at, status, capture_mode, content_hash, storage_relpath, archive_root,
                relative_html, relative_md, relative_meta, plain_text, media_total, media_missing,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'URL_AUTO', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                extracted.canonical_url,
                extracted.source_url,
                extracted.site_key,
                extracted.site_label,
                extracted.title,
                extracted.author,
                extracted.published_at,
                captured_at,
                status,
                content_hash,
                storage_rel,
                archive_root,
                relative_html,
                relative_md,
                relative_meta,
                plain_text,
                media_total,
                media_missing,
                now,
                now,
            ),
        )
        article_id = int(cursor.lastrowid)
        version = 1
        _fts_insert(connection, article_id, extracted.title, extracted.author, plain_text)
    else:
        article_id = int(existing["id"])
        connection.execute(
            """
            UPDATE web_articles SET
                source_url = ?, title = ?, author = ?, published_at = ?, captured_at = ?,
                status = ?, content_hash = ?, storage_relpath = ?, archive_root = ?,
                relative_html = ?, relative_md = ?, relative_meta = ?, plain_text = ?,
                media_total = ?, media_missing = ?, failure_code = NULL, failure_message = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                extracted.source_url,
                extracted.title,
                extracted.author,
                extracted.published_at,
                captured_at,
                status,
                content_hash,
                storage_rel,
                archive_root,
                relative_html,
                relative_md,
                relative_meta,
                plain_text,
                media_total,
                media_missing,
                now,
                article_id,
            ),
        )
        _fts_update(
            connection,
            article_id,
            old_title=existing["title"] or "",
            old_author=existing["author"] or "",
            old_plain=existing["plain_text"] or "",
            title=extracted.title,
            author=extracted.author,
            plain_text=plain_text,
        )
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM web_captures WHERE article_id = ?",
            (article_id,),
        ).fetchone()
        version = int(row[0]) + 1
    connection.execute(
        """
        INSERT INTO web_captures (
            article_id, version, status, captured_at, content_hash,
            relative_html, relative_md, relative_meta, media_total, media_missing
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            article_id,
            version,
            status,
            captured_at,
            content_hash,
            relative_html,
            relative_md,
            relative_meta,
            media_total,
            media_missing,
        ),
    )
    connection.commit()
    return article_id


def _fts_insert(
    connection: sqlite3.Connection,
    article_id: int,
    title: str,
    author: str | None,
    plain_text: str,
) -> None:
    connection.execute(
        "INSERT INTO web_article_search(rowid, title, author, plain_text) VALUES (?, ?, ?, ?)",
        (article_id, title, author or "", plain_text or ""),
    )


def _fts_update(
    connection: sqlite3.Connection,
    article_id: int,
    *,
    old_title: str,
    old_author: str,
    old_plain: str,
    title: str,
    author: str | None,
    plain_text: str,
) -> None:
    connection.execute(
        "INSERT INTO web_article_search(web_article_search, rowid, title, author, plain_text) "
        "VALUES('delete', ?, ?, ?, ?)",
        (article_id, old_title, old_author, old_plain),
    )
    _fts_insert(connection, article_id, title, author, plain_text)


def _stash_version(layout: WebPathLayout, article_root: Path) -> None:
    html = layout.article_html(article_root)
    if not html.is_file():
        return
    stamp = _now_stamp()
    dest = layout.version_dir(article_root, stamp)
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("article.html", "article.md", "metadata.json"):
        src = article_root / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    media = article_root / "media"
    if media.is_dir():
        shutil.copytree(media, dest / "media", dirs_exist_ok=True)


def _wrap_document(title: str, author: str | None, body: str, url: str) -> str:
    byline = f'<p class="byline">{_escape(author)}</p>' if author else ""
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{_escape(title)}</title></head><body>"
        f"<header><h1>{_escape(title)}</h1>{byline}"
        f'<p class="source"><a href="{_escape(url)}">{_escape(url)}</a></p></header>'
        f'<article class="content">{body}</article></body></html>'
    )


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "web").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _status_for_code(code: str | None) -> str:
    if code == "NEEDS_LOGIN":
        return ArchiveStatus.NEEDS_LOGIN
    if code in {"QUESTION_HUB", "UNSUPPORTED"}:
        return ArchiveStatus.LINK_ONLY if code == "QUESTION_HUB" else ArchiveStatus.FAILED
    return ArchiveStatus.FAILED


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
