"""Background probe and download. The Qt GUI thread does not call yt-dlp."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from techvault.db.connection import connect
from techvault.logging_setup import get_logger
from techvault.video.errors import VideoArchiveError
from techvault.video.service import VideoArchiveService

logger = get_logger()


@dataclass
class VideoTaskResult:
    task_id: str
    status: str
    message: str
    code: str = ""
    item_id: int | None = None
    preview: dict | None = None


class VideoCoverWorker(QThread):
    """Extract one cover frame off the GUI thread."""

    ready = Signal(int)

    def __init__(self, database_path: Path, item_id: int) -> None:
        super().__init__()
        self._database_path = Path(database_path)
        self._item_id = int(item_id)

    def run(self) -> None:
        connection = connect(self._database_path)
        try:
            VideoArchiveService(connection).ensure_cover(self._item_id)
        except Exception:
            logger.exception("Video cover frame failed")
        finally:
            connection.close()
        self.ready.emit(self._item_id)


class VideoDownloadWorker(QThread):
    progress = Signal(object)
    completed = Signal(object)

    def __init__(
        self,
        database_path: Path,
        *,
        mode: str,
        url: str,
        quality: str = "auto",
        replace: bool = False,
        task_id: str | None = None,
    ) -> None:
        super().__init__()
        self._database_path = Path(database_path)
        self._mode = mode
        self._url = url
        self._quality = quality or "auto"
        self._replace = replace
        self._task_id = task_id or uuid.uuid4().hex
        self._cancel = Event()

    @property
    def task_id(self) -> str:
        return self._task_id

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        connection = connect(self._database_path)
        try:
            service = VideoArchiveService(connection)
            if self._mode == "probe":
                self.progress.emit({"taskId": self._task_id, "phase": "FETCHING_METADATA"})
                preview = service.probe(self._url, preview_token=self._task_id)
                self.completed.emit(
                    VideoTaskResult(self._task_id, "READY", "解析完成", preview=preview)
                )
                return
            item = service.download(
                self._url,
                self._quality,
                task_id=self._task_id,
                replace=self._replace,
                cancel_event=self._cancel,
                on_progress=lambda payload: self.progress.emit({"taskId": self._task_id, **payload}),
                preview_token=self._task_id,
            )
            self.completed.emit(
                VideoTaskResult(
                    self._task_id,
                    "COMPLETED",
                    item.get("message") or "视频已保存",
                    item_id=int(item["id"]),
                )
            )
        except VideoArchiveError as exc:
            status = "CANCELLED" if exc.code == "CANCELLED" else "FAILED"
            self.completed.emit(VideoTaskResult(self._task_id, status, exc.message, code=exc.code))
        except Exception:
            logger.exception("Video worker failed")
            self.completed.emit(VideoTaskResult(self._task_id, "FAILED", "下载没有完成", code="DOWNLOAD_FAILED"))
        finally:
            connection.close()
