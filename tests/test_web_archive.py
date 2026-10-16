"""Web Archive P1 — URL, Zhihu extract, capture, offline read, isolation."""

from __future__ import annotations

from pathlib import Path
from threading import Event

import httpx
import pytest
import respx

from techvault.app.bootstrap import prepare_context
from techvault.web_archive.adapters.zhihu import ZhihuAdapter
from techvault.web_archive.extract import extract_article
from techvault.web_archive.models import ArchiveStatus
from techvault.web_archive.paths import WebPathLayout
from techvault.web_archive.service import WebArchiveService
from techvault.web_archive.urls import parse_web_url

FIXTURES = Path(__file__).parent / "fixtures" / "web_archive"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_url_recognition() -> None:
    answer = parse_web_url("https://www.zhihu.com/question/1001/answer/2002")
    assert answer.kind == "answer"
    assert answer.canonical.endswith("/question/1001/answer/2002")

    article = parse_web_url("https://zhuanlan.zhihu.com/p/555")
    assert article.kind == "article"

    question = parse_web_url("https://www.zhihu.com/question/1001")
    assert question.kind == "question"
    assert "具体回答" in question.message

    bad = parse_web_url("not a url")
    assert bad.kind in {"unsupported", "generic"}


def test_zhihu_answer_and_article_extract() -> None:
    adapter = ZhihuAdapter()
    answer_url = parse_web_url("https://www.zhihu.com/question/1001/answer/2002")
    answer = adapter.extract(answer_url, _html("zhihu_answer.html"), answer_url.canonical)
    assert answer.ok
    assert "高效学习" in answer.title
    assert answer.author == "测试答主"
    assert "print('hello')" in answer.body_html
    assert "formula" in answer.body_html

    article_url = parse_web_url("https://zhuanlan.zhihu.com/p/555")
    article = adapter.extract(article_url, _html("zhihu_article.html"), article_url.canonical)
    assert article.ok
    assert article.title == "知乎专栏长文示例"
    assert "中文长文章" in article.body_html


def test_login_wall_not_success() -> None:
    adapter = ZhihuAdapter()
    parsed = parse_web_url("https://www.zhihu.com/question/1/answer/2")
    result = adapter.extract(parsed, _html("zhihu_login.html"), parsed.canonical)
    assert not result.ok
    assert result.failure_code == "NEEDS_LOGIN"


def test_soft_login_ui_with_body_is_not_needs_login() -> None:
    """Login buttons on anonymous shells must not block extraction when body exists."""
    adapter = ZhihuAdapter()
    parsed = parse_web_url("https://www.zhihu.com/question/1001/answer/2002")
    result = adapter.extract(parsed, _html("zhihu_soft_login_with_body.html"), parsed.canonical)
    assert result.ok
    assert result.failure_code is None
    assert "高效学习" in result.title
    assert "print('hello')" in result.body_html


def test_generic_readability() -> None:
    parsed = parse_web_url("https://example.com/post/1")
    extracted = extract_article(parsed, _html("generic_article.html"), parsed.canonical)
    assert not isinstance(extracted, type(None))
    from techvault.web_archive.models import ExtractedArticle

    assert isinstance(extracted, ExtractedArticle)
    assert "Hello world" in extracted.plain_text


def test_path_layout_sanitizes(tmp_path: Path) -> None:
    layout = WebPathLayout(tmp_path)
    path = layout.article_dir("zhuanlan.zhihu.com", '标题<>:"/\\?*', "abc123")
    assert path.is_relative_to(tmp_path / "web")
    assert ":" not in path.name
    assert path.name.endswith("-abc123")


def test_archive_pipeline_offline_and_duplicate(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    context = prepare_context(tmp_path / "db.sqlite", archive)
    service = WebArchiveService(context.connection)
    answer_url = "https://www.zhihu.com/question/1001/answer/2002"
    image = b"\x89PNG\r\n\x1a\n" + b"fake"
    try:
        with respx.mock:
            respx.get(answer_url).mock(return_value=httpx.Response(200, text=_html("zhihu_answer.html")))
            respx.get("https://pic1.zhimg.com/v2-demo.png").mock(
                return_value=httpx.Response(200, content=image, headers={"Content-Type": "image/png"})
            )
            first = service.archive(answer_url)
        assert first.status in {ArchiveStatus.COMPLETE, ArchiveStatus.PARTIAL}
        assert first.article_id is not None
        folder = Path(first.archive_folder or "")
        assert folder.is_dir()
        assert (folder / "article.html").is_file()
        assert (folder / "article.md").is_file()
        assert (folder / "metadata.json").is_file()
        html = (folder / "article.html").read_text(encoding="utf-8")
        assert "media/" in html or "print" in html
        assert "zhimg.com" not in html or "media/" in html

        detail = service.get_article(first.article_id)
        assert detail is not None
        assert "高效学习" in detail.title
        assert detail.body_html
        # Offline: body is local; file URI rewrite for media when present
        assert "需要登录" not in detail.body_html

        listed = service.list_articles()
        assert len(listed) == 1
        searched = service.search_articles("编程")
        assert len(searched) == 1

        with respx.mock:
            respx.get(answer_url).mock(return_value=httpx.Response(200, text=_html("zhihu_answer.html")))
            respx.get("https://pic1.zhimg.com/v2-demo.png").mock(
                return_value=httpx.Response(200, content=image)
            )
            second = service.archive(answer_url)
        assert second.article_id == first.article_id
        versions = list((folder / "versions").glob("*")) if (folder / "versions").exists() else []
        # Second archive may stash previous version
        assert context.connection.execute(
            "SELECT COUNT(*) FROM web_captures WHERE article_id = ?",
            (first.article_id,),
        ).fetchone()[0] == 2

        # Failed re-archive keeps old success
        with respx.mock:
            respx.get(answer_url).mock(return_value=httpx.Response(500))
            failed = service.archive(answer_url)
        assert failed.status == ArchiveStatus.FAILED
        still = service.get_article(first.article_id)
        assert still is not None
        assert still.status in {ArchiveStatus.COMPLETE, ArchiveStatus.PARTIAL}
        assert (folder / "article.html").is_file()

        # Cancel mid-flight
        cancel = Event()
        cancel.set()
        with respx.mock:
            respx.get(answer_url).mock(return_value=httpx.Response(200, text=_html("zhihu_answer.html")))
            cancelled = service.archive(answer_url, cancel_event=cancel)
        assert cancelled.status == ArchiveStatus.CANCELLED

        assert service.delete_article(first.article_id)
        assert service.get_article(first.article_id) is None
        assert not folder.exists()
        # GitHub tables untouched
        assert context.connection.execute("SELECT COUNT(*) FROM repositories").fetchone()[0] == 0
    finally:
        context.close()


def test_image_download_failure_marks_partial(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    context = prepare_context(tmp_path / "db.sqlite", archive)
    service = WebArchiveService(context.connection)
    url = "https://zhuanlan.zhihu.com/p/555"
    try:
        with respx.mock:
            respx.get(url).mock(return_value=httpx.Response(200, text=_html("zhihu_article.html")))
            respx.get("https://picx.zhimg.com/v2-cover.jpg").mock(return_value=httpx.Response(404))
            result = service.archive(url)
        assert result.status == ArchiveStatus.PARTIAL
        detail = service.get_article(result.article_id or 0)
        assert detail is not None
        assert detail.media_missing >= 1
        assert "中文长文章" in detail.body_html
    finally:
        context.close()


def test_dead_link(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    try:
        with respx.mock:
            respx.get("https://www.zhihu.com/question/1/answer/2").mock(return_value=httpx.Response(404))
            result = service.archive("https://www.zhihu.com/question/1/answer/2")
        assert result.status == ArchiveStatus.FAILED
        assert result.failure_code == "NOT_FOUND"
    finally:
        context.close()


def test_question_hub_not_archived(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    try:
        result = service.archive("https://www.zhihu.com/question/1001")
        assert result.status in {ArchiveStatus.LINK_ONLY, ArchiveStatus.FAILED}
        assert context.connection.execute("SELECT COUNT(*) FROM web_articles").fetchone()[0] == 0
    finally:
        context.close()


def test_migration_includes_web_archive(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    try:
        versions = [
            row[0]
            for row in context.connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        assert 8 in versions
        assert 9 in versions
        cols = {
            row[1]
            for row in context.connection.execute("PRAGMA table_info(web_articles)")
        }
        assert "canonical_url" in cols
        assert "plain_text" in cols
        assert "snapshot_status" in cols
        assert "relative_snapshot" in cols
    finally:
        context.close()
