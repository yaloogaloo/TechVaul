"""P2.3: consecutive web-archive tasks must always reach a terminal state.

All scenarios share one TaskBridge / QApplication lifecycle (no process restart).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx
from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from techvault.app.bootstrap import prepare_context
from techvault.ui.bridge.task_bridge import TaskBridge
from techvault.ui.web_capture_window import WebCaptureWindow
from techvault.web_archive.dom_extract import (
    payload_ready_for_auto_capture,
    payload_usable_on_timeout,
)
from techvault.web_archive.failure_codes import FailureCode
from techvault.web_archive.models import ArchiveStatus, CaptureResult

FIXTURES = Path(__file__).parent / "fixtures" / "web_archive"
ANSWER_URL = "https://www.zhihu.com/question/1001/answer/2002"
COLUMN_URL = "https://zhuanlan.zhihu.com/p/2058207332111218352"
ANSWER_URL_B = "https://www.zhihu.com/question/1003/answer/2004"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app


def _pump(_qapp, ms: int = 50) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class ScriptedCaptureWindow(QObject):
    """Deterministic stand-in for WebCaptureWindow used inside one TaskBridge life."""

    captureFinished = Signal(object)
    phaseChanged = Signal(str)
    instances: list[ScriptedCaptureWindow] = []
    script: list[dict] = []

    def __init__(self, database_path, url, **kwargs):
        super().__init__()
        self.database_path = database_path
        self.url = url
        self.kwargs = kwargs
        self.task_id = kwargs.get("task_id") or "scripted"
        self._auto_capture = kwargs.get("auto_capture", False)
        self._finished_emitted = False
        self._shown = False
        self._load_begun = False
        self._closed = False
        ScriptedCaptureWindow.instances.append(self)

    def begin_load(self) -> None:
        self._load_begun = True
        self.phaseChanged.emit("BROWSER_LOADING|直接获取受限，正在使用浏览器加载网页……")
        step = ScriptedCaptureWindow.script.pop(0) if ScriptedCaptureWindow.script else {"action": "success"}
        action = step.get("action", "success")
        delay_ms = int(step.get("delay_ms", 20))
        if action == "hang_until_timeout_then_user":
            # Simulate the pre-fix bug path: stay in BROWSER_LOADING until stage timeout logic.
            QTimer.singleShot(delay_ms, self._emit_waiting_user)
        elif action == "never_load_then_fail":
            QTimer.singleShot(delay_ms, self._emit_browser_gone)
        elif action == "waiting_user":
            QTimer.singleShot(delay_ms, self._emit_waiting_user)
        elif action == "waiting_user_sticky":
            QTimer.singleShot(delay_ms, self._emit_waiting_user_sticky)
        elif action == "cancel":
            QTimer.singleShot(delay_ms, self.cancel_capture)
        elif action == "fail":
            QTimer.singleShot(delay_ms, lambda: self._finish(ArchiveStatus.FAILED, "DOM 未就绪", "EXTRACT_FAILED"))
        elif action == "partial":
            QTimer.singleShot(
                delay_ms,
                lambda: self._finish(ArchiveStatus.PARTIAL, "正文已保存，快照失败", None, article_id=step.get("article_id", 9)),
            )
        else:
            QTimer.singleShot(
                delay_ms,
                lambda: self._finish(
                    ArchiveStatus.COMPLETE,
                    "网页已归档",
                    None,
                    article_id=step.get("article_id", 1),
                    title=step.get("title", "T"),
                ),
            )

    def show(self) -> None:
        self._shown = True

    def raise_(self) -> None:
        pass

    def activateWindow(self) -> None:
        pass

    def prepare_teardown(self) -> None:
        self._finished_emitted = True
        self._auto_capture = False

    def close(self) -> None:
        self._closed = True

    def deleteLater(self) -> None:
        # Must actually schedule QObject deletion so TaskBridge._dying_capture clears.
        self._closed = True
        super().deleteLater()

    def isVisible(self) -> bool:
        return self._shown and not self._closed

    def cancel_capture(self) -> None:
        self._finish(ArchiveStatus.CANCELLED, "已取消", "CANCELLED")

    def _emit_waiting_user(self) -> None:
        self.phaseChanged.emit("WAITING_FOR_USER|请确认文章完整显示，然后点击保存当前页面。")
        # Auto-resolve like a user clicking save shortly after (keeps sequential tests finite).
        QTimer.singleShot(
            15,
            lambda: self._finish(ArchiveStatus.COMPLETE, "网页已归档", None, article_id=3, title="手动确认后"),
        )

    def _emit_waiting_user_sticky(self) -> None:
        self.phaseChanged.emit("WAITING_FOR_USER|请确认文章完整显示，然后点击保存当前页面。")

    def _emit_browser_gone(self) -> None:
        self._finish(ArchiveStatus.FAILED, "浏览器窗口已失效，归档失败", "BROWSER_GONE")

    def _finish(self, status, message, failure_code, *, article_id=None, title=None) -> None:
        if self._finished_emitted:
            return
        self._finished_emitted = True
        self.phaseChanged.emit("PUBLISHING|正在写入本地归档……")
        self.captureFinished.emit(
            CaptureResult(
                status,
                message,
                article_id=article_id,
                title=title,
                failure_code=failure_code,
                capture_mode="BROWSER",
                diagnostics={"task_id": self.task_id, "url": self.url},
            )
        )
        self.close()


def _wait_terminal(bridge: TaskBridge, qapp, *, timeout_ms: int = 5000) -> dict:
    del qapp  # API compat; use a private event loop so QApplication.quit() cannot abort us.
    holder = {"done": False}
    loop = QEventLoop()

    def tick() -> None:
        st = bridge._state
        if not st.get("active") and st.get("status") in {"success", "failed", "cancelled"}:
            holder["done"] = True
            loop.quit()

    # Already terminal (fast scripted path).
    tick()
    if holder["done"]:
        return dict(bridge._state)

    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(tick)
    timer.start()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    timer.stop()
    assert holder["done"], f"task did not terminate: {bridge._state}"
    return dict(bridge._state)


def _start_restricted(bridge: TaskBridge, url: str, qapp) -> None:
    """Drive HTTP ACCESS_RESTRICTED → browser fallback using the scripted window."""
    zse = (FIXTURES / "zhihu_zse_challenge.html").read_text(encoding="utf-8")
    with respx.mock:
        respx.get(url).mock(return_value=httpx.Response(403, text=zse))
        with patch("techvault.ui.web_capture_window.WebCaptureWindow", ScriptedCaptureWindow):
            resp = json.loads(bridge.startWebArchive(url))
            assert resp["ok"], resp
            _wait_terminal(bridge, qapp)


def test_column_dom_fixture_ready_for_auto_capture() -> None:
    html = (FIXTURES / "zhihu_article.html").read_text(encoding="utf-8")
    # Simulate extract payload shape for column selectors.
    payload = {
        "title": "知乎专栏长文示例",
        "body_html": "<p>" + ("专栏正文。" * 30) + "</p>",
        "body_len": 120,
        "has_body_container": True,
        "page_type": "zhihu_article",
    }
    assert payload_ready_for_auto_capture(payload)
    assert payload_usable_on_timeout({"body_len": 50, "body_html": "<p>短</p>" * 10, "has_body_container": True})
    assert "Post-RichText" in html


def test_begin_load_arms_timeout_without_load_finished(tmp_path: Path, qapp) -> None:
    """Regression: loadFinished(false)/missing must not leave auto-capture without a timer."""
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    try:
        window = WebCaptureWindow(
            context.database_path,
            "about:blank",
            auto_capture=True,
            auto_timeout_ms=5000,
            show_dialogs=False,
            task_id="t-timeout-arm",
        )
        assert not window._timeout_timer.isActive()
        window.begin_load()
        assert window._timeout_timer.isActive()
        assert window._poll_timer.isActive()
        window._finished_emitted = True
        window._stop_auto_timers()
        window.close()
        _pump(qapp, 30)
    finally:
        context.close()


def test_sequential_answer_then_column_same_bridge(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    events: list[dict] = []
    bridge.taskChanged.connect(lambda raw: events.append(json.loads(raw)))
    ScriptedCaptureWindow.instances.clear()
    ScriptedCaptureWindow.script = [
        {"action": "success", "article_id": 1, "title": "回答"},
        {"action": "success", "article_id": 2, "title": "专栏"},
    ]
    try:
        _start_restricted(bridge, ANSWER_URL, qapp)
        assert bridge._state["status"] == "success"
        assert bridge._state["active"] is False
        first_task = bridge._state.get("taskId")

        _start_restricted(bridge, COLUMN_URL, qapp)
        assert bridge._state["status"] == "success"
        assert bridge._state["active"] is False
        assert bridge._state.get("pageKind") == "article"
        assert bridge._state.get("taskId") != first_task
        assert len(ScriptedCaptureWindow.instances) == 2
        assert all(w._load_begun for w in ScriptedCaptureWindow.instances)
        # React-visible terminal emissions for both tasks.
        terminals = [e for e in events if not e.get("active") and e.get("status") == "success"]
        assert len(terminals) >= 2
    finally:
        bridge.shutdown()
        context.close()


def test_sequential_two_answers_same_bridge(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    ScriptedCaptureWindow.instances.clear()
    ScriptedCaptureWindow.script = [
        {"action": "success", "article_id": 11, "title": "A1"},
        {"action": "success", "article_id": 12, "title": "A2"},
    ]
    try:
        _start_restricted(bridge, ANSWER_URL, qapp)
        _start_restricted(bridge, ANSWER_URL_B, qapp)
        assert bridge._state["active"] is False
        assert bridge._state["status"] == "success"
    finally:
        bridge.shutdown()
        context.close()


def test_same_url_repeated(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    ScriptedCaptureWindow.script = [
        {"action": "success", "article_id": 21},
        {"action": "success", "article_id": 22},
    ]
    try:
        _start_restricted(bridge, ANSWER_URL, qapp)
        _start_restricted(bridge, ANSWER_URL, qapp)
        assert bridge._state["status"] == "success"
        assert bridge._state["active"] is False
    finally:
        bridge.shutdown()
        context.close()


def test_fail_then_retry(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    ScriptedCaptureWindow.script = [
        {"action": "fail"},
        {"action": "success", "article_id": 31, "title": "重试成功"},
    ]
    try:
        _start_restricted(bridge, COLUMN_URL, qapp)
        assert bridge._state["status"] == "failed"
        assert bridge._state["active"] is False
        _start_restricted(bridge, COLUMN_URL, qapp)
        assert bridge._state["status"] == "success"
        assert bridge._state["active"] is False
    finally:
        bridge.shutdown()
        context.close()


def test_cancel_then_new_archive(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    ScriptedCaptureWindow.script = [{"action": "waiting_user_sticky", "delay_ms": 20}]
    zse = (FIXTURES / "zhihu_zse_challenge.html").read_text(encoding="utf-8")
    try:
        with respx.mock:
            respx.get(ANSWER_URL).mock(return_value=httpx.Response(403, text=zse))
            with patch("techvault.ui.web_capture_window.WebCaptureWindow", ScriptedCaptureWindow):
                assert json.loads(bridge.startWebArchive(ANSWER_URL))["ok"]
                for _ in range(50):
                    _pump(qapp, 15)
                    if bridge._state.get("phaseCode") == "WAITING_FOR_USER":
                        break
                assert bridge._state.get("phaseCode") == "WAITING_FOR_USER"
                cancel = json.loads(bridge.cancel())
                assert cancel["ok"]
                state = _wait_terminal(bridge, qapp)
                assert state["status"] == "cancelled"
                assert state["active"] is False

        ScriptedCaptureWindow.script = [{"action": "success", "article_id": 41}]
        _start_restricted(bridge, COLUMN_URL, qapp)
        assert bridge._state["status"] == "success"
    finally:
        bridge.shutdown()
        context.close()


def test_browser_load_timeout_enters_waiting_or_finishes(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    phases: list[str] = []
    bridge.taskChanged.connect(lambda raw: phases.append(json.loads(raw).get("phaseCode") or ""))
    ScriptedCaptureWindow.script = [{"action": "hang_until_timeout_then_user", "delay_ms": 30}]
    try:
        _start_restricted(bridge, COLUMN_URL, qapp)
        assert "WAITING_FOR_USER" in phases or bridge._state["status"] == "success"
        assert bridge._state["active"] is False
    finally:
        bridge.shutdown()
        context.close()


def test_dom_never_ready_resolves_via_user_or_fail(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    ScriptedCaptureWindow.script = [{"action": "never_load_then_fail", "delay_ms": 25}]
    try:
        _start_restricted(bridge, COLUMN_URL, qapp)
        assert bridge._state["status"] == "failed"
        assert bridge._state["active"] is False
        assert bridge._state.get("browserFailureCode") == "BROWSER_GONE" or bridge._state.get("failureCode")
    finally:
        bridge.shutdown()
        context.close()


def test_browser_unexpected_close_cancels(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    ScriptedCaptureWindow.script = [{"action": "cancel", "delay_ms": 20}]
    try:
        _start_restricted(bridge, ANSWER_URL, qapp)
        assert bridge._state["status"] == "cancelled"
        assert bridge._state["active"] is False
    finally:
        bridge.shutdown()
        context.close()


def test_stale_and_duplicate_callbacks_ignored(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    ScriptedCaptureWindow.script = [{"action": "success", "article_id": 51}]
    try:
        _start_restricted(bridge, ANSWER_URL, qapp)
        terminal = dict(bridge._state)
        # Late duplicate finished must not revive active task.
        bridge._on_browser_fallback_finished(
            CaptureResult(
                ArchiveStatus.COMPLETE,
                "stale",
                article_id=99,
                capture_mode="BROWSER",
                diagnostics={"task_id": "not-current"},
            )
        )
        assert bridge._state["active"] is False
        assert bridge._state.get("articleId") == terminal.get("articleId")
    finally:
        bridge.shutdown()
        context.close()


def test_partial_body_snapshot_failure_still_terminal(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    ScriptedCaptureWindow.script = [{"action": "partial", "article_id": 61}]
    try:
        _start_restricted(bridge, COLUMN_URL, qapp)
        assert bridge._state["active"] is False
        assert bridge._state["status"] == "success"
        assert bridge._state["phaseCode"] == "PARTIAL"
    finally:
        bridge.shutdown()
        context.close()


def test_ten_consecutive_tasks_same_bridge_no_orphan_active(tmp_path: Path, qapp) -> None:
    """Acceptance: ≥10 controlled archives in one DesktopWindow/TaskBridge life."""
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    terminals: list[dict] = []
    bridge.taskChanged.connect(
        lambda raw: terminals.append(json.loads(raw))
        if (lambda s: (not s.get("active") and s.get("kind") == "web" and s.get("status") in {"success", "failed", "cancelled"}))(
            json.loads(raw)
        )
        else None
    )
    urls = [
        ANSWER_URL,
        COLUMN_URL,
        ANSWER_URL_B,
        ANSWER_URL,
        COLUMN_URL,
        ANSWER_URL_B,
        ANSWER_URL,
        COLUMN_URL,
        ANSWER_URL,
        COLUMN_URL,
    ]
    actions = [
        {"action": "success", "article_id": i + 1, "title": f"T{i}"}
        for i in range(7)
    ] + [
        {"action": "fail"},
        {"action": "success", "article_id": 88},
        {"action": "partial", "article_id": 89},
    ]
    ScriptedCaptureWindow.script = list(actions)
    ScriptedCaptureWindow.instances.clear()
    try:
        for url in urls:
            if bridge._state.get("active"):
                pytest.fail(f"orphan active before start: {bridge._state}")
            assert not bridge._busy()
            _start_restricted(bridge, url, qapp)
            assert bridge._state["active"] is False, bridge._state
            assert bridge._state["status"] in {"success", "failed", "cancelled"}
        assert len(ScriptedCaptureWindow.instances) == 10
        assert all(w._load_begun for w in ScriptedCaptureWindow.instances)
        terminal_events = [
            t
            for t in terminals
            if not t.get("active") and t.get("status") in {"success", "failed", "cancelled"}
        ]
        assert len(terminal_events) >= 10
        task_ids = [t.get("taskId") for t in terminal_events if t.get("taskId")]
        assert len(set(task_ids)) >= 10
    finally:
        bridge.shutdown()
        context.close()


def test_python_terminal_matches_react_payload_fields(tmp_path: Path, qapp) -> None:
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    bridge = TaskBridge(context.database_path)
    last_react: dict = {}
    bridge.taskChanged.connect(lambda raw: last_react.update(json.loads(raw)))
    ScriptedCaptureWindow.script = [{"action": "success", "article_id": 77, "title": "一致性"}]
    try:
        _start_restricted(bridge, COLUMN_URL, qapp)
        assert last_react.get("active") is False
        assert last_react.get("status") == bridge._state["status"] == "success"
        assert last_react.get("phaseCode") == bridge._state["phaseCode"] == "COMPLETED"
        assert last_react.get("taskId") == bridge._state.get("taskId")
        assert last_react.get("articleId") == 77
        assert last_react.get("pageKind") == "article"
    finally:
        bridge.shutdown()
        context.close()
