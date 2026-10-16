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
from techvault.web_archive.adapters.base import AdapterResult
from techvault.web_archive.extract import extract_article
from techvault.web_archive.failure_codes import FailureCode, suggest_browser_capture, user_message_for
from techvault.web_archive.fetch import fetch_html
from techvault.web_archive.media import localize_article_images
from techvault.web_archive.models import ArchiveStatus, CaptureResult, ExtractedArticle
from techvault.web_archive.paths import WebPathLayout, stable_short_id
from techvault.web_archive.sanitize_article import html_to_markdown_rough, html_to_plain_text, sanitize_article_html
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
        code = FailureCode.UNSUPPORTED_PAGE if parsed.kind == "unsupported" else FailureCode.QUESTION_HUB
        return CaptureResult(
            ArchiveStatus.FAILED if parsed.kind == "unsupported" else ArchiveStatus.LINK_ONLY,
            parsed.message or user_message_for(code),
            failure_code=code,
            suggest_browser=False,
        )
    if not parsed.canonical:
        return CaptureResult(
            ArchiveStatus.FAILED,
            user_message_for(FailureCode.INVALID_URL, "无法规范化链接"),
            failure_code=FailureCode.INVALID_URL,
        )

    settings = SettingsService(connection)
    root = settings.get_archive_root()
    if root is None:
        return CaptureResult(
            ArchiveStatus.FAILED,
            "尚未配置归档目录",
            failure_code=FailureCode.NO_ARCHIVE_ROOT,
        )

    def report(phase: str, file: str = "", done: int = 0, total: int = 1) -> None:
        if on_progress is not None:
            on_progress(phase, file, done, total)

    if cancel_event is not None and cancel_event.is_set():
        return CaptureResult(ArchiveStatus.CANCELLED, "已取消", failure_code=FailureCode.CANCELLED)

    report("正在获取网页", parsed.canonical)
    fetched = fetch_html(parsed.canonical)
    if not fetched.ok:
        code = fetched.failure_code or FailureCode.HTTP_ERROR
        return CaptureResult(
            _status_for_code(code),
            fetched.failure_message or user_message_for(code),
            failure_code=code,
            suggest_browser=suggest_browser_capture(code),
            diagnostics=fetched.diagnostics,
            capture_mode="URL_AUTO",
        )

    if cancel_event is not None and cancel_event.is_set():
        return CaptureResult(ArchiveStatus.CANCELLED, "已取消", failure_code=FailureCode.CANCELLED)

    report("正在提取正文")
    extracted = extract_article(parsed, fetched.html, fetched.final_url)
    if isinstance(extracted, AdapterResult):
        code = extracted.failure_code or FailureCode.EXTRACTION_FAILED
        return CaptureResult(
            _status_for_code(code),
            extracted.failure_message or user_message_for(code),
            failure_code=code,
            title=extracted.title or None,
            suggest_browser=suggest_browser_capture(code),
            diagnostics=fetched.diagnostics,
            capture_mode="URL_AUTO",
        )
    assert isinstance(extracted, ExtractedArticle)
    return publish_extracted(
        connection,
        extracted,
        capture_mode="URL_AUTO",
        cancel_event=cancel_event,
        on_progress=on_progress,
        extra_meta={"http_diagnostics": fetched.diagnostics},
    )


def publish_from_rendered_html(
    connection: sqlite3.Connection,
    *,
    url: str,
    page_html: str,
    final_url: str | None = None,
    capture_mode: str = "BROWSER",
    cancel_event: Event | None = None,
    on_progress=None,
    preextracted: dict | None = None,
) -> CaptureResult:
    """Publish from already-rendered browser DOM. Must NOT re-fetch the article via httpx."""
    parsed = parse_web_url(url)
    if parsed.kind in {"unsupported", "question"} and not (preextracted and preextracted.get("body_html")):
        code = FailureCode.UNSUPPORTED_PAGE if parsed.kind == "unsupported" else FailureCode.QUESTION_HUB
        return CaptureResult(
            ArchiveStatus.FAILED if parsed.kind == "unsupported" else ArchiveStatus.LINK_ONLY,
            parsed.message or user_message_for(code),
            failure_code=code,
            capture_mode=capture_mode,
        )
    if not parsed.canonical and not final_url:
        return CaptureResult(
            ArchiveStatus.FAILED,
            "无法规范化链接",
            failure_code=FailureCode.INVALID_URL,
            capture_mode=capture_mode,
        )

    def report(phase: str, file: str = "", done: int = 0, total: int = 1) -> None:
        if on_progress is not None:
            on_progress(phase, file, done, total)

    report("正在从当前页面提取正文")
    extracted: ExtractedArticle | AdapterResult
    if preextracted and (preextracted.get("body_html") or "").strip():
        body = sanitize_article_html(preextracted["body_html"])
        plain = html_to_plain_text(body)
        if len(plain) < 20:
            extracted = AdapterResult(
                ok=False,
                failure_code=FailureCode.CONTENT_INCOMPLETE,
                failure_message="当前页面提取到的正文过短",
                extractor="browser-dom",
            )
        else:
            canon = parsed.canonical or final_url or url
            extracted = ExtractedArticle(
                title=(preextracted.get("title") or "未命名文章").strip() or "未命名文章",
                author=preextracted.get("author"),
                published_at=None,
                body_html=body,
                plain_text=plain,
                site_key=parsed.site_key or "generic",
                site_label=parsed.site_label or "网页",
                canonical_url=canon,
                source_url=url,
                extractor="browser-dom",
            )
    else:
        extracted = extract_article(parsed, page_html, final_url or parsed.canonical or url)

    if isinstance(extracted, AdapterResult):
        code = extracted.failure_code or FailureCode.EXTRACTION_FAILED
        return CaptureResult(
            _status_for_code(code),
            extracted.failure_message or user_message_for(code),
            failure_code=code,
            title=extracted.title or None,
            suggest_browser=False,
            capture_mode=capture_mode,
        )
    return publish_extracted(
        connection,
        extracted,
        capture_mode=capture_mode,
        cancel_event=cancel_event,
        on_progress=on_progress,
        extra_meta={"source": "browser_dom"},
    )


def publish_extracted(
    connection: sqlite3.Connection,
    extracted: ExtractedArticle,
    *,
    capture_mode: str = "URL_AUTO",
    cancel_event: Event | None = None,
    on_progress=None,
    extra_meta: dict | None = None,
) -> CaptureResult:
    settings = SettingsService(connection)
    root = settings.get_archive_root()
    if root is None:
        return CaptureResult(
            ArchiveStatus.FAILED,
            "尚未配置归档目录",
            failure_code=FailureCode.NO_ARCHIVE_ROOT,
            capture_mode=capture_mode,
        )
    layout = WebPathLayout(root)

    def report(phase: str, file: str = "", done: int = 0, total: int = 1) -> None:
        if on_progress is not None:
            on_progress(phase, file, done, total)

    domain = _domain(extracted.canonical_url)
    short_id = stable_short_id(extracted.canonical_url)
    article_root = layout.article_dir(domain, extracted.title, short_id)
    storage_rel = layout.article_relpath(domain, extracted.title, short_id)

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
        return CaptureResult(
            ArchiveStatus.CANCELLED,
            "已取消",
            failure_code=FailureCode.CANCELLED,
            capture_mode=capture_mode,
        )

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
        "capture_mode": capture_mode,
        "captured_at": _now(),
        "content_hash": content_hash,
        "media_total": media_total,
        "media_missing": media_missing,
        "warnings": extracted.warnings,
    }
    if extra_meta:
        meta.update(extra_meta)

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
            failure_code=FailureCode.IO_ERROR,
            title=extracted.title,
            capture_mode=capture_mode,
        )

    if not html_path.is_file() or html_path.stat().st_size < 20:
        return CaptureResult(
            ArchiveStatus.FAILED,
            "正文文件未正确生成",
            failure_code=FailureCode.INTEGRITY,
            capture_mode=capture_mode,
        )

    status = ArchiveStatus.COMPLETE if media_missing == 0 else ArchiveStatus.PARTIAL
    message = (
        "网页已归档"
        if status == ArchiveStatus.COMPLETE
        else f"正文已保存，但 {media_missing} 张图片未能下载"
    )
    captured_at = _now()
    article_id = _upsert_article(
        connection,
        existing=existing,
        extracted=extracted,
        status=status,
        capture_mode=capture_mode,
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
        capture_mode=capture_mode,
    )


def _upsert_article(
    connection: sqlite3.Connection,
    *,
    existing,
    extracted: ExtractedArticle,
    status: str,
    capture_mode: str,
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
    mode = capture_mode if capture_mode in {"URL_AUTO", "BROWSER", "IMPORT"} else "URL_AUTO"
    if existing is None:
        cursor = connection.execute(
            """
            INSERT INTO web_articles (
                canonical_url, source_url, site_key, site_label, title, author, published_at,
                captured_at, status, capture_mode, content_hash, storage_relpath, archive_root,
                relative_html, relative_md, relative_meta, plain_text, media_total, media_missing,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                mode,
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
                status = ?, capture_mode = ?, content_hash = ?, storage_relpath = ?, archive_root = ?,
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
                mode,
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
    for name in ("article.html", "article.md", "metadata.json", "snapshot.mhtml"):
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
    if code == FailureCode.NEEDS_LOGIN:
        return ArchiveStatus.NEEDS_LOGIN
    if code == FailureCode.QUESTION_HUB:
        return ArchiveStatus.LINK_ONLY
    if code == FailureCode.CANCELLED:
        return ArchiveStatus.CANCELLED
    return ArchiveStatus.FAILED


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
