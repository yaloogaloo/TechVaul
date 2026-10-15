"""Slate design tokens and desktop shell smoke checks."""

from __future__ import annotations

from pathlib import Path

from techvault.app.bootstrap import prepare_context
from techvault.ui.desktop_window import DesktopWindow
from techvault.ui.web_scheme import register_scheme

ROOT = Path(__file__).resolve().parents[1]
TOKENS = ROOT / "frontend" / "src" / "design" / "tokens.css"


def test_slate_tokens_define_approved_palette() -> None:
    text = TOKENS.read_text(encoding="utf-8").lower()
    for token in ("#0d1118", "#131b26", "#171f2b", "#304258", "#8ebce9", "#edf3fa"):
        assert token in text


def test_desktop_window_exposes_slate_bridges(qtbot, tmp_path: Path) -> None:
    register_scheme()
    context = prepare_context(tmp_path / "db.sqlite", tmp_path / "archive")
    try:
        window = DesktopWindow(context)
        qtbot.addWidget(window)
        assert window.windowTitle() == "TechVault"
        assert window.minimumWidth() == 960
        assert window.knowledge is not None
        assert window.settings is not None
        assert window.github is not None
        assert window.tasks is not None
        assert window.application is not None
    finally:
        context.close()
