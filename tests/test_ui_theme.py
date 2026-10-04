"""Phase 8 theme, Chinese chrome, and launch checks."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

from techvault.app.bootstrap import prepare_context
from techvault.ui.i18n import NAV_ARCHIVE, NAV_KNOWLEDGE, NAV_SETTINGS
from techvault.ui.main_window import MainWindow
from techvault.ui.theme import (
    SIDEBAR_SELECTED,
    SURFACE,
    TEXT_PRIMARY,
    WINDOW_BACKGROUND,
    apply_theme,
    stylesheet,
)

ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = ROOT / "main.py"


class _MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self.passwords[(service, username)]
        except KeyError as exc:
            raise PasswordDeleteError("missing") from exc


@pytest.fixture(autouse=True)
def memory_keyring():
    previous = keyring.get_keyring()
    keyring.set_keyring(_MemoryKeyring())
    try:
        yield
    finally:
        keyring.set_keyring(previous)


@pytest.fixture
def window(qtbot, tmp_path):
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "a")
    host = MainWindow(context)
    qtbot.addWidget(host)
    host.show()
    try:
        yield host
    finally:
        context.close()


def test_theme_loads_dark_tokens(qtbot) -> None:
    apply_theme()
    style = stylesheet()
    assert "@" not in style
    assert WINDOW_BACKGROUND in style
    assert SURFACE in style
    assert SIDEBAR_SELECTED in style
    assert TEXT_PRIMARY in style
    assert "#FFFFFF" not in style.upper()
    assert "#000000" not in style.upper()
    app = QApplication.instance()
    assert app is not None
    assert WINDOW_BACKGROUND in app.styleSheet()
    window_color = app.palette().color(app.palette().ColorRole.Window)
    assert window_color == QColor(WINDOW_BACKGROUND)
    assert window_color != QColor("#FFFFFF")
    assert window_color != QColor("#000000")


def test_widget_state_rules_are_shared() -> None:
    style = stylesheet()
    assert "QPushButton:hover" in style
    assert "QPushButton:pressed" in style
    assert "QPushButton:disabled" in style
    assert "QLineEdit:focus" in style
    assert "QLineEdit:disabled" in style
    assert 'QLineEdit[error="true"]' in style
    assert "QListWidget#mainNav::item:hover" in style
    assert "QListWidget#mainNav::item:selected" in style
    assert "QScrollBar::handle:vertical" in style
    assert "QScrollBar::handle:horizontal" in style


def test_pages_do_not_hardcode_colors() -> None:
    root = Path("src/techvault/ui")
    for path in root.rglob("*.py"):
        if path.name == "theme.py":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if "http" in line:
                continue
            assert "#" not in line


def test_main_window_navigation_is_chinese_and_selected(window: MainWindow) -> None:
    labels = [window.nav.item(row).text() for row in range(window.nav.count())]
    assert labels == [NAV_KNOWLEDGE, NAV_ARCHIVE, NAV_SETTINGS]
    assert "知识" in labels[0]
    assert "GitHub 项目" in labels[1]
    assert "设置" in labels[2]
    assert window.nav.currentRow() == 0
    assert window.nav.item(0).isSelected()
    window.show_section(1)
    assert window.nav.currentRow() == 1
    assert window.nav.item(1).isSelected()
    assert window.stack.currentWidget() is window.archive_page
    image = window.grab().toImage()
    corner = image.pixelColor(8, 8)
    assert corner != QColor("#FFFFFF")
    assert corner != QColor("#000000")


def test_knowledge_page_chinese_layout(window: MainWindow) -> None:
    page = window.knowledge_page
    assert page.search_edit.placeholderText().startswith("搜索")
    assert page.new_button.text() == "新建"
    assert page.save_button.text() == "保存"
    assert page.cancel_button.text() == "取消"
    assert page.delete_button.text() == "删除"
    assert page.copy_button.text() == "复制"
    assert page.favorite_box.text() == "收藏"
    assert page.category_list.item(0).text() == "全部"
    assert page.type_list.item(0).text() == "全部"
    assert page.type_list.item(1).text() == "命令"
    assert page.document_list.objectName() == "documentList"
    assert page.type_combo.itemText(0) == "命令"


def test_archive_page_chinese_layout(window: MainWindow) -> None:
    page = window.archive_page
    assert page.archive_button.text() == "添加项目"
    assert page.cancel_button.text() == "取消"
    assert page.open_offline_button.text() == "查看 README"
    assert page.open_online_button.text() == "打开 GitHub"
    assert page.open_folder_button.text() == "打开本地"
    assert page.release_label.text() == "暂无正式 Release"
    page.show_progress("Looking up repository", "source-code.zip", 1, 3)
    assert page.operation_label.text() == "正在获取仓库信息"
    assert page.file_label.text() == "source-code.zip"
    page.show_progress("Downloading", "asset.bin", 2, 3)
    assert page.operation_label.text() == "正在下载文件"
    assert page.file_label.text() == "asset.bin"


def test_settings_page_chinese_layout_hides_token(window: MainWindow) -> None:
    page = window.settings_page
    assert page.browse_button.text() == "选择目录"
    assert page.save_root_button.text() == "保存"
    assert page.save_token_button.text() == "设置令牌"
    assert page.clear_token_button.text() == "清除令牌"
    assert page.token_status.text() == "未配置"
    assert page.token_edit.echoMode() == QLineEdit.EchoMode.Password
    secret = "ghp_VISIBLE_SHOULD_NOT_APPEAR"
    page.token_edit.setText(secret)
    page.save_token()
    assert secret not in page.token_status.text()
    assert secret not in page.status_label.text()
    assert page.token_edit.text() == ""
    assert page.token_status.text() == "已配置"


def test_button_and_input_properties(window: MainWindow) -> None:
    save = window.knowledge_page.save_button
    assert save.property("role") == "primary"
    assert save.isEnabled()
    save.setEnabled(False)
    assert not save.isEnabled()
    save.setEnabled(True)
    title = window.knowledge_page.title_edit
    assert title.isEnabled()
    title.setProperty("error", True)
    assert title.property("error") is True
    title.setProperty("error", False)
    disabled = QPushButton("禁用")
    disabled.setEnabled(False)
    assert not disabled.isEnabled()


def test_main_py_launches(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["LOCALAPPDATA"] = str(tmp_path / "appdata")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["TECHVAULT_SMOKE"] = "1"
    completed = subprocess.run(
        [sys.executable, str(MAIN_PY)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    assert "Traceback" not in completed.stdout
