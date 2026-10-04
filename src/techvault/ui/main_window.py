"""Main window with knowledge, archive, and settings pages."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QListWidget, QMainWindow, QStackedWidget, QWidget

from techvault.app.bootstrap import AppContext
from techvault.ui.archive_page import ArchivePage
from techvault.ui.i18n import NAV_ARCHIVE, NAV_KNOWLEDGE, NAV_SETTINGS
from techvault.ui.knowledge_page import KnowledgePage
from techvault.ui.settings_page import SettingsPage
from techvault.ui.theme import apply_theme


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.setWindowTitle("TechVault")
        self.resize(1440, 900)
        self.setMinimumSize(960, 640)
        self.nav = QListWidget()
        self.nav.setObjectName("mainNav")
        self.nav.setFixedWidth(120)
        self.nav.setSpacing(1)
        self.nav.addItem(NAV_KNOWLEDGE)
        self.nav.addItem(NAV_ARCHIVE)
        self.nav.addItem(NAV_SETTINGS)
        self.stack = QStackedWidget()
        self.stack.setObjectName("mainStack")
        self.knowledge_page = KnowledgePage(context.documents)
        self.archive_page = ArchivePage(context.database_path, context.connection)
        self.settings_page = SettingsPage(context.settings)
        self.stack.addWidget(self.knowledge_page)
        self.stack.addWidget(self.archive_page)
        self.stack.addWidget(self.settings_page)
        holder = QWidget()
        holder.setObjectName("windowBody")
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.nav, 0)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(holder)
        self.statusBar().showMessage("就绪")
        self.nav.currentRowChanged.connect(self._show_section)
        self.nav.setCurrentRow(0)
        apply_theme()

    def closeEvent(self, event) -> None:
        self.archive_page.cancel_archive()
        worker = self.archive_page._worker
        if worker is not None and worker.isRunning():
            worker.wait(3000)
        super().closeEvent(event)

    def show_section(self, index: int) -> None:
        self.nav.setCurrentRow(index)

    def _show_section(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.statusBar().showMessage("就绪")
