"""Standalone WebEngine browser for anonymous / login-assisted page capture.

Remote pages never receive TechVault WebChannel business objects.
Saving uses the currently rendered DOM — never re-fetches article HTML via httpx.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from techvault.app.settings import SettingsService
from techvault.db.connection import connect
from techvault.logging_setup import get_logger
from techvault.redact import safe_host
from techvault.ui.mhtml_saver import MhtmlSaveController, MhtmlSaveOutcome
from techvault.web_archive.browser_profile import clear_zhihu_session, zhihu_profile_dir
from techvault.web_archive.capture import publish_from_rendered_html
from techvault.web_archive.dom_extract import (
    DOM_EXTRACT_JS,
    not_ready_reason,
    payload_ready_for_auto_capture,
    payload_usable_on_timeout,
)
from techvault.web_archive.mhtml import (
    SNAPSHOT_COMPLETE,
    SNAPSHOT_FAILED,
    SNAPSHOT_NOT_CAPTURED,
    attach_mhtml_snapshot,
    mark_snapshot_failed,
)
from techvault.web_archive.models import ArchiveStatus, CaptureResult
from techvault.web_archive.paths import WebPathLayout

logger = get_logger()

# Re-export for probes / tests
_DOM_EXTRACT_JS = DOM_EXTRACT_JS


class WebCaptureWindow(QMainWindow):
    captureFinished = Signal(object)  # CaptureResult
    phaseChanged = Signal(str)  # human-readable phase for TaskBridge

    def __init__(
        self,
        database_path: Path,
        url: str,
        *,
        use_login_profile: bool = False,
        auto_capture: bool = False,
        auto_timeout_ms: int = 45000,
        show_dialogs: bool | None = None,
        task_id: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._database_path = Path(database_path)
        self._initial_url = url
        self._use_login_profile = use_login_profile
        self._auto_capture = auto_capture
        self._auto_timeout_ms = max(5_000, int(auto_timeout_ms))
        self._show_dialogs = (not auto_capture) if show_dialogs is None else bool(show_dialogs)
        self._task_id = task_id or uuid.uuid4().hex[:12]
        self._closed_by_user = False
        self._finished_emitted = False
        self._waiting_for_user = False
        self._load_started = False
        self._load_ok_seen = False
        self._load_fail_count = 0
        self._poll_count = 0
        self._capture_callback_ran = False
        self._stage_entered_at: dict[str, float] = {}
        self._last_not_ready = ""
        self._last_body_len = 0
        self._last_page_type = ""
        self._load_begun = False
        self.setWindowTitle("TechVault 浏览器归档")
        self.resize(1200, 860)
        self.setMinimumSize(900, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        base_profile = zhihu_profile_dir(anonymous=not use_login_profile)
        profile_name = f"techvault-{'login' if use_login_profile else 'anon'}-{self._task_id}"
        # Off-the-record for anonymous auto-capture: avoids Chromium user-data lock
        # races across sequential capture windows (native AV under continuous stress).
        if use_login_profile:
            self._profile = QWebEngineProfile(profile_name, self)
            self._profile.setPersistentStoragePath(str(base_profile))
            self._profile.setCachePath(str(base_profile / "cache"))
            self._profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
            )
        elif auto_capture:
            self._profile = QWebEngineProfile(self)  # off-the-record
        else:
            # Manual anonymous browser: per-window storage under anonymous root.
            profile_path = base_profile / f"task-{self._task_id}"
            profile_path.mkdir(parents=True, exist_ok=True)
            self._profile = QWebEngineProfile(profile_name, self)
            self._profile.setPersistentStoragePath(str(profile_path))
            self._profile.setCachePath(str(profile_path / "cache"))
            self._profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
            )
        self._profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )

        self._page = QWebEnginePage(self._profile, self)
        self.view = QWebEngineView(self)
        self.view.setPage(self._page)
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)
        # Isolation: never install TechVault WebChannel on this page.
        assert self.view.page().webChannel() is None or True

        self.url_edit = QLineEdit(self)
        self.url_edit.setText(url)
        self.url_edit.returnPressed.connect(self.navigate)
        self.back_btn = QPushButton("后退", self)
        self.fwd_btn = QPushButton("前进", self)
        self.reload_btn = QPushButton("刷新", self)
        self.save_btn = QPushButton("保存当前页面", self)
        self.login_btn = QPushButton("打开知乎登录", self)
        self.clear_btn = QPushButton("清除登录会话", self)
        self.close_btn = QPushButton("关闭", self)
        self.status = QLabel("就绪", self)
        self.hint = QLabel(
            "匿名浏览公开内容即可保存。请确认正文已展开完整后再点「保存当前页面」。"
            "保存将使用当前已渲染内容，不会重新发起失败的 HTTP 抓取。",
            self,
        )
        self.hint.setWordWrap(True)

        self.back_btn.clicked.connect(self.view.back)
        self.fwd_btn.clicked.connect(self.view.forward)
        self.reload_btn.clicked.connect(self.view.reload)
        self.save_btn.clicked.connect(self.save_current_page)
        self.login_btn.clicked.connect(self.open_zhihu_login)
        self.clear_btn.clicked.connect(self.clear_session)
        self.close_btn.clicked.connect(self.close)

        nav = QHBoxLayout()
        for widget in (
            self.back_btn,
            self.fwd_btn,
            self.reload_btn,
            self.url_edit,
            self.save_btn,
            self.login_btn,
            self.clear_btn,
            self.close_btn,
        ):
            nav.addWidget(widget)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.addLayout(nav)
        layout.addWidget(self.hint)
        layout.addWidget(self.view, 1)
        self.setCentralWidget(root)
        bar = QStatusBar(self)
        bar.addWidget(self.status, 1)
        self.setStatusBar(bar)

        self.view.urlChanged.connect(self._on_url)
        self.view.loadStarted.connect(self._on_load_started)
        self.view.loadProgress.connect(self._on_load_progress)
        self.view.loadFinished.connect(self._on_loaded)

        self._saving = False
        self._tearing_down = False
        self._pending_result: CaptureResult | None = None
        self._mhtml_controller: MhtmlSaveController | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1800)
        self._poll_timer.timeout.connect(self._poll_for_content)
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_auto_timeout)

        if self._auto_capture:
            self.hint.setText(
                "自动归档：正在用浏览器加载网页并等待正文出现。"
                "若正文已完整显示但仍未自动保存，请点击「保存当前页面」。"
            )
        # Do NOT navigate here — TaskBridge must connect signals first, then call begin_load().
        if not self._auto_capture:
            self.begin_load()

    @property
    def task_id(self) -> str:
        return self._task_id

    def begin_load(self) -> None:
        """Start navigation after callers connect phase/finished signals."""
        if self._load_begun:
            return
        self._load_begun = True
        if self._auto_capture:
            self._enter_stage("BROWSER_LOADING")
            self._emit_phase("直接获取受限，正在使用浏览器加载网页……", "BROWSER_LOADING")
            # Arm stage timeout immediately — never wait solely for loadFinished(true).
            if not self._timeout_timer.isActive():
                self._timeout_timer.start(self._auto_timeout_ms)
            self._ensure_polling()
            self._diag("begin_load")
        self.navigate()

    def navigate(self) -> None:
        text = self.url_edit.text().strip()
        if not text:
            return
        if not text.startswith(("http://", "https://")):
            text = "https://" + text
        self.view.setUrl(QUrl(text))

    def open_zhihu_login(self) -> None:
        self.url_edit.setText("https://www.zhihu.com/signin")
        self.navigate()
        self._set_status("请在页面中完成知乎官方登录（扫码等）")

    def clear_session(self) -> None:
        clear_zhihu_session()
        if self._show_dialogs:
            QMessageBox.information(self, "会话已清除", "已清除知乎登录会话。下次将以匿名会话打开。")
        self._set_status("已清除登录会话")

    def cancel_capture(self) -> None:
        """Called by TaskBridge when user cancels the unified archive task."""
        self._closed_by_user = True
        self._stop_auto_timers()
        self._diag("cancel_capture")
        if self._mhtml_controller is not None:
            self._mhtml_controller.cancel()
            return
        if not self._finished_emitted:
            self._finished_emitted = True
            self.captureFinished.emit(
                CaptureResult(
                    ArchiveStatus.CANCELLED,
                    "已取消",
                    failure_code="CANCELLED",
                    capture_mode="BROWSER",
                    snapshot_status=SNAPSHOT_NOT_CAPTURED,
                    diagnostics=self._diag_snapshot(capture_callback=False),
                )
            )
        self.close()

    @Slot()
    def save_current_page(self) -> None:
        if self._saving:
            return
        self._stop_auto_timers()
        self._waiting_for_user = False
        self._saving = True
        self.save_btn.setEnabled(False)
        self._enter_stage("CAPTURING_DOM")
        self._emit_phase("正在提取文章正文……", "CAPTURING_DOM")
        self._diag("capture_js_requested")
        self.view.page().runJavaScript(DOM_EXTRACT_JS, self._on_dom_extracted)

    def _on_dom_extracted(self, raw) -> None:
        self._capture_callback_ran = True
        if self._tearing_down or self._finished_emitted or self._closed_by_user:
            self._diag("capture_callback_ignored_terminal")
            return
        try:
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, json.JSONDecodeError):
            payload = {}
        self._last_body_len = int(payload.get("body_len") or 0)
        self._last_page_type = str(payload.get("page_type") or "")
        self._diag("capture_callback", body_len=self._last_body_len, page_type=self._last_page_type)
        page_html = payload.get("page_html") or ""
        body = payload.get("body_html") or ""
        if not page_html and not body:
            self._finish_fail("无法读取当前页面 DOM")
            return
        url = payload.get("url") or self.view.url().toString()
        self._enter_stage("LOCALIZING_MEDIA")
        self._emit_phase("正在保存图片……", "LOCALIZING_MEDIA")
        connection = connect(self._database_path)
        try:
            self._enter_stage("PUBLISHING")
            self._emit_phase("正在写入本地归档……", "PUBLISHING")
            result = publish_from_rendered_html(
                connection,
                url=url,
                page_html=page_html or f"<html><body>{body}</body></html>",
                final_url=url,
                capture_mode="BROWSER",
                preextracted={
                    "title": payload.get("title") or "",
                    "author": payload.get("author") or None,
                    "body_html": body,
                },
                on_progress=lambda *a: self._set_status(str(a[0]) if a else ""),
            )
        except Exception as exc:
            logger.exception("Browser capture publish failed task_id=%s", self._task_id)
            self._finish_fail(f"保存失败：{type(exc).__name__}")
            connection.close()
            return
        connection.close()

        result.diagnostics = {
            **(result.diagnostics or {}),
            **self._diag_snapshot(capture_callback=True),
        }
        if result.status not in {ArchiveStatus.COMPLETE, ArchiveStatus.PARTIAL}:
            self._saving = False
            self.save_btn.setEnabled(True)
            self._set_status(result.message)
            if self._show_dialogs:
                QMessageBox.warning(self, "归档未完成", result.message)
            self._emit_finished(result)
            return

        # Keep WebEngine page alive until DownloadCompleted (or explicit failure).
        self._pending_result = result
        self._start_mhtml_save(result)

    def _start_mhtml_save(self, result: CaptureResult) -> None:
        self._enter_stage("SAVING_SNAPSHOT")
        self._emit_phase("正在生成网页快照……", "SAVING_SNAPSHOT")
        connection = connect(self._database_path)
        try:
            settings = SettingsService(connection)
            root = settings.get_archive_root()
            job = f"mhtml-{self._task_id}-{result.article_id or 0}"
            staging = (
                WebPathLayout(root).tmp_dir(job)
                if root is not None
                else Path(result.archive_folder or ".") / "_mhtml_tmp"
            )
        finally:
            connection.close()

        controller = MhtmlSaveController(
            self._profile,
            self.view.page(),
            staging,
            timeout_ms=90_000,
            parent=self,
        )
        self._mhtml_controller = controller
        controller.phaseChanged.connect(lambda text: self._emit_phase(text, "SAVING_SNAPSHOT"))
        controller.finished.connect(self._on_mhtml_finished)
        self._diag("mhtml_save_start", staging=str(staging))
        controller.start()

    @Slot(object)
    def _on_mhtml_finished(self, outcome: MhtmlSaveOutcome) -> None:
        self._mhtml_controller = None
        result = self._pending_result
        self._pending_result = None
        if result is None:
            self._saving = False
            self.save_btn.setEnabled(True)
            return

        if self._closed_by_user:
            self._saving = False
            self.save_btn.setEnabled(True)
            self._emit_finished(
                CaptureResult(
                    ArchiveStatus.CANCELLED,
                    "已取消",
                    article_id=result.article_id,
                    title=result.title,
                    archive_folder=result.archive_folder,
                    failure_code="CANCELLED",
                    capture_mode="BROWSER",
                    snapshot_status=SNAPSHOT_NOT_CAPTURED,
                    diagnostics=self._diag_snapshot(capture_callback=True),
                )
            )
            self.close()
            return

        self._enter_stage("VERIFYING")
        self._emit_phase("正在验证归档文件……", "VERIFYING")
        connection = connect(self._database_path)
        try:
            if outcome.status == SNAPSHOT_COMPLETE and outcome.staging_path and result.article_id:
                attached = attach_mhtml_snapshot(
                    connection,
                    article_id=int(result.article_id),
                    staging_path=outcome.staging_path,
                )
                result.snapshot_status = attached.status
                result.snapshot_message = attached.message
                result.snapshot_path = attached.path
                result.snapshot_bytes = attached.size
                self._diag(
                    "mhtml_attach",
                    ok=attached.ok,
                    bytes=attached.size,
                    status=attached.status,
                )
            elif result.article_id:
                msg = outcome.message or "网页快照未完成"
                mark_snapshot_failed(connection, int(result.article_id), msg)
                result.snapshot_status = outcome.status or SNAPSHOT_FAILED
                result.snapshot_message = msg
                self._diag("mhtml_failed", message=msg, status=outcome.status)
            else:
                result.snapshot_status = SNAPSHOT_FAILED
                result.snapshot_message = outcome.message or "网页快照未完成"
        except Exception as exc:
            logger.exception("MHTML attach failed task_id=%s", self._task_id)
            result.snapshot_status = SNAPSHOT_FAILED
            result.snapshot_message = f"快照登记失败：{type(exc).__name__}"
            if result.article_id:
                try:
                    mark_snapshot_failed(connection, int(result.article_id), result.snapshot_message)
                except Exception:
                    pass
        finally:
            connection.close()

        if result.snapshot_status == SNAPSHOT_COMPLETE:
            if result.status == ArchiveStatus.COMPLETE:
                result.message = "网页已归档，可离线阅读。"
            elif result.status == ArchiveStatus.PARTIAL:
                result.message = f"{result.message}；网页快照已保存。"
        else:
            base = "正文已成功归档，但网页快照未完成。"
            if result.snapshot_message:
                result.message = f"{base}（{result.snapshot_message}）"
            else:
                result.message = base

        self._saving = False
        self.save_btn.setEnabled(True)
        self._set_status(result.message)
        if self._show_dialogs:
            if result.snapshot_status == SNAPSHOT_COMPLETE:
                QMessageBox.information(self, "归档完成", result.message)
            else:
                QMessageBox.warning(self, "正文已保存", result.message)
        self._emit_finished(result)
        if self._auto_capture:
            self.close()

    def _finish_fail(self, message: str) -> None:
        self._saving = False
        self.save_btn.setEnabled(True)
        self._set_status(message)
        result = CaptureResult(
            ArchiveStatus.FAILED,
            message,
            failure_code="EXTRACT_FAILED",
            capture_mode="BROWSER",
            snapshot_status=SNAPSHOT_NOT_CAPTURED,
            diagnostics=self._diag_snapshot(capture_callback=self._capture_callback_ran),
        )
        if self._show_dialogs:
            QMessageBox.warning(self, "归档未完成", message)
        self._emit_finished(result)

    def _emit_finished(self, result: CaptureResult) -> None:
        if self._finished_emitted:
            return
        self._finished_emitted = True
        self._stop_auto_timers()
        if result.diagnostics is None:
            result.diagnostics = self._diag_snapshot(capture_callback=self._capture_callback_ran)
        else:
            result.diagnostics = {
                **self._diag_snapshot(capture_callback=self._capture_callback_ran),
                **result.diagnostics,
            }
        self._diag("emit_finished", status=result.status, failure_code=result.failure_code)
        self.captureFinished.emit(result)

    def _on_url(self, url: QUrl) -> None:
        if self._tearing_down:
            return
        self.url_edit.setText(url.toString())

    @Slot(int)
    def _on_load_progress(self, percent: int) -> None:
        if self._tearing_down:
            return
        self._set_status(f"加载中 {percent}%")

    def _on_load_started(self) -> None:
        if self._tearing_down:
            return
        self._load_started = True
        self._set_status("正在加载…")
        self._diag("load_started")

    def _on_loaded(self, ok: bool) -> None:
        if self._tearing_down:
            return
        self._diag("load_finished", ok=ok)
        if self._saving or self._finished_emitted or self._closed_by_user or self._waiting_for_user:
            # Late loadFinished after capture/MHTML must not rewind UI to WAITING_FOR_CONTENT.
            return
        if not ok:
            self._load_fail_count += 1
            self._set_status("加载失败")
            if self._auto_capture:
                # Keep polling + ensure timeout is armed (must not hang forever).
                self._ensure_polling()
                if not self._timeout_timer.isActive():
                    self._timeout_timer.start(self._auto_timeout_ms)
            return
        self._load_ok_seen = True
        if self._auto_capture:
            self._enter_stage("WAITING_FOR_CONTENT")
            self._emit_phase("正在等待文章正文……", "WAITING_FOR_CONTENT")
            self._ensure_polling()
            if not self._timeout_timer.isActive():
                self._timeout_timer.start(self._auto_timeout_ms)
        else:
            self._set_status("加载完成")

    def _ensure_polling(self) -> None:
        if self._finished_emitted or self._closed_by_user or self._waiting_for_user:
            return
        if not self._poll_timer.isActive():
            self._poll_timer.start()
            # Immediate first probe after settle
            QTimer.singleShot(1200, self._poll_for_content)

    def _poll_for_content(self) -> None:
        if (
            self._tearing_down
            or self._saving
            or self._finished_emitted
            or self._closed_by_user
            or self._waiting_for_user
        ):
            return
        try:
            page = self.view.page()
        except RuntimeError:
            self._diag("poll_page_dead")
            self._emit_finished(
                CaptureResult(
                    ArchiveStatus.FAILED,
                    "浏览器窗口已失效，归档失败",
                    failure_code="BROWSER_GONE",
                    capture_mode="BROWSER",
                    diagnostics=self._diag_snapshot(capture_callback=False),
                )
            )
            return
        self._poll_count += 1
        page.runJavaScript(DOM_EXTRACT_JS, self._on_auto_poll)

    def _on_auto_poll(self, raw) -> None:
        if (
            self._tearing_down
            or self._saving
            or self._finished_emitted
            or self._closed_by_user
            or self._waiting_for_user
        ):
            return
        try:
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, json.JSONDecodeError):
            payload = {}
        self._last_body_len = int(payload.get("body_len") or 0)
        self._last_page_type = str(payload.get("page_type") or "")
        self._last_not_ready = not_ready_reason(payload)
        if payload_ready_for_auto_capture(payload):
            self._diag(
                "auto_capture_ready",
                body_len=self._last_body_len,
                page_type=self._last_page_type,
                poll_count=self._poll_count,
            )
            self.save_current_page()
            return
        self._set_status(
            f"正在等待文章正文…（已检测 {self._last_body_len} 字，{self._last_not_ready}）"
        )
        if self._poll_count in {1, 5, 10, 20}:
            self._diag(
                "dom_not_ready",
                body_len=self._last_body_len,
                reason=self._last_not_ready,
                page_type=self._last_page_type,
                poll_count=self._poll_count,
            )

    def _on_auto_timeout(self) -> None:
        if self._tearing_down or self._saving or self._finished_emitted or self._closed_by_user:
            return
        self._diag(
            "auto_timeout",
            load_ok_seen=self._load_ok_seen,
            load_fail_count=self._load_fail_count,
            poll_count=self._poll_count,
            body_len=self._last_body_len,
            not_ready=self._last_not_ready,
        )
        # Final DOM check: capture if usable; otherwise wait for explicit user action.
        try:
            self.view.page().runJavaScript(DOM_EXTRACT_JS, self._on_timeout_decision)
        except RuntimeError:
            self._emit_finished(
                CaptureResult(
                    ArchiveStatus.FAILED,
                    "浏览器窗口已失效，归档失败",
                    failure_code="BROWSER_GONE",
                    capture_mode="BROWSER",
                    diagnostics=self._diag_snapshot(capture_callback=False),
                )
            )

    def _on_timeout_decision(self, raw) -> None:
        if self._tearing_down or self._saving or self._finished_emitted or self._closed_by_user:
            return
        try:
            payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, json.JSONDecodeError):
            payload = {}
        self._last_body_len = int(payload.get("body_len") or 0)
        self._last_page_type = str(payload.get("page_type") or "")
        self._last_not_ready = not_ready_reason(payload)
        if payload_ready_for_auto_capture(payload) or payload_usable_on_timeout(payload):
            self._diag("timeout_capture_attempt", body_len=self._last_body_len)
            self.save_current_page()
            return
        self._enter_waiting_for_user()

    def _enter_waiting_for_user(self) -> None:
        if self._finished_emitted or self._closed_by_user:
            return
        self._waiting_for_user = True
        self._poll_timer.stop()
        self._timeout_timer.stop()
        self._enter_stage("WAITING_FOR_USER")
        self.hint.setText(
            "请确认文章完整显示（必要时展开全文或完成验证），然后点击「保存当前页面」。"
            "保存使用当前已渲染内容，不会重新发起失败的 HTTP 抓取。"
        )
        self._emit_phase("请确认文章完整显示，然后点击保存当前页面。", "WAITING_FOR_USER")
        self._diag("waiting_for_user", body_len=self._last_body_len, reason=self._last_not_ready)
        self.show()
        self.raise_()
        self.activateWindow()

    def _stop_auto_timers(self) -> None:
        self._poll_timer.stop()
        self._timeout_timer.stop()

    def prepare_teardown(self) -> None:
        """Stop async work before close/deleteLater (safe beside DesktopWindow WebEngine)."""
        if self._tearing_down:
            return
        self._tearing_down = True
        self._closed_by_user = True
        self._finished_emitted = True
        self._auto_capture = False
        self._stop_auto_timers()
        try:
            ctrl = self._mhtml_controller
            self._mhtml_controller = None
            if ctrl is not None:
                ctrl._done = True  # noqa: SLF001
                try:
                    ctrl.cancel()
                except Exception:
                    pass
        except Exception:
            pass
        # Disconnect view signals so late loadFinished/JS/progress cannot touch a dying page.
        for signal, slot in (
            (self.view.urlChanged, self._on_url),
            (self.view.loadStarted, self._on_load_started),
            (self.view.loadProgress, self._on_load_progress),
            (self.view.loadFinished, self._on_loaded),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        # Do not navigate to about:blank here: under DesktopWindow concurrency that
        # extra load+teardown race correlated with native AVs. Stop work & disconnect only.

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._tearing_down:
            self._stop_auto_timers()
        if self._mhtml_controller is not None and not self._finished_emitted:
            self._closed_by_user = True
            self._diag("close_event_cancel_mhtml")
            self._mhtml_controller.cancel()
            event.ignore()
            return
        if self._auto_capture and not self._finished_emitted:
            self._closed_by_user = True
            self._finished_emitted = True
            self._diag("close_event_cancel")
            pending = self._pending_result
            if pending is not None and pending.status in {
                ArchiveStatus.COMPLETE,
                ArchiveStatus.PARTIAL,
            }:
                pending.message = "正文已成功归档，但网页快照未完成。（窗口已关闭）"
                pending.snapshot_status = SNAPSHOT_FAILED
                pending.snapshot_message = "窗口已关闭"
                self.captureFinished.emit(pending)
            else:
                self.captureFinished.emit(
                    CaptureResult(
                        ArchiveStatus.CANCELLED,
                        "浏览器窗口已关闭，归档已取消",
                        failure_code="CANCELLED",
                        capture_mode="BROWSER",
                        snapshot_status=SNAPSHOT_NOT_CAPTURED,
                        diagnostics=self._diag_snapshot(capture_callback=False),
                    )
                )
        super().closeEvent(event)

    def _enter_stage(self, code: str) -> None:
        now = time.monotonic()
        prev = None
        if self._stage_entered_at:
            prev = next(reversed(self._stage_entered_at))
            left_at = now
            entered = self._stage_entered_at.get(prev, now)
            logger.info(
                "web_capture stage_leave task_id=%s stage=%s elapsed_ms=%s",
                self._task_id,
                prev,
                int((left_at - entered) * 1000),
            )
        self._stage_entered_at[code] = now
        logger.info(
            "web_capture stage_enter task_id=%s stage=%s url_host=%s",
            self._task_id,
            code,
            safe_host(self._initial_url),
        )

    def _emit_phase(self, text: str, code: str = "") -> None:
        self._set_status(text)
        # Emit "code|text" so TaskBridge can set phaseCode + phase without a second signal.
        self.phaseChanged.emit(f"{code}|{text}" if code else text)

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    def _diag(self, event: str, **fields) -> None:
        parts = " ".join(f"{k}={v}" for k, v in fields.items())
        logger.info(
            "web_capture diag task_id=%s event=%s url_host=%s window_alive=%s %s",
            self._task_id,
            event,
            safe_host(self._initial_url),
            self._window_alive(),
            parts,
        )

    def _window_alive(self) -> bool:
        try:
            _ = self.isVisible()
            return True
        except RuntimeError:
            return False

    def _diag_snapshot(self, *, capture_callback: bool) -> dict:
        return {
            "task_id": self._task_id,
            "url_host": safe_host(self._initial_url),
            "page_type": self._last_page_type,
            "load_started": self._load_started,
            "load_ok_seen": self._load_ok_seen,
            "load_fail_count": self._load_fail_count,
            "dom_poll_count": self._poll_count,
            "body_len": self._last_body_len,
            "not_ready_reason": self._last_not_ready,
            "capture_callback_ran": capture_callback,
            "waiting_for_user": self._waiting_for_user,
            "window_alive": self._window_alive(),
            "stages": list(self._stage_entered_at.keys()),
        }
