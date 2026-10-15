"""Desktop entry point — TechVault Slate (WebEngine + React)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from techvault.app.bootstrap import default_database_path, open_window_context
from techvault.logging_setup import configure_logging
from techvault.ui.i18n import DATABASE_INIT_DIALOG
from techvault.ui.web_scheme import register_scheme


def _database_path() -> Path:
    override = os.environ.get("TECHVAULT_DATABASE", "").strip()
    if override:
        return Path(override)
    return default_database_path()


def _archive_root() -> Path | None:
    override = os.environ.get("TECHVAULT_ARCHIVE_ROOT", "").strip()
    if override:
        return Path(override)
    return None


def main(argv: list[str] | None = None) -> int:
    # Scheme registration must happen before QApplication / WebEngine init.
    register_scheme()
    args = argv if argv is not None else sys.argv
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    QApplication.instance() or QApplication(args)

    from techvault.ui.desktop_window import DesktopWindow

    context, error = open_window_context(_database_path(), archive_root=_archive_root())
    if context is None:
        QMessageBox.critical(None, "TechVault", DATABASE_INIT_DIALOG)
        return 1
    configure_logging(context.database_path.parent / "logs")
    window = DesktopWindow(context)
    window.show()
    app = QApplication.instance()
    assert app is not None
    if os.environ.get("TECHVAULT_SMOKE") == "1":
        QTimer.singleShot(0, app.quit)
    return app.exec()
