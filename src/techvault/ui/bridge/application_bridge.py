"""Shell-level bridge: navigation hints, app metadata, and close coordination."""

from __future__ import annotations

import os

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal, Slot

from techvault import __version__
from techvault.ui.bridge._json import ok


class ApplicationBridge(QObject):
    navigateRequested = Signal(str)
    toastRequested = Signal(str, str)  # level, message
    closeCheckRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mode = "shell-demo" if os.environ.get("TECHVAULT_SLATE_SHELL_DEMO") == "1" else "production"
        self._close_loop: QEventLoop | None = None
        self._close_allowed = True
        self._guard_registered = False

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    @Slot(result=str)
    def getBootstrap(self) -> str:  # noqa: N802
        return ok(
            {
                "appName": "TechVault",
                "version": __version__,
                "designSystem": "TechVault Slate",
                "mode": self._mode,
                "workspaces": ["github", "web", "knowledge", "settings"],
                "knowledgeReady": True,
                "webArchiveReady": True,
                "settingsReady": True,
            }
        )

    @Slot(str, result=str)
    def setWorkspace(self, workspace: str) -> str:  # noqa: N802
        self.navigateRequested.emit(workspace)
        return ok({"workspace": workspace})

    @Slot(result=str)
    def registerCloseGuard(self) -> str:  # noqa: N802
        self._guard_registered = True
        return ok({"registered": True})

    @Slot(str, result=str)
    def answerClose(self, decision: str) -> str:  # noqa: N802
        self._close_allowed = decision == "allow"
        loop = self._close_loop
        self._close_loop = None
        if loop is not None and loop.isRunning():
            loop.quit()
        return ok({"decision": decision})

    def request_close_permission(self) -> bool:
        """Block until React answers. Deny (keep open) if guard never answers."""
        if not self._guard_registered:
            return True
        self._close_allowed = False
        self._close_loop = QEventLoop()
        self.closeCheckRequested.emit()
        # Keep window open if UI never answers — never force-discard drafts.
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self.answerClose("deny"))
        timer.start(60_000)
        self._close_loop.exec()
        timer.stop()
        return self._close_allowed
