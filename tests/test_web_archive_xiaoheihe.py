"""Xiaoheihe / Heybox URL normalization and adapter extraction."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from techvault.app.bootstrap import prepare_context
from techvault.web_archive.adapters.xiaoheihe import XiaoheiheAdapter
from techvault.web_archive.failure_codes import FailureCode
from techvault.web_archive.models import ArchiveStatus
from techvault.web_archive.service import WebArchiveService
from techvault.web_archive.urls import parse_web_url

FIXTURES = Path(__file__).parent / "fixtures" / "web_archive"


def test_share_link_normalizes_to_bbs_link() -> None:
    share = "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=f1ba7299a477&h_src=track&h_session_id=secret"
    parsed = parse_web_url(share)
    assert parsed.site_key == "xiaoheihe"
    assert parsed.kind == "article"
    assert parsed.canonical == "https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477"
    assert "h_session_id" not in parsed.canonical
    assert "secret" not in parsed.canonical


def test_direct_bbs_link_and_dedupe() -> None:
    a = parse_web_url("https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477")
    b = parse_web_url(
        "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=f1ba7299a477&h_camp=1"
    )
    assert a.canonical == b.canonical


def test_short_a_link_unsupported_without_proof() -> None:
    parsed = parse_web_url("https://www.xiaoheihe.cn/a/f1ba7299a477")
    assert parsed.kind == "unsupported"


def test_adapter_spa_shell_incomplete() -> None:
    html = (FIXTURES / "xiaoheihe_spa_shell.html").read_text(encoding="utf-8")
    parsed = parse_web_url("https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477")
    out = XiaoheiheAdapter().extract(parsed, html, parsed.canonical)
    assert not out.ok
    assert out.failure_code == FailureCode.CONTENT_INCOMPLETE


def test_adapter_rendered_post() -> None:
    html = (FIXTURES / "xiaoheihe_post_rendered.html").read_text(encoding="utf-8")
    parsed = parse_web_url("https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477")
    out = XiaoheiheAdapter().extract(parsed, html, parsed.canonical)
    assert out.ok
    assert "免费的数据库" in out.title
    assert out.author == "小稿的AI日记"
    assert "Supabase" in out.body_html


def test_http_spa_suggests_browser(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    url = "https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477"
    shell = (FIXTURES / "xiaoheihe_spa_shell.html").read_text(encoding="utf-8")
    try:
        with respx.mock:
            respx.get(url).mock(return_value=httpx.Response(200, text=shell))
            result = WebArchiveService(context.connection).archive(url)
        assert result.status == ArchiveStatus.FAILED
        assert result.suggest_browser is True
        assert result.failure_code in {
            FailureCode.CONTENT_INCOMPLETE,
            FailureCode.EXTRACTION_FAILED,
            FailureCode.EMPTY,
        }
    finally:
        context.close()


def test_browser_dom_publish_xiaoheihe(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    url = "https://www.xiaoheihe.cn/app/bbs/link/f1ba7299a477"
    html = (FIXTURES / "xiaoheihe_post_rendered.html").read_text(encoding="utf-8")
    body = (
        "<p>如果盒友想要搞一个带登录、还要存数据的小网站，没必要上来就考虑服务器。</p>"
        "<p>推荐一下 Supabase。一个免费的数据库，后端也顺便有了。</p>"
    )
    try:
        with respx.mock:
            respx.route(host="www.xiaoheihe.cn").mock(return_value=httpx.Response(403))
            respx.route(host="api.xiaoheihe.cn").mock(return_value=httpx.Response(403))
            result = service.archive_from_browser_dom(
                url=url,
                page_html=html,
                final_url=url,
                title="如何获得一个免费的数据库+后端",
                author="小稿的AI日记",
                body_html=body,
            )
        assert result.status == ArchiveStatus.COMPLETE
        assert result.article_id is not None
        detail = service.get_article(int(result.article_id))
        assert detail is not None
        assert detail.site_key == "xiaoheihe"
        assert "Supabase" in detail.body_html or "Supabase" in (detail.plain_text or "")
        # get_article hot path may omit plain_text — body html still present.
        assert "数据库" in detail.body_html or "数据库" in detail.title
    finally:
        context.close()
