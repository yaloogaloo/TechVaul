"""P2.2: MHTML validation, attach, old-snapshot protection, version mapping."""

from __future__ import annotations

from pathlib import Path

from techvault.app.bootstrap import prepare_context
from techvault.web_archive.mhtml import (
    SNAPSHOT_COMPLETE,
    SNAPSHOT_FAILED,
    attach_mhtml_snapshot,
    mark_snapshot_failed,
    validate_mhtml_file,
)
from techvault.web_archive.models import ArchiveStatus
from techvault.web_archive.service import WebArchiveService

_MINIMAL_MHTML = b"""\
From: <Saved by Blink>
Snapshot-Content-Location: https://example.com/a
Subject: Example
Date: Thu, 15 Oct 2026 00:00:00 +0000
MIME-Version: 1.0
Content-Type: multipart/related;
	type="text/html";
	boundary="----MultipartBoundary--abc123----"

------MultipartBoundary--abc123----
Content-Type: text/html
Content-Location: https://example.com/a

<!DOCTYPE html><html><body><h1>Example Title</h1><p>Body text for offline snapshot validation.</p></body></html>
------MultipartBoundary--abc123------
"""


def test_validate_mhtml_rejects_empty(tmp_path: Path) -> None:
    empty = tmp_path / "empty.mhtml"
    empty.write_bytes(b"")
    out = validate_mhtml_file(empty)
    assert not out.ok


def test_validate_mhtml_accepts_multipart(tmp_path: Path) -> None:
    path = tmp_path / "ok.mhtml"
    path.write_bytes(_MINIMAL_MHTML)
    out = validate_mhtml_file(path)
    assert out.ok
    assert out.size == len(_MINIMAL_MHTML)
    assert out.sha256


def test_attach_mhtml_updates_sqlite_and_file(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    url = "https://www.zhihu.com/question/11/answer/22"
    body = "<p>" + ("正文内容足够长。" * 20) + "</p>"
    result = service.archive_from_browser_dom(
        url=url,
        page_html=f"<html><body>{body}</body></html>",
        final_url=url,
        title="快照测试",
        author="作者",
        body_html=body,
    )
    assert result.status == ArchiveStatus.COMPLETE
    assert result.article_id is not None

    staging = tmp_path / "stage" / "snapshot.mhtml"
    staging.parent.mkdir(parents=True)
    staging.write_bytes(_MINIMAL_MHTML)

    attached = attach_mhtml_snapshot(
        context.connection,
        article_id=int(result.article_id),
        staging_path=staging,
    )
    assert attached.ok
    assert attached.status == SNAPSHOT_COMPLETE
    detail = service.get_article(int(result.article_id))
    assert detail is not None
    assert detail.snapshot_status == SNAPSHOT_COMPLETE
    assert detail.snapshot_path
    assert Path(detail.snapshot_path).is_file()
    assert Path(detail.snapshot_path).stat().st_size == len(_MINIMAL_MHTML)
    context.close()


def test_failed_attach_preserves_previous_snapshot(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    url = "https://www.zhihu.com/question/33/answer/44"
    body = "<p>" + ("保留旧快照。" * 20) + "</p>"
    result = service.archive_from_browser_dom(
        url=url,
        page_html=f"<html><body>{body}</body></html>",
        final_url=url,
        title="旧快照保护",
        author="作者",
        body_html=body,
    )
    article_id = int(result.article_id)
    staging = tmp_path / "good.mhtml"
    staging.write_bytes(_MINIMAL_MHTML)
    assert attach_mhtml_snapshot(context.connection, article_id=article_id, staging_path=staging).ok

    detail1 = service.get_article(article_id)
    assert detail1 is not None
    good_path = Path(detail1.snapshot_path)
    good_bytes = good_path.read_bytes()

    bad = tmp_path / "bad.mhtml"
    bad.write_bytes(b"not-mhtml")
    failed = attach_mhtml_snapshot(context.connection, article_id=article_id, staging_path=bad)
    assert not failed.ok
    assert failed.status == SNAPSHOT_FAILED

    detail2 = service.get_article(article_id)
    assert detail2 is not None
    # Previous successful snapshot file and pointer remain.
    assert detail2.snapshot_status == SNAPSHOT_COMPLETE
    assert Path(detail2.snapshot_path).read_bytes() == good_bytes
    assert detail2.snapshot_failure  # failure noted without wiping pointer
    context.close()


def test_mark_snapshot_failed_on_fresh_article(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    url = "https://www.zhihu.com/question/55/answer/66"
    body = "<p>" + ("无快照文章。" * 20) + "</p>"
    result = service.archive_from_browser_dom(
        url=url,
        page_html=f"<html><body>{body}</body></html>",
        final_url=url,
        title="无快照",
        author="作者",
        body_html=body,
    )
    mark_snapshot_failed(context.connection, int(result.article_id), "网页保存中断：disk full")
    detail = service.get_article(int(result.article_id))
    assert detail is not None
    assert detail.snapshot_status == SNAPSHOT_FAILED
    assert "中断" in detail.snapshot_failure
    context.close()


def test_list_captures_includes_snapshot_fields(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    url = "https://www.zhihu.com/question/77/answer/88"
    body = "<p>" + ("版本列表。" * 20) + "</p>"
    result = service.archive_from_browser_dom(
        url=url,
        page_html=f"<html><body>{body}</body></html>",
        final_url=url,
        title="版本",
        author="作者",
        body_html=body,
    )
    staging = tmp_path / "s.mhtml"
    staging.write_bytes(_MINIMAL_MHTML)
    attach_mhtml_snapshot(context.connection, article_id=int(result.article_id), staging_path=staging)
    caps = service.list_captures(int(result.article_id))
    assert len(caps) >= 1
    assert caps[0].snapshot_status == SNAPSHOT_COMPLETE
    assert caps[0].has_snapshot_file is True
    path = service.resolve_snapshot_path(int(result.article_id), caps[0].id)
    assert path is not None and path.is_file()
    context.close()
