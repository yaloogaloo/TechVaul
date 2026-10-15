"""Settings bridge — archive root and GitHub token via SettingsService."""

from __future__ import annotations

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QFileDialog

from techvault import __version__
from techvault.app.settings import SettingsService
from techvault.errors import KeyringUnavailableError, UnsafePathError
from techvault.ui.bridge._json import err, ok
from techvault.ui.i18n import token_status_text


class SettingsBridge(QObject):
    def __init__(self, settings: SettingsService, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings

    @Slot(result=str)
    def getStatus(self) -> str:  # noqa: N802
        root = self._settings.get_archive_root()
        return ok(
            {
                "ready": True,
                "archiveRoot": None if root is None else str(root),
                "tokenStatus": token_status_text(self._settings.token_status()),
                "tokenStatusCode": self._settings.token_status(),
                "version": __version__,
                "appName": "TechVault",
                "migrationNote": "修改归档目录不会自动迁移已有存档。",
            }
        )

    @Slot(result=str)
    def chooseArchiveRoot(self) -> str:  # noqa: N802
        parent = self.parent()
        current = self._settings.get_archive_root()
        start = str(current) if current is not None else ""
        path = QFileDialog.getExistingDirectory(
            parent,
            "选择本地归档目录",
            start,
        )
        if not path:
            return ok({"cancelled": True, "path": None})
        return ok({"cancelled": False, "path": path})

    @Slot(str, result=str)
    def setArchiveRoot(self, path: str) -> str:  # noqa: N802
        try:
            root = self._settings.set_archive_root(path)
        except UnsafePathError as exc:
            return err("INVALID_PATH", str(exc) or "路径无效")
        except Exception as exc:  # noqa: BLE001
            return err("SAVE_FAILED", str(exc) or "保存失败")
        return ok(
            {
                "archiveRoot": str(root),
                "migrationNote": "修改归档目录不会自动迁移已有存档。",
            }
        )

    @Slot(str, result=str)
    def saveToken(self, token: str) -> str:  # noqa: N802
        try:
            status = self._settings.save_github_token(token)
        except KeyringUnavailableError:
            return err("KEYRING", "凭据存储不可用，无法保存 Token")
        except Exception as exc:  # noqa: BLE001
            return err("SAVE_FAILED", str(exc) or "保存 Token 失败")
        return ok(
            {
                "tokenStatus": token_status_text(status),
                "tokenStatusCode": status,
            }
        )

    @Slot(result=str)
    def clearToken(self) -> str:  # noqa: N802
        try:
            status = self._settings.clear_github_token()
        except KeyringUnavailableError:
            return err("KEYRING", "凭据存储不可用，无法清除 Token")
        except Exception as exc:  # noqa: BLE001
            return err("CLEAR_FAILED", str(exc) or "清除 Token 失败")
        return ok(
            {
                "tokenStatus": token_status_text(status),
                "tokenStatusCode": status,
            }
        )
