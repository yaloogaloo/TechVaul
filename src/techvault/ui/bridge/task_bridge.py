"""Archive task orchestration. Owns ArchiveWorker lifecycle for the Web UI."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from techvault.app.archive_service import ArchiveResult
from techvault.archive.progress import ProgressEvent, format_bytes
from techvault.ui.archive_worker import ArchiveWorker
from techvault.ui.bridge._json import err, ok
from techvault.ui.i18n import operation_text, result_status_text
from techvault.ui.web_archive_worker import WebArchiveWorker
from techvault.web_archive.models import ArchiveStatus, CaptureResult


class TaskBridge(QObject):
    taskChanged = Signal(str)  # JSON snapshot for React

    def __init__(self, database_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._database_path = Path(database_path)
        self._worker: ArchiveWorker | WebArchiveWorker | None = None
        self._state = _idle_state()

    @Slot(result=str)
    def getTask(self) -> str:  # noqa: N802
        return ok(self._state)

    @Slot(str, result=str)
    def startArchive(self, url: str) -> str:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            return err("BUSY", "已有归档任务正在运行")
        cleaned = (url or "").strip()
        if not cleaned:
            return err("INVALID_URL", "请输入 GitHub 地址")
        self._start_worker(cleaned, repository_id=None, asset=None, owner_repo="")
        return ok(self._state)

    @Slot(int, result=str)
    def startRearchive(self, repository_id: int) -> str:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            return err("BUSY", "已有归档任务正在运行")
        # URL filled by caller via startArchive(htmlUrl); this slot accepts id + empty url path
        # Prefer explicit URL from frontend; keep id-based retry for assets.
        return err("USE_URL", "请通过仓库地址启动更新")

    @Slot(str, result=str)
    def startRearchiveUrl(self, url: str) -> str:  # noqa: N802
        return self.startArchive(url)

    @Slot(str, result=str)
    def startWebArchive(self, url: str) -> str:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            return err("BUSY", "已有归档任务正在运行")
        cleaned = (url or "").strip()
        if not cleaned:
            return err("INVALID_URL", "请输入网页链接")
        self._state = {
            "active": True,
            "ownerRepo": cleaned,
            "phase": "正在准备网页归档",
            "status": "running",
            "progressKind": "indeterminate",
            "percent": 0,
            "bytesLabel": "",
            "fileLabel": "",
            "countLabel": "",
            "message": "",
            "cancelable": True,
            "kind": "web",
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
        if self._worker is not None and self._worker.isRunning():
            return err("BUSY", "已有归档任务正在运行")
        asset = (kind or "").strip().upper()
        if asset not in {"README", "SOURCE", "RELEASE"}:
            return err("INVALID_ASSET", "无法重试该资料")
        self._start_worker("", repository_id=int(repository_id), asset=asset, owner_repo="")
        return ok(self._state)

    @Slot(result=str)
    def cancel(self) -> str:
        if self._worker is None or not self._worker.isRunning():
            return err("IDLE", "当前没有可取消的任务")
        self._worker.cancel()
        self._state = {
            **self._state,
            "phase": "正在取消…",
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

    def _start_worker(
        self,
        url: str,
        *,
        repository_id: int | None,
        asset: str | None,
        owner_repo: str,
    ) -> None:
        self._state = {
            "active": True,
            "ownerRepo": owner_repo or url,
            "phase": "正在准备",
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
        )
        worker.progress.connect(self._on_progress)
        worker.detail.connect(self._on_detail)
        worker.completed.connect(self._on_completed)
        self._worker = worker
        worker.start()

    @Slot(str, str, int, int)
    def _on_progress(self, operation: str, current_file: str, completed: int, total: int) -> None:
        self._state = {
            **self._state,
            "active": True,
            "phase": operation_text(operation),
            "status": "running",
            "fileLabel": current_file,
            "countLabel": f"{completed} / {total}" if total else "",
            "cancelable": True,
        }
        self._emit()

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
        self._emit()

    @Slot(object)
    def _on_completed(self, result: ArchiveResult) -> None:
        status = {
            "COMPLETED": "success",
            "CANCELLED": "cancelled",
            "PARTIAL": "failed",
        }.get(result.status, "failed")
        self._state = {
            "active": False,
            "ownerRepo": result.repository_name or self._state.get("ownerRepo") or "",
            "phase": result_status_text(result.status, result.message),
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
        if result.status == ArchiveStatus.COMPLETE:
            ui_status = "success"
        elif result.status == ArchiveStatus.CANCELLED:
            ui_status = "cancelled"
        elif result.status == ArchiveStatus.PARTIAL:
            ui_status = "failed"
        else:
            ui_status = "failed"
        self._state = {
            "active": False,
            "ownerRepo": result.title or self._state.get("ownerRepo") or "",
            "phase": result.message,
            "status": ui_status,
            "progressKind": "determinate",
            "percent": 100 if ui_status == "success" else 0,
            "bytesLabel": "",
            "fileLabel": "",
            "countLabel": "",
            "message": result.message,
            "cancelable": False,
            "resultStatus": result.status,
            "kind": "web",
            "articleId": result.article_id,
        }
        self._emit()

    def _emit(self) -> None:
        self.taskChanged.emit(json.dumps(self._state, ensure_ascii=False))


def _idle_state() -> dict:
    return {
        "active": False,
        "ownerRepo": "",
        "phase": "",
        "status": "idle",
        "progressKind": "determinate",
        "percent": 0,
        "bytesLabel": "",
        "fileLabel": "",
        "countLabel": "",
        "message": "",
        "cancelable": False,
    }
