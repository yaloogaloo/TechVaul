"""Archive folder and GitHub token settings."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from techvault.app.settings import SettingsService
from techvault.errors import DatabaseError, KeyringUnavailableError, UnsafePathError
from techvault.ui.i18n import token_status_text
from techvault.ui.theme import LG, MD, SM


class SettingsPage(QWidget):
    def __init__(self, settings: SettingsService) -> None:
        super().__init__()
        self.settings = settings
        self.setObjectName("settingsPage")
        title = QLabel("设置")
        title.setObjectName("pageTitle")

        self.root_edit = QLineEdit()
        self.root_edit.setObjectName("archiveRoot")
        current = settings.get_archive_root()
        if current is not None:
            self.root_edit.setText(str(current))
        self.browse_button = QPushButton("选择目录")
        self.browse_button.setObjectName("browseArchiveRoot")
        self.save_root_button = QPushButton("保存")
        self.save_root_button.setObjectName("saveArchiveRoot")
        self.save_root_button.setProperty("role", "primary")
        self.token_edit = QLineEdit()
        self.token_edit.setObjectName("githubToken")
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("不会显示已保存的令牌")
        self.save_token_button = QPushButton("设置令牌")
        self.save_token_button.setObjectName("saveGithubToken")
        self.save_token_button.setProperty("role", "primary")
        self.clear_token_button = QPushButton("清除令牌")
        self.clear_token_button.setObjectName("clearGithubToken")
        self.clear_token_button.setProperty("role", "danger")
        self.token_status = QLabel(token_status_text(settings.token_status()))
        self.token_status.setObjectName("tokenStatus")
        self.status_label = QLabel("")
        self.status_label.setObjectName("settingsStatus")
        self.status_label.setWordWrap(True)

        root_row = QHBoxLayout()
        root_row.setSpacing(SM)
        root_row.addWidget(self.root_edit, 1)
        root_row.addWidget(self.browse_button)
        root_row.addWidget(self.save_root_button)
        token_row = QHBoxLayout()
        token_row.setSpacing(SM)
        token_row.addWidget(self.token_edit, 1)
        token_row.addWidget(self.save_token_button)
        token_row.addWidget(self.clear_token_button)
        status_row = QHBoxLayout()
        status_row.setSpacing(SM)
        status_name = QLabel("状态")
        status_name.setObjectName("sectionLabel")
        status_row.addWidget(status_name)
        status_row.addWidget(self.token_status)
        status_row.addStretch(1)

        storage = self._group(
            "存储",
            ("项目资料位置", root_row),
        )
        github = self._group(
            "GitHub",
            ("访问令牌", token_row),
            (None, status_row),
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(LG, LG, LG, LG)
        layout.setSpacing(LG)
        about = self._group("关于", (None, QLabel("TechVault\n个人技术资产\n版本 0.1.0")))
        layout.addWidget(title)
        layout.addWidget(storage)
        layout.addWidget(github)
        layout.addWidget(about)
        layout.addWidget(self.status_label)
        layout.addStretch(1)

        self.browse_button.clicked.connect(self.browse_root)
        self.save_root_button.clicked.connect(self.save_root)
        self.save_token_button.clicked.connect(self.save_token)
        self.clear_token_button.clicked.connect(self.clear_token)

    def browse_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "选择目录", self.root_edit.text())
        if selected:
            self.root_edit.setText(selected)

    def save_root(self) -> None:
        try:
            stored = self.settings.set_archive_root(self.root_edit.text())
        except (UnsafePathError, DatabaseError, OSError):
            self.status_label.setText("无法保存这个目录。")
            return
        self.root_edit.setText(str(stored))
        self.status_label.setText("目录已保存。")

    def save_token(self) -> None:
        token = self.token_edit.text()
        self.token_edit.clear()
        try:
            status = self.settings.save_github_token(token)
        except ValueError:
            self.token_status.setText(token_status_text(self.settings.token_status()))
            self.status_label.setText("请先输入令牌。")
            return
        except KeyringUnavailableError:
            self.token_status.setText(token_status_text("Unavailable"))
            self.status_label.setText("系统密钥环不可用。")
            return
        self.token_status.setText(token_status_text(status))
        self.status_label.setText("GitHub 令牌已保存。")

    def clear_token(self) -> None:
        self.token_edit.clear()
        try:
            status = self.settings.clear_github_token()
        except KeyringUnavailableError:
            self.token_status.setText(token_status_text("Unavailable"))
            self.status_label.setText("系统密钥环不可用。")
            return
        self.token_status.setText(token_status_text(status))
        self.status_label.setText("GitHub 令牌已清除。")

    def _group(self, title: str, *rows: tuple[str | None, object]) -> QFrame:
        frame = QFrame()
        frame.setObjectName("settingsSection")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(LG, LG, LG, LG)
        layout.setSpacing(MD)
        heading = QLabel(title)
        heading.setObjectName("sectionLabel")
        layout.addWidget(heading)
        for label, widget in rows:
            if label:
                name = QLabel(label)
                layout.addWidget(name)
            if isinstance(widget, QHBoxLayout):
                layout.addLayout(widget)
            else:
                layout.addWidget(widget)
        return frame
