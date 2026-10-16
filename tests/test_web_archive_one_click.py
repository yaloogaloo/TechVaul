"""P2.1: one-click HTTP → Chromium fallback stays one logical task."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from techvault.app.bootstrap import prepare_context
from techvault.ui.bridge.task_bridge import TaskBridge
from techvault.web_archive.dom_extract import payload_ready_for_auto_capture
from techvault.web_archive.failure_codes import FailureCode
from techvault.web_archive.models import ArchiveStatus, CaptureResult
from techvault.web_archive.service import WebArchiveService

FIXTURES = Path(__file__).parent / "fixtures" / "web_archive"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_payload_ready_rejects_empty_and_login_title() -> None:
    assert not payload_ready_for_auto_capture({})
    assert not payload_ready_for_auto_capture(
        {"body_len": 200, "body_html": "<p>x</p>" * 20, "has_body_container": False}
    )
    assert not payload_ready_for_auto_capture(
        {
            "body_len": 200,
            "body_html": "<p>正文</p>" * 30,
            "has_body_container": True,
            "title": "安全验证",
        }
    )
    assert payload_ready_for_auto_capture(
        {
            "body_len": 120,
            "body_html": "<p>这是一段足够长的回答正文，用于自动捕获判定。</p>" * 3,
            "has_body_container": True,
            "title": "如何学习编程？",
        }
    )


def test_http_restricted_still_suggests_browser_flag(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    try:
        url = "https://www.zhihu.com/question/1001/answer/2002"
        with respx.mock:
            respx.get(url).mock(
                return_value=httpx.Response(
                    403,
                    text=(FIXTURES / "zhihu_zse_challenge.html").read_text(encoding="utf-8"),
                )
            )
            result = WebArchiveService(context.connection).archive(url)
        assert result.suggest_browser is True
        assert result.failure_code == FailureCode.ACCESS_RESTRICTED
    finally:
        context.close()


def test_task_bridge_http_success_skips_browser(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(tmp_path / "db.sqlite")
    success = CaptureResult(
        ArchiveStatus.COMPLETE,
        "网页已归档",
        article_id=7,
        title="T",
        capture_mode="URL_AUTO",
    )
    with patch.object(bridge, "_begin_browser_fallback") as begin:
        bridge._on_web_completed(success)
        begin.assert_not_called()
    assert bridge._state["status"] == "success"
    assert bridge._state["articleId"] == 7
    assert bridge._state["active"] is False
    context.close()


def test_task_bridge_access_restricted_starts_browser_fallback(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    bridge._web_url = "https://www.zhihu.com/question/1001/answer/2002"
    bridge._state = {**bridge._state, "active": True, "kind": "web", "ownerRepo": bridge._web_url}

    class FakeWindow(QObject):
        captureFinished = Signal(object)
        phaseChanged = Signal(str)

        def __init__(self, *a, **k):
            super().__init__()
            self._shown = False
            self._load_begun = False
            self.task_id = k.get("task_id") or "fake"

        def begin_load(self):
            self._load_begun = True

        def show(self):
            self._shown = True

        def raise_(self):
            pass

        def activateWindow(self):
            pass

        def close(self):
            pass

        def deleteLater(self):
            pass

        def cancel_capture(self):
            self.captureFinished.emit(
                CaptureResult(ArchiveStatus.CANCELLED, "已取消", failure_code="CANCELLED")
            )

    http_fail = CaptureResult(
        ArchiveStatus.FAILED,
        "网页访问被限制（可能需要浏览器渲染）。",
        failure_code=FailureCode.ACCESS_RESTRICTED,
        suggest_browser=True,
        capture_mode="URL_AUTO",
    )

    with patch("techvault.ui.web_capture_window.WebCaptureWindow", FakeWindow):
        bridge._on_web_completed(http_fail)

    assert bridge._state["active"] is True
    assert bridge._state["phaseCode"] == "BROWSER_LOADING"
    assert "浏览器" in bridge._state["phase"]
    assert bridge._capture_window is not None
    assert bridge._capture_window._shown is True
    assert bridge._capture_window._load_begun is True

    # Same task completes when browser publishes.
    browser_ok = CaptureResult(
        ArchiveStatus.COMPLETE,
        "网页已归档",
        article_id=42,
        title="答",
        capture_mode="BROWSER",
    )
    bridge._on_browser_fallback_finished(browser_ok)
    assert bridge._state["active"] is False
    assert bridge._state["status"] == "success"
    assert bridge._state["articleId"] == 42
    assert bridge._state["captureMode"] == "BROWSER"
    assert "可离线" in bridge._state["message"]
    context.close()


def test_task_bridge_browser_fail_merges_http_context(tmp_path: Path, qapp) -> None:
    bridge = TaskBridge(tmp_path / "db.sqlite")
    bridge._http_stage = CaptureResult(
        ArchiveStatus.FAILED,
        "网页访问被限制（可能需要浏览器渲染）。",
        failure_code=FailureCode.ACCESS_RESTRICTED,
        suggest_browser=True,
    )
    bridge._state = {"active": True, "kind": "web", "ownerRepo": "u"}
    bridge._on_browser_fallback_finished(
        CaptureResult(
            ArchiveStatus.FAILED,
            "无法读取当前页面 DOM",
            failure_code="EXTRACT_FAILED",
            capture_mode="BROWSER",
        )
    )
    assert bridge._state["status"] == "failed"
    assert "HTTP" in bridge._state["message"]
    assert "浏览器" in bridge._state["message"]
    assert bridge._state["httpFailureCode"] == FailureCode.ACCESS_RESTRICTED


def test_publish_browser_writes_sqlite_listable(tmp_path: Path) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    service = WebArchiveService(context.connection)
    url = "https://www.zhihu.com/question/1001/answer/2002"
    body = "<p>" + ("足够长的回答正文。" * 20) + "</p>"
    result = service.archive_from_browser_dom(
        url=url,
        page_html=f"<html><body>{body}</body></html>",
        final_url=url,
        title="一键归档测试",
        author="作者",
        body_html=body,
    )
    assert result.status == ArchiveStatus.COMPLETE
    assert result.article_id is not None
    items = service.list_articles()
    assert any(i.id == result.article_id for i in items)
    detail = service.get_article(result.article_id)
    assert detail is not None
    assert "足够长的回答正文" in detail.plain_text
    assert Path(detail.archive_folder).joinpath("article.html").is_file()
    context.close()
