"""Desktop entry point."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from techvault.app.bootstrap import default_database_path, open_window_context
from techvault.logging_setup import configure_logging
from techvault.ui.i18n import DATABASE_INIT_DIALOG
from techvault.ui.main_window import MainWindow
from techvault.ui.theme import apply_theme


def main(argv: list[str] | None = None) -> int:
    QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    apply_theme()
    context, error = open_window_context(default_database_path())
    if context is None:
        QMessageBox.critical(None, "TechVault", DATABASE_INIT_DIALOG)
        return 1
    configure_logging(context.database_path.parent / "logs")
    window = MainWindow(context)
    window.show()
    app = QApplication.instance()
    assert app is not None
    if os.environ.get("TECHVAULT_SMOKE") == "1":
        QTimer.singleShot(0, app.quit)
    return app.exec()
