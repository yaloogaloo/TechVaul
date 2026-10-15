"""TechVault Slate desktop shell hosted in Qt WebEngine."""

from __future__ import annotations

import os

from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow

from techvault.app.bootstrap import AppContext
from techvault.ui.bridge import (
    ApplicationBridge,
    GitHubBridge,
    KnowledgeBridge,
    SettingsBridge,
    TaskBridge,
    WebArchiveBridge,
)
from techvault.ui.web_scheme import DistSchemeHandler, app_index_url, frontend_dist_dir
from techvault.web_archive.service import WebArchiveService


class DesktopWindow(QMainWindow):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.setWindowTitle("TechVault")
        self.resize(1440, 900)
        self.setMinimumSize(960, 640)

        self.application = ApplicationBridge(self)
        self.github = GitHubBridge(context.connection, context.database_path, self)
        self.knowledge = KnowledgeBridge(context.documents, self)
        self.settings = SettingsBridge(context.settings, self)
        self.tasks = TaskBridge(context.database_path, self)
        self.webArchive = WebArchiveBridge(WebArchiveService(context.connection), self)

        self.view = QWebEngineView(self)
        profile = QWebEngineProfile.defaultProfile()
        self._handler = DistSchemeHandler(frontend_dist_dir(), self)
        profile.installUrlSchemeHandler(b"techvault", self._handler)

        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ErrorPageEnabled, True)

        channel = QWebChannel(self.view.page())
        channel.registerObject("application", self.application)
        channel.registerObject("github", self.github)
        channel.registerObject("knowledge", self.knowledge)
        channel.registerObject("settings", self.settings)
        channel.registerObject("tasks", self.tasks)
        channel.registerObject("webArchive", self.webArchive)
        self.view.page().setWebChannel(channel)

        self.setCentralWidget(self.view)
        self.statusBar().hide()

        dist = frontend_dist_dir()
        if not (dist / "index.html").is_file():
            self.view.setHtml(
                "<html><body style='background:#0D1118;color:#EDF3FA;font-family:sans-serif;padding:48px'>"
                "<h1>前端尚未构建</h1>"
                "<p>请在仓库根目录执行：<code>cd frontend && npm ci && npm run build</code></p>"
                f"<p>期望路径：{dist}</p></body></html>"
            )
        else:
            self.view.load(app_index_url())

        if os.environ.get("TECHVAULT_SLATE_CAPTURE") == "1":
            # Capture helper injects after load; see tools/slate_capture.py
            pass

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self.application.request_close_permission():
            event.ignore()
            return
        self.tasks.shutdown()
        super().closeEvent(event)
