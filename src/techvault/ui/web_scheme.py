"""Register and serve local Vite build assets under techvault://app/."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QUrl
from PySide6.QtWebEngineCore import (
    QWebEngineUrlRequestJob,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)

from techvault.readme_images.media_path import file_matches_hash, resolve_media_file
from techvault.video.paths import resolve_cover_file, resolve_preview_file
from techvault.readme_images.pool import invalidate_object
from techvault.readme_images.sniff import MAX_IMAGE_BYTES, sanitize_svg

SCHEME = "techvault"
HOST = "app"


def register_scheme() -> None:
    """Must run before QApplication / QWebEngineProfile is created."""
    scheme = QWebEngineUrlScheme(SCHEME.encode("ascii"))
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.LocalScheme
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
        | QWebEngineUrlScheme.Flag.CorsEnabled
    )
    QWebEngineUrlScheme.registerScheme(scheme)


def frontend_dist_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"


def app_index_url() -> QUrl:
    return QUrl(f"{SCHEME}://{HOST}/index.html")


class DistSchemeHandler(QWebEngineUrlSchemeHandler):
    def __init__(
        self,
        root: Path | None = None,
        parent=None,
        *,
        media_root: Path | None = None,
        archive_root_getter=None,
    ) -> None:
        super().__init__(parent)
        self._root = (root or frontend_dist_dir()).resolve()
        self._media_root = None if media_root is None else Path(media_root)
        self._archive_root_getter = archive_root_getter

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802
        url = job.requestUrl()
        if url.host() != HOST:
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        relative = url.path().lstrip("/") or "index.html"
        if relative.startswith("media/"):
            self._reply_media(job, "/" + relative)
            return
        if relative.startswith("video-preview/") or relative.startswith("video-cover/"):
            self._reply_video_image(job, relative)
            return
        if ".." in Path(relative).parts:
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        target = (self._root / relative).resolve()
        try:
            target.relative_to(self._root)
        except ValueError:
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        if not target.is_file():
            # SPA fallback for client routes.
            target = self._root / "index.html"
            if not target.is_file():
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
        data = target.read_bytes()
        mime, _ = mimetypes.guess_type(str(target))
        if not mime:
            mime = "application/octet-stream"
        if target.suffix == ".js":
            mime = "text/javascript"
        elif target.suffix == ".css":
            mime = "text/css"
        elif target.suffix == ".html":
            mime = "text/html"
        elif target.suffix == ".svg":
            mime = "image/svg+xml"
        buffer = QBuffer(parent=job)
        buffer.setData(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime.encode("ascii"), buffer)

    def _reply_video_image(self, job: QWebEngineUrlRequestJob, relative: str) -> None:
        root = self._archive_root_getter() if self._archive_root_getter is not None else None
        if root is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        path = resolve_preview_file(Path(root), relative) if relative.startswith("video-preview/") else resolve_cover_file(Path(root), relative)
        if path is None or not path.is_file() or path.stat().st_size > 2_000_000:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        try:
            data = path.read_bytes()
        except OSError:
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return
        mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png" if path.suffix.lower() == ".png" else "image/webp"
        buffer = QBuffer(parent=job)
        buffer.setData(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime.encode("ascii"), buffer)

    def _reply_media(self, job: QWebEngineUrlRequestJob, url_path: str) -> None:
        if self._media_root is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        resolved = resolve_media_file(self._media_root, url_path)
        if resolved is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        path, sha256 = resolved
        try:
            if path.stat().st_size > MAX_IMAGE_BYTES:
                job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
                return
            if not file_matches_hash(path, sha256):
                invalidate_object(self._media_root, sha256)
                job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
                return
            data = path.read_bytes()
        except OSError:
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return
        mime = _MEDIA_MIME.get(path.suffix.lower(), "application/octet-stream")
        if path.suffix.lower() == ".svg":
            sanitized = sanitize_svg(data)
            if sanitized is None:
                job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
                return
            data = sanitized
            mime = "image/svg+xml"
        buffer = QBuffer(parent=job)
        buffer.setData(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime.encode("ascii"), buffer)


_MEDIA_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}
