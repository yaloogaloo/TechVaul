"""Run an archive on a worker thread. This object does not touch widgets."""

from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from techvault.app.archive_service import ArchiveResult, ArchiveService
from techvault.app.messages import user_message
from techvault.db.connection import connect
from techvault.logging_setup import get_logger

logger = get_logger()


class ArchiveWorker(QThread):
    progress = Signal(str, str, int, int)
    detail = Signal(object)
    completed = Signal(object)

    def __init__(
        self,
        database_path: Path,
        url: str = "",
        *,
        repository_id: int | None = None,
        asset: str | None = None,
        mode: str = "archive",
    ) -> None:
        super().__init__()
        self._database_path = Path(database_path)
        self._url = url
        self._repository_id = repository_id
        self._asset = asset
        self._mode = mode
        self._cancel = Event()
        self.thread_marker: QThread | None = None

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        self.thread_marker = QThread.currentThread()
        connection = connect(self._database_path)
        try:
            service = ArchiveService(connection)
            if self._mode == "readme-images" and self._repository_id is not None:
                result = service.backfill_readme_images(
                    self._repository_id,
                    cancel_event=self._cancel,
                    on_progress=self._emit_progress,
                )
            elif self._asset and self._repository_id is not None:
                result = service.retry_failed_asset(
                    self._repository_id,
                    self._asset,
                    cancel_event=self._cancel,
                    on_progress=self._emit_progress,
                    on_detail=self.detail.emit,
                )
            else:
                result = service.archive_repository(
                    self._url,
                    cancel_event=self._cancel,
                    on_progress=self._emit_progress,
                    on_detail=self.detail.emit,
                )
            self.completed.emit(result)
        except Exception:
            logger.exception("Archive worker failed")
            self.completed.emit(ArchiveResult("FAILED", user_message("FAILED")))
        finally:
            connection.close()

    def _emit_progress(self, operation: str, current_file: str, completed: int, total: int) -> None:
        self.progress.emit(operation, current_file, int(completed), int(total))
