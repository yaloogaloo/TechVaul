"""Background thread for web archive capture."""

from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from techvault.db.connection import connect
from techvault.logging_setup import get_logger
from techvault.web_archive.models import ArchiveStatus, CaptureResult
from techvault.web_archive.service import WebArchiveService

logger = get_logger()


class WebArchiveWorker(QThread):
    progress = Signal(str, str, int, int)
    completed = Signal(object)

    def __init__(self, database_path: Path, url: str) -> None:
        super().__init__()
        self._database_path = Path(database_path)
        self._url = url
        self._cancel = Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        connection = connect(self._database_path)
        try:
            service = WebArchiveService(connection)
            result = service.archive(
                self._url,
                cancel_event=self._cancel,
                on_progress=self._emit_progress,
            )
            self.completed.emit(result)
        except Exception:
            logger.exception("Web archive worker failed")
            self.completed.emit(
                CaptureResult(ArchiveStatus.FAILED, "网页归档失败", failure_code="WORKER")
            )
        finally:
            connection.close()

    def _emit_progress(self, operation: str, current_file: str, completed: int, total: int) -> None:
        self.progress.emit(operation, current_file, int(completed), int(total))
