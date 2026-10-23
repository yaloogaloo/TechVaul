"""WebChannel API for the video archive. Downloads run on TaskBridge's worker."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from techvault.ui.video_worker import VideoCoverWorker
from techvault.video.errors import VideoArchiveError
from techvault.video.service import VideoArchiveService
from techvault.ui.bridge._json import err, ok


class VideoBridge(QObject):
    coverReady = Signal(int)

    def __init__(self, connection, parent=None) -> None:
        super().__init__(parent)
        self._service = VideoArchiveService(connection)
        self._database_path = _database_path(connection)
        self._cover_worker: VideoCoverWorker | None = None

    @Slot(result=str)
    def dependencies(self) -> str:  # noqa: N802
        try:
            return ok(self._service.dependencies())
        except VideoArchiveError as exc:
            return err(exc.code, exc.message)

    @Slot(result=str)
    def listVideos(self) -> str:  # noqa: N802
        try:
            items = self._service.list_items()
        except VideoArchiveError as exc:
            return err(exc.code, exc.message)
        except Exception:
            return err("VIDEO_LIST_FAILED", "无法读取视频列表")
        return ok({"items": items})

    @Slot(int, result=str)
    def getVideo(self, item_id: int) -> str:  # noqa: N802
        try:
            item = self._service.get_item(int(item_id))
        except VideoArchiveError as exc:
            return err(exc.code, exc.message)
        if item is None:
            return err("NOT_FOUND", "视频不存在")
        return ok(item)

    @Slot(int, result=str)
    def ensureCover(self, item_id: int) -> str:  # noqa: N802
        try:
            if self._service.cover_is_ready(int(item_id)):
                item = self._service.get_item(int(item_id))
                return ok(item if item is not None else {"id": int(item_id)})
        except VideoArchiveError as exc:
            return err(exc.code, exc.message)
        if self._cover_worker is not None and self._cover_worker.isRunning():
            return ok({"pending": True, "id": int(item_id)})
        worker = VideoCoverWorker(self._database_path, int(item_id))
        worker.ready.connect(self.coverReady)
        self._cover_worker = worker
        worker.start()
        return ok({"pending": True, "id": int(item_id)})

    @Slot(int, result=str)
    def deleteVideo(self, item_id: int) -> str:  # noqa: N802
        try:
            self._service.delete_item(int(item_id))
        except VideoArchiveError as exc:
            return err(exc.code, exc.message)
        return ok({"id": int(item_id)})

    @Slot(int, result=str)
    def openVideo(self, item_id: int) -> str:  # noqa: N802
        try:
            path = self._service.local_file(int(item_id))
        except VideoArchiveError as exc:
            return err(exc.code, exc.message)
        _open_path(path)
        return ok({"path": str(path)})

    @Slot(int, result=str)
    def openVideoFolder(self, item_id: int) -> str:  # noqa: N802
        try:
            path = self._service.local_dir(int(item_id))
        except VideoArchiveError as exc:
            return err(exc.code, exc.message)
        _open_path(path)
        return ok({"path": str(path)})

    @Slot(int, result=str)
    def openSource(self, item_id: int) -> str:  # noqa: N802
        try:
            item = self._service.get_item(int(item_id))
        except VideoArchiveError as exc:
            return err(exc.code, exc.message)
        if item is None:
            return err("NOT_FOUND", "视频不存在")
        url = str(item.get("canonicalUrl") or "")
        if not url.startswith("https://"):
            return err("UNSAFE_URL", "来源地址无效")
        QDesktopServices.openUrl(QUrl(url))
        return ok({"url": url})

    @Slot(int, result=str)
    def copySource(self, item_id: int) -> str:  # noqa: N802
        try:
            item = self._service.get_item(int(item_id))
        except VideoArchiveError as exc:
            return err(exc.code, exc.message)
        if item is None:
            return err("NOT_FOUND", "视频不存在")
        url = str(item.get("canonicalUrl") or "")
        if not url.startswith("https://"):
            return err("UNSAFE_URL", "来源地址无效")
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return err("CLIPBOARD", "无法访问剪贴板")
        try:
            clipboard.setText(url)
        except Exception:
            return err("CLIPBOARD", "复制失败")
        return ok({"copied": True})


def _database_path(connection) -> Path:
    rows = connection.execute("PRAGMA database_list").fetchall()
    for row in rows:
        file_name = row["file"] if hasattr(row, "keys") else row[2]
        if file_name:
            return Path(file_name)
    raise VideoArchiveError("NO_DATABASE", "找不到视频数据库文件")


def _open_path(path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.run([opener, str(path)], check=False)
