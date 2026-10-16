"""Isolated offline MHTML viewer — no TechVault WebChannel, block remote navigation."""

from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from techvault.logging_setup import get_logger

logger = get_logger()


class _OfflineInterceptor(QWebEngineUrlRequestInterceptor):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.blocked: list[str] = []

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802
        url = info.requestUrl()
        scheme = (url.scheme() or "").lower()
        if scheme in {"http", "https", "ws", "wss", "ftp"}:
            self.blocked.append(url.toString())
            info.block(True)


class _GuardedPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, parent=None) -> None:
        super().__init__(profile, parent)
        self._allow_file = True

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool):  # noqa: N802
        scheme = (url.scheme() or "").lower()
        if scheme in {"http", "https"}:
            # External links require explicit user action via desktop services.
            if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
                QDesktopServices.openUrl(url)
            return False
        if scheme in {"file", "data", "blob", "qrc", "about"}:
            return True
        return False


class SnapshotViewerWindow(QMainWindow):
    def __init__(
        self,
        mhtml_path: Path,
        *,
        title: str = "网页快照",
        subtitle: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._path = Path(mhtml_path)
        self.setWindowTitle(f"TechVault 快照 — {title}")
        self.resize(1200, 860)
        self.setMinimumSize(800, 560)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        # Unique profile name per viewer instance avoids colliding with a prior
        # SnapshotViewerWindow still pending deleteLater beside DesktopWindow.
        self._profile = QWebEngineProfile(f"techvault-mhtml-{uuid.uuid4().hex[:10]}", self)
        self._profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        self._interceptor = _OfflineInterceptor(self)
        self._profile.setUrlRequestInterceptor(self._interceptor)

        self._page = _GuardedPage(self._profile, self)
        self.view = QWebEngineView(self)
        self.view.setPage(self._page)
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)

        self.hint = QLabel(
            subtitle
            or "离线快照预览：已拦截外部网络请求。点击页面内链接触发系统浏览器打开（需联网）。",
            self,
        )
        self.hint.setWordWrap(True)
        self.open_file_btn = QPushButton("打开本地文件", self)
        self.close_btn = QPushButton("关闭", self)
        self.status = QLabel("正在加载快照…", self)
        self.open_file_btn.clicked.connect(self._open_file)
        self.close_btn.clicked.connect(self.close)

        bar = QHBoxLayout()
        bar.addWidget(self.open_file_btn)
        bar.addWidget(self.close_btn)
        bar.addStretch(1)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.addWidget(self.hint)
        layout.addLayout(bar)
        layout.addWidget(self.view, 1)
        self.setCentralWidget(root)
        sb = QStatusBar(self)
        sb.addWidget(self.status, 1)
        self.setStatusBar(sb)

        self.view.loadFinished.connect(self._on_loaded)
        if not self._path.is_file():
            self.status.setText("快照文件不存在")
            return
        self.view.setUrl(QUrl.fromLocalFile(str(self._path.resolve())))

    @Slot(bool)
    def _on_loaded(self, ok: bool) -> None:
        blocked = len(self._interceptor.blocked)
        if ok:
            msg = "快照已离线加载"
            if blocked:
                msg += f"（已拦截 {blocked} 个外部请求）"
            self.status.setText(msg)
        else:
            self.status.setText("快照无法离线加载")
        logger.info(
            "MHTML viewer loaded ok=%s blocked=%s path=%s",
            ok,
            blocked,
            self._path.name,
        )

    def _open_file(self) -> None:
        if self._path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path.resolve())))
