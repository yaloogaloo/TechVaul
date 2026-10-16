"""Async MHTML save via QWebEnginePage.save + DownloadRequest completion.

Calling page.save() alone is NOT success — wait for DownloadCompleted and validate file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
)

from techvault.logging_setup import get_logger
from techvault.web_archive.mhtml import (
    SNAPSHOT_CANCELLED,
    SNAPSHOT_COMPLETE,
    SNAPSHOT_FAILED,
    SNAPSHOT_SAVING,
    validate_mhtml_file,
)

logger = get_logger()


@dataclass
class MhtmlSaveOutcome:
    status: str
    message: str
    staging_path: Path | None = None
    bytes_received: int = 0
    interrupt_reason: str | None = None
    download_id: int | None = None


class MhtmlSaveController(QObject):
    """One-shot save-page download bound to a profile/page pair."""

    finished = Signal(object)  # MhtmlSaveOutcome
    phaseChanged = Signal(str)

    def __init__(
        self,
        profile: QWebEngineProfile,
        page: QWebEnginePage,
        staging_dir: Path,
        *,
        timeout_ms: int = 90_000,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._profile = profile
        self._page = page
        self._staging_dir = Path(staging_dir)
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        self._staging_path = self._staging_dir / "snapshot.mhtml"
        self._timeout_ms = max(5_000, int(timeout_ms))
        self._request: QWebEngineDownloadRequest | None = None
        self._done = False
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._on_timeout)

    def start(self) -> None:
        if self._done:
            return
        if self._staging_path.exists():
            try:
                self._staging_path.unlink()
            except OSError:
                pass
        self.phaseChanged.emit("正在生成网页快照……")
        self._profile.downloadRequested.connect(self._on_download_requested)
        self._timeout.start(self._timeout_ms)
        try:
            fmt = QWebEngineDownloadRequest.SavePageFormat.MimeHtmlSaveFormat
            self._page.save(str(self._staging_path), fmt)
            logger.info("MHTML save requested staging=%s", self._staging_path)
        except Exception as exc:
            logger.exception("MHTML page.save failed")
            self._finish(
                MhtmlSaveOutcome(
                    SNAPSHOT_FAILED,
                    f"无法启动网页快照保存：{type(exc).__name__}",
                )
            )

    def cancel(self) -> None:
        if self._request is not None:
            try:
                self._request.cancel()
            except RuntimeError:
                pass
        self._finish(
            MhtmlSaveOutcome(SNAPSHOT_CANCELLED, "网页快照保存已取消", staging_path=self._staging_path)
        )

    @Slot(QWebEngineDownloadRequest)
    def _on_download_requested(self, request: QWebEngineDownloadRequest) -> None:
        if self._done:
            return
        # Only handle save-page downloads from our explicit page.save() call.
        if not request.isSavePageDownload():
            return
        if self._request is not None:
            return
        self._request = request
        try:
            request.setDownloadDirectory(str(self._staging_dir))
            request.setDownloadFileName(self._staging_path.name)
            if hasattr(request, "setSavePageFormat"):
                request.setSavePageFormat(
                    QWebEngineDownloadRequest.SavePageFormat.MimeHtmlSaveFormat
                )
        except RuntimeError:
            pass
        request.stateChanged.connect(self._on_state_changed)
        request.isFinishedChanged.connect(self._on_finished_changed)
        request.accept()
        self.phaseChanged.emit("正在生成网页快照……")
        logger.info(
            "MHTML download accepted id=%s format=%s",
            request.id(),
            request.savePageFormat(),
        )

    @Slot()
    def _on_state_changed(self) -> None:
        if self._done or self._request is None:
            return
        state = self._request.state()
        if state == QWebEngineDownloadRequest.DownloadState.DownloadInProgress:
            self.phaseChanged.emit(
                f"正在生成网页快照…（已接收 {self._request.receivedBytes()} 字节）"
            )
        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            self._complete_from_request()
        elif state == QWebEngineDownloadRequest.DownloadState.DownloadCancelled:
            self._finish(
                MhtmlSaveOutcome(
                    SNAPSHOT_CANCELLED,
                    "网页快照保存已取消",
                    staging_path=self._staging_path,
                    download_id=self._request.id(),
                )
            )
        elif state == QWebEngineDownloadRequest.DownloadState.DownloadInterrupted:
            reason = ""
            try:
                reason = self._request.interruptReasonString() or str(self._request.interruptReason())
            except RuntimeError:
                reason = "interrupted"
            self._finish(
                MhtmlSaveOutcome(
                    SNAPSHOT_FAILED,
                    f"网页保存中断：{reason}",
                    staging_path=self._staging_path,
                    bytes_received=int(self._request.receivedBytes() or 0),
                    interrupt_reason=reason,
                    download_id=self._request.id(),
                )
            )

    @Slot()
    def _on_finished_changed(self) -> None:
        if self._done or self._request is None:
            return
        if self._request.isFinished():
            state = self._request.state()
            if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                self._complete_from_request()

    def _complete_from_request(self) -> None:
        if self._done:
            return
        # Prefer the path we configured; fall back to download directory+name.
        path = self._staging_path
        if self._request is not None:
            try:
                alt = Path(self._request.downloadDirectory()) / self._request.downloadFileName()
                if alt.is_file():
                    path = alt
            except RuntimeError:
                pass
        validation = validate_mhtml_file(path)
        if not validation.ok:
            self._finish(
                MhtmlSaveOutcome(
                    SNAPSHOT_FAILED,
                    validation.message or "快照文件无效",
                    staging_path=path,
                    bytes_received=validation.size,
                    download_id=self._request.id() if self._request else None,
                )
            )
            return
        self._finish(
            MhtmlSaveOutcome(
                SNAPSHOT_COMPLETE,
                "网页快照文件已生成",
                staging_path=path,
                bytes_received=validation.size,
                download_id=self._request.id() if self._request else None,
            )
        )

    def _on_timeout(self) -> None:
        if self._done:
            return
        if self._request is not None:
            try:
                self._request.cancel()
            except RuntimeError:
                pass
        self._finish(
            MhtmlSaveOutcome(
                SNAPSHOT_FAILED,
                "网页快照保存超时",
                staging_path=self._staging_path,
            )
        )

    def _finish(self, outcome: MhtmlSaveOutcome) -> None:
        if self._done:
            return
        self._done = True
        self._timeout.stop()
        try:
            self._profile.downloadRequested.disconnect(self._on_download_requested)
        except (RuntimeError, TypeError):
            pass
        # Keep status token for diagnostics; SAVING is transitional only.
        if outcome.status == SNAPSHOT_SAVING:
            outcome = MhtmlSaveOutcome(
                SNAPSHOT_FAILED,
                outcome.message or "快照未完成",
                staging_path=outcome.staging_path,
                bytes_received=outcome.bytes_received,
                interrupt_reason=outcome.interrupt_reason,
                download_id=outcome.download_id,
            )
        self.finished.emit(outcome)
