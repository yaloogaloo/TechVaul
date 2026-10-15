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
    def __init__(self, root: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self._root = (root or frontend_dist_dir()).resolve()

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802
        url = job.requestUrl()
        if url.host() != HOST:
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        relative = url.path().lstrip("/") or "index.html"
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
