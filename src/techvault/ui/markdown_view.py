"""Shared Markdown reader for knowledge and repository READMEs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QTextBrowser, QWidget

from techvault.markdown.render import rebase_html, render_markdown
from techvault.ui.theme import MARKDOWN_CSS


class _Reader(QTextBrowser):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("markdownView")
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMaximumWidth(760)
        self.document().setDefaultStyleSheet(MARKDOWN_CSS)
        self.document().setDocumentMargin(16)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 80)

    def sizeHint(self) -> QSize:
        return QSize(480, 240)


class MarkdownView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("markdownHost")
        self.browser = _Reader()
        self.browser.anchorClicked.connect(self._open_link)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.browser, 1)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 80)

    def set_markdown(self, source: str, *, base: Path | None = None) -> None:
        html = render_markdown(source, base=base)
        if not html.strip():
            self.set_unavailable("没有可阅读的内容。")
            return
        self.browser.setHtml(html)

    def set_html(self, html: str, *, base: Path | None = None) -> None:
        rewritten = rebase_html(html, base=base)
        if not rewritten.strip():
            self.set_unavailable("没有可阅读的内容。")
            return
        self.browser.setHtml(rewritten)

    def set_unavailable(self, message: str) -> None:
        self.browser.setPlainText(message)

    def plain_text(self) -> str:
        return self.browser.toPlainText()

    def _open_link(self, url: QUrl) -> None:
        if url.scheme() in {"http", "https"}:
            QDesktopServices.openUrl(url)
