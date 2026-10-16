"""P2: login false-positive fixes, access-restricted taxonomy, browser DOM publish."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from techvault.app.bootstrap import prepare_context
from techvault.web_archive.failure_codes import FailureCode
from techvault.web_archive.fetch import fetch_html
from techvault.web_archive.models import ArchiveStatus
from techvault.web_archive.service import WebArchiveService

FIXTURES = Path(__file__).parent / "fixtures" / "web_archive"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_http_403_zse_is_access_restricted_not_login() -> None:
    url = "https://www.zhihu.com/question/1/answer/2"
    with respx.mock:
        respx.get(url).mock(
            return_value=httpx.Response(
                403,
                text=_html("zhihu_zse_challenge.html"),
                headers={"content-type": "text/html"},
            )
        )
        out = fetch_html(url)
    assert not out.ok
    assert out.failure_code == FailureCode.ACCESS_RESTRICTED
    assert out.failure_code != FailureCode.NEEDS_LOGIN
    assert out.diagnostics is not None
    assert out.diagnostics["has_zse_challenge"] is True


def test_archive_403_suggests_browser(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    url = "https://www.zhihu.com/question/1001/answer/2002"
    try:
        with respx.mock:
            respx.get(url).mock(
                return_value=httpx.Response(403, text=_html("zhihu_zse_challenge.html"))
            )
            result = service.archive(url)
        assert result.status == ArchiveStatus.FAILED
        assert result.failure_code == FailureCode.ACCESS_RESTRICTED
        assert result.suggest_browser is True
        assert "登录" not in (result.message or "") or "浏览器" in (result.message or "")
        assert "限制" in (result.message or "") or "浏览器" in (result.message or "")
    finally:
        context.close()


def test_browser_dom_publish_does_not_http_refetch(tmp_path: Path) -> None:
    """Saving from rendered DOM must not call Zhihu over HTTP again."""
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    url = "https://www.zhihu.com/question/1001/answer/2002"
    page = _html("zhihu_soft_login_with_body.html")
    body = (
        "<p>先建立最小可运行项目，把知识点落到可验证的小练习上，再逐步扩展复杂度。</p>"
        "<pre><code>print('hello')</code></pre>"
    )
    try:
        with respx.mock:
            # Any accidental HTTP to Zhihu should fail the test via assertion below.
            route = respx.route(host="www.zhihu.com").mock(return_value=httpx.Response(403))
            result = service.archive_from_browser_dom(
                url=url,
                page_html=page,
                final_url=url,
                title="如何高效学习编程？",
                author="测试答主",
                body_html=body,
            )
            assert route.call_count == 0
        assert result.status in {ArchiveStatus.COMPLETE, ArchiveStatus.PARTIAL}
        assert result.capture_mode == "BROWSER"
        assert result.article_id is not None
        detail = service.get_article(result.article_id)
        assert detail is not None
        assert detail.capture_mode == "BROWSER"
        assert "print" in detail.body_html or "hello" in detail.body_html
        assert Path(detail.archive_folder).joinpath("article.html").is_file()
    finally:
        context.close()


def test_soft_login_http_archive_succeeds(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    url = "https://www.zhihu.com/question/1001/answer/2002"
    try:
        with respx.mock:
            respx.get(url).mock(return_value=httpx.Response(200, text=_html("zhihu_soft_login_with_body.html")))
            result = service.archive(url)
        assert result.status in {ArchiveStatus.COMPLETE, ArchiveStatus.PARTIAL}
        assert result.failure_code is None
    finally:
        context.close()


def test_extraction_failed_distinct_from_login(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    url = "https://www.zhihu.com/question/1001/answer/2002"
    emptyish = "<html><head><title>知乎</title></head><body><div>推荐阅读</div></body></html>"
    try:
        with respx.mock:
            respx.get(url).mock(return_value=httpx.Response(200, text=emptyish))
            result = service.archive(url)
        assert result.failure_code == FailureCode.EXTRACTION_FAILED
        assert result.failure_code != FailureCode.NEEDS_LOGIN
        assert result.suggest_browser is True
    finally:
        context.close()
