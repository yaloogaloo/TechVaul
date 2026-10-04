"""Shared dark theme. Pages use these tokens instead of their own colors."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

WINDOW_BACKGROUND = "#0F1115"
SURFACE = "#151922"
SURFACE_ELEVATED = "#1B202B"
SURFACE_HOVER = "#242A36"
SURFACE_SELECTED = "#2C3544"

BORDER = "#2A3040"
BORDER_SUBTLE = "#232838"

TEXT_PRIMARY = "#E6EAF0"
TEXT_SECONDARY = "#A7AFBD"
TEXT_MUTED = "#737C8C"
TEXT_DISABLED = "#5C6573"
TEXT_ON_ACCENT = "#F4F7FB"

ACCENT = "#6E93B5"
ACCENT_HOVER = "#81A6C6"

SUCCESS = "#7DCEA0"
WARNING = "#D4A656"
ERROR = "#D4766A"
INFO = "#7AA2C4"

INPUT_BACKGROUND = "#12161E"
INPUT_BORDER = "#31384A"
INPUT_FOCUS = "#6E93B5"

SIDEBAR_BACKGROUND = "#12151C"
SIDEBAR_SELECTED = "#2A3544"

XS = 4
SM = 8
MD = 12
LG = 16
XL = 24
XXL = 32

RADIUS = 8

MARKDOWN_CSS = """
body { color: #E6EAF0; font-size: 14px; }
h1 { font-size: 22px; color: #F4F7FB; }
h2 { font-size: 18px; color: #F4F7FB; }
h3 { font-size: 15px; color: #E6EAF0; }
p, li { color: #E6EAF0; }
a { color: #81A6C6; }
code, pre { background-color: #1B202B; color: #E6EAF0; }
blockquote { color: #A7AFBD; }
td, th { padding: 4px 8px; color: #E6EAF0; }
"""

_TOKENS = {
    "WINDOW": WINDOW_BACKGROUND,
    "SURFACE": SURFACE,
    "ELEVATED": SURFACE_ELEVATED,
    "HOVER": SURFACE_HOVER,
    "SELECTED": SURFACE_SELECTED,
    "BORDER": BORDER,
    "BORDER_SUBTLE": BORDER_SUBTLE,
    "TEXT": TEXT_PRIMARY,
    "TEXT_SECONDARY": TEXT_SECONDARY,
    "MUTED": TEXT_MUTED,
    "DISABLED": TEXT_DISABLED,
    "ON_ACCENT": TEXT_ON_ACCENT,
    "ACCENT": ACCENT,
    "ACCENT_HOVER": ACCENT_HOVER,
    "SUCCESS": SUCCESS,
    "WARNING": WARNING,
    "ERROR": ERROR,
    "INFO": INFO,
    "INPUT": INPUT_BACKGROUND,
    "INPUT_BORDER": INPUT_BORDER,
    "FOCUS": INPUT_FOCUS,
    "SIDEBAR": SIDEBAR_BACKGROUND,
    "SIDEBAR_SELECTED": SIDEBAR_SELECTED,
    "RADIUS": str(RADIUS),
}

_QSS = """
QWidget {
    background: @WINDOW@;
    color: @TEXT@;
    font-family: "Microsoft YaHei UI";
}
QMainWindow, QDialog, QMessageBox {
    background: @WINDOW@;
    color: @TEXT@;
}
QWidget#knowledgePage, QWidget#archivePage, QWidget#settingsPage {
    background: @WINDOW@;
    color: @TEXT@;
}
QLabel {
    color: @TEXT@;
    background: transparent;
}
QLabel#pageTitle {
    color: @TEXT@;
    font-size: 15px;
    font-weight: 600;
}
QLabel#sectionLabel {
    color: @TEXT_SECONDARY@;
    font-size: 12px;
}
QLabel[tone="muted"] { color: @MUTED@; }
QLabel[tone="success"] { color: @SUCCESS@; font-weight: 600; }
QLabel[tone="warning"] { color: @WARNING@; font-weight: 600; }
QLabel[tone="error"] { color: @ERROR@; font-weight: 600; }
QFrame#surface {
    background: @ELEVATED@;
    border: 1px solid @BORDER@;
    border-radius: @RADIUS@px;
}
QFrame#sidebar {
    background: @SIDEBAR@;
    border-right: 1px solid @BORDER@;
}
QStatusBar {
    background: @SURFACE@;
    color: @TEXT_SECONDARY@;
    border-top: 1px solid @BORDER@;
}
QListWidget#mainNav {
    background: @SIDEBAR@;
    color: @TEXT_SECONDARY@;
    border: none;
    outline: none;
    padding: 8px;
    font-size: 13px;
}
QListWidget#mainNav::item {
    color: @TEXT_SECONDARY@;
    background: transparent;
    border-radius: 4px;
    padding: 6px 10px;
    margin: 1px 0;
}
QListWidget#mainNav::item:hover {
    background: @HOVER@;
    color: @TEXT@;
}
QListWidget#mainNav::item:selected {
    background: @SIDEBAR_SELECTED@;
    color: @TEXT@;
}
QListWidget#categoryList, QListWidget#typeList, QListWidget#repositoryCategoryRail, QListWidget#documentList {
    background: transparent;
    color: @TEXT@;
    border: none;
    outline: none;
    padding: 0;
}
QListWidget#categoryList::item, QListWidget#typeList::item, QListWidget#repositoryCategoryRail::item {
    color: @TEXT_SECONDARY@;
    border-radius: 4px;
    padding: 6px 8px;
}
QListWidget#categoryList::item:hover, QListWidget#typeList::item:hover, QListWidget#repositoryCategoryRail::item:hover {
    background: @HOVER@;
    color: @TEXT@;
}
QListWidget#categoryList::item:selected, QListWidget#typeList::item:selected, QListWidget#repositoryCategoryRail::item:selected {
    background: @SELECTED@;
    color: @TEXT@;
}
QListWidget#repositoryList, QListWidget#documentList {
    background: transparent;
    color: @TEXT@;
    border: none;
    outline: none;
    padding: 0;
}
QListWidget#repositoryList::item, QListWidget#documentList::item {
    color: @TEXT@;
    border-radius: 4px;
    padding: 6px 8px;
    margin: 1px 0;
}
QListWidget#repositoryList::item:hover, QListWidget#documentList::item:hover {
    background: @HOVER@;
}
QListWidget#repositoryList::item:selected, QListWidget#documentList::item:selected {
    background: @SELECTED@;
    color: @TEXT@;
}
QLabel#readTitle {
    font-size: 18px;
    font-weight: 600;
}
QLabel#metaLine {
    color: @MUTED@;
    font-size: 12px;
}
QScrollArea, QTextBrowser#repositoryDetail, QTextBrowser#markdownView {
    background: transparent;
    border: none;
    color: @TEXT@;
    font-size: 14px;
}
QPlainTextEdit#contentEdit {
    font-family: "Cascadia Mono", Consolas, "Microsoft YaHei UI";
    font-size: 13px;
    padding: 10px;
}
QPlainTextEdit#contentView {
    background: transparent;
    border: none;
    padding: 0;
    font-size: 13px;
}
QTableWidget {
    background: @SURFACE@;
    color: @TEXT@;
    alternate-background-color: @ELEVATED@;
    gridline-color: @BORDER_SUBTLE@;
    border: 1px solid @BORDER@;
    border-radius: @RADIUS@px;
    selection-background-color: @SELECTED@;
    selection-color: @TEXT@;
    outline: none;
}
QTableWidget::item { padding: 6px; }
QTableWidget::item:hover { background: @HOVER@; }
QTableWidget::item:selected { background: @SELECTED@; color: @TEXT@; }
QHeaderView::section {
    background: @ELEVATED@;
    color: @TEXT_SECONDARY@;
    border: none;
    border-bottom: 1px solid @BORDER@;
    padding: 8px;
}
QLineEdit, QPlainTextEdit, QComboBox {
    background: @INPUT@;
    color: @TEXT@;
    border: 1px solid @INPUT_BORDER@;
    border-radius: 6px;
    padding: 6px 8px;
    selection-background-color: @ACCENT@;
    selection-color: @ON_ACCENT@;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border: 1px solid @FOCUS@;
}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {
    color: @DISABLED@;
    background: @SURFACE@;
}
QLineEdit[error="true"] { border: 1px solid @ERROR@; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: @ELEVATED@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    selection-background-color: @SELECTED@;
    outline: none;
}
QPushButton {
    background: @HOVER@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 4px;
    padding: 3px 10px;
    min-height: 22px;
}
QPushButton:hover { background: @HOVER@; }
QPushButton:pressed { background: @SELECTED@; }
QPushButton:disabled {
    color: @DISABLED@;
    background: @SURFACE@;
    border-color: @BORDER_SUBTLE@;
}
QPushButton[role="primary"] {
    background: @ACCENT@;
    color: @ON_ACCENT@;
    border: none;
}
QPushButton[role="primary"]:hover { background: @ACCENT_HOVER@; }
QPushButton[role="primary"]:pressed { background: @ACCENT@; }
QPushButton[role="primary"]:disabled { background: @SURFACE@; color: @DISABLED@; }
QPushButton[role="danger"] { color: @ERROR@; }
QPushButton[role="danger"]:hover { background: @HOVER@; }
QPushButton[role="filter"] {
    background: transparent;
    color: @TEXT_SECONDARY@;
    border: none;
    padding: 4px 8px;
    min-height: 24px;
}
QPushButton[role="filter"]:hover { background: @HOVER@; color: @TEXT@; }
QPushButton[role="filter"]:checked {
    background: @SELECTED@;
    color: @TEXT@;
}
QPushButton[role="quiet"] {
    background: transparent;
    color: @TEXT_SECONDARY@;
    border: none;
    padding: 4px 8px;
}
QPushButton[role="quiet"]:hover { background: @HOVER@; color: @TEXT@; }
QPushButton[role="quiet"]:disabled { color: @DISABLED@; background: transparent; }
QCheckBox { color: @TEXT@; spacing: 8px; background: transparent; }
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid @BORDER@;
    border-radius: 3px;
    background: @INPUT@;
}
QCheckBox::indicator:hover { border-color: @FOCUS@; }
QCheckBox::indicator:checked { background: @ACCENT@; border-color: @ACCENT@; }
QCheckBox:disabled { color: @DISABLED@; }
QScrollBar:vertical, QScrollBar:horizontal {
    background: @SURFACE@;
    border: none;
    margin: 0;
}
QScrollBar:vertical { width: 10px; }
QScrollBar:horizontal { height: 10px; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: @BORDER@;
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: @MUTED@; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QProgressBar {
    background: @INPUT@;
    color: @TEXT@;
    border: 1px solid @BORDER@;
    border-radius: 4px;
    text-align: center;
    min-height: 14px;
}
QProgressBar::chunk {
    background: @ACCENT@;
    border-radius: 3px;
}
QSplitter::handle { background: @BORDER_SUBTLE@; }
QMessageBox QLabel { color: @TEXT@; }
"""


def stylesheet() -> str:
    text = _QSS
    for key, value in _TOKENS.items():
        text = text.replace(f"@{key}@", value)
    return text


def app_font() -> QFont:
    font = QFont("Microsoft YaHei UI")
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPointSize(10)
    return font


def restyle(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def apply_theme() -> None:
    app = QApplication.instance()
    if app is None:
        return
    app.setStyle("Fusion")
    app.setFont(app_font())
    palette = QPalette()
    window = QColor(WINDOW_BACKGROUND)
    text = QColor(TEXT_PRIMARY)
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, QColor(INPUT_BACKGROUND))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE_ELEVATED))
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, QColor(SURFACE_ELEVATED))
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.Highlight, QColor(SURFACE_SELECTED))
    palette.setColor(QPalette.ColorRole.HighlightedText, text)
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(SURFACE_ELEVATED))
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    palette.setColor(QPalette.ColorRole.Mid, QColor(BORDER))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet())
