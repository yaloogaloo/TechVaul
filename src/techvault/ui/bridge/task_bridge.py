"""Archive task orchestration. Owns ArchiveWorker lifecycle for the Web UI."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from techvault.app.archive_service import ArchiveResult
from techvault.archive.progress import ProgressEvent, format_bytes
from techvault.logging_setup import get_logger
from techvault.redact import safe_host
from techvault.ui.archive_worker import ArchiveWorker
from techvault.ui.bridge._json import err, ok
from techvault.ui.video_worker import VideoDownloadWorker, VideoTaskResult
from techvault.ui.i18n import operation_text, result_status_text
from techvault.ui.web_archive_worker import WebArchiveWorker
from techvault.web_archive.failure_codes import suggest_browser_capture
from techvault.web_archive.models import ArchiveStatus, CaptureResult
from techvault.web_archive.urls import parse_web_url

logger = get_logger()


class TaskBridge(QObject):
    taskChanged = Signal(str)  # JSON snapshot for React

    def __init__(self, database_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._database_path = Path(database_path)
        self._worker: ArchiveWorker | WebArchiveWorker | None = None
        self._capture_window = None
        self._dying_capture = None  # prior capture awaiting destroyed()
        self._pending_browser_http: CaptureResult | None = None
        self._http_stage: CaptureResult | None = None
        self._web_url: str = ""
        self._web_task_id: str = ""
        self._web_page_kind: str = ""
        self._terminal_emitted = False
        self._last_progress_emit_at = 0.0
        self._pending_throttle_flush = False
        self._throttle_flush_timer = QTimer(self)
        self._throttle_flush_timer.setSingleShot(True)
        self._throttle_flush_timer.timeout.connect(self._flush_throttled_emit)
        self._browser_ready_timer = QTimer(self)
        self._browser_ready_timer.setSingleShot(True)
        self._browser_ready_timer.timeout.connect(self._try_open_pending_browser)
        self._state = _idle_state()
        self._github_mode = "archive"

    @Slot(result=str)
    def getTask(self) -> str:  # noqa: N802
        return ok(self._state)

    @Slot(str, result=str)
    def startArchive(self, url: str) -> str:  # noqa: N802
        if self._busy():
            return err("BUSY", "已有归档任务正在运行")
        cleaned = (url or "").strip()
        if not cleaned:
            return err("INVALID_URL", "请输入 GitHub 地址")
        self._start_worker(cleaned, repository_id=None, asset=None, owner_repo="")
        return ok(self._state)

    @Slot(int, result=str)
    def startRearchive(self, repository_id: int) -> str:  # noqa: N802
        if self._busy():
            return err("BUSY", "已有归档任务正在运行")
        return err("USE_URL", "请通过仓库地址启动更新")

    @Slot(str, result=str)
    def startRearchiveUrl(self, url: str) -> str:  # noqa: N802
        return self.startArchive(url)

    @Slot(str, result=str)
    def startWebArchive(self, url: str) -> str:  # noqa: N802
        if self._busy():
            return err("BUSY", "已有归档任务正在运行")
        cleaned = (url or "").strip()
        if not cleaned:
            return err("INVALID_URL", "请输入网页链接")
        self._close_capture_window(silent=True)
        self._http_stage = None
        self._web_url = cleaned
        self._web_task_id = uuid.uuid4().hex[:12]
        self._terminal_emitted = False
        parsed = parse_web_url(cleaned)
        self._web_page_kind = parsed.kind or "unknown"
        logger.info(
            "web_task start task_id=%s page_kind=%s url_host=%s",
            self._web_task_id,
            self._web_page_kind,
            safe_host(cleaned),
        )
        self._state = {
            "active": True,
            "ownerRepo": cleaned,
            "phase": "正在获取网页",
            "phaseCode": "FETCHING_HTTP",
            "status": "running",
            "progressKind": "indeterminate",
            "percent": 0,
            "bytesLabel": "",
            "fileLabel": "",
            "countLabel": "",
            "message": "",
            "cancelable": True,
            "kind": "web",
            "suggestBrowser": False,
            "articleId": None,
            "failureCode": None,
            "captureMode": "URL_AUTO",
            "httpFailureCode": None,
            "browserFailureCode": None,
            "taskId": self._web_task_id,
            "pageKind": self._web_page_kind,
        }
        self._emit()
        worker = WebArchiveWorker(self._database_path, cleaned)
        worker.progress.connect(self._on_progress)
        worker.completed.connect(self._on_web_completed)
        self._worker = worker
        worker.start()
        return ok(self._state)

    @Slot(int, str, result=str)
    def retryAsset(self, repository_id: int, kind: str) -> str:  # noqa: N802
        if self._busy():
            return err("BUSY", "已有归档任务正在运行")
        asset = (kind or "").strip().upper()
        if asset not in {"README", "SOURCE", "RELEASE"}:
            return err("INVALID_ASSET", "无法重试该资料")
        self._start_worker("", repository_id=int(repository_id), asset=asset, owner_repo="")
        return ok(self._state)

    @Slot(int, result=str)
    def backfillReadmeImages(self, repository_id: int) -> str:  # noqa: N802
        if self._busy():
            return err("BUSY", "已有归档任务正在运行")
        self._start_worker(
            "",
            repository_id=int(repository_id),
            asset=None,
            owner_repo="",
            mode="readme-images",
        )
        return ok(self._state)

    @Slot(str, result=str)
    def startVideoProbe(self, url: str) -> str:  # noqa: N802
        if self._busy():
            return err("BUSY", "已有归档任务正在运行")
        cleaned = (url or "").strip()
        if not cleaned:
            return err("EMPTY_INPUT", "请输入视频链接或 BV 号")
        self._start_video("probe", cleaned, "auto", False)
        return ok(self._state)

    @Slot(str, str, str, result=str)
    def startVideoDownload(self, url: str, quality: str, replace: str) -> str:  # noqa: N802
        if self._busy():
            return err("BUSY", "已有归档任务正在运行")
        cleaned = (url or "").strip()
        if not cleaned:
            return err("EMPTY_INPUT", "请输入视频链接或 BV 号")
        self._start_video("download", cleaned, (quality or "auto").strip() or "auto", replace in {"1", "true", "yes"})
        return ok(self._state)

    def _start_video(self, mode: str, url: str, quality: str, replace: bool) -> None:
        self._terminal_emitted = False
        worker = VideoDownloadWorker(
            self._database_path,
            mode=mode,
            url=url,
            quality=quality,
            replace=replace,
        )
        self._state = {
            "active": True,
            "ownerRepo": url,
            "phase": "正在解析视频" if mode == "probe" else "正在准备下载",
            "phaseCode": "FETCHING_METADATA",
            "status": "running",
            "progressKind": "indeterminate",
            "percent": 0,
            "bytesLabel": "",
            "fileLabel": "",
            "countLabel": "",
            "message": "",
            "cancelable": True,
            "kind": "video",
            "taskId": worker.task_id,
            "videoItemId": None,
            "videoPreview": None,
            "failureCode": None,
        }
        self._emit()
        worker.progress.connect(self._on_video_progress)
        worker.completed.connect(self._on_video_completed)
        self._worker = worker
        worker.start()

    @Slot(object)
    def _on_video_progress(self, payload: object) -> None:
        if self._terminal_emitted or not isinstance(payload, dict):
            return
        if payload.get("taskId") != self._state.get("taskId"):
            return
        phase = str(payload.get("phase") or "DOWNLOADING")
        labels = {
            "FETCHING_METADATA": "正在解析视频",
            "DOWNLOADING": "正在下载",
            "MERGING": "正在合并音视频",
            "VERIFYING": "正在检查文件",
            "PUBLISHING": "正在保存",
        }
        downloaded = payload.get("downloaded")
        total = payload.get("total")
        speed = payload.get("speed")
        determinate = phase == "DOWNLOADING" and isinstance(downloaded, int) and isinstance(total, int) and total > 0
        percent = int(downloaded * 100 / total) if determinate else 0
        if isinstance(downloaded, int):
            bytes_label = _video_bytes(downloaded, total if isinstance(total, int) else None, speed)
        else:
            bytes_label = ""
        previous = self._state.get("phaseCode")
        self._state = {
            **self._state,
            "active": True,
            "phase": labels.get(phase, "正在处理视频"),
            "phaseCode": phase,
            "status": "cancelling" if self._state.get("status") == "cancelling" else "running",
            "progressKind": "determinate" if determinate else "indeterminate",
            "percent": percent,
            "bytesLabel": bytes_label,
            "cancelable": self._state.get("status") != "cancelling",
        }
        self._emit_throttled(min_interval_s=0.12, force=phase != previous or phase in {"MERGING", "VERIFYING", "PUBLISHING"})

    @Slot(object)
    def _on_video_completed(self, result: VideoTaskResult) -> None:
        if result.task_id != self._state.get("taskId") or self._terminal_emitted:
            return
        self._terminal_emitted = True
        if result.status == "READY":
            status = "ready"
            phase = "解析完成"
        elif result.status == "COMPLETED":
            status = "success"
            phase = "视频已保存"
        elif result.status == "CANCELLED":
            status = "cancelled"
            phase = "已取消"
        else:
            status = "failed"
            phase = "解析失败" if self._state.get("phaseCode") == "FETCHING_METADATA" else "下载失败"
        self._state = {
            **self._state,
            "active": False,
            "phase": phase,
            "phaseCode": "METADATA_READY" if result.status == "READY" else result.status,
            "status": status,
            "progressKind": "determinate" if status == "success" else "indeterminate",
            "percent": 100 if status == "success" else 0,
            "bytesLabel": "",
            "message": result.message,
            "cancelable": False,
            "kind": "video",
            "taskId": result.task_id,
            "videoItemId": result.item_id,
            "videoPreview": result.preview,
            "failureCode": result.code or None,
            "resultStatus": result.status,
        }
        self._emit()

    @Slot(result=str)
    def cancel(self) -> str:
        if not self._busy():
            return err("IDLE", "当前没有可取消的任务")
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
        if self._capture_window_alive():
            try:
                self._capture_window.cancel_capture()
            except RuntimeError:
                self._close_capture_window(silent=True)
                self._finalize_web(
                    CaptureResult(ArchiveStatus.CANCELLED, "已取消", failure_code="CANCELLED")
                )
            # cancel_capture often finalizes synchronously — do not overwrite terminal state.
            if self._terminal_emitted or not self._state.get("active"):
                return ok(self._state)
        elif self._state.get("kind") == "web" and self._state.get("active"):
            # HTTP-only cancel path when browser window is already gone.
            if self._worker is None or not self._worker.isRunning():
                self._finalize_web(
                    CaptureResult(ArchiveStatus.CANCELLED, "已取消", failure_code="CANCELLED")
                )
                return ok(self._state)
        self._state = {
            **self._state,
            "phase": "正在取消…",
            "phaseCode": "CANCELLED",
            "status": "cancelling",
            "progressKind": "indeterminate",
            "cancelable": False,
        }
        self._emit()
        return ok(self._state)

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            if self._worker.isRunning():
                self._worker.wait(3000)
        self._close_capture_window(silent=True)

    def _busy(self) -> bool:
        if self._worker is not None and self._worker.isRunning():
            return True
        if self._capture_window_alive():
            return True
        # A dying capture (deferred deleteLater) must not block starting the next
        # HTTP archive; browser fallback waits on _dying_capture separately.
        if self._pending_browser_http is not None:
            return True
        if self._state.get("active") and self._state.get("kind") == "web":
            # Browser fallback keeps active=True after HTTP worker exits.
            return True
        return False

    def _capture_window_alive(self) -> bool:
        window = self._capture_window
        if window is None:
            return False
        try:
            # Probe the C++ wrapper; hidden-but-alive windows still count as busy.
            window.isVisible()
            return True
        except RuntimeError:
            self._capture_window = None
            return False
        except AttributeError:
            return True

    def _start_worker(
        self,
        url: str,
        *,
        repository_id: int | None,
        asset: str | None,
        owner_repo: str,
        mode: str = "archive",
    ) -> None:
        self._github_mode = mode
        self._state = {
            "active": True,
            "ownerRepo": owner_repo or url,
            "phase": "正在补全 README 图片" if mode == "readme-images" else "正在准备",
            "status": "running",
            "progressKind": "indeterminate",
            "percent": 0,
            "bytesLabel": "",
            "fileLabel": "",
            "countLabel": "",
            "message": "",
            "cancelable": True,
            "kind": "github",
        }
        self._emit()
        worker = ArchiveWorker(
            self._database_path,
            url,
            repository_id=repository_id,
            asset=asset,
            mode=mode,
        )
        worker.progress.connect(self._on_progress)
        worker.detail.connect(self._on_detail)
        worker.completed.connect(self._on_completed)
        self._worker = worker
        worker.start()

    @Slot(str, str, int, int)
    def _on_progress(self, operation: str, current_file: str, completed: int, total: int) -> None:
        if self._terminal_emitted:
            return
        prev_code = self._state.get("phaseCode") or "FETCHING_HTTP"
        phase_code = prev_code
        if "提取" in operation:
            phase_code = "CAPTURING_DOM"
        elif "图片" in operation or "资源" in operation:
            phase_code = "LOCALIZING_MEDIA"
        elif "写入" in operation or "发布" in operation or "校验" in operation:
            phase_code = "PUBLISHING"
        web = self._state.get("kind") == "web"
        self._state = {
            **self._state,
            "active": True,
            "phase": operation_text(operation) if not web else operation,
            "phaseCode": phase_code if web else self._state.get("phaseCode"),
            "status": "running",
            "fileLabel": current_file,
            "countLabel": f"{completed} / {total}" if total else "",
            "cancelable": True,
        }
        force = web and phase_code != prev_code
        self._emit_throttled(min_interval_s=0.12, force=force)

    @Slot(object)
    def _on_detail(self, event: ProgressEvent) -> None:
        phase = event.phase
        if phase == "committing":
            label = "正在提交"
        elif phase == "verifying" or event.percentage == 100:
            label = "正在校验"
        else:
            label = self._state.get("phase") or "正在下载"
        if event.percentage is None:
            kind = "indeterminate"
            percent = 0
        else:
            kind = "determinate"
            percent = int(event.percentage)
        downloaded = format_bytes(event.overall_downloaded_bytes)
        if event.overall_expected_bytes is None:
            bytes_label = f"已下载 {downloaded}"
        else:
            bytes_label = f"{downloaded} / {format_bytes(event.overall_expected_bytes)}"
        self._state = {
            **self._state,
            "active": True,
            "phase": label,
            "status": "running" if self._state.get("status") != "cancelling" else "cancelling",
            "progressKind": kind,
            "percent": percent,
            "bytesLabel": bytes_label,
            "fileLabel": event.current_file or self._state.get("fileLabel") or "",
            "cancelable": self._state.get("status") != "cancelling",
        }
        # Download ticks can fire many times per second — coalesce for React.
        force = percent >= 100 or phase in {"committing", "verifying"}
        self._emit_throttled(min_interval_s=0.12, force=force)

    @Slot(object)
    def _on_completed(self, result: ArchiveResult) -> None:
        status = {
            "COMPLETED": "success",
            "CANCELLED": "cancelled",
            "PARTIAL": "failed",
        }.get(result.status, "failed")
        phase = result.message if self._github_mode == "readme-images" else result_status_text(result.status, result.message)
        self._state = {
            "active": False,
            "ownerRepo": result.repository_name or self._state.get("ownerRepo") or "",
            "phase": phase,
            "status": status,
            "progressKind": "determinate",
            "percent": 100 if status == "success" else 0,
            "bytesLabel": "",
            "fileLabel": "",
            "countLabel": "",
            "message": result.message,
            "cancelable": False,
            "resultStatus": result.status,
            "kind": "github",
        }
        self._emit()

    @Slot(object)
    def _on_web_completed(self, result: CaptureResult) -> None:
        """HTTP-stage completion. May continue into Chromium fallback (same logical task)."""
        if self._terminal_emitted:
            logger.info(
                "web_task ignore_stale_http task_id=%s status=%s",
                self._web_task_id,
                result.status,
            )
            return
        logger.info(
            "web_task http_done task_id=%s status=%s failure=%s suggest_browser=%s",
            self._web_task_id,
            result.status,
            result.failure_code,
            result.suggest_browser,
        )
        if result.status in {ArchiveStatus.COMPLETE, ArchiveStatus.PARTIAL}:
            self._finalize_web(result)
            return
        if result.status == ArchiveStatus.CANCELLED:
            self._finalize_web(result)
            return
        if result.suggest_browser or suggest_browser_capture(result.failure_code):
            self._http_stage = result
            self._begin_browser_fallback(result)
            return
        self._finalize_web(result)

    def _begin_browser_fallback(self, http_result: CaptureResult) -> None:
        url = self._web_url or self._state.get("ownerRepo") or ""
        self._state = {
            **self._state,
            "active": True,
            "phase": "直接获取受限，正在使用浏览器加载网页……",
            "phaseCode": "BROWSER_LOADING",
            "status": "running",
            "progressKind": "indeterminate",
            "percent": 35,
            "bytesLabel": "",
            "fileLabel": "",
            "countLabel": "",
            "message": "",
            "cancelable": True,
            "kind": "web",
            "suggestBrowser": False,
            "httpFailureCode": http_result.failure_code,
            "captureMode": "BROWSER",
            "failureCode": http_result.failure_code,
            "taskId": self._web_task_id,
            "pageKind": self._web_page_kind,
        }
        self._emit()
        logger.info(
            "web_task browser_fallback task_id=%s page_kind=%s url_host=%s http_failure=%s",
            self._web_task_id,
            self._web_page_kind,
            safe_host(url),
            http_result.failure_code,
        )

        self._close_capture_window(silent=True)
        # Do not construct a new WebEngine capture while a prior window is still
        # tearing down (DesktopWindow + capture concurrency native AV risk).
        self._pending_browser_http = http_result
        self._try_open_pending_browser()

    def _try_open_pending_browser(self) -> None:
        if self._pending_browser_http is None:
            return
        if self._dying_capture_alive():
            # Event-driven retry when destroyed fires; also arm a short poll.
            self._browser_ready_timer.start(50)
            return
        http_result = self._pending_browser_http
        self._pending_browser_http = None
        url = self._web_url or self._state.get("ownerRepo") or ""
        from techvault.ui.web_capture_window import WebCaptureWindow

        window = WebCaptureWindow(
            self._database_path,
            url,
            use_login_profile=False,
            auto_capture=True,
            show_dialogs=False,
            task_id=self._web_task_id,
            parent=None,
        )
        # Connect before begin_load so early phase/load events are not lost.
        window.phaseChanged.connect(self._on_browser_phase)
        window.captureFinished.connect(self._on_browser_fallback_finished)
        self._capture_window = window
        window.begin_load()
        window.show()
        window.raise_()
        window.activateWindow()

    def _dying_capture_alive(self) -> bool:
        dying = self._dying_capture
        if dying is None:
            return False
        try:
            # Scripted / already-closed doubles: treat as gone even before destroyed().
            if getattr(dying, "_closed", False) and not hasattr(dying, "view"):
                self._dying_capture = None
                return False
            dying.objectName()
            return True
        except RuntimeError:
            self._dying_capture = None
            return False

    @Slot(str)
    def _on_browser_phase(self, payload: str) -> None:
        if self._terminal_emitted:
            return
        sender = self.sender()
        sender_task = getattr(sender, "task_id", None) if sender is not None else None
        if sender_task and self._web_task_id and sender_task != self._web_task_id:
            logger.info(
                "web_task ignore_stale_phase task_id=%s current=%s",
                sender_task,
                self._web_task_id,
            )
            return
        code = ""
        text = payload
        if "|" in payload:
            code, text = payload.split("|", 1)
        prev_code = self._state.get("phaseCode")
        status = "waiting_user" if code == "WAITING_FOR_USER" else "running"
        self._state = {
            **self._state,
            "active": True,
            "phase": text,
            "phaseCode": code or self._state.get("phaseCode") or "BROWSER_LOADING",
            "status": status,
            "progressKind": "indeterminate",
            "bytesLabel": "",
            "fileLabel": "",
            "countLabel": "",
            "cancelable": True,
            "kind": "web",
            "captureMode": "BROWSER",
            "taskId": self._web_task_id,
        }
        logger.info(
            "web_task phase task_id=%s phaseCode=%s",
            self._web_task_id,
            self._state.get("phaseCode"),
        )
        force = bool(code) and code != prev_code
        self._emit_throttled(min_interval_s=0.15, force=force)

    @Slot(object)
    def _on_browser_fallback_finished(self, result: CaptureResult) -> None:
        sender = self.sender()
        sender_task = getattr(sender, "task_id", None) if sender is not None else None
        diag_task = None
        if result.diagnostics and isinstance(result.diagnostics, dict):
            diag_task = result.diagnostics.get("task_id")
        task_id = sender_task or diag_task
        if task_id and self._web_task_id and task_id != self._web_task_id:
            logger.info(
                "web_task ignore_stale_finished task_id=%s current=%s status=%s",
                task_id,
                self._web_task_id,
                result.status,
            )
            return
        if self._terminal_emitted:
            logger.info(
                "web_task ignore_duplicate_finished task_id=%s status=%s",
                self._web_task_id,
                result.status,
            )
            return
        logger.info(
            "web_task browser_finished task_id=%s status=%s failure=%s article_id=%s",
            self._web_task_id,
            result.status,
            result.failure_code,
            result.article_id,
        )
        if result.status in {ArchiveStatus.COMPLETE, ArchiveStatus.PARTIAL}:
            self._finalize_web(result)
            return
        # Merge HTTP + browser failure context for actionable recovery.
        http = self._http_stage
        parts: list[str] = []
        if http is not None:
            parts.append(f"HTTP：{http.message or http.failure_code or '失败'}")
        parts.append(f"浏览器：{result.message or result.failure_code or '失败'}")
        if result.status == ArchiveStatus.CANCELLED:
            message = "已取消"
        else:
            message = "；".join(parts) + "。可展开全文后点「保存当前页面」，或使用「在浏览器中打开」登录后重试。"
        merged = CaptureResult(
            status=result.status,
            message=message,
            article_id=result.article_id,
            title=result.title,
            archive_folder=result.archive_folder,
            failure_code=result.failure_code or (http.failure_code if http else None),
            capture_mode="BROWSER",
            suggest_browser=False,
            diagnostics={
                "http_failure_code": http.failure_code if http else None,
                "http_message": http.message if http else None,
                "browser_failure_code": result.failure_code,
                "browser_message": result.message,
                "got_body": bool(result.article_id),
                "task_id": self._web_task_id,
                **(result.diagnostics or {}),
            },
        )
        self._finalize_web(merged)

    def _finalize_web(self, result: CaptureResult) -> None:
        if self._terminal_emitted:
            return
        self._terminal_emitted = True
        if result.status == ArchiveStatus.COMPLETE:
            ui_status = "success"
            phase_code = "COMPLETED"
        elif result.status == ArchiveStatus.CANCELLED:
            ui_status = "cancelled"
            phase_code = "CANCELLED"
        elif result.status == ArchiveStatus.PARTIAL:
            ui_status = "success"
            phase_code = "PARTIAL"
        else:
            ui_status = "failed"
            phase_code = "FAILED"

        http_code = None
        browser_code = None
        if result.diagnostics and isinstance(result.diagnostics, dict):
            http_code = result.diagnostics.get("http_failure_code")
            browser_code = result.diagnostics.get("browser_failure_code")
        if self._http_stage is not None and result.capture_mode == "BROWSER":
            http_code = http_code or self._http_stage.failure_code

        message = result.message
        snap_status = getattr(result, "snapshot_status", None) or "NOT_CAPTURED"
        if result.status in {ArchiveStatus.COMPLETE, ArchiveStatus.PARTIAL} and snap_status not in {
            "COMPLETE",
            "NOT_CAPTURED",
        }:
            # Body archived but snapshot failed/cancelled — keep actionable snapshot wording.
            message = result.message or "正文已成功归档，但网页快照未完成。"
        elif result.status == ArchiveStatus.COMPLETE:
            message = "网页已归档，可离线阅读。"

        self._close_capture_window(silent=True)
        self._http_stage = None
        self._state = {
            "active": False,
            "ownerRepo": result.title or self._state.get("ownerRepo") or "",
            "phase": message,
            "phaseCode": phase_code,
            "status": ui_status,
            "progressKind": "determinate",
            "percent": 100 if ui_status == "success" else (50 if result.status == ArchiveStatus.PARTIAL else 0),
            "bytesLabel": "",
            "fileLabel": "",
            "countLabel": "",
            "message": message,
            "cancelable": False,
            "resultStatus": result.status,
            "kind": "web",
            "articleId": result.article_id,
            "suggestBrowser": bool(result.suggest_browser),
            "failureCode": result.failure_code,
            "captureMode": result.capture_mode,
            "httpFailureCode": http_code,
            "browserFailureCode": browser_code,
            "taskId": self._web_task_id,
            "pageKind": self._web_page_kind,
            "snapshotStatus": snap_status,
            "snapshotMessage": getattr(result, "snapshot_message", None),
            "snapshotBytes": getattr(result, "snapshot_bytes", None),
        }
        logger.info(
            "web_task terminal task_id=%s phaseCode=%s status=%s article_id=%s react_active=false",
            self._web_task_id,
            phase_code,
            ui_status,
            result.article_id,
        )
        self._emit()

    def _close_capture_window(self, *, silent: bool) -> None:
        window = self._capture_window
        self._capture_window = None
        if window is None:
            return
        try:
            try:
                window.phaseChanged.disconnect(self._on_browser_phase)
            except (TypeError, RuntimeError):
                pass
            try:
                window.captureFinished.disconnect(self._on_browser_fallback_finished)
            except (TypeError, RuntimeError):
                pass
            try:
                window.prepare_teardown()
            except Exception:
                # Fallback if prepare_teardown unavailable mid-hot-reload.
                window._finished_emitted = True  # noqa: SLF001
                window._auto_capture = False  # noqa: SLF001
                try:
                    window._stop_auto_timers()  # noqa: SLF001
                except Exception:
                    pass
            self._dying_capture = window

            def _clear_dying(_obj=None, dying=window) -> None:
                if self._dying_capture is dying:
                    self._dying_capture = None
                if self._pending_browser_http is not None:
                    QTimer.singleShot(0, self._try_open_pending_browser)

            try:
                window.destroyed.connect(_clear_dying)
            except RuntimeError:
                self._dying_capture = None
            # prepare_teardown already stopped JS/timers/signals. close+deleteLater
            # here; callers must not pump processEvents heavily in the same tick
            # as taskChanged (that combination caused native AV with DesktopWindow).
            window.close()
            window.deleteLater()
        except RuntimeError:
            self._dying_capture = None

    def _emit_throttled(self, *, min_interval_s: float = 0.12, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_progress_emit_at) < min_interval_s:
            # Coalesce bursts but always flush the latest snapshot after the window.
            self._pending_throttle_flush = True
            remaining_ms = max(1, int((min_interval_s - (now - self._last_progress_emit_at)) * 1000))
            self._throttle_flush_timer.start(remaining_ms)
            return
        self._pending_throttle_flush = False
        self._throttle_flush_timer.stop()
        self._emit()

    def _flush_throttled_emit(self) -> None:
        if self._terminal_emitted or not self._pending_throttle_flush:
            return
        self._pending_throttle_flush = False
        self._emit()

    def _emit(self) -> None:
        self._pending_throttle_flush = False
        self._throttle_flush_timer.stop()
        self._last_progress_emit_at = time.monotonic()
        self.taskChanged.emit(json.dumps(self._state, ensure_ascii=False))


def _video_bytes(downloaded: int, total: int | None, speed: object) -> str:
    from techvault.archive.progress import format_bytes

    label = format_bytes(downloaded)
    if total:
        label = f"{label} / {format_bytes(total)}"
    else:
        label = f"已下载 {label}，大小未知"
    if isinstance(speed, (int, float)) and speed > 0:
        label = f"{label} · {format_bytes(int(speed))}/s"
    return label


def _idle_state() -> dict:
    return {
        "active": False,
        "ownerRepo": "",
        "phase": "",
        "phaseCode": "",
        "status": "idle",
        "progressKind": "determinate",
        "percent": 0,
        "bytesLabel": "",
        "fileLabel": "",
        "countLabel": "",
        "message": "",
        "cancelable": False,
        "taskId": "",
        "pageKind": "",
    }

